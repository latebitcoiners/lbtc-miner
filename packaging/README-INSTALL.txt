LBTC-PRO v69 - Pure Miner (free)                      https://latebitcoiners.com
================================================================================

LINUX (Ubuntu, Debian, Mint, Fedora, Arch, Pop!_OS... 64-bit PC or ARM64)
  Easiest - open a Terminal and paste:
      curl -fsSL https://raw.githubusercontent.com/latebitcoiners/lbtc-miner/main/packaging/install-linux.sh | bash
  Or, from this folder:
      ./install.sh
  Or just run it without installing:
      ./LBTC-PRO
  Then open "LBTC-PRO Miner" from your apps menu (or type: lbtc-pro).

macOS 11 or newer (Apple M1/M2/M3/M4 and Intel Macs)
  Easiest - open Terminal (Cmd+Space, type Terminal) and paste:
      curl -fsSL https://raw.githubusercontent.com/latebitcoiners/lbtc-miner/main/packaging/install-mac.sh | bash
  Or with the .dmg: drag LBTC-PRO to Applications. If macOS says the app
  "is damaged" or "cannot be opened", open Terminal and paste:
      xattr -dr com.apple.quarantine /Applications/LBTC-PRO.app
  (or: System Settings > Privacy & Security > "Open Anyway").

Wallet and settings: the folder LBTC in your home folder (~/LBTC).
Copy your wallet_gui.json there, or use Import in the miner.

GPU: NVIDIA and AMD cards are used through OpenCL when the graphics driver
is installed. Apple Silicon Macs mine on the CPU (+ GPU when its self-test
passes). If no GPU is found the miner simply uses the CPU.

Problem? Run the self-test and send us the text it prints:
  Linux:  ~/.local/share/lbtc-pro/LBTC-PRO --selftest
  macOS:  /Applications/LBTC-PRO.app/Contents/MacOS/LBTC-PRO --selftest
