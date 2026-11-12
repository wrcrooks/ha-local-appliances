#!/usr/bin/env python3
"""Pre-flight check: can this Samsung appliance be controlled locally?

    python3 probe.py <appliance ip>

Samsung appliances are OCF devices. Local control needs a credential the
appliance accepts on its DTLS port, and which kind it wants is readable
without any credential at all, from its plaintext CoAP port. This asks, and
says which of the LocalThings setup paths to expect. It only reads public
discovery resources; it changes nothing on the appliance. Standard library
only.
"""

from __future__ import annotations

import os
import socket
import sys

COAP_PORT = 5683
LEGACY_PORT = 8888  # 2018-2022 appliances: HTTPS + device token
ACCEPT_CBOR = 60

# /oic/sec/doxm "sct" (supported credential types) bits
SCT = {1: "symmetric pair-wise key (PSK)", 2: "symmetric group key", 4: "asymmetric key",
       8: "certificate", 16: "PIN"}
OXM = {0: "just works", 1: "random PIN", 2: "manufacturer certificate",
       0xFF00: "Samsung vendor method (0xFF00)", 0xFF01: "Samsung vendor method (0xFF01)",
       0xFF02: "Samsung vendor method (0xFF02)"}


# -- just enough CBOR ---------------------------------------------------------
def cbor(data: bytes, pos: int = 0):
    head = data[pos]
    major, info = head >> 5, head & 0x1F
    pos += 1
    if info < 24:
        arg = info
    elif info < 28:
        size = 1 << (info - 24)
        arg = int.from_bytes(data[pos : pos + size], "big")
        pos += size
    elif info == 31:
        arg = None  # indefinite length
    else:
        raise ValueError("bad CBOR")

    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if arg is None:
            out = b""
            while data[pos] != 0xFF:
                chunk, pos = cbor(data, pos)
                out += chunk if isinstance(chunk, bytes) else chunk.encode()
            pos += 1
        else:
            out, pos = data[pos : pos + arg], pos + arg
        return (out.decode("utf-8", "replace") if major == 3 else out), pos
    if major in (4, 5):
        items = []
        while (arg is None and data[pos] != 0xFF) or (arg is not None and len(items) < arg * (major - 3)):
            item, pos = cbor(data, pos)
            items.append(item)
        if arg is None:
            pos += 1
        if major == 4:
            return items, pos
        return dict(zip(items[::2], items[1::2])), pos
    if major == 6:
        return cbor(data, pos)
    if info in (20, 21):
        return info == 21, pos
    if info in (22, 23):
        return None, pos
    return arg, pos  # floats: not needed here


# -- just enough CoAP ---------------------------------------------------------
def option(delta: int, value: bytes) -> bytes:
    def nibble(n: int) -> tuple[int, bytes]:
        return (n, b"") if n < 13 else (13, bytes([n - 13]))

    d, d_ext = nibble(delta)
    length, l_ext = nibble(len(value))
    return bytes([d << 4 | length]) + d_ext + l_ext + value


def get(ip: str, path: str, query: str = "", timeout: float = 2.0):
    """One confirmable GET. Returns (code, payload) or None on silence."""
    token = os.urandom(4)
    message = bytes([0x44, 0x01]) + os.urandom(2) + token
    number = 0
    for segment in path.strip("/").split("/"):
        message += option(11 - number, segment.encode())
        number = 11
    if query:
        message += option(15 - number, query.encode())
        number = 15
    message += option(17 - number, bytes([ACCEPT_CBOR]))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(3):
            sock.sendto(message, (ip, COAP_PORT))
            try:
                while True:
                    # replies may come from another source port; match the token
                    data, peer = sock.recvfrom(8192)
                    if peer[0] == ip and data[4 : 4 + (data[0] & 0x0F)] == token and data[1]:
                        break
            except socket.timeout:
                continue
            code = data[1]
            body = data[4 + (data[0] & 0x0F) :]
            marker = body.find(b"\xff")
            return code, (body[marker + 1 :] if marker >= 0 else b"")
    return None


def tcp_open(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=2):
            return True
    except OSError:
        return False


def first_map(value):
    while isinstance(value, list) and value:
        value = value[0]
    return value if isinstance(value, dict) else {}


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ip = sys.argv[1]
    print(f"Probing {ip}\n")

    legacy = tcp_open(ip, LEGACY_PORT)
    print(f"TCP {LEGACY_PORT} (older HTTPS interface): {'open' if legacy else 'closed'}")

    reply = get(ip, "/oic/sec/doxm")
    if reply is None:
        print(f"UDP {COAP_PORT} (OCF discovery): no answer")
        if legacy:
            print("\n=> Older-generation interface. LocalThings will ask for a device token.")
            return 0
        print("\n=> Nothing answered. Check the IP, that the fridge is on Wi-Fi, and that this\n"
              "   machine is on the same VLAN (appliances ignore routed discovery).")
        return 1

    code, payload = reply
    print(f"UDP {COAP_PORT} (OCF discovery): answered, CoAP code {code >> 5}.{code & 31:02d}")
    doxm = {}
    try:
        doxm = first_map(cbor(payload)[0]) if payload else {}
    except (ValueError, IndexError) as err:
        print(f"  could not decode the reply ({err}); raw: {payload.hex()}")

    if doxm:
        sct = doxm.get("sct")
        print(f"  device id        : {doxm.get('deviceuuid')}")
        print(f"  owned            : {doxm.get('owned')}  (owner {doxm.get('devowneruuid')})")
        if isinstance(sct, int):
            kinds = [name for bit, name in SCT.items() if sct & bit]
            print(f"  credential types : {sct} = {', '.join(kinds) or 'none'}")
        oxms = doxm.get("oxms")
        if isinstance(oxms, list):
            print("  ownership methods: " + ", ".join(OXM.get(o, hex(o)) for o in oxms))

        print()
        if isinstance(sct, int) and sct & 8:
            print("=> Certificate-capable: most likely the generation LocalThings sets up from the\n"
                  "   IP address alone. Add it in Home Assistant; only trying it settles the matter.")
        elif isinstance(sct, int) and sct & 1:
            print("=> PSK-only (newer OCF-PKI generation). LocalThings will refuse the automatic\n"
                  "   certificate and ask for an owner pre-shared key, which nobody can mint for\n"
                  "   you yet. See README: fall back to sensors, and watch localthings issue #435.")
        else:
            print("=> Unrecognised credential types; try LocalThings and keep this output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
