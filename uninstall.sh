#!/usr/bin/env bash
# Removes everything install.sh put on the system and puts the hardware back
# to firmware defaults. Safe to run more than once.
set -uo pipefail
[ "$EUID" -eq 0 ] || { echo "run this with sudo"; exit 1; }
say() { echo "  $*"; }

echo "Reverting hardware settings"
/usr/local/bin/aorusctl reset 2>/dev/null | sed 's/^/  /' || say "aorusctl not available, skipping"

echo "Stopping services"
for u in aorusctld.service aorusctl-web.service aorusctl-profile.service; do
  systemctl disable --now "$u" >/dev/null 2>&1 && say "disabled $u"
  rm -f "/etc/systemd/system/$u"
done
systemctl daemon-reload

echo "Removing kernel module"
rmmod aorus_laptop 2>/dev/null && say "unloaded aorus_laptop"
rm -f /etc/modules-load.d/aorus-laptop.conf
for v in $(dkms status 2>/dev/null | grep '^aorus-laptop' | sed 's|^aorus-laptop[/,] *\([^,]*\).*|\1|' | sort -u); do
  dkms remove "aorus-laptop/$v" --all >/dev/null 2>&1 && say "removed aorus-laptop/$v from DKMS"
  rm -rf "/usr/src/aorus-laptop-$v"
done

echo "Secure Boot key"
say "leaving /var/lib/shim-signed and the enrolled MOK alone - other DKMS modules"
say "on this machine may be signed with it. Remove it with: sudo mokutil --delete <cert>"

echo "Removing files"
rm -f  /usr/local/bin/aorusctl /usr/local/bin/aorus-probe
rm -rf /usr/local/share/aorusctl
rm -rf /var/lib/aorusctl
say "left /etc/aorusctl alone (your config lives there) - delete it by hand if you want it gone"

echo
echo "Done. Reboot is not required; the firmware is back in charge of the fans."
