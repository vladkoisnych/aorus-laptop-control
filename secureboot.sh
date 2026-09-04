#!/usr/bin/env bash
# Secure Boot signing for the aorus-laptop module.
#
#   sudo ./secureboot.sh status    what the state is, changes nothing
#   sudo ./secureboot.sh setup     make a key if needed, sign the module, offer to enroll
#   sudo ./secureboot.sh sign      re-sign the module with the existing key
#   sudo ./secureboot.sh enroll    queue the key for enrollment at the next boot
#   sudo ./secureboot.sh framework point DKMS at the key for future kernel rebuilds
#
# Signing a module you built yourself is the supported way to run out-of-tree
# drivers with Secure Boot on. It does not weaken Secure Boot: the key lives on
# this machine, you enroll it by hand at the firmware prompt, and it only ever
# signs modules you build here.

set -uo pipefail
R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'
ok()   { echo "${G}  ok${N}   $*"; }
warn() { echo "${Y}  warn${N} $*"; }
bad()  { echo "${R}  fail${N} $*"; }
step() { echo; echo "${B}$*${N}"; }

MODNAME=aorus-laptop
KVER=$(uname -r)
KEY=""; CRT=""

find_key() {
  # Ubuntu's shim-signed puts the machine owner key here; Debian's dkms uses
  # /var/lib/dkms. Take whichever pair actually exists.
  local pairs=(
    "/var/lib/shim-signed/mok.priv:/var/lib/shim-signed/mok.der"
    "/var/lib/dkms/mok.key:/var/lib/dkms/mok.pub"
    "/root/.mok/client.priv:/root/.mok/client.der"
  )
  for p in "${pairs[@]}"; do
    local k="${p%%:*}" c="${p##*:}"
    if [ -f "$k" ] && [ -f "$c" ]; then KEY="$k"; CRT="$c"; return 0; fi
  done
  return 1
}

find_signfile() {
  for p in "/usr/src/linux-headers-$KVER/scripts/sign-file" \
           "/lib/modules/$KVER/build/scripts/sign-file" \
           /usr/lib/linux-kbuild-*/scripts/sign-file; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}

find_module() {
  for d in "/lib/modules/$KVER/updates/dkms" "/lib/modules/$KVER/updates" \
           "/lib/modules/$KVER/kernel/drivers/misc"; do
    for ext in ko ko.zst ko.xz ko.gz; do
      [ -f "$d/$MODNAME.$ext" ] && { echo "$d/$MODNAME.$ext"; return 0; }
    done
  done
  return 1
}

sb_state() { mokutil --sb-state 2>/dev/null | head -1; }

cmd_status() {
  step "Secure Boot"
  local s; s=$(sb_state)
  echo "  ${s:-mokutil not available}"
  case "$s" in
    *enabled*) warn "unsigned modules will be rejected with \"Key was rejected by service\"" ;;
    *disabled*) ok "nothing to do here, unsigned modules load fine" ;;
  esac

  step "Signing key"
  if find_key; then
    ok "key  $KEY"
    ok "cert $CRT"
    if mokutil --test-key "$CRT" 2>/dev/null | grep -qi "already enrolled"; then
      ok "enrolled in the firmware"
    else
      warn "NOT enrolled yet - run: sudo $0 enroll"
    fi
  else
    warn "no machine owner key on this system yet - run: sudo $0 setup"
  fi

  step "Module"
  local m; if m=$(find_module); then
    ok "$m"
    local signer; signer=$(modinfo -F signer "$m" 2>/dev/null)
    if [ -n "$signer" ]; then ok "signed by: $signer"; else warn "UNSIGNED"; fi
  else
    warn "$MODNAME is not installed for kernel $KVER (run install.sh first)"
  fi

  step "Loaded?"
  if lsmod | grep -q "^${MODNAME//-/_}"; then
    ok "${MODNAME//-/_} is loaded"
  else
    warn "not loaded. modprobe says:"
    modprobe "$MODNAME" 2>&1 | sed 's/^/       /' || true
  fi
}

cmd_makekey() {
  if find_key; then ok "reusing existing key $KEY"; return 0; fi
  step "Creating a machine owner key"
  if command -v update-secureboot-policy >/dev/null; then
    echo "  update-secureboot-policy --new-key"
    update-secureboot-policy --new-key
  fi
  if ! find_key; then
    warn "falling back to generating one with openssl"
    mkdir -p /var/lib/shim-signed
    local cfg; cfg=$(mktemp)
    cat > "$cfg" <<'CFG'
[ req ]
default_bits = 2048
distinguished_name = req_dn
prompt = no
string_mask = utf8only
x509_extensions = v3_mod
[ req_dn ]
CN = aorusctl local module signing key
[ v3_mod ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
basicConstraints = critical,CA:FALSE
keyUsage = digitalSignature
extendedKeyUsage = codeSigning,1.3.6.1.4.1.2312.16.1.2
CFG
    if ! openssl req -new -x509 -nodes -days 36500 -config "$cfg" \
      -outform DER -keyout /var/lib/shim-signed/mok.priv \
      -out /var/lib/shim-signed/mok.der 2>/tmp/aorus-openssl.err; then
      bad "openssl failed:"; sed 's/^/       /' /tmp/aorus-openssl.err; rm -f "$cfg"; return 1
    fi
    rm -f "$cfg"
    chmod 600 /var/lib/shim-signed/mok.priv
    find_key || { bad "could not create a signing key"; return 1; }
  fi
  ok "key  $KEY"
  ok "cert $CRT"
}

cmd_framework() {
  # Point DKMS at the key so future kernel upgrades sign the rebuilt module too.
  local f=/etc/dkms/framework.conf
  [ -f "$f" ] || touch "$f"
  grep -q "^mok_signing_key=" "$f" && sed -i "s|^mok_signing_key=.*|mok_signing_key=\"$KEY\"|" "$f" \
                                   || echo "mok_signing_key=\"$KEY\"" >> "$f"
  grep -q "^mok_certificate=" "$f" && sed -i "s|^mok_certificate=.*|mok_certificate=\"$CRT\"|" "$f" \
                                   || echo "mok_certificate=\"$CRT\"" >> "$f"
  grep -q "^sign_tool=" "$f" || true
  ok "DKMS will sign future rebuilds ($f)"
}

cmd_sign() {
  find_key || { bad "no signing key; run: sudo $0 setup"; return 1; }
  local mod sf
  mod=$(find_module) || { bad "$MODNAME not installed for $KVER; run install.sh first"; return 1; }
  sf=$(find_signfile) || { bad "sign-file not found; install linux-headers-$KVER"; return 1; }

  step "Signing $mod"
  local ko="$mod" packed=""
  case "$mod" in
    *.zst) command -v zstd >/dev/null || { bad "zstd needed to unpack $mod"; return 1; }
           zstd -q -d -f "$mod" -o "${mod%.zst}" && ko="${mod%.zst}" && packed=zst ;;
    *.xz)  xz -d -k -f "$mod" && ko="${mod%.xz}" && packed=xz ;;
    *.gz)  gzip -d -k -f "$mod" && ko="${mod%.gz}" && packed=gz ;;
  esac

  if "$sf" sha256 "$KEY" "$CRT" "$ko"; then
    ok "signed"
  else
    bad "sign-file failed"; [ -n "$packed" ] && rm -f "$ko"; return 1
  fi

  case "$packed" in
    zst) zstd -q -19 -f "$ko" -o "$mod" && rm -f "$ko" ;;
    xz)  xz -f -c "$ko" > "$mod" && rm -f "$ko" ;;
    gz)  gzip -f -c "$ko" > "$mod" && rm -f "$ko" ;;
  esac
  depmod -a "$KVER"
  local signer; signer=$(modinfo -F signer "$mod" 2>/dev/null)
  [ -n "$signer" ] && ok "modinfo signer: $signer" || warn "modinfo shows no signer, the signature may not have stuck"
}

cmd_enroll() {
  find_key || { bad "no signing key; run: sudo $0 setup"; return 1; }
  if mokutil --test-key "$CRT" 2>/dev/null | grep -qi "already enrolled"; then
    ok "already enrolled, nothing to do"
    return 0
  fi
  step "Enrolling the key"
  cat <<'TXT'
  mokutil will ask you to choose a one-time password now. It is not your login
  password and you only need it once, at the next boot.

  Then reboot. Before Ubuntu starts you get a blue "MOK management" screen:

    1. Enroll MOK
    2. Continue
    3. Yes
    4. type the one-time password you are about to set
    5. Reboot

  If you miss the prompt it times out and boots normally; just run
  `sudo mokutil --import` again.

TXT
  mokutil --import "$CRT"
  echo
  ok "queued. Reboot and follow the blue screen, then run: aorusctl status"
}

cmd_setup() {
  local s; s=$(sb_state)
  echo "Secure Boot: ${s:-unknown}"
  case "$s" in
    *disabled*) ok "Secure Boot is off, no signing needed"; return 0 ;;
  esac
  cmd_makekey || return 1
  cmd_framework
  if find_module >/dev/null; then
    cmd_sign || return 1
  else
    warn "module not installed yet - run install.sh, then: sudo $0 sign"
  fi
  cmd_enroll
}

[ "$EUID" -eq 0 ] || { echo "run this with sudo"; exit 1; }
case "${1:-status}" in
  status) cmd_status ;;
  setup)  cmd_setup ;;
  framework) find_key && cmd_framework || { bad "no key yet; run: sudo $0 key"; exit 1; } ;;
  sign)   cmd_sign ;;
  enroll) cmd_makekey && cmd_enroll ;;
  key)    cmd_makekey ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
