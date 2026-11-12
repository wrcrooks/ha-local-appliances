"""Smoke test inside a real Home Assistant: config flow, a simulated washer and
dryer, the start button, the options flow, diagnostics, reload and unload.

Run from the repository root (needs Docker; nothing is published or left behind):

    docker run --rm --entrypoint python3 --tmpfs /config \
      -v "$PWD/custom_components:/config/custom_components:ro" \
      -v "$PWD/tests/ha_smoke.py:/run.py:ro" \
      ghcr.io/home-assistant/home-assistant:stable /run.py

Prints "HA INTEGRATION TEST OK" on success. Last run: Home Assistant 2026.11.1.
"""
import asyncio, json, os, ssl, sys, logging
os.makedirs("/config", exist_ok=True)
open("/config/configuration.yaml", "w").write("logger:\n  default: warning\n  logs:\n    custom_components.thinq_local: debug\n")

from homeassistant import bootstrap, runner
from homeassistant.helpers import device_registry as dr, entity_registry as er

DID = "11111111-2222-3333-4444-555555555555"
DID2 = "99999999-2222-3333-4444-555555555555"
EC_HEAVY_DUTY = "AA3C20EC0019050018011A0200050304000000000410000000050000006400001906001D011E0200000304000000000410000000050000006400FABB"
NORMAL_TURBO_ON = "AA3C20EC001901000100000000000000000000000080000000000000006700001901003B003A010003030400000000400000000000000000670060BB"
EC_DRY = "AA4030EC001B320036003601000305000100000000A90000000100000064000000001B320035003601000305000100000000A90000530100000064000000AFBB"

def ln(n):
    out = bytearray()
    while True:
        d = n % 128; n //= 128
        out.append(d | 0x80 if n else d)
        if not n: return bytes(out)
def pkt(h, body): return bytes([h]) + ln(len(body)) + body
def s(x): x = x.encode(); return len(x).to_bytes(2, "big") + x
def pub(topic, obj): return pkt(0x30, s(topic) + json.dumps(obj).encode())

async def rd(reader):
    while True:
        head = (await reader.readexactly(1))[0]
        n = sh = 0
        while True:
            b = (await reader.readexactly(1))[0]; n |= (b & 127) << sh; sh += 7
            if not b & 128: break
        body = await reader.readexactly(n)
        if head >> 4 == 3:
            tl = int.from_bytes(body[:2], "big"); return json.loads(body[2 + tl:])

async def modem(did, kind, dtype, legacy=False):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    if legacy:  # what old modem firmware offers: TLS 1.2 + ECDHE-CBC-SHA
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2; ctx.set_ciphers("ECDHE-RSA-AES128-SHA:@SECLEVEL=0")
    r, w = await asyncio.open_connection("127.0.0.1", 18883, ssl=ctx, server_hostname="thinq.test")
    w.write(pkt(0x10, s("MQTT") + b"\x04\x02\x00\x3c" + s(did)) + pkt(0x82, b"\x00\x01" + s(f"lime/devices/{did}") + b"\x00"))
    w.write(pub(f"clip/provisioning/devices/{did}", {"mid": 1, "did": did, "kind": kind, "cmd": "deploy", "type": 0,
        "data": {"appInfo": {"modelName": kind, "softVer": "1.0.0", "DeviceType": dtype, "HomeApSsid": "secret"}}}))
    assert (await rd(r))["cmd"] == "completeProvisioning"
    return r, w

async def main():
    hass = await bootstrap.async_setup_hass(runner.RuntimeConfig(config_dir="/config", skip_pip=True, skip_pip_packages=[], recovery_mode=False, verbose=False, log_rotate_days=None, log_file=None, log_no_color=True, debug=False, open_ui=False, safe_mode=False))
    assert hass, "hass failed to start"
    flow = await hass.config_entries.flow.async_init("thinq_local", context={"source": "user"})
    assert flow["type"] == "form", flow
    bad = await hass.config_entries.flow.async_configure(flow["flow_id"], {"hostname": "192.168.1.5", "https_port": 18443, "mqtts_port": 18883, "bind": "127.0.0.1"})
    assert bad["errors"] == {"hostname": "invalid_hostname"}, bad
    done = await hass.config_entries.flow.async_configure(flow["flow_id"], {"hostname": "thinq.test", "https_port": 18443, "mqtts_port": 18883, "bind": "127.0.0.1"})
    assert done["type"] == "create_entry", done
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries("thinq_local")[0]
    assert entry.state.value == "loaded", entry.state
    assert os.path.exists("/config/.storage/thinq_local/ca.key")

    # washer (known model), over a legacy cipher suite
    r, w = await modem(DID, "T1789EFH_F", "201", legacy=True)
    q = await rd(r); assert q["data"] == "AA0EF0ED1121010000001800B5BB", q
    w.write(pub(f"clip/message/devices/{DID}", {"did": DID, "cmd": "device_packet", "data": NORMAL_TURBO_ON, "type": 1}))
    await asyncio.sleep(0.5); await hass.async_block_till_done()
    names = sorted(st.entity_id for st in hass.states.async_all() if "lg_washer" in st.entity_id)
    print(len(names), "washer entities:", names)
    g = lambda e: hass.states.get(e).state
    assert g("sensor.lg_washer_status") == "Standby", g("sensor.lg_washer_status")
    assert g("sensor.lg_washer_course") == "Normal"
    assert g("sensor.lg_washer_remaining_time") == "59", g("sensor.lg_washer_remaining_time")
    assert g("binary_sensor.lg_washer_turbo_wash") == "on"
    assert g("binary_sensor.lg_washer_door_lock") == "on"
    print("remaining_time attrs:", dict(hass.states.get("sensor.lg_washer_remaining_time").attributes))
    await hass.services.async_call("button", "press", {"entity_id": "button.lg_washer_start"}, blocking=True)
    cmd = await rd(r); assert cmd["data"] == "AA15F02601030304000000000000000040000376BB", cmd
    w.write(pub(f"clip/message/devices/{DID}", {"did": DID, "cmd": "device_packet", "data": EC_HEAVY_DUTY, "type": 1}))
    await asyncio.sleep(0.3)
    assert g("sensor.lg_washer_status") == "Rinsing"

    # unknown model -> generic, then pointed at the dryer profile through options
    r2, w2 = await modem(DID2, "RV13_SOMETHING_NEW", "202")
    await rd(r2)
    w2.write(pub(f"clip/message/devices/{DID2}", {"did": DID2, "cmd": "device_packet", "data": EC_DRY, "type": 1}))
    await asyncio.sleep(0.5); await hass.async_block_till_done()
    gen = sorted(st.entity_id for st in hass.states.async_all() if "rv13" in st.entity_id)
    print("generic entities:", gen)
    assert g("sensor.lg_rv13_something_new_last_frame_type") == "30 ec (60 bytes)"

    from custom_components.thinq_local.diagnostics import async_get_config_entry_diagnostics
    diag = await async_get_config_entry_diagnostics(hass, entry)
    json.dumps(diag)
    assert diag["devices"][DID]["app_info"]["HomeApSsid"] == "**REDACTED**"
    assert diag["devices"][DID2]["frames"][-1][2] == EC_DRY.lower()

    opt = await hass.config_entries.options.async_init(entry.entry_id)
    assert opt["type"] == "form", opt
    res = await hass.config_entries.options.async_configure(opt["flow_id"], {"device": DID2, "profile": "RV13U6AM8W_D_US_WIFI"})
    assert res["type"] == "create_entry", res
    await hass.async_block_till_done(); await asyncio.sleep(0.5); await hass.async_block_till_done()
    assert entry.state.value == "loaded"
    # reload dropped the connections; entities exist but are unavailable until the appliance returns
    assert g("sensor.lg_washer_status") == "unavailable", g("sensor.lg_washer_status")
    assert hass.states.get("sensor.lg_rv13_something_new_last_frame_type") is None
    r2, w2 = await modem(DID2, "RV13_SOMETHING_NEW", "202")
    await rd(r2)
    w2.write(pub(f"clip/message/devices/{DID2}", {"did": DID2, "cmd": "device_packet", "data": EC_DRY, "type": 1}))
    await asyncio.sleep(0.5); await hass.async_block_till_done()
    dry = sorted(st.entity_id for st in hass.states.async_all() if "dryer" in st.entity_id)
    print("dryer entities:", dry)
    assert g("sensor.lg_dryer_status") == "Drying" and g("sensor.lg_dryer_remaining_time") == "53"
    w2.close(); await asyncio.sleep(0.3)
    assert g("sensor.lg_dryer_status") == "unavailable"

    devs = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    print("devices:", [(d.name, d.model, d.model_id, d.sw_version) for d in devs])
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()
    print("HA INTEGRATION TEST OK")

asyncio.run(main())
