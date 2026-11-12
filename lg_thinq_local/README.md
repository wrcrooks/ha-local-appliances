# LG ThinQ Local (`thinq_local`)

A Home Assistant custom integration that stands in for the LG ThinQ cloud on
your LAN. Appliances connect to Home Assistant instead of LG; their status
frames become entities directly. No add-on, no MQTT broker, no polling, no
internet.

Protocol knowledge is from the [rethink](https://github.com/anszom/rethink)
project (GPL-2.0, as is this folder). If you would rather run the original,
its Home Assistant add-on does the same job with far more models decoded.

## Status

| | |
|---|---|
| Unit tests (`tests/test_core.py`) | pass - decoders checked against real WT7300CW / DLE7300WE captures, plus a simulated Wi-Fi module going through enrolment and MQTT over TLS |
| Inside real Home Assistant (`tests/ha_smoke.py`) | passes on 2026.11.1 - config flow, entities, start button, options, diagnostics, reload |
| Your WT8400CB / DLE8400BE | **untested** - nothing here has talked to real hardware yet |

The decoders shipped are for the closest relatives rethink has verified:

| Profile (ThinQ model id) | Sold as | Entities |
|---|---|---|
| `T1789EFH_F` | WT7300CW top-load washer | status, times, course, options, locks; start / pause / power off |
| `RV13U6AM8W_D_US_WIFI` | DLE7300WE electric dryer | status, time, course, temperature, dry level, error (read-only) |
| `generic` | anything else | raw frames only |

An appliance announces its ThinQ model id when it first connects. The WT8400
and DLE8400 ids are not known yet, so expect them to land on `generic` at
first - see [When a model is not recognised](#when-a-model-is-not-recognised).

## Install

1. Copy `custom_components/thinq_local/` into `/config/custom_components/` and
   restart Home Assistant.
2. On your LAN's DNS server (router, Pi-hole, ...) add two records pointing at
   the Home Assistant machine:
   - a name of your choice, e.g. `thinq.lan` - permanent
   - `common.lgthinq.com` - needed only while provisioning; remove it after

   Give them a non-zero TTL. The Wi-Fi modules use plain DNS: `.local` names
   and bare IP addresses do not work.
3. Settings > Devices & services > Add integration > **LG ThinQ Local**. Enter
   the hostname from step 2. Keep ports 443 and 8883 unless you forward them;
   appliances look for 443 during setup.

   Port 443 must be free on the Home Assistant host. If a reverse-proxy add-on
   owns it, choose another port here and DNAT `:443` to it for the appliance
   VLAN during provisioning.

The first start generates a CA under `/config/.storage/thinq_local/`. The
appliances pin it. It is included in Home Assistant backups; if it is lost,
every appliance has to be provisioned again.

## Provision an appliance

Needs a computer with Wi-Fi and Python 3 (no packages).

1. Put the appliance in Wi-Fi setup mode (see its manual; on most LG laundry,
   hold the Wi-Fi / Remote Start button about 3 seconds until the Wi-Fi light
   blinks).
2. Join the access point it opens: SSID `LGE_xxx_yyyy`, password `yyyy` twice
   (e.g. `LGE_WM_1a2b` / `1a2b1a2b`).
3. `python3 tools/provision.py "<home ssid>" "<home wifi password>"`
4. The appliance joins your Wi-Fi (2.4 GHz), enrols with Home Assistant, and
   appears as a new device within a minute or two.

If it shows "connecting" for several minutes, power-cycle the appliance once;
newer modules need a cold boot after enrolment.

To undo: reset the appliance's Wi-Fi and set it up with the LG app, with the
`common.lgthinq.com` DNS record removed.

## When a model is not recognised

The device appears with four diagnostic sensors and a warning in the log.
Nothing is lost: the link is kept alive and every frame is recorded.

1. **Try a sibling decoder.** Integration > Configure: pick the appliance and
   a profile. The entities are only right if that model shares the record
   layout, so sanity-check them against the control panel through one cycle
   before trusting them - especially before pressing Start.
2. **If the values are wrong**, switch back to `generic`, run a cycle, and
   download diagnostics from the device page. `frames` holds the last 300
   packets as `[time, rx|tx|ack, hex]`. Each status frame is
   `AA <len> <class> <EB|EC> <record...> <checksum> BB`; `EC` carries the
   previous record then the current one. A new profile is one file in
   `core/devices/` (copy `dryer_rv13u6am8w.py`: about 100 lines of offsets and
   code tables) plus a line in `core/devices/__init__.py`. rethink's
   `cloud/devices/` has ~40 worked examples, including newer top-load washers
   that use a longer frame format this integration does not parse yet.
3. If the layout turns out identical to an existing profile, add the model id
   to `ALIASES` in `core/devices/__init__.py` instead.

## Things to know

- **Remote start is armed at the machine.** The washer ignores Start unless
  Remote Start was enabled on its panel, which also locks the lid. Start sends
  back exactly what is dialled in; there is no cycle picker.
- **No client authentication.** As with the real protocol, anything on the LAN
  that can reach ports 443/8883 can enrol as an appliance. Keep those ports
  reachable only from the appliance VLAN.
- **Legacy TLS on those two ports.** The modules only speak TLS 1.2 with old
  cipher suites, so the listeners accept them. Nothing else in Home Assistant
  is affected.
- **Footprint.** Two listening sockets and one small object per appliance in
  the Home Assistant process; idle CPU is zero because appliances push.

## Tests

```
python3 -m unittest discover -s tests -v      # needs only `cryptography`
```

`tests/ha_smoke.py` runs the integration in a throwaway Home Assistant
container; the command is in its docstring.

## Layout

```
custom_components/thinq_local/
  core/            protocol, no Home Assistant imports
    pki.py         CA, server certificates, CSR signing
    http.py        the four enrolment endpoints
    mqtt.py        minimal MQTT 3.1.1 broker
    cloud.py       TLS listeners, enrolment handshake, sessions
    devices/       AA..BB framing and per-model decoders
  __init__.py      hub: persistence, appliance lifecycle
  sensor.py, binary_sensor.py, button.py, diagnostics.py, config_flow.py
tools/provision.py Wi-Fi setup without the LG app
```
