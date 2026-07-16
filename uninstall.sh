#!/usr/bin/env bash
# Remove user-local registration for Spectre Desktop (does not delete the project).

set -euo pipefail

APP_ID="com.digitizable.spectre-desktop"
LAUNCHER_NAME="spectre-desktop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DESKTOP_DST="${XDG_DATA_HOME:-$HOME/.local/share}/applications/${APP_ID}.desktop"
ICON_DST="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/${APP_ID}.svg"
LAUNCHER_USER="${XDG_BIN_HOME:-$HOME/.local/bin}/$LAUNCHER_NAME"
LAUNCHER_PROJECT="$SCRIPT_DIR/bin/$LAUNCHER_NAME"

info() { printf '==> %s\n' "$*"; }

remove_file() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    info "Removing $path"
    rm -f "$path"
  fi
}

remove_file "$DESKTOP_DST"
remove_file "$ICON_DST"
remove_file "$LAUNCHER_USER"
remove_file "$LAUNCHER_PROJECT"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" 2>/dev/null || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
fi

info "Uninstalled desktop integration. Project files at $SCRIPT_DIR were left intact."
info "To also remove the venv: rm -rf $SCRIPT_DIR/.venv"
