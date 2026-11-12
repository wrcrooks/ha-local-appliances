# LG ThinQ Local

A Home Assistant custom integration (`thinq_local`) that stands in for the LG
ThinQ cloud on your LAN. Appliances connect to Home Assistant instead of LG;
their status frames become entities directly. No add-on, no MQTT broker, no
polling, no internet.

Protocol knowledge is from the [rethink](https://github.com/anszom/rethink)
project (GPL-2.0, as is this repository). If you would rather run the
original, its Home Assistant add-on does the same job with far more models
decoded; this integration trades model coverage for running inside the Home
Assistant process with nothing else to install.

Also in this repository: [`samsung_fridge/`](samsung_fridge/), a read-only
probe and notes on local control of Samsung appliances. It is separate from
the integration.

## Status

| | |
|---|---|
| Unit tests (`tests/test_core.py`) | pass - decoders checked against real WT7300CW / DLE7300WE captures, plus a simulated Wi-Fi module going through enrolment and MQTT over TLS |
| Inside real Home Assistant (`tests/ha_smoke.py`) | passes on 2026.11.1 - config flow, entities, start button, options, diagnostics, reload |
| Real appliances | **untested** - this code has not talked to real hardware yet |

Decoders shipped:

| Profile (ThinQ model id) | Sold as | Entities |
|---|---|---|
| `T1789EFH_F` | WT7300CW top-load washer | status, times, course, options, locks; start / pause / power off |
| `RV13U6AM8W_D_US_WIFI` | DLE7300WE electric dryer | status, time, course, temperature, dry level, error (read-only) |
| `generic` | anything else | raw frames only |

An appliance announces its ThinQ model id when it first connects. Any other
model lands on `generic` at first - see
[When a model is not recognised](#when-a-model-is-not-recognised).

## Install

### From the Apps page (Home Assistant OS)

1. Settings > Apps > Install app > three-dot menu > **Repositories**. Add
   `https://github.com/wrcrooks/ha-local-appliances`.
2. Install **Get LG ThinQ Local** and press **Start**. It copies the
   integration into `/config/custom_components/` and stops; nothing keeps
   running. Its Log tab shows the result.
3. Restart Home Assistant.

Start the app again to update the integration later.

### With HACS

1. HACS > three-dot menu > **Custom repositories**. Add
   `https://github.com/wrcrooks/ha-local-appliances` with type **Integration**.
2. Find **LG ThinQ Local** in HACS, download it, and restart Home Assistant.

### Manually

Copy `custom_components/thinq_local/` into `/config/custom_components/` and
restart Home Assistant.

### Then

1. On your LAN's DNS server (router, Pi-hole, ...) add two records pointing at
   the Home Assistant machine:
   - a name of your choice, e.g. `thinq.lan` - permanent
   - `common.lgthinq.com` - needed only while provisioning; remove it after

   Give them a non-zero TTL. The Wi-Fi modules use plain DNS: `.local` names
   and bare IP addresses do not work.
2. Settings > Devices & services > Add integration > **LG ThinQ Local**. Enter
   the hostname from step 1. Keep ports 443 and 8883 unless you forward them;
   appliances look for 443 during setup.

   **If port 443 is already taken on the Home Assistant host** (a reverse
   proxy app, for example), the form says so. Then:
   - set "HTTPS port to listen on" to a free port such as `8443`
   - set "HTTPS port appliances connect to" to `443`
   - on your router, forward (DNAT) TCP 443 addressed to the Home Assistant
     IP to port 8443, for traffic coming from the appliances only

   The same applies to MQTT port 8883, which the Mosquitto app occupies:
   either free it in that app's network settings, or listen on e.g. `18883`,
   set "MQTT port appliances connect to" to `8883`, and forward 8883 to 18883.

   That forward only works if the appliances reach Home Assistant through the
   router, i.e. they are on a different VLAN/subnet.

The first start generates a CA under `/config/.storage/thinq_local/`. The
appliances pin it. It is included in Home Assistant backups; if it is lost,
every appliance has to be provisioned again.

## Provision an appliance

Needs a computer with Wi-Fi and Python 3 (no packages), and
[`tools/provision.py`](tools/provision.py) from this repository.

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

The LG app and LG firmware updates stop working for an appliance provisioned
this way. To undo: reset the appliance's Wi-Fi and set it up with the LG app,
with the `common.lgthinq.com` DNS record removed.

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
get_thinq_local/   one-shot installer app for the Apps page
tools/provision.py Wi-Fi setup without the LG app
samsung_fridge/    separate: OCF probe and notes for Samsung appliances
```
