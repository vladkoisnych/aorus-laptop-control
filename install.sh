#!/usr/bin/env bash
# aorusctl installer.
#
#   sudo ./install.sh                   full install (driver + tool + config + units)
#   sudo ./install.sh --check           report what would happen, change nothing
#   sudo ./install.sh --no-driver       skip the kernel module, install the tool only
#   sudo ./install.sh --driver-dir DIR  build the module from a local checkout instead
#                                       of downloading it (offline installs)
#   sudo ./install.sh --driver-ref REF  build a different upstream commit/branch/tag
#                                       (skips checksum verification, prints a warning)
#
# Everything it installs is listed in uninstall.sh, which removes all of it.

set -uo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DRV_VER="0.2.0"

# The kernel module is not part of this repository. It is Albert Tang's
# gigabyte-laptop-wmi (GPL-2.0-or-later), fetched from upstream at a pinned
# commit and checked against known hashes before anything is built.
DRV_REPO="tangalbert919/gigabyte-laptop-wmi"
DRV_REF="8abb6655109726bca1d4fd869909d2cb0252e380"   # 2026-08-08
SHA_C="1a36ac4c7a61070ac1b3b4e440004fc6c55f2181483cea887b67c14aafd8ede4"
SHA_MK="d56950c5ebefd94358b3f90f3bd2250b9c1d659e796345a1c79af9533ba1be72"
SHA_DK="4eb85714de16092b1a44f0f68ff914505cb82cc1d7fb790e2c037aa5f51011f6"

CHECK=0; NO_DRIVER=0; DRIVER_DIR=""; PINNED=1
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --no-driver) NO_DRIVER=1 ;;
    --driver-dir) DRIVER_DIR="${2:-}"; shift ;;
    --driver-ref) DRV_REF="${2:-}"; PINNED=0; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
  shift
done

# `cmd | grep -q PATTERN` is a trap under `set -o pipefail`: grep -q exits on the
# first match, the writer dies of SIGPIPE, and the pipeline reports 141 even
# though the pattern matched. Capture the output first and match against that.
contains() { case "$2" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'
ok()   { echo "${G}  ok${N}   $*"; }
warn() { echo "${Y}  warn${N} $*"; }
die()  { echo "${R}  fail${N} $*"; exit 1; }
step() { echo; echo "${B}$*${N}"; }
do_() { if [ "$CHECK" = 1 ]; then echo "  would run: $*"; else "$@"; fi; }

[ "$CHECK" = 1 ] || [ "$EUID" -eq 0 ] || die "run this with sudo"

step "1. Checking the machine"
VENDOR=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)
PRODUCT=$(cat /sys/class/dmi/id/product_name 2>/dev/null)
FAMILY=$(cat /sys/class/dmi/id/product_family 2>/dev/null)
echo "  vendor:  $VENDOR"
echo "  product: $PRODUCT"
echo "  family:  $FAMILY"
echo "  kernel:  $(uname -r)"
case "$VENDOR" in
  *GIGABYTE*|*Gigabyte*) ok "Gigabyte hardware" ;;
  *) warn "this does not look like a Gigabyte laptop - the fan driver will refuse to bind, the rest still works" ;;
esac

SB=$(mokutil --sb-state 2>/dev/null | head -1)
if [ -n "$SB" ]; then
  echo "  secure boot: $SB"
  case "$SB" in
    *enabled*) warn "Secure Boot is on - step 4 sets up module signing for it" ;;
    *) ok "Secure Boot off, unsigned modules load fine" ;;
  esac
fi

step "2. Checking WMI interface"
GUID_HI="ABBC0F6F-8EA1-11D1-00A0-C90629100000"
GUID_LO=$(echo "$GUID_HI" | tr 'A-Z' 'a-z')
if [ -e "/sys/bus/wmi/devices/$GUID_HI" ] || [ -e "/sys/bus/wmi/devices/$GUID_LO" ]; then
  ok "Gigabyte WMBC method GUID present - the fan driver should bind"
else
  warn "WMBC GUID not visible in /sys/bus/wmi/devices."
  warn "The driver may still bind (the WMI bus enumerates lazily). If it does not,"
  warn "run ./probe.sh and the report will say why."
fi

step "3. Dependencies"
NEED=()
command -v dkms      >/dev/null || NEED+=(dkms)
command -v make      >/dev/null || NEED+=(build-essential)
command -v python3   >/dev/null || NEED+=(python3)
command -v curl      >/dev/null || NEED+=(curl)
command -v sensors   >/dev/null || NEED+=(lm-sensors)
command -v iasl      >/dev/null || NEED+=(acpica-tools)
[ -d "/lib/modules/$(uname -r)/build" ] || NEED+=("linux-headers-$(uname -r)")
if [ "$NO_DRIVER" = 1 ]; then NEED=("${NEED[@]/dkms}"); fi
if [ ${#NEED[@]} -gt 0 ] && [ -n "${NEED[*]// }" ]; then
  echo "  installing: ${NEED[*]}"
  do_ apt-get update -qq
  do_ apt-get install -y ${NEED[*]} || warn "apt could not install everything; continuing"
else
  ok "all dependencies present"
fi
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
case "$PYV" in
  3.1[1-9]|3.[2-9]*) ok "python $PYV" ;;
  *) warn "python $PYV - config.toml parsing needs 3.11+; profiles will be unavailable" ;;
esac

fetch_driver() {
  # Prints the directory holding aorus-laptop.c, Makefile and dkms.conf.
  local dst="$HERE/.driver-cache/$DRV_REF"
  if [ -n "$DRIVER_DIR" ]; then
    [ -f "$DRIVER_DIR/aorus-laptop.c" ] || { warn "no aorus-laptop.c in $DRIVER_DIR"; return 1; }
    echo "$DRIVER_DIR"; return 0
  fi
  mkdir -p "$dst"
  local base="https://raw.githubusercontent.com/$DRV_REPO/$DRV_REF"
  local f
  for f in aorus-laptop.c Makefile dkms.conf; do
    if [ ! -s "$dst/$f" ]; then
      curl -fsSL --retry 2 --connect-timeout 15 "$base/$f" -o "$dst/$f" || {
        warn "could not download $f from $base"; return 1; }
    fi
  done
  if [ "$PINNED" = 1 ]; then
    local got_c got_mk got_dk
    got_c=$(sha256sum  "$dst/aorus-laptop.c" | cut -d' ' -f1)
    got_mk=$(sha256sum "$dst/Makefile"       | cut -d' ' -f1)
    got_dk=$(sha256sum "$dst/dkms.conf"      | cut -d' ' -f1)
    if [ "$got_c" != "$SHA_C" ] || [ "$got_mk" != "$SHA_MK" ] || [ "$got_dk" != "$SHA_DK" ]; then
      warn "checksum mismatch on the downloaded driver source - refusing to build it"
      warn "  expected aorus-laptop.c $SHA_C"
      warn "  got               $got_c"
      rm -rf "$dst"
      return 1
    fi
  else
    warn "building unpinned ref '$DRV_REF' - checksums not verified"
  fi
  echo "$dst"
}

SB_ON=0
case "$(mokutil --sb-state 2>/dev/null | head -1)" in *enabled*) SB_ON=1 ;; esac

if [ "$NO_DRIVER" = 0 ]; then
step "4. Secure Boot signing key"
  if [ "$SB_ON" = 1 ]; then
    echo "  Secure Boot is on, so the module has to be signed with a key you enroll"
    echo "  yourself. This does not weaken Secure Boot: the key stays on this machine,"
    echo "  you approve it by hand at the firmware prompt, and it only signs modules"
    echo "  built here. The alternative is turning Secure Boot off in the BIOS, which"
    echo "  is worth avoiding on a dual-boot machine because BitLocker treats it as"
    echo "  tampering and asks for your recovery key."
    if [ "$CHECK" = 1 ]; then
      echo "  would run: ./secureboot.sh key   (generate or reuse a machine owner key)"
    else
      if "$HERE/secureboot.sh" key; then
        "$HERE/secureboot.sh" framework
      else
        warn "could not prepare a signing key; the module will build but not load"
      fi
    fi
  else
    ok "Secure Boot off, no signing needed"
  fi

step "5. Fetching the aorus-laptop driver source"
  if [ -n "$DRIVER_DIR" ]; then
    ok "using local source: $DRIVER_DIR"
    SRC="$DRIVER_DIR"
  elif [ "$CHECK" = 1 ]; then
    echo "  would download $DRV_REPO @ ${DRV_REF:0:12} and verify its sha256"
    SRC=""
  elif SRC=$(fetch_driver); then
    ok "$DRV_REPO @ ${DRV_REF:0:12}"
    [ "$PINNED" = 1 ] && ok "checksums verified"
  else
    SRC=""
    warn "could not obtain the driver source; skipping the kernel module"
    warn "for an offline install, clone it yourself and pass --driver-dir <path>"
  fi

step "6. Building the kernel module (DKMS)"
  if [ -z "$SRC" ] && [ "$CHECK" = 0 ]; then
    DRIVER_OK=0
    warn "no source, nothing to build"
  else
  DKMS_ST=$(dkms status 2>/dev/null)
  if contains "aorus-laptop" "$DKMS_ST"; then
    OLD=$(printf '%s\n' "$DKMS_ST" | grep "^aorus-laptop" | head -1 | sed 's|^aorus-laptop[/,] *\([^,]*\).*|\1|')
    warn "aorus-laptop/$OLD already in the DKMS tree - removing it first"
    do_ dkms remove "aorus-laptop/$OLD" --all >/dev/null 2>&1
    do_ rm -rf "/usr/src/aorus-laptop-$OLD"
  fi
  do_ mkdir -p "/usr/src/aorus-laptop-$DRV_VER"
  if [ "$CHECK" = 0 ]; then
    cp "$SRC/aorus-laptop.c" "$SRC/Makefile" "/usr/src/aorus-laptop-$DRV_VER/"
    sed "s/@PKGVER@/$DRV_VER/" "$SRC/dkms.conf" > "/usr/src/aorus-laptop-$DRV_VER/dkms.conf"
  fi
  if [ "$CHECK" = 1 ]; then
    echo "  would run: dkms add/build/install -m aorus-laptop -v $DRV_VER"
    DRIVER_OK=1
  elif dkms add     -m aorus-laptop -v "$DRV_VER" >/dev/null 2>&1 &&
       dkms build   -m aorus-laptop -v "$DRV_VER" >/tmp/aorus-dkms-build.log 2>&1 &&
       dkms install -m aorus-laptop -v "$DRV_VER" >>/tmp/aorus-dkms-build.log 2>&1; then
    ok "module built and installed for kernel $(uname -r)"
    DRIVER_OK=1
  else
    warn "DKMS build failed - see /tmp/aorus-dkms-build.log"
    tail -15 /tmp/aorus-dkms-build.log | sed 's/^/       /'
    warn "the tool will still install; CPU and GPU control work without the module"
    DRIVER_OK=0
  fi
  fi

step "7. Loading the module"
  if [ "${DRIVER_OK:-0}" = 1 ]; then
    if [ "$SB_ON" = 1 ] && [ "$CHECK" = 0 ]; then
      "$HERE/secureboot.sh" sign || warn "signing failed; the module will not load under Secure Boot"
    fi
    do_ modprobe aorus-laptop 2>/tmp/aorus-modprobe.err
    if [ "$CHECK" = 0 ]; then
      LOADED=$(lsmod)
      if contains "aorus_laptop" "$LOADED"; then
        ok "aorus_laptop loaded"
        if [ -e /sys/devices/platform/aorus_laptop/fan_mode ]; then
          ok "control nodes present: /sys/devices/platform/aorus_laptop/"
        else
          warn "module loaded but bound to nothing. dmesg says:"
          dmesg | tail -20 | grep -i 'aorus\|wmi' | sed 's/^/       /' || true
        fi
      elif grep -i 'key was rejected\|required key not available' /tmp/aorus-modprobe.err >/dev/null 2>&1; then
        warn "the kernel rejected the module's signature."
        warn "The key is signed but not yet enrolled in your firmware."
        warn "The last step of this installer will queue it; then reboot and approve it."
      else
        warn "modprobe failed: $(cat /tmp/aorus-modprobe.err 2>/dev/null)"
        dmesg | tail -20 | grep -i 'aorus\|wmi' | sed 's/^/       /' || true
      fi
    fi
    do_ install -Dm644 /dev/null /etc/modules-load.d/aorus-laptop.conf
    [ "$CHECK" = 0 ] && echo aorus_laptop > /etc/modules-load.d/aorus-laptop.conf
    ok "will load on boot (/etc/modules-load.d/aorus-laptop.conf)"
  fi
else
  step "4-7. Kernel module skipped (--no-driver)"
fi

step "8. Installing aorusctl"
do_ install -Dm755 "$HERE/aorusctl" /usr/local/bin/aorusctl
do_ install -Dm755 "$HERE/probe.sh" /usr/local/share/aorusctl/probe.sh
do_ install -Dm755 "$HERE/secureboot.sh" /usr/local/share/aorusctl/secureboot.sh
[ "$CHECK" = 0 ] && ln -sf /usr/local/share/aorusctl/probe.sh /usr/local/bin/aorus-probe
ok "/usr/local/bin/aorusctl"
if [ -f /etc/aorusctl/config.toml ]; then
  do_ install -Dm644 "$HERE/config.toml" /etc/aorusctl/config.toml.new
  warn "kept your /etc/aorusctl/config.toml - the new default is at config.toml.new"
else
  do_ install -Dm644 "$HERE/config.toml" /etc/aorusctl/config.toml
  ok "/etc/aorusctl/config.toml"
fi
do_ mkdir -p /var/lib/aorusctl

step "9. systemd units (installed, not enabled)"
for u in aorusctld.service aorusctl-web.service aorusctl-profile.service; do
  do_ install -Dm644 "$HERE/systemd/$u" "/etc/systemd/system/$u"
done
do_ systemctl daemon-reload
ok "installed. Nothing starts until you enable it:"
echo "       sudo systemctl enable --now aorusctl-web.service   # dashboard on :8787"
echo "       sudo systemctl enable --now aorusctld.service      # software fan curve"
echo "       sudo systemctl enable --now aorusctl-profile.service  # apply a profile at boot"

step "10. Verifying"
if [ "$CHECK" = 0 ]; then
  /usr/local/bin/aorusctl version || warn "aorusctl did not run"
  echo
  /usr/local/bin/aorusctl status || true
fi

if [ "$CHECK" = 0 ] && [ "$NO_DRIVER" = 0 ] && [ "$SB_ON" = 1 ]; then
  ENROLLED=0
  for c in /var/lib/shim-signed/mok.der /var/lib/dkms/mok.pub; do
    [ -f "$c" ] || continue
    TK=$(mokutil --test-key "$c" 2>/dev/null)
    contains "already enrolled" "$TK" && ENROLLED=1
  done
  if [ "$ENROLLED" = 1 ]; then
    ok "signing key already enrolled, no reboot needed"
  else
    step "11. Enrolling the signing key (one time, needs a reboot)"
    "$HERE/secureboot.sh" enroll
    echo
    echo "${B}Reboot, approve the key on the blue screen, then run:${N}"
    echo "  aorusctl status"
    echo "  sudo /usr/local/share/aorusctl/secureboot.sh status   # if anything looks wrong"
  fi
fi

echo
echo "${B}Done.${N}"
echo "  aorusctl status          snapshot"
echo "  sudo aorusctl mon        live terminal dashboard"
echo "  sudo aorusctl web        browser dashboard on http://127.0.0.1:8787"
echo "  sudo aorusctl profile apply gaming"
echo "  sudo aorusctl reset      undo everything aorusctl changed"
echo "  sudo ./uninstall.sh      remove all of it"
