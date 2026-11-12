#!/bin/sh
# Download the integration from this repository and put it where Home
# Assistant loads custom integrations from. Safe to run again to update.
set -eu

REPO="wrcrooks/ha-local-appliances"
DOMAIN="thinq_local"
CONFIG="/homeassistant"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ ! -f "$CONFIG/configuration.yaml" ] && [ ! -d "$CONFIG/.storage" ]; then
    echo "ERROR: $CONFIG does not look like the Home Assistant config directory"
    exit 1
fi

echo "Downloading $REPO (main)..."
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar xz -C "$WORK"
SRC="$(echo "$WORK"/*/custom_components/"$DOMAIN")"
if [ ! -f "$SRC/manifest.json" ]; then
    echo "ERROR: the download does not contain custom_components/$DOMAIN"
    exit 1
fi

mkdir -p "$CONFIG/custom_components"
rm -rf "$CONFIG/custom_components/$DOMAIN"
cp -r "$SRC" "$CONFIG/custom_components/$DOMAIN"

VERSION="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$SRC/manifest.json")"
echo "Installed LG ThinQ Local $VERSION to $CONFIG/custom_components/$DOMAIN"
echo "Now restart Home Assistant, then add the integration:"
echo "  Settings > Devices & services > Add integration > LG ThinQ Local"
