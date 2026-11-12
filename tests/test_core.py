"""Tests for the Home Assistant independent core.

Run from the repository root:   python3 -m unittest discover -s tests -v

The appliance frames are real captures published with the rethink project's
test-suite (WT7300CW washer, DLE7300WE dryer). The end-to-end test plays a
ThinQ2 Wi-Fi module against the real HTTPS and MQTT listeners.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import ssl
import sys
import tempfile
import types
import unittest
from pathlib import Path

# Load `core` without importing the integration's __init__ (which needs
# Home Assistant).
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "thinq_local"
pkg = types.ModuleType("thinq_local")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("thinq_local", pkg)

from thinq_local.core import mqtt  # noqa: E402
from thinq_local.core.cloud import CloudConfig, ThinqCloud  # noqa: E402
from thinq_local.core.devices import PROFILES, profile_for  # noqa: E402
from thinq_local.core.devices.aabb import ack_for, frame, unframe  # noqa: E402
from thinq_local.core.pki import CertAuthority  # noqa: E402

assert importlib.util.find_spec("cryptography"), "cryptography is required"


class FakeSession:
    connected = True
    device_type = "201"

    def __init__(self):
        self.sent: list[str] = []
        self.acks: list[str] = []
        self.on_packet = None

    def send_packet(self, data: bytes) -> None:
        self.sent.append(data.hex())

    def send_ack(self, data: bytes) -> None:
        self.acks.append(data.hex())


def make(profile: str):
    appliance = PROFILES[profile]("dev")
    session = FakeSession()
    appliance.attach(session)
    return appliance, session


def feed(profile: str, *frames: str) -> dict:
    appliance, session = make(profile)
    for hexframe in frames:
        session.on_packet(bytes.fromhex(hexframe))
    return appliance.state


# -- washer (T1789EFH_F / WT7300CW) -----------------------------------------
W = "T1789EFH_F"
EC_HEAVY_DUTY = "AA3C20EC0019050018011A0200050304000000000410000000050000006400001906001D011E0200000304000000000410000000050000006400FABB"
EC_RUNNING = "AA3C20EC001905002B00340800030104000000000410000000030000006400001902002B0034080003010400000000041000000005000000640054BB"
EC_SPIN_TO_COMPLETE = "AA3C20EC00190700010019010000030000000000440000000006000000640000190800000019010000030000000000440000000107000000640099BB"
EB_IDLE = "AA2120EB00190000010023000000000000000000400000000208000000670091BB"
E2_STALE_WASHING = "AA2120E2031905012A012A0800030104040000000415010002020000007800BBBB"
NORMAL_TURBO_ON = "AA3C20EC001901000100000000000000000000000080000000000000006700001901003B003A010003030400000000400000000000000000670060BB"
WHITES_EXTRA_RINSE = "AA3C20EC0019010100003B0800010204000000000000000000006B00006B00001901020602050100050205020000004001000000008300008300F9BB"
CONTROL_LOCK = "AA3C20EC00190100390038040001010100000000001000000000000000830000190100390038040001010100000000011000000000000000830068BB"
DELAY_16H = "AA3C20EC00190100390038040001010100000F0000100000000000000083000019010039003804000101010000100000100000000000000083000EBB"
REMOTE_START = "AA3C20EC00190100390038040001010100000000001001000000000000830000190100390038040001010100000000041001000000000000830017BB"
IDENTITY = "AA372031020153414133393935353130330000D05F0000800000000000025341413339393534393033000044FC000040000000000008BB"


class WasherTest(unittest.TestCase):
    def test_status_query_on_connect(self):
        _, session = make(W)
        self.assertEqual(session.sent, ["aa0ef0ed1121010000001800b5bb"])

    def test_current_record_is_the_last_one(self):
        state = feed(W, EC_SPIN_TO_COMPLETE)
        self.assertEqual(state["status"], "End")
        self.assertEqual(state["remaining_time"], 0)
        self.assertTrue(state["power"])
        self.assertEqual(feed(W, EC_RUNNING)["status"], "Paused")

    def test_heavy_duty(self):
        state = feed(W, EC_HEAVY_DUTY)
        self.assertEqual(state["status"], "Rinsing")
        self.assertEqual(state["remaining_time"], 29)
        self.assertEqual(state["initial_time"], 90)
        self.assertEqual(state["course"], "Heavy Duty")
        self.assertEqual(state["spin"], "High")
        self.assertEqual(state["temp"], "Warm")
        self.assertEqual(state["soil"], "Not selected")

    def test_off_reports_nothing_selected(self):
        state = feed(W, EB_IDLE)
        self.assertFalse(state["power"])
        self.assertEqual(state["status"], "Off")
        self.assertEqual(state["remaining_time"], 0)
        self.assertEqual(state["spin"], "Not selected")
        self.assertFalse(state["turbo_wash"])  # the bit is still set in the record

    def test_options(self):
        self.assertTrue(feed(W, NORMAL_TURBO_ON)["turbo_wash"])
        whites = feed(W, WHITES_EXTRA_RINSE)
        self.assertTrue(whites["extra_rinse"])
        self.assertEqual(whites["smart_course"], "Whites")
        self.assertEqual(whites["course"], "Normal")
        self.assertEqual(whites["remaining_time"], 126)
        self.assertTrue(feed(W, CONTROL_LOCK)["child_lock"])
        self.assertEqual(feed(W, DELAY_16H)["delay_wash"], 16 * 60)

    def test_remote_start_and_door_lock(self):
        state = feed(W, REMOTE_START)
        self.assertTrue(state["remote_start"])
        self.assertFalse(state["door_lock"])  # lock class: off = locked
        self.assertTrue(feed(W, NORMAL_TURBO_ON)["door_lock"])

    def test_non_status_frames_are_ignored(self):
        self.assertEqual(feed(W, E2_STALE_WASHING, IDENTITY), {})

    def test_unknown_phase_is_unknown(self):
        bad = "AA2120EB0019FF00010023000000000000000000400000000208000000670091BB"
        self.assertIsNone(feed(W, bad)["status"])

    def test_start_matches_the_lg_app_byte_for_byte(self):
        appliance, session = make(W)
        session.on_packet(bytes.fromhex(NORMAL_TURBO_ON))
        session.sent.clear()
        appliance.press("start")
        self.assertEqual(session.sent, ["aa15f02601030304000000000000000040000376bb"])

    def test_start_while_paused_resumes(self):
        appliance, session = make(W)
        session.on_packet(bytes.fromhex(EC_RUNNING))
        session.sent.clear()
        appliance.press("start")
        self.assertEqual(unframe(bytes.fromhex(session.sent[0]))[-1], 0x01)

    def test_buttons_need_a_status_frame_first(self):
        appliance, session = make(W)
        session.sent.clear()
        appliance.press("start")
        self.assertEqual(session.sent, [])

    def test_frames_are_acknowledged_like_the_cloud_does(self):
        _, session = make(W)
        session.on_packet(bytes.fromhex(IDENTITY))  # type 0x31: acked
        session.on_packet(bytes.fromhex(EB_IDLE))  # bare status: not acked
        self.assertEqual(session.acks, [frame(bytes([0xF0, 0x00, 0x31, 0x04])).hex()])

    def test_listeners_fire_only_on_change(self):
        appliance, session = make(W)
        calls = []
        appliance.listen(lambda: calls.append(1))
        session.on_packet(bytes.fromhex(EB_IDLE))
        session.on_packet(bytes.fromhex(EB_IDLE))
        self.assertEqual(len(calls), 1)


# -- dryer (RV13U6AM8W_D_US_WIFI / DLE7300WE) --------------------------------
D = "RV13U6AM8W_D_US_WIFI"
EB_RECONNECT = "AA2330EB001B010001000100000000000100000000A8000000000000006400000046BB"
EC_DRY_HEAVY_DUTY = "AA4030EC001B320036003601000305000100000000A90000000100000064000000001B320035003601000305000100000000A90000530100000064000000AFBB"
EB_PAUSED = "AA2330EB001B030012001412000002010100000040A8000074320000006400000061BB"
EC_TRUNCATED = "AA4030EC001B330001010301000305000100000040A8000BFD33000000640000000BBB"


class DryerTest(unittest.TestCase):
    def test_mid_cycle(self):
        state = feed(D, EC_DRY_HEAVY_DUTY)
        self.assertEqual(state["status"], "Drying")
        self.assertEqual(state["remaining_time"], 53)
        self.assertEqual(state["course"], "Heavy Duty")
        self.assertEqual(state["dry_level"], "Normal")
        self.assertEqual(state["temp"], "High")
        self.assertTrue(state["drum_running"])
        self.assertFalse(state["error"])
        self.assertEqual(state["error_message"], "OK")

    def test_paused_and_reconnect(self):
        paused = feed(D, EB_PAUSED)
        self.assertEqual(paused["status"], "Paused")
        self.assertEqual(paused["remaining_time"], 18)
        self.assertEqual(paused["temp"], "Low")
        self.assertFalse(paused["drum_running"])
        self.assertEqual(feed(D, EB_RECONNECT)["status"], "Standby")

    def test_malformed_length_is_ignored(self):
        self.assertEqual(feed(D, EC_TRUNCATED), {})


class RegistryTest(unittest.TestCase):
    def test_unknown_model_gets_the_generic_profile(self):
        self.assertEqual(profile_for("SOMETHING_NEW").PROFILE, "generic")
        self.assertEqual(profile_for("T1789EFH_F").PROFILE, W)
        self.assertEqual(profile_for("SOMETHING_NEW", W).PROFILE, W)
        self.assertEqual(profile_for("SOMETHING_NEW", "auto").PROFILE, "generic")

    def test_generic_exposes_raw_frames(self):
        state = feed("generic", EC_HEAVY_DUTY, IDENTITY)
        self.assertEqual(state["frames"], 2)
        self.assertEqual(state["frame_type"], "20 31 (51 bytes)")
        self.assertTrue(state["last_status_frame"].startswith("20ec0019"))


class AabbTest(unittest.TestCase):
    def test_frame_round_trip(self):
        for sample in (EB_IDLE, EC_HEAVY_DUTY, EC_DRY_HEAVY_DUTY):
            raw = bytes.fromhex(sample)
            self.assertEqual(frame(unframe(raw)), raw)

    def test_envelope_ack_carries_the_sequence(self):
        inner = bytes([0x20, 0x0A, 0, 20, 0, 0x12, 0x34, 0, 0, 0, 0xEB, 0, 2, 1, 2])
        self.assertEqual(
            unframe(ack_for(frame(inner))), bytes([0xF0, 0x00, 0x0A, 0x04, 0x12, 0x34])
        )


# -- end to end: a simulated Wi-Fi module -----------------------------------
DID = "11111111-2222-3333-4444-555555555555"


def _mqtt_connect(client_id: str) -> bytes:
    cid = client_id.encode()
    body = b"\x00\x04MQTT\x04\x02\x00\x3c" + len(cid).to_bytes(2, "big") + cid
    return bytes([0x10]) + mqtt.encode_length(len(body)) + body


def _mqtt_subscribe(topic: str) -> bytes:
    raw = topic.encode()
    body = b"\x00\x01" + len(raw).to_bytes(2, "big") + raw + b"\x01"
    return bytes([0x82]) + mqtt.encode_length(len(body)) + body


async def _read_packet(reader) -> tuple[int, bytes]:
    head = (await reader.readexactly(1))[0]
    length = shift = 0
    while True:
        byte = (await reader.readexactly(1))[0]
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    return head >> 4, await reader.readexactly(length)


async def _read_publish(reader) -> dict:
    while True:
        kind, body = await asyncio.wait_for(_read_packet(reader), 5)
        if kind == mqtt.PUBLISH:
            topic_len = int.from_bytes(body[:2], "big")
            return json.loads(body[2 + topic_len :])


async def _http(port: int, ctx, request: bytes) -> dict:
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, ssl=ctx, server_hostname="common.lgthinq.com"
    )
    writer.write(request)
    head = await reader.readuntil(b"\r\n\r\n")
    length = int(
        [l for l in head.decode().split("\r\n") if l.lower().startswith("content-length")][0]
        .split(":")[1]
    )
    body = await reader.readexactly(length)
    writer.close()
    return json.loads(body)


class EndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def test_provision_connect_and_decode(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        opened, closed = [], []
        with tempfile.TemporaryDirectory() as tmp:
            cloud = ThinqCloud(
                CloudConfig(Path(tmp), "thinq.test", "127.0.0.1", 0, 0),
                opened.append,
                closed.append,
            )
            # port 0: let the OS choose, then read the real ports back
            await cloud.start()
            https_port, mqtts_port = (
                s.sockets[0].getsockname()[1] for s in cloud._servers
            )

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            # 1. bootstrap, as an unprovisioned module asking common.lgthinq.com
            route = await _http(https_port, ctx, b"GET /route HTTP/1.1\r\nHost: common.lgthinq.com\r\n\r\n")
            self.assertEqual(route["result"]["apiServer"], "https://thinq.test:0")
            names = await _http(https_port, ctx, b"GET /route/certificate HTTP/1.1\r\nHost: x\r\n\r\n")
            self.assertEqual(names["result"], ["common-server", "aws-iot"])
            root = await _http(https_port, ctx, b"GET /route/certificate?name=aws-iot HTTP/1.1\r\nHost: x\r\n\r\n")
            ca_pem = root["result"]["certificatePem"]

            # 2. CSR
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            csr = (
                x509.CertificateSigningRequestBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, DID)]))
                .sign(key, hashes.SHA256())
            ).public_bytes(serialization.Encoding.PEM).decode()
            body = json.dumps({"csr": csr}).encode()
            signed = await _http(
                https_port,
                ctx,
                f"POST /device/{DID}/certificate HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body,
            )
            self.assertEqual(signed["resultCode"], "0000")
            cert = x509.load_pem_x509_certificate(signed["result"]["certificatePem"].encode())
            ca = x509.load_pem_x509_certificate(ca_pem.encode())
            self.assertEqual(cert.issuer, ca.subject)
            self.assertEqual(cert.serial_number, 0x64)

            # 3. MQTT, verifying the broker against the CA we were just given
            verify = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            verify.load_verify_locations(cadata=ca_pem)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", mqtts_port, ssl=verify, server_hostname="thinq.test"
            )
            writer.write(_mqtt_connect(DID) + _mqtt_subscribe(f"lime/devices/{DID}"))
            deploy = {
                "mid": 1, "did": DID, "kind": "T1789EFH_F", "cmd": "deploy", "rssi": -50,
                "data": {"appInfo": {"modelName": "T1789EFH_F", "softVer": "1.2.3", "DeviceType": "201"}},
                "type": 0,
            }
            writer.write(mqtt.publish_packet(f"clip/provisioning/devices/{DID}", json.dumps(deploy).encode() + b"\x00"))
            reply = await _read_publish(reader)
            self.assertEqual(reply["cmd"], "completeProvisioning")
            self.assertEqual(reply["data"]["provisioningType"], "deploy")
            self.assertEqual(
                reply["data"]["appInfo"]["publication"]["provisioning"],
                f"clip/provisioning/devices/{DID}",
            )

            session = opened[0]
            self.assertEqual((session.id, session.model_id, session.sw_version), (DID, "T1789EFH_F", "1.2.3"))
            washer = profile_for(session.model_id)(session.id)
            washer.attach(session)
            query = await _read_publish(reader)
            self.assertEqual((query["cmd"], query["data"]), ("packet", "AA0EF0ED1121010000001800B5BB"))

            # 4. time sync and a status frame, on the assigned message topic
            message_topic = f"clip/message/devices/{DID}"
            writer.write(mqtt.publish_packet(message_topic, json.dumps({"did": DID, "cmd": "req_timesync", "data": None, "type": 1}).encode()))
            self.assertEqual((await _read_publish(reader))["cmd"], "resp_timesync")
            packet = {"did": DID, "cmd": "device_packet", "data": EC_HEAVY_DUTY, "type": 1, "rssi": -47}
            writer.write(mqtt.publish_packet(message_topic, json.dumps(packet).encode()))
            for _ in range(50):
                if washer.state:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(washer.state["status"], "Rinsing")
            self.assertEqual(session.rssi, -47)
            self.assertTrue(washer.available)

            # 5. drop
            writer.close()
            for _ in range(50):
                if closed:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(closed, [session])
            self.assertFalse(washer.available)
            await cloud.stop()

    async def test_ca_survives_a_restart_and_is_never_half_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = CertAuthority.load_or_create(Path(tmp))
            again = CertAuthority.load_or_create(Path(tmp))
            self.assertEqual(first.cert_pem, again.cert_pem)
            leaf = first.server_cert_file("thinq.test")
            self.assertEqual(again.server_cert_file("thinq.test").read_bytes(), leaf.read_bytes())
            (Path(tmp) / "ca.key").unlink()
            with self.assertRaises(RuntimeError):
                CertAuthority.load_or_create(Path(tmp))


if __name__ == "__main__":
    unittest.main()
