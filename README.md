# ha-local-appliances

Local, cloud-free Home Assistant control for three appliances:

| Appliance | Folder | Approach | State |
|---|---|---|---|
| LG WT8400CB washer | [`lg_thinq_local/`](lg_thinq_local/) | Home Assistant impersonates the ThinQ cloud on the LAN | Integration written and tested against simulated appliances; **not yet run against the real machines** |
| LG DLE8400BE dryer | [`lg_thinq_local/`](lg_thinq_local/) | same | same |
| Samsung RF90F29EECR fridge | [`samsung_fridge/`](samsung_fridge/) | Existing [LocalThings](https://github.com/mbillow/localthings) integration (OCF over CoAP/DTLS) | Nothing to write; a pre-flight probe and setup notes are here |

## Why it is split this way

Neither vendor offers a local API, so both halves rest on reverse engineering
done by others.

**LG.** A ThinQ Wi-Fi module ships with no trust root: at setup it asks "its
cloud" for one, then only ever talks to that host. Answer the setup yourself
and the appliance is yours. The [rethink](https://github.com/anszom/rethink)
project worked this out and ships a Node.js server that bridges to MQTT.
`lg_thinq_local` is the same protocol as a native Home Assistant integration:
no add-on, no Node runtime, no MQTT broker, no polling - two listening sockets
inside the HA process. That is the "light" part, and why it is new code.

**Samsung.** The fridge is an OCF device and LocalThings already speaks to it
from inside Home Assistant, with a test corpus that includes a 2025 fridge
board. Rewriting that would be heavier and worse, so this repo does not.

## What is and is not cloud-free

- LG: fully local, including initial setup (`tools/provision.py` replaces the
  LG app). The LG app and LG firmware updates stop working for these machines.
- Samsung: local in operation, **not** at setup. The fridge only gets onto
  Wi-Fi through the SmartThings app and a Samsung account, and it must stay
  registered there. Details and the firewall posture are in
  [`samsung_fridge/README.md`](samsung_fridge/README.md).

## Start here

1. LG: [`lg_thinq_local/README.md`](lg_thinq_local/README.md) - install, two
   DNS records, provision each appliance.
2. Samsung: run `samsung_fridge/probe.py <fridge ip>`, then follow its README.
