# Samsung RF90F29EECR (Family Hub fridge)

There is no integration code in this folder on purpose. The fridge is an OCF
device (CoAP over DTLS on the LAN), and
[LocalThings](https://github.com/mbillow/localthings) already drives that
protocol from inside Home Assistant - no add-on, no Samsung account, and its
test corpus includes a 2025 fridge board (`AILITE_REF_25K`). A second
implementation would be a few thousand lines of DTLS and block-wise CoAP to
arrive at the same place.

What is here: `probe.py`, which tells you in a few seconds which setup path
this particular fridge will take.

## What "cloud-free" can mean for this fridge

| Phase | Cloud needed? |
|---|---|
| Getting the fridge onto Wi-Fi | **Yes.** Only the SmartThings app can do it, with a Samsung account. |
| Adding the first appliance to LocalThings | One anonymous request to Samsung to read a public identifier. No account. |
| Day-to-day control and state | No. Home Assistant talks to the fridge directly. |
| The Family Hub screen (apps, cameras, AI Vision) | Yes - a separate computer in the door; unrelated to the integration. |

Two behaviours reported by LocalThings users decide the firewall posture:

- **Do not delete the fridge from SmartThings.** An appliance removed there
  wipes its Wi-Fi settings the next time it reaches Samsung.
- Blocking the fridge's internet access is fine for control, but push updates
  go quiet on some firmware; LocalThings then falls back to polling every 30
  seconds.

So the practical arrangement is: onboard once with the app, leave it
registered, then block the fridge at the router if you want it silent.

## Steps

1. Onboard the fridge with the SmartThings app and give it a DHCP reservation.
2. From a machine on the same VLAN:

   ```
   python3 probe.py <fridge ip>
   ```

   It reads the fridge's public discovery resource and reports which
   credential types it accepts. It writes nothing to the appliance.
3. Install LocalThings (HACS > Integrations > LocalThings, or copy its
   `custom_components/localthings/`), restart, then Settings > Devices &
   services > Add integration > LocalThings and enter the IP.

Expected entities for refrigerators: per-compartment temperatures and
setpoints, door state, rapid cool/freeze, filter status, energy.

## If the fridge refuses the automatic certificate

Some newer Samsung firmware ("OCF-PKI") only accepts a pre-shared key that is
created during SmartThings onboarding, and there is no supported way to
obtain it yet (tracked in
[localthings #435](https://github.com/mbillow/localthings/issues/435);
the one documented recovery needed a rooted phone). `probe.py` flags this
case, and LocalThings will say so when you add the IP.

If that happens the fridge cannot be read locally today, and the fully local
fallback is external sensors: a Zigbee or ESPHome contact sensor per door and
a temperature probe in each compartment cover the automations people actually
use (door left open, temperature alarm) with nothing vendor-side involved.

## Not verified

Nothing here has been run against the actual fridge. Whether the RF90F29EECR
takes the certificate path or the PSK path is unknown until step 2.
