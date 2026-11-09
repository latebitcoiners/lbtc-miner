#!/usr/bin/env bash
# Builds LBTC-PRO for the machine it runs on (macOS or Linux) and packs it for download.
#   bash packaging/build.sh            -> dist/LBTC-PRO-v69-<os>-<arch>.(zip|tar.gz)
# Needs: Python 3.10+ with Tk, a C compiler (clang on Mac, gcc on Linux).
set -euo pipefail
cd "$(dirname "$0")/.."
VER=69
NAME=LBTC-PRO
OS=$(uname -s); ARCH=$(uname -m)
case "$ARCH" in x86_64|amd64) ARCH=x64;; arm64|aarch64) ARCH=arm64;; esac
PY=${PYTHON:-python3}

echo "== 1/5 native CPU library"
mkdir -p build
if [ "$OS" = "Darwin" ]; then
  export MACOSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET:-11.0}
  LIB=build/liblbtc_scan.dylib
  clang -O3 -dynamiclib -pthread -o "$LIB" native/lbtc_scan.c
  PLAT=macos
else
  LIB=build/liblbtc_scan.so
  if [ "$ARCH" = "x64" ]; then MARCH="-march=x86-64-v2 -mtune=generic"; else MARCH=""; fi
  gcc -O3 $MARCH -shared -fPIC -pthread -o "$LIB" native/lbtc_scan.c
  PLAT=linux
fi

echo "== 2/5 python packages"
$PY -m pip install --upgrade pip >/dev/null
$PY -m pip install -r packaging/requirements.txt pyinstaller
if [ "$OS" = "Darwin" ]; then
  # pip on a new Mac picks wheels that only run on macOS 14+. Force wheels that run on macOS 11+.
  MA=$(uname -m)
  SITE=$($PY -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')
  PYV=$($PY -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
  $PY -m pip uninstall -y numpy pyopencl pillow cryptography cffi
  $PY -m pip install --upgrade --no-deps --only-binary=:all: --platform "macosx_11_0_$MA" \
      --python-version "$PYV" --implementation cp --target "$SITE" numpy pyopencl pillow cryptography cffi
fi

echo "== 3/5 icons"
$PY packaging/make_icons.py src/logo.ico build

echo "== 4/5 pyinstaller"
ICON=build/icon.png; [ "$OS" = "Darwin" ] && ICON=build/icon.icns
$PY -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name "$NAME" --icon "$ICON" \
  --paths src \
  --add-binary "$LIB:." \
  --add-data "build/icon.png:." \
  --collect-data customtkinter \
  --collect-data pyopencl \
  --hidden-import miner_config --hidden-import fast_engine \
  --hidden-import gpu_engine --hidden-import amd_engine \
  --osx-bundle-identifier com.latebitcoiners.lbtcpro \
  src/LBTC_Miner_v69.py

echo "== 5/5 self-test + package"
mkdir -p release
OUT="$NAME-v$VER-$PLAT-$ARCH"
if [ "$OS" = "Darwin" ]; then
  APP="dist/$NAME.app"
  /usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$APP/Contents/Info.plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VER.0" "$APP/Contents/Info.plist" 2>/dev/null || true
  # every binary inside must run on macOS 11
  BAD=0
  while IFS= read -r f; do
    m=$(otool -l "$f" 2>/dev/null | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit}')
    if [ -n "$m" ] && [ "${m%%.*}" -gt 11 ]; then echo "too new for macOS 11 (minos $m): $f"; BAD=1; fi
  done < <(find "$APP" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -111 \))
  [ "$BAD" = 0 ] || { echo "ERROR: some files need a newer macOS"; exit 1; }
  codesign --force --deep --sign - "$APP"              # ad-hoc signature (needed on Apple Silicon)
  "$APP/Contents/MacOS/$NAME" --selftest
  ditto -c -k --keepParent "$APP" "release/$OUT.zip"
  rm -rf build/dmg; mkdir -p build/dmg; cp -R "$APP" build/dmg/; ln -s /Applications build/dmg/Applications
  cp packaging/README-INSTALL.txt "build/dmg/READ ME FIRST.txt"
  hdiutil create -volname "$NAME v$VER" -srcfolder build/dmg -ov -format UDZO "release/$OUT.dmg" >/dev/null
else
  "dist/$NAME/$NAME" --selftest
  cp packaging/install-linux.sh "dist/$NAME/install.sh"; chmod +x "dist/$NAME/install.sh" "dist/$NAME/$NAME"
  cp packaging/README-INSTALL.txt "dist/$NAME/README.txt"
  tar -C dist -czf "release/$OUT.tar.gz" "$NAME"
fi
( cd release && for f in "$OUT".*; do shasum -a 256 "$f" 2>/dev/null || sha256sum "$f"; done > "$OUT.sha256" )
ls -la release
