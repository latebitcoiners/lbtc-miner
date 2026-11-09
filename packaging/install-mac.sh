#!/usr/bin/env bash
# LBTC-PRO installer for macOS (Apple Silicon M1-M4 and Intel). Paste in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/latebitcoiners/lbtc-miner/main/packaging/install-mac.sh | bash
# Downloading with curl avoids the "app is damaged / can't be opened" macOS warning.
set -euo pipefail
VER=69
REPO=latebitcoiners/lbtc-miner
NAME=LBTC-PRO

say(){ printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "This installer is for macOS."
MAJOR=$(sw_vers -productVersion | cut -d. -f1)
[ "$MAJOR" -ge 11 ] || die "macOS 11 (Big Sur) or newer is needed. You have $(sw_vers -productVersion)."

# An Intel build running under Rosetta reports x86_64, so ask the hardware
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then ARCH=arm64; else ARCH=x64; fi

APPS=/Applications
[ -w "$APPS" ] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/$REPO/releases/download/v69.2/$NAME-v$VER-macos-$ARCH.zip"
say "Downloading $URL"
curl -fL --progress-bar "$URL" -o "$TMP/app.zip" || die "Download failed."

say "Installing to $APPS/$NAME.app"
pkill -f "$NAME.app/Contents/MacOS" 2>/dev/null || true
rm -rf "$APPS/$NAME.app"
ditto -x -k "$TMP/app.zip" "$APPS"
xattr -dr com.apple.quarantine "$APPS/$NAME.app" 2>/dev/null || true

say "Checking the miner..."
"$APPS/$NAME.app/Contents/MacOS/$NAME" --selftest || die "Self-test failed. Please send the text above to the LBTC team."

say "Done! Opening LBTC-PRO... (next time: Launchpad or Applications > LBTC-PRO)"
open "$APPS/$NAME.app"
echo "Your wallet and settings are kept in ~/LBTC"
