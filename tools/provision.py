#!/usr/bin/env python3
"""Point an LG ThinQ2 appliance at your own network - without the LG app.

Put the appliance into Wi-Fi setup mode, join this computer to the access
point it opens (SSID `LGE_xxx_yyyy`, password `yyyy` repeated twice), then:

    python3 provision.py "<home wifi ssid>" "<home wifi password>"

The appliance joins your Wi-Fi and looks for its cloud at
https://common.lgthinq.com/route - which your LAN's DNS must answer with the
Home Assistant machine. Standard library only.

A Python port of the ThinQ2 branch of rethink-setup
(https://github.com/anszom/rethink, GPL-2.0).
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import ssl
import sys
import warnings

# The key the module encrypts its enrolment token with. Any cloud that does
# not check the token (ours) can use a fixed one; this is rethink's.
# No whitespace inside the PEM body: newer (RTL8720) firmware parses strictly.
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApYRAZXRWijMuWNr9LHOJ
fcPcZHDYcO3CwRF9olsPvtJpkrDXR7jEDA6qPHF1jvJ7ArxDLVj8rbkwXb3oXNmN
Sc+n0DPNDiRgghDaDyJpN0qfzmt06MKdihVScwghyYKWD+oA9d1+j3wy3W32he+X
7FnS+yUmmbQ8cT0PYS7p2E8YtbgHrH+SbUzHAgBbaS8E92l7f0qOpQFmYEyP/OX+
1n0dLdXXJ8kFxCLP2n8Wy6XXTutrT0YuZCxabPVYSKsjLh86MuHEM6V8BdBoZItW
qA1bDeDvjP7QC93lGxmwIYR0H8VVQq7gBZYWpPfsRSfwsE/PCMrF1WS4sPnSauaV
QwIDAQAB
-----END PUBLIC KEY-----
"""

PORT = 5500
REDACT = {"password", "ssid", "publicKey", "encrypt_val"}


def read_json(sock: ssl.SSLSocket) -> dict:
    """Read one JSON object: the protocol has no delimiters between messages."""
    buf = bytearray()
    depth = 0
    in_string = escaped = False
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("the appliance closed the connection")
        byte = chunk[0]
        buf.append(byte)
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
        elif byte in (0x7D, 0x5D):
            depth -= 1
            if depth == 0:
                return json.loads(buf)


def show(direction: str, message: dict) -> None:
    data = {
        k: ("<redacted>" if k in REDACT else v)
        for k, v in (message.get("data") or {}).items()
    }
    print(f"{direction} {message.get('cmd')}: {json.dumps(data)}")


def call(sock: ssl.SSLSocket, cmd: str, data: dict) -> dict:
    request = {"type": "request", "cmd": cmd, "data": data}
    show("->", request)
    sock.sendall(json.dumps(request).encode())
    while True:
        reply = read_json(sock)
        show("<-", reply)
        if reply.get("type") == "response" and reply.get("cmd") == cmd:
            result = (reply.get("data") or {}).get("result")
            if result and result != "000":
                raise RuntimeError(f"{cmd} failed with result {result}")
            return reply.get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ssid", help="your home Wi-Fi network (2.4 GHz)")
    parser.add_argument("password", help="its passphrase")
    parser.add_argument(
        "--host",
        default="192.168.120.254",
        help="appliance address on its own access point (default %(default)s; "
        "use 192.168.1.1 if its AP hands out 192.168.1.x addresses)",
    )
    # The defaults are the values rethink-setup sends and has been proven with,
    # on US appliances too. They only label the enrolment; override at your own risk.
    parser.add_argument("--country", default="DE", help="default %(default)s")
    parser.add_argument("--region", default="eic", help="LG region code (default %(default)s)")
    parser.add_argument("--timezone", default="+0100", help="UTC offset (default %(default)s)")
    args = parser.parse_args()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # every module serves the same self-signed cert
    ctx.set_ciphers("ALL:@SECLEVEL=0")
    ctx.options |= 0x4  # SSL_OP_LEGACY_SERVER_CONNECT: old modules lack RFC 5746
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1  # some modules are that old
        except ValueError:
            pass

    print(f"connecting to {args.host}:{PORT}")
    with socket.create_connection((args.host, PORT), timeout=20) as raw:
        with ctx.wrap_socket(raw) as sock:
            keep = {"constantConnect": "Y"}
            call(sock, "setDeviceInit", {"set": "true", **keep})
            info = call(
                sock,
                "getDeviceInfo",
                {
                    "subCountryCode": args.country,
                    "regionalCode": args.region,
                    "timezone": args.timezone,
                    "publicKey": PUBLIC_KEY,
                    **keep,
                },
            )
            if "encryptRes:ffff" in str(info.get("extra", "")):
                print("warning: the module could not use the setup key; enrolment will likely fail")
            call(
                sock,
                "setCertInfo",
                {
                    "otp": "0123456789abcdef0123456789abcdef0123456789abcdef",
                    "svccode": "SVC202",
                    # OP is the production default; QA/ST would enable the debug UART
                    "svcphase": "OP",
                    **keep,
                },
            )
            call(
                sock,
                "setApInfo",
                {
                    "format": "B64",
                    "ssid": base64.b64encode(args.ssid.encode()).decode(),
                    "password": base64.b64encode(args.password.encode()).decode(),
                    "security": "WPA2_PSK",
                    "cipher": "AES",
                    **keep,
                },
            )
            call(sock, "releaseDev", {})

    print(
        f"\nDone: {info.get('modelName', 'the appliance')} "
        f"(type {info.get('deviceType', '?')}, uuid {info.get('uuid', '?')}) will now join "
        f"'{args.ssid}'.\nWatch Home Assistant for a new LG device. If it sits in "
        "'connecting' for minutes, power-cycle the appliance once."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as err:
        sys.exit(f"setup failed: {err}")
