# LBTC-PRO v69 for Mac + Linux: how to publish

## What changed in the miner (mining logic is the same)
* Settings (`mining_mode.json`, `miner_config.json`) are now saved in `~/LBTC`. Before, they were saved
  next to the program. That folder is read-only inside a Mac app or a Linux install, so saving failed there.
  Old settings files are copied over automatically.
* The CPU library is found inside the packed app on every system (`.dll`, `.so` or `.dylib`).
* GPU: NVIDIA and AMD are tried first, as before. After that any OpenCL GPU is tried (Apple, Intel), but it is
  used only if the hash self-test passes. Otherwise mining stays on the CPU.
* "Start Local Node" works in the packed app: it runs `node.py` with the system `python3`.
* New `--selftest` option. It checks the CPU library (the hash is compared with Python's sha256) and the GPU, then prints PASS or FAIL.
  The installers run it automatically.

## Why people got errors before
1. The old Mac workflow named the library `lbtc_scan.dylib`, but the miner looks for `liblbtc_scan.dylib`.
2. It was a bare `--onefile` program, not a `.app`. macOS blocks these, and they start slowly.
3. pip on a new Mac downloads numpy and similar packages that only run on macOS 14+.
4. Linux builds made on a new Ubuntu fail on older distros (the "GLIBC_2.xx not found" error).
5. "No module named tkinter" when running the .py file directly.

The new build fixes all of these. Tk is packed inside, Linux is built on Debian 11 (glibc 2.31), Mac packages
are pinned to macOS 11+, and every build is self-tested on GitHub before it is released.

## Publish (about 10 minutes, once)
1. In the GitHub repo `latebitcoiners/lbtc-miner`, **delete** the old `.github/workflows/build-mac.yml` and
   `build-macos.yaml`.
2. Copy into the repo root: `src/`, `native/`, `packaging/`, `.github/workflows/build-mac-linux.yml`.
3. Commit, then create the tag: `git tag v69 && git push origin main --tags`
4. Actions tab: 4 builds run (Linux x64, Linux ARM64, Mac M-series, Mac Intel). Each one is tested. Then a
   Release is created with the `.tar.gz`, `.zip` and `.dmg` files, the installers and `SHA256SUMS.txt`.
5. **Before announcing it:** ask one Mac user (M-series) to run the Mac line below and send you the self-test text.

## Lines for the /download page
Linux:
    curl -fsSL https://raw.githubusercontent.com/latebitcoiners/lbtc-miner/main/packaging/install-linux.sh | bash
macOS:
    curl -fsSL https://raw.githubusercontent.com/latebitcoiners/lbtc-miner/main/packaging/install-mac.sh | bash
Manual download: https://github.com/latebitcoiners/lbtc-miner/releases/latest

## Next version
Change `VER=69` in `packaging/build.sh`, `install-linux.sh` and `install-mac.sh`, then push the tag (e.g. `v70`).
