#!/usr/bin/env bash
# Adds an `everlast` command and an app-menu entry for the current user.
set -e
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
chmod +x "$HERE/everlast" "$HERE/eqtool-native"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$HERE/everlast" "$HOME/.local/bin/everlast"
ln -sf "$HERE/everlast" "$HOME/.local/bin/eqtool-native"      # the old name still works
rm -f "$HOME/.local/share/applications/eqtool-native.desktop"
for n in 16 22 24 32 48 64 128 256; do
  d="$HOME/.local/share/icons/hicolor/${n}x${n}/apps"
  mkdir -p "$d" && cp "$HERE/eqtool_native/icons/everlast-$n.png" "$d/everlast.png"
done
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
cat > "$HOME/.local/share/applications/everlast.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Everlast
Comment=Everlast by Sarekrn: spell timers, DPS, maps and mob info for Project 1999
Exec=$HERE/everlast
Terminal=false
Icon=everlast
Categories=Game;Utility;
DESK
python3 -c "import tkinter" 2>/dev/null || echo "NOTE: install Tk for Python first (e.g. sudo apt install python3-tk)"
echo "Installed. Run 'everlast' or find Everlast in your app menu."
