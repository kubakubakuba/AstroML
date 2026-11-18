#!/usr/bin/env bash
# Sets up a Raspberry Pi (Raspberry Pi OS Bookworm, NetworkManager) as the AstroML companion:
#   - installs the app into /opt/astroml-companion
#   - lets it use Canon cameras on USB (udev rule)
#   - creates an open Wi-Fi hotspot "AstroML" at 10.42.0.1 whose DNS points every name at the
#     Pi, so phones open the status page as the network's login page
#   - starts the app at boot (systemd)
# Usage: sudo ./install.sh   (from the companion/setup directory)
# The hotspot takes over the Wi-Fi interface: run it from a local console, Ethernet or USB
# gadget networking, not over a Wi-Fi SSH session (that session drops when the hotspot starts).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/astroml-companion
SSID="${ASTROML_SSID:-AstroML}"
WIFI_IF="${ASTROML_WIFI_IF:-wlan0}"
ADDRESS=10.42.0.1

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo $0" >&2
    exit 1
fi

echo "== packages"
apt-get install -y --no-install-recommends python3-venv libusb-1.0-0 python3-gpiozero

echo "== app in $PREFIX"
id astroml >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin astroml
# plugdev: the camera (udev rule below); dialout: the mount's USB-serial cable; gpio: ST-4 board.
for group in plugdev dialout gpio; do
    getent group "$group" >/dev/null && usermod -aG "$group" astroml
done
# System packages stay visible: gpiozero and its GPIO backend ship with Raspberry Pi OS.
python3 -m venv --system-site-packages "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$HERE/.."

echo "== USB access"
install -m 0644 "$HERE/99-astroml-canon.rules" /etc/udev/rules.d/
udevadm control --reload-rules
# gphoto2's desktop monitor grabs cameras as soon as they appear; the app needs the camera.
if [[ -e /usr/lib/systemd/user/gvfs-gphoto2-volume-monitor.service ]]; then
    systemctl --global mask gvfs-gphoto2-volume-monitor.service
fi

echo "== hotspot $SSID on $WIFI_IF"
nmcli connection delete "$SSID" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$WIFI_IF" con-name "$SSID" autoconnect yes ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared ipv4.addresses "$ADDRESS/24" ipv6.method disabled
# NetworkManager's dnsmasq for shared connections reads this directory: answer every name
# with the Pi, which is what makes phones show the captive "sign in" page.
install -d /etc/NetworkManager/dnsmasq-shared.d
echo "address=/#/$ADDRESS" > /etc/NetworkManager/dnsmasq-shared.d/astroml-captive.conf
nmcli connection up "$SSID"

echo "== service"
install -m 0644 "$HERE/astroml-companion.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now astroml-companion.service

echo "Done. Join the Wi-Fi \"$SSID\"; the status page opens as its login page (or http://$ADDRESS/)."
echo "Check the camera link with: sudo -u astroml $PREFIX/venv/bin/astroml-companion status"
