#!/usr/bin/env bash
# LBTC-PRO installer for Linux (no root needed).
#  - Inside the downloaded folder:  ./install.sh
#  - Or online, in one line:        curl -fsSL https://raw.githubusercontent.com/latebitcoiners/lbtc-miner/main/packaging/install-linux.sh | bash
set -euo pipefail
VER=69
REPO=latebitcoiners/lbtc-miner
NAME=LBTC-PRO
DEST="$HOME/.local/share/lbtc-pro"
BIN="$HOME/.local/bin"

say(){ printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

case "$(uname -m)" in
  x86_64|amd64) ARCH=x64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "This CPU ($(uname -m)) has no ready-made build. Use the source version: see README." ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
if [ -x "$HERE/$NAME" ] && [ -d "$HERE/_internal" ]; then
  SRC="$HERE"
else
  command -v curl >/dev/null || die "curl is missing. Install it (sudo apt install curl) and run again."
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  URL="https://github.com/$REPO/releases/download/v69.2/$NAME-v$VER-linux-$ARCH.tar.gz"
  say "Downloading $URL"
  curl -fL --progress-bar "$URL" -o "$TMP/pkg.tar.gz" || die "Download failed."
  tar -xzf "$TMP/pkg.tar.gz" -C "$TMP"
  SRC="$TMP/$NAME"
fi

say "Installing to $DEST"
rm -rf "$DEST"; mkdir -p "$DEST" "$BIN" "$HOME/.local/share/applications"
cp -a "$SRC/." "$DEST/"
chmod +x "$DEST/$NAME"
ln -sf "$DEST/$NAME" "$BIN/lbtc-pro"

ICON="$DEST/_internal/icon.png"
cat > "$HOME/.local/share/applications/lbtc-pro.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LBTC-PRO Miner
Comment=Late Bitcoin miner v$VER
Exec="$DEST/$NAME"
Icon=$ICON
Terminal=false
Categories=Utility;Finance;
EOF
chmod +x "$HOME/.local/share/applications/lbtc-pro.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

# Missing system libraries (very rare on a desktop): tell the user exactly what to install
MISSING=$(ldd "$DEST/$NAME" "$DEST"/_internal/*.so* 2>/dev/null | grep "not found" | awk '{print $1}' | sort -u || true)
if [ -n "$MISSING" ]; then
  say "Some system libraries are missing: $MISSING"
  say "Ubuntu/Debian: sudo apt install libx11-6 libxft2 libxss1 libfontconfig1"
  say "Fedora:        sudo dnf install libX11 libXft libXScrnSaver fontconfig"
fi

say "Checking the miner..."
"$DEST/$NAME" --selftest || die "Self-test failed. Please send the text above to the LBTC team."

say "Done! Open 'LBTC-PRO Miner' from your apps menu, or run:  lbtc-pro"
case ":$PATH:" in *":$BIN:"*) ;; *) say "(tip: add ~/.local/bin to your PATH to use the 'lbtc-pro' command)";; esac
echo "GPU mining: needs your graphics driver with OpenCL (NVIDIA driver, or AMD ROCm). Without it the miner uses the CPU."
echo "Your wallet and settings are kept in ~/LBTC"
