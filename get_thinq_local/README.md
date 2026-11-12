# Get LG ThinQ Local

Installs the [LG ThinQ Local](https://github.com/wrcrooks/ha-local-appliances)
integration, the same way "Get HACS" installs HACS: it copies the integration
into `/config/custom_components/thinq_local` and stops. Nothing keeps running.

1. Install this app and press **Start**. The Log tab shows the result.
2. Restart Home Assistant.
3. Settings > Devices & services > Add integration > **LG ThinQ Local**.

Start it again at any time to update the integration to the latest version,
then restart Home Assistant. Your settings and the appliance CA live under
`/config/.storage` and are not touched.

Once the integration is installed you can uninstall this app.
