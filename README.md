# LBTC Wallet & Miner — Source

Source code for the LBTC Wallet & Miner desktop application, published
for transparency and community verification.

## What this is

A single-file Python GUI application built with `customtkinter`. It provides:

- LBTC wallet (create, import, send, receive)
- CPU miner
- NFT minting and transfers
- Staking
- Bridge to Arbitrum (wLBTC)

Your private keys are encrypted locally with AES-256-GCM. They never
leave your machine.

## Verify the binary you downloaded

The releases page hosts the compiled `.dmg` and `.zip`. To verify the
binary matches this source:

1. Download the release for your platform
2. Compare the SHA-256 hash against the one shown in the release notes
3. Or rebuild from source (instructions below)

## Building from source

### Requirements

- Python 3.12 or newer
- macOS, Linux, or Windows
- `tkinter` (bundled with Python on macOS and Windows; `apt install python3-tk` on Linux)

### Install dependencies

    pip install -r requirements.txt

### Run directly (no build)

    python LBTC_Wallet_Miner_v51.py

### Build a standalone app

    pip install pyinstaller
    pyinstaller --windowed --name "LBTC Wallet & Miner" LBTC_Wallet_Miner_v51.py

The built app appears in `dist/`.

### Automated macOS builds

Push a tag starting with `v` and GitHub Actions builds both Intel and
Apple Silicon `.dmg` and `.zip` files automatically:

    git tag v51
    git push origin v51

Download the artifacts from the Actions tab.

## macOS: first launch warning

The app is not code-signed with an Apple Developer certificate. On first
launch macOS will say:

> "LBTC Wallet & Miner" cannot be opened because the developer cannot be verified.

Bypass it one of three ways:

1. Right-click the app → Open → Open (only needed the first time)
2. System Settings → Privacy & Security → "Open Anyway"
3. Terminal: `xattr -cr "/Applications/LBTC Wallet & Miner.app"`

This is normal for unsigned open-source software.

## Wallet file location

| Platform | Path |
|---|---|
| macOS | `~/LBTC/wallet_gui.json` |
| Windows | `C:\Users\<you>\LBTC\wallet_gui.json` |
| Linux | `~/LBTC/wallet_gui.json` |

Back this file up. There is no recovery if you lose your passphrase.

## Security

- Private keys encrypted with AES-256-GCM
- PBKDF2 key derivation
- No telemetry, no analytics, no external calls except to the LBTC node
- This repository contains no secrets — only client-side code

## License

Community software. Use at your own risk. No warranty.

## Links

- Website: https://latebitcoiners.com
- Discord: https://discord.gg/4GU4QnAKJA