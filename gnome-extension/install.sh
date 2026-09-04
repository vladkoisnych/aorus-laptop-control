#!/usr/bin/env bash
# Installs the aorusctl GNOME Shell extension for the current user.
# Run this WITHOUT sudo: extensions live under your home directory.
set -uo pipefail

UUID="aorusctl@vladkoisnych.github.io"
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'
ok()   { echo "${G}  ok${N}   $*"; }
warn() { echo "${Y}  warn${N} $*"; }

[ "$EUID" -ne 0 ] || { echo "run this as your normal user, not with sudo"; exit 1; }

case "${1:-install}" in
  uninstall)
    gnome-extensions disable "$UUID" 2>/dev/null
    rm -rf "$DEST"
    ok "removed $DEST"
    echo "  Log out and back in, or on X11 press Alt+F2 then r."
    exit 0 ;;
esac

echo "${B}Installing $UUID${N}"
command -v gnome-shell >/dev/null || warn "gnome-shell not found; installing anyway"
SHELL_VER=$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1)
[ -n "$SHELL_VER" ] && echo "  GNOME Shell $SHELL_VER"

mkdir -p "$DEST/schemas" "$DEST/icons"
cp "$SRC/extension.js" "$SRC/prefs.js" "$SRC/stylesheet.css" "$SRC/metadata.json" "$DEST/"
cp "$SRC/schemas/"*.xml "$DEST/schemas/"
cp "$SRC/icons/"*.svg "$DEST/icons/"
ok "copied to $DEST"

if command -v glib-compile-schemas >/dev/null; then
  glib-compile-schemas "$DEST/schemas/" && ok "compiled settings schema"
else
  warn "glib-compile-schemas not found; install libglib2.0-dev-bin, then rerun"
fi

if [ -n "${WAYLAND_DISPLAY:-}" ]; then
  echo
  echo "${B}Log out and back in${N} to load it. Wayland cannot restart the shell in place."
else
  echo
  echo "${B}Press Alt+F2, type r, press Enter${N} to restart the shell."
fi
echo "Then:"
echo "  gnome-extensions enable $UUID"
echo "  gnome-extensions prefs $UUID"
echo
echo "It reads from aorusctl web, so for GPU readings and the fan mode buttons:"
echo "  sudo systemctl enable --now aorusctl-web.service"
