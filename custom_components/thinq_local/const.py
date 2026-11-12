"""Constants for the LG ThinQ Local integration."""

DOMAIN = "thinq_local"

CONF_HOSTNAME = "hostname"
CONF_BIND = "bind"
CONF_HTTPS_PORT = "https_port"
CONF_MQTTS_PORT = "mqtts_port"
# what appliances are told to dial when 443 is forwarded to https_port
CONF_HTTPS_ADVERTISE_PORT = "https_advertise_port"
CONF_MQTTS_ADVERTISE_PORT = "mqtts_advertise_port"

DEFAULT_HOSTNAME = "thinq.lan"
DEFAULT_HTTPS_PORT = 443
DEFAULT_MQTTS_PORT = 8883

# options: {device id: profile name} for models no profile claims by itself
OPT_PROFILES = "profiles"
