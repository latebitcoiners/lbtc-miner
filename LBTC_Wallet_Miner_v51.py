import time
#!/usr/bin/env python3
# ============================================
# LBTC-PRO WALLET & MINER v51
# - Bridge tab, Memecoins tab
# - Inline smooth difficulty (no external module)
# - Dedicated stats thread (stable hashrate reporting)
# - Clean error handling
# ============================================

import customtkinter as ctk
import tkinter.messagebox as messagebox
import json, requests, threading, time, hashlib, base64, os, sys, random, socket, webbrowser
import subprocess, secrets
import urllib.request as _urllib
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ecdsa import SigningKey, VerifyingKey, SECP256k1
from PIL import Image
from io import BytesIO


def next_difficulty_smooth(chain, target_time=180, fallback="0000", log=None):
    """Smooth difficulty — inline so the exe has no external dependency."""
    import time as _t
    if not chain:
        return format(2 ** 232, "064x")
    tip = chain[-1].get("difficulty", "")
    if not tip:
        return format(2 ** 232, "064x")
    try:
        current = int(tip, 16) if len(tip) > 16 else int(tip + "f" * (64 - len(tip)), 16)
    except Exception:
        current = 2 ** 232
    if len(chain) < 2:
        return format(current, "064x")
    recent = chain[-9:]
    times = []
    for i in range(1, len(recent)):
        dt = recent[i]["timestamp"] - recent[i - 1]["timestamp"]
        if dt > 0:
            times.append(dt)
    if not times:
        return format(current, "064x")
    ema = times[0]
    for dt in times[1:]:
        ema = 0.3 * dt + 0.7 * ema
    age = _t.time() - chain[-1].get("timestamp", 0)
    if age > 300:
        nudge = 0.5 if age > 600 else 0.75
        nt = int(current / nudge)
        if log:
            log(f"[diff] STALE tip {int(age)}s nudge x{nudge}")
        return format(max(2 ** 220, min(2 ** 252, nt)), "064x")
    ratio = max(0.90, min(1.10, ema / target_time))
    nt = int(current * ratio)
    nt = max(2 ** 220, min(2 ** 252, nt))
    return format(nt, "064x")


COIN = 100_000_000
BASE_REWARD = 50 * COIN
HALVING_INTERVAL = 210_000
ADJUSTMENT_INTERVAL = 5
DESIRED_BLOCK_TIME = 180
MIN_DIFFICULTY = "00"
MAX_DIFFICULTY = "00000000"
FEE_PER_TX = 10000
MIN_STAKE_LBTC = 10
EARLY_UNLOCK_FEE_LBTC = 5
MAX_TXS_PER_BLOCK = 50

DATA_DIR = os.path.join(os.path.expanduser('~'), 'LBTC')
PUBLIC_NODE_URL = "http://139.59.190.254:8080"
WEBSITE_URL = "https://latebitcoiners.com"
EXPLORER_URL = "https://latebitcoiners.com/explorer"
BRIDGE_URL = "https://latebitcoiners.com/bridge"
MEMECOINS_URL = "https://latebitcoiners.com/memecoins"
MEMECOINS_CREATE_URL = "https://latebitcoiners.com/memecoins/create"
BRIDGE_VAULT_ADDR = "1215RNfGGMzXeNhn9SQvhtmrunwVAz1aUn"
BRIDGE_OWNER_ADDR = "0x4bB6fB87818A95CE34c745FC1675f10942CCD15c"
WLBTC_ADDR = "0x09D188e0612259bB37371bda11b8bd152751bAfE"
os.makedirs(DATA_DIR, exist_ok=True)


def resource_path(rel):
    try: base = sys._MEIPASS
    except: base = os.path.abspath(".")
    return os.path.join(base, rel)


def format_lbtc(u): return f"{u / COIN:.8f}"


def play_coin_sound():
    import sys as _sys
    if _sys.platform != "win32":
        return
    def _play():
        try:
            import winsound
            pattern = [
                (2400, 40), (2800, 35), (2200, 45), (2600, 40),
                (3000, 40), (2800, 35), (3200, 45), (3000, 40),
                (2600, 35), (3000, 40), (3400, 55), (3200, 45),
                (3600, 70), (3800, 90), (4000, 250),
            ]
            for freq, dur in pattern:
                winsound.Beep(freq, dur)
        except Exception as e:
            print(f"Sound error: {e}")
    threading.Thread(target=_play, daemon=True).start()


BASE58_ALPH = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def generate_keypair():
    sk = SigningKey.generate(curve=SECP256k1)
    return sk, sk.get_verifying_key()


def address_from_vk(vk):
    pub = vk.to_string()
    sha = hashlib.sha256(pub).digest()
    h = hashlib.new('ripemd160'); h.update(sha)
    ripe = h.digest(); v = b'\x00' + ripe
    chk = hashlib.sha256(hashlib.sha256(v).digest()).digest()[:4]
    return base58_encode(v + chk)


def base58_encode(b):
    num = int.from_bytes(b, 'big'); res = ""
    while num > 0:
        num, rem = divmod(num, 58)
        res = BASE58_ALPH[rem] + res
    for byte in b:
        if byte == 0: res = '1' + res
        else: break
    return res


def encrypt_private_key(kb, pw):
    salt = os.urandom(32)
    kdf = PBKDF2HMAC(hashes.SHA256(), 32, salt, 600000)
    key = kdf.derive(pw.encode()); nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, kb, None)
    return {"salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ct).decode()}


def decrypt_private_key(enc, pw):
    salt = base64.b64decode(enc["salt"]); nonce = base64.b64decode(enc["nonce"])
    ct = base64.b64decode(enc["ct"])
    kdf = PBKDF2HMAC(hashes.SHA256(), 32, salt, 600000)
    key = kdf.derive(pw.encode())
    return AESGCM(key).decrypt(nonce, ct, None)


def sign_message(sk, msg):
    return sk.sign(msg.encode(), hashfunc=hashlib.sha256).hex()


class Blockchain:
    def __init__(self):
        self.chain_file = f"{DATA_DIR}/chain.json"
        self.mempool_file = f"{DATA_DIR}/mempool.json"
        self.balances = {}; self.chain = []; self.mempool = []
        self.load()

    def load(self):
        if os.path.exists(self.chain_file):
            with open(self.chain_file) as f: self.chain = json.load(f)
            self.chain = sorted(self.chain, key=lambda b: b.get('index', 0))
        else:
            self.chain = [self.genesis_block()]; self.save()
        if os.path.exists(self.mempool_file):
            with open(self.mempool_file) as f: self.mempool = json.load(f)
        self.update_balances()

    def save(self):
        with open(self.chain_file, 'w') as f: json.dump(self.chain, f)
        with open(self.mempool_file, 'w') as f: json.dump(self.mempool, f)

    def genesis_block(self):
        return {"index":0,"timestamp":int(time.time()),"transactions":[],
                "previous_hash":"0"*64,"nonce":0,"hash":"0"*64,"difficulty":"0000"}

    def update_balances(self):
        self.balances = {}
        for b in self.chain:
            for tx in b.get("transactions", []):
                if tx.get("sender") == "coinbase":
                    self.balances[tx["recipient"]] = self.balances.get(tx["recipient"], 0) + tx["amount"]
                else:
                    self.balances[tx["sender"]] = self.balances.get(tx["sender"], 0) - (tx["amount"] + tx.get("fee", 0))
                    self.balances[tx["recipient"]] = self.balances.get(tx["recipient"], 0) + tx["amount"]

    def get_effective_balance(self, address):
        balance = self.balances.get(address, 0)
        for tx in self.mempool:
            if tx.get("sender") == address: balance -= tx.get("amount", 0) + tx.get("fee", 0)
            if tx.get("recipient") == address: balance += tx.get("amount", 0)
        return balance

    def get_balance(self, a): return self.balances.get(a, 0)
    def get_chain(self): return self.chain
    def get_mempool(self): return self.mempool


class LBTC_Miner:
    def __init__(self):
        self.running = False; self.thread = None; self.node_url = ""
        self.address = ""; self.sk = None; self.vk = None
        self.blocks_mined = 0; self.total_hashes = 0; self.start_time = 0
        self.last_block_hash = ""; self.last_block = None
        self.log_callback = None; self.throttle = 100
        self.current_hashes = 0; self.stats_callback = None
        self._last_reported_hashes = 0; self.pending_own_txs = []
        self.next_difficulty = None
        # Rolling hashrate window: list of (timestamp, cumulative_hashes)
        self._hr_window = []
        self._stats_thread = None

    def set_node(self, url): self.node_url = url.rstrip('/')

    def load_wallet(self, address, encrypted, pw):
        try:
            kb = decrypt_private_key(encrypted, pw)
            self.sk = SigningKey.from_string(kb, curve=SECP256k1)
            self.vk = self.sk.get_verifying_key(); self.address = address
            return True
        except: return False

    def create_wallet(self, pw):
        sk, vk = generate_keypair(); addr = address_from_vk(vk)
        enc = encrypt_private_key(sk.to_string(), pw)
        self.sk = sk; self.vk = vk; self.address = addr
        return addr, enc

    def load_from_private_key(self, hexk, pw):
        try:
            sk = SigningKey.from_string(bytes.fromhex(hexk), curve=SECP256k1)
            vk = sk.get_verifying_key(); addr = address_from_vk(vk)
            enc = encrypt_private_key(sk.to_string(), pw)
            self.sk = sk; self.vk = vk; self.address = addr
            return addr, enc
        except Exception as e: raise ValueError(f"Invalid private key: {e}")

    def get_balance(self):
        try:
            r = requests.get(f"{self.node_url}/api/balance/{self.address}", timeout=5)
            if r.status_code == 200: return r.json().get("balance", 0)
        except: pass
        return None

    def get_block_reward(self, h):
        hv = h // HALVING_INTERVAL
        if hv >= 64: return 0
        return BASE_REWARD // (2 ** hv)

    def calculate_next_difficulty(self, chain, interval=ADJUSTMENT_INTERVAL, target_time=DESIRED_BLOCK_TIME):
        return next_difficulty_smooth(
            chain,
            target_time=target_time,
            fallback="0000",
            log=self.log_callback,
        )

    def mine_once(self):
        if not self.running: return False
        if not self.sk:
            raise Exception('Wallet not fully loaded (no private key)')
        try:
            cr = requests.get(f"{self.node_url}/api/chain", timeout=10)
            if cr.status_code != 200: raise ConnectionError('Cannot fetch chain')
            chain_raw = cr.json()
            if isinstance(chain_raw, dict):
                if "chain" in chain_raw:
                    chain_raw = chain_raw["chain"]
                elif "blocks" in chain_raw:
                    chain_raw = chain_raw["blocks"]
                else:
                    raise Exception(f"Bad /api/chain dict keys={list(chain_raw.keys())[:5]}")
            if isinstance(chain_raw, str):
                import json as _json
                chain_raw = _json.loads(chain_raw)
            if not isinstance(chain_raw, list):
                raise Exception(f"/api/chain is {type(chain_raw).__name__}: {str(chain_raw)[:120]}")
            if not chain_raw:
                return False
            if not isinstance(chain_raw[0], dict):
                raise Exception(f"chain[0] is {type(chain_raw[0]).__name__}: {str(chain_raw[0])[:120]}")
            chain = sorted(chain_raw, key=lambda b: b.get('index', 0))
            mr = requests.get(f"{self.node_url}/api/mempool", timeout=10)
            mempool = mr.json() if mr.status_code == 200 else []
            if isinstance(mempool, dict):
                mempool = mempool.get("mempool", mempool.get("transactions", []))
            if not isinstance(mempool, list):
                mempool = []
            mempool = [tx for tx in mempool if isinstance(tx, dict)]
        except Exception:
            raise
        if not chain: return False
        for tx in self.pending_own_txs:
            if isinstance(tx, dict) and tx not in mempool:
                mempool.insert(0, tx)
        chain_hashes = set()
        for b in chain:
            if not isinstance(b, dict):
                continue
            for tx in b.get('transactions', []):
                if isinstance(tx, dict) and tx.get('tx_hash'):
                    chain_hashes.add(tx['tx_hash'])
        mempool = [tx for tx in mempool if isinstance(tx, dict) and tx.get('tx_hash') not in chain_hashes]
        last_block = chain[-1]
        index = last_block.get('index', 0) + 1
        timestamp = int(time.time())
        previous_hash = last_block['hash']
        if self.next_difficulty:
            difficulty = self.next_difficulty
            self.next_difficulty = None
        else:
            difficulty = self.calculate_next_difficulty(chain)
        reward = self.get_block_reward(index)
        mempool_in_block = mempool[:MAX_TXS_PER_BLOCK] if mempool else []
        total_fees = sum(tx.get('fee', 0) for tx in mempool_in_block if tx.get('sender') != 'coinbase')
        coinbase = {'sender': 'coinbase', 'recipient': self.address,
            'amount': reward + total_fees, 'fee': total_fees, 'timestamp': timestamp}
        transactions = [coinbase] + mempool_in_block
        block = {'index': index, 'timestamp': timestamp, 'transactions': transactions,
            'previous_hash': previous_hash, 'nonce': 0, 'difficulty': difficulty}
        target = difficulty
        target_lower = target.lower() if len(target) == 64 else None
        _block_src = json.dumps({**block, 'nonce': 0}, sort_keys=True)
        _needle = '"nonce": 0'
        if _needle in _block_src:
            _head, _tail = _block_src.split(_needle, 1)
            _head = (_head + '"nonce": ').encode()
            _tail = _tail.encode()
            _preserialized = True
        else:
            _preserialized = False
        while self.running:
            if _preserialized:
                _bytes = _head + str(block['nonce']).encode() + _tail
            else:
                _bytes = json.dumps(block, sort_keys=True).encode()
            bh = hashlib.sha256(_bytes).hexdigest()
            if target_lower is not None:
                ok = bh < target_lower
            else:
                ok = bh.startswith(target)
            if ok:
                block['hash'] = bh
                break
            block['nonce'] += 1
            self.current_hashes += 1
            if self.throttle < 100 and self.current_hashes % 1000 == 0:
                st = (100 - self.throttle) * 0.001
                if st > 0: time.sleep(st)
        if not self.running: return False
        message = f"BLOCK|{block['index']}|{block['hash']}|{self.address}"
        block_sig = sign_message(self.sk, message)
        block_pub = self.vk.to_string().hex()
        try:
            headers = {'X-Block-Signature': block_sig, 'X-Block-Pubkey': block_pub}
            sr = requests.post(f"{self.node_url}/api/block", json=block, timeout=30, headers=headers)
            try:
                resp = sr.json()
            except Exception:
                resp = {}
            status_str = resp.get('status', '')
            error_str = resp.get('error', '')
            if sr.status_code == 200 and status_str == 'ok':
                self.blocks_mined += 1
                self.last_block_hash = block['hash']
                self.last_block = block
                for tx in block['transactions']:
                    if tx in self.pending_own_txs: self.pending_own_txs.remove(tx)
                self.next_difficulty = self.calculate_next_difficulty(chain + [block])
                return True
            elif status_str == 'already exists':
                self.next_difficulty = None
                if self.log_callback:
                    self.log_callback(f"Block #{block['index']} already exists - retrying")
                return False
            else:
                if self.log_callback:
                    self.log_callback(f"Block rejected: HTTP {sr.status_code} - {status_str or error_str or sr.text[:150]}")
                return False
        except Exception:
            raise

    def start_mining(self, callback):
        self.running = True; self.blocks_mined = 0; self.total_hashes = 0
        self.current_hashes = 0; self._last_reported_hashes = 0; self.next_difficulty = None
        self._hr_window = []
        self.start_time = time.time(); self.stats_callback = callback
        # Main mining loop
        self.thread = threading.Thread(target=self._mine_loop, args=(callback,))
        self.thread.daemon = True; self.thread.start()
        # Separate stats reporting thread (stable hashrate, unaffected by submit gaps)
        self._stats_thread = threading.Thread(target=self._stats_loop, daemon=True)
        self._stats_thread.start()

    def stop_mining(self):
        self.running = False
        if self.thread: self.thread.join(timeout=2)
        if self._stats_thread: self._stats_thread.join(timeout=1)

    def _stats_loop(self):
        """Report hashrate every 1s using a rolling 10-second window."""
        while self.running:
            time.sleep(1.0)
            if not self.running:
                break
            now = time.time()
            self._hr_window.append((now, self.current_hashes))
            # Keep only the last 10 seconds
            self._hr_window = [(t, h) for t, h in self._hr_window if now - t <= 10]
            if len(self._hr_window) >= 2:
                oldest_t, oldest_h = self._hr_window[0]
                newest_t, newest_h = self._hr_window[-1]
                dt = newest_t - oldest_t
                hr = (newest_h - oldest_h) / dt if dt > 0 else 0
            else:
                hr = 0
            if self.stats_callback:
                try:
                    self.stats_callback("stats", {
                        "blocks": self.blocks_mined,
                        "hashrate": hr,
                        "uptime": now - self.start_time,
                        "last_hash": self.last_block_hash,
                    })
                except Exception:
                    pass

    def _mine_loop(self, callback):
        if self.log_callback: self.log_callback(f"⛏️ Mining started for {self.address[:20]}...")
        while self.running:
            try:
                if self.mine_once(): callback("block_mined", self.last_block)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionError) as e:
                if self.log_callback: self.log_callback(f"⚠️ Node connection: {e}")
                time.sleep(2)
            except Exception as e:
                if not hasattr(self, "_err_logged"):
                    self._err_logged = set()
                key = str(type(e).__name__) + ":" + str(e)[:60]
                if key not in self._err_logged:
                    self._err_logged.add(key)
                    if self.log_callback:
                        self.log_callback(f"⚠️ Mining error: {e}")
                time.sleep(0.5)


class LBTCApp:
    def __init__(self):
        ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("dark-blue")
        self.root = ctk.CTk(); self.root.title("⛏️ LBTC-PRO Wallet & Miner v51")
        self.root.geometry("1150x950"); self.root.minsize(1000, 850)
        self.miner = LBTC_Miner(); self.miner.log_callback = self.add_mining_log
        self.wallet_file = os.path.join(DATA_DIR, "wallet_gui.json")
        self.load_wallet_file()
        self.node_url = PUBLIC_NODE_URL; self.pending_sent_txs = []
        self.node_process = None
        self.node_registered = False
        self.stake_widgets = {}
        self.nft_image_refs = {}
        self.market_image_refs = {}
        self.treasury_rows = {}
        self.build_ui(); self.update_status(); self.update_uptime(); self.refresh_pending()
        self.root.after(1000, self.update_stake_countdown)
        self.root.after(3000, self._initial_refreshes)

    def _initial_refreshes(self):
        self.refresh_dashboard()
        self.refresh_nfts()
        self.load_proposals()
        self.refresh_marketplace()
        self.refresh_my_listings()
        self.refresh_memecoins()

    def load_wallet_file(self):
        if os.path.exists(self.wallet_file):
            with open(self.wallet_file, 'r') as f: self.wallet_data = json.load(f)
        else: self.wallet_data = {}

    def save_wallet_file(self):
        with open(self.wallet_file, 'w') as f: json.dump(self.wallet_data, f)

    def build_ui(self):
        self.main = ctk.CTkFrame(self.root, fg_color="#0a0a0a")
        self.main.pack(fill="both", expand=True, padx=10, pady=10)
        top = ctk.CTkFrame(self.main, height=60, fg_color="#1a1a1a")
        top.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(top, text="⛏️ LBTC-PRO v51", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color="#f7931a").pack(side="left", padx=10)
        self.status_label = ctk.CTkLabel(top, text="⚪ Disconnected", text_color="gray", font=ctk.CTkFont(size=14))
        self.status_label.pack(side="right", padx=10)
        self.tab_view = ctk.CTkTabview(self.main, fg_color="#111111")
        self.tab_view.pack(fill="both", expand=True)
        for t in ["Dashboard", "Wallet", "Mining", "Transactions", "Pending",
                  "Staking", "NFTs", "Marketplace", "Bridge", "Memecoins",
                  "Governance", "Settings"]:
            self.tab_view.add(t)
        self.build_dashboard(); self.build_wallet(); self.build_mining()
        self.build_transactions(); self.build_pending(); self.build_staking()
        self.build_nfts(); self.build_marketplace()
        self.build_bridge(); self.build_memecoins()
        self.build_governance(); self.build_settings()

    def copy_address(self):
        a = self.wallet_addr_entry.get()
        if a and a != "(not loaded)":
            self.root.clipboard_clear(); self.root.clipboard_append(a)
            self.log("📋 Address copied!")
            self.copy_btn.configure(text="✅ Copied!", fg_color="green")
            self.root.after(1500, lambda: self.copy_btn.configure(text="📋 Copy", fg_color=("gray", "gray")))
        else: messagebox.showwarning("Warning", "No wallet loaded")

    # ============================================
    # DASHBOARD
    # ============================================
    def build_dashboard(self):
        f = self.tab_view.tab("Dashboard")
        scroll = ctk.CTkScrollableFrame(f, fg_color="#0a0a0a")
        scroll.pack(fill="both", expand=True)
        hf = ctk.CTkFrame(scroll, fg_color="#1a1a1a"); hf.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(hf, text="LATE BITCOINERS", font=ctk.CTkFont(size=28, weight="bold"),
                     text_color="#f7931a").pack(pady=(12, 2))
        ctk.CTkLabel(hf, text="LBTC Wallet & Miner v51", font=ctk.CTkFont(size=14),
                     text_color="#aaaaaa").pack(pady=(0, 12))

        promo = ctk.CTkFrame(scroll, fg_color="#1a1a1a", corner_radius=12,
                             border_width=1, border_color="#f7931a")
        promo.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(promo, text="🌐 Explore on the web",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(promo,
                     text="Bridge to Arbitrum, launch memecoins, mint NFTs, stake, and explore the full chain.",
                     font=ctk.CTkFont(size=13), text_color="#c9d1d9", justify="left"
                     ).pack(anchor="w", padx=16, pady=(0, 8))
        prow = ctk.CTkFrame(promo, fg_color="transparent"); prow.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkButton(prow, text="🚀  Open Website", command=self.open_website,
                      fg_color="#f7931a", hover_color="#d97f0f", text_color="#0a0a0a",
                      font=ctk.CTkFont(weight="bold"), width=170, height=38).pack(side="left", padx=(0, 10))
        ctk.CTkButton(prow, text="🔍  View Explorer", command=self.open_explorer,
                      fg_color="#21262d", hover_color="#30363d", font=ctk.CTkFont(weight="bold"),
                      width=170, height=38).pack(side="left", padx=(0, 10))
        ctk.CTkButton(prow, text="🎨  NFT Gallery", command=self.open_nft_gallery,
                      fg_color="#21262d", hover_color="#30363d", font=ctk.CTkFont(weight="bold"),
                      width=170, height=38).pack(side="left")
        prow2 = ctk.CTkFrame(promo, fg_color="transparent"); prow2.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(prow2, text="🌉  Open Bridge", command=self.open_bridge,
                      fg_color="#1f6feb", hover_color="#1a60c9",
                      font=ctk.CTkFont(weight="bold"), width=170, height=38
                      ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(prow2, text="🔥  Memecoins", command=self.open_memecoins,
                      fg_color="#ff9f2e", hover_color="#e0851a", text_color="#0a0a0a",
                      font=ctk.CTkFont(weight="bold"), width=170, height=38
                      ).pack(side="left")

        tb = ctk.CTkFrame(scroll, fg_color="#0d1a10", corner_radius=12,
                          border_width=2, border_color="#3fb950")
        tb.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(tb, text="🏦 Staking Treasury", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#3fb950").pack(anchor="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(tb,
                     text="All app-layer fees flow here and distribute to stakers every 20 blocks.",
                     font=ctk.CTkFont(size=12), text_color="#8b949e",
                     justify="left").pack(anchor="w", padx=16, pady=(0, 10))
        src_box = ctk.CTkFrame(tb, fg_color="#0a1a0d", corner_radius=8)
        src_box.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(src_box, text="💰 WHERE THE YIELD COMES FROM",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#3fb950").pack(anchor="w", padx=12, pady=(10, 6))
        sources_text = (
            "• NFT mint fees — 18% to treasury\n"
            "• NFT transfer fees — 0.90 LBTC per transfer\n"
            "• NFT redeem fees — 0.90 LBTC per redeem\n"
            "• Marketplace commission — 1.8% of every NFT sale\n"
            "• Early stake unlock — 2.70 LBTC per early unlock\n"
            "• Memecoin creation — 4,000 LBTC per launch (dripped over 31 days)\n"
            "• Memecoin swaps — 0.10% of every buy/sell trade\n\n"
            "10% of every fee → Reserve (permanent, never distributed)\n"
            "72% of mint cost → NFT escrow (returned on redeem)\n"
            "36% of early-unlock fee → burned (removed from supply)"
        )
        ctk.CTkLabel(src_box, text=sources_text, font=ctk.CTkFont(size=11),
                     text_color="#c9d1d9", justify="left").pack(anchor="w", padx=12, pady=(0, 10))

        for key, label, color in [
            ("treasury", "Treasury balance", "#3fb950"),
            ("meme_reserve", "Memecoin treasury reserve (drip queue)", "#ffaa33"),
            ("reserve", "Reserve balance (permanent)", "#58a6ff"),
            ("revenue", "Lifetime revenue (never resets)", "#58a6ff"),
            ("burned", "🔥 Total burned", "#f85149"),
            ("pending", "Pending staker claims", "#58a6ff"),
        ]:
            row = ctk.CTkFrame(tb, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=2)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=12),
                         text_color="#8b949e").pack(side="left")
            val = ctk.CTkLabel(row, text="-- LBTC", font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                               text_color=color)
            val.pack(side="right")
            self.treasury_rows[key] = val
        ctk.CTkFrame(tb, fg_color="transparent", height=8).pack()

        nf = ctk.CTkFrame(scroll, fg_color="#1a1a1a"); nf.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(nf, text="🌐 Network Status", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#4fc3f7").pack(anchor="w", padx=12, pady=(10, 4))
        self.dash_status = ctk.CTkLabel(nf, text="🔴 Disconnected", font=ctk.CTkFont(size=14))
        self.dash_status.pack(anchor="w", padx=12, pady=2)
        self.dash_blocks = ctk.CTkLabel(nf, text="Block Height: --", font=ctk.CTkFont(size=14))
        self.dash_blocks.pack(anchor="w", padx=12, pady=2)
        self.dash_peers = ctk.CTkLabel(nf, text="Peers: --", font=ctk.CTkFont(size=14))
        self.dash_peers.pack(anchor="w", padx=12, pady=(2, 12))

        cs = ctk.CTkFrame(scroll, fg_color="#1a1a1a"); cs.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(cs, text="⛓️  Chain Status",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=12, pady=(10, 6))
        grid = ctk.CTkFrame(cs, fg_color="transparent"); grid.pack(fill="x", padx=12, pady=(0, 12))
        for i in range(3): grid.grid_columnconfigure(i, weight=1)
        self.chain_labels = {}
        items = [
            ("status", "Node status"), ("server", "Server"), ("server_ip", "Server IP"),
            ("uptime", "Server uptime"), ("height", "Block height"), ("wallets", "Wallets created"),
            ("supply", "Total supply"), ("meme_reserve", "Memecoin reserve"),
            ("reserve", "Reserve balance"), ("revenue", "Lifetime revenue"),
            ("burned", "🔥 Total burned"), ("mined", "Blocks mined"),
            ("difficulty", "Difficulty"), ("peers", "Peers connected"),
            ("mempool", "Mempool pending"), ("latest_hash", "Latest block hash"),
        ]
        for i, (key, label) in enumerate(items):
            r = i // 3; c = i % 3
            cell = ctk.CTkFrame(grid, fg_color="#0a0a0a", corner_radius=6)
            cell.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
            ctk.CTkLabel(cell, text=label.upper(), font=ctk.CTkFont(size=9),
                         text_color="#6e7681").pack(anchor="w", padx=10, pady=(8, 0))
            val_lbl = ctk.CTkLabel(cell, text="--",
                                   font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                                   text_color="#c9d1d9", anchor="w")
            val_lbl.pack(anchor="w", padx=10, pady=(2, 8), fill="x")
            self.chain_labels[key] = val_lbl

        wp = ctk.CTkFrame(scroll, fg_color="#1a1a1a"); wp.pack(fill="x", pady=(0, 12))
        wh = ctk.CTkFrame(wp, fg_color="transparent"); wh.pack(fill="x", padx=12, pady=(10, 4))
        self.wallet_dot = ctk.CTkLabel(wh, text="🔴", width=20); self.wallet_dot.pack(side="left")
        ctk.CTkLabel(wh, text="Wallet", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#4caf50").pack(side="left")
        self.dash_wallet_address = ctk.CTkLabel(wp, text="Address: --",
                                                font=ctk.CTkFont(family="Consolas", size=12))
        self.dash_wallet_address.pack(anchor="w", padx=12, pady=2)
        self.dash_wallet_balance = ctk.CTkLabel(wp, text="Balance: --",
                                                font=ctk.CTkFont(size=14, weight="bold"),
                                                text_color="#4fc3f7")
        self.dash_wallet_balance.pack(anchor="w", padx=12, pady=(2, 12))

        ctk.CTkButton(scroll, text="🔄 Refresh Dashboard", command=self.refresh_dashboard,
                      fg_color="#f7931a", hover_color="#d97f0f", text_color="#0a0a0a",
                      font=ctk.CTkFont(weight="bold"), width=200, height=38).pack(pady=(4, 10))

    def open_website(self): webbrowser.open(WEBSITE_URL)
    def open_explorer(self): webbrowser.open(EXPLORER_URL)
    def open_nft_gallery(self): webbrowser.open(f"{EXPLORER_URL}/nfts")
    def open_nft_detail(self, nft_id): webbrowser.open(f"{EXPLORER_URL}/nft/{nft_id}")
    def open_peers_registry(self): webbrowser.open(f"{PUBLIC_NODE_URL}/api/peers/registry")
    def open_bridge(self): webbrowser.open(BRIDGE_URL)
    def open_memecoins(self): webbrowser.open(MEMECOINS_URL)
    def open_memecoins_create(self): webbrowser.open(MEMECOINS_CREATE_URL)

    def refresh_dashboard(self):
        if self.miner.address:
            self.wallet_dot.configure(text="🟢")
            self.dash_wallet_address.configure(text=f"Address: {self.miner.address}")
            bal = self.miner.get_balance()
            self.dash_wallet_balance.configure(
                text=f"Balance: {format_lbtc(bal) if bal is not None else '?'} LBTC")
        else:
            self.wallet_dot.configure(text="🔴")
            self.dash_wallet_address.configure(text="Address: --")
            self.dash_wallet_balance.configure(text="Balance: --")
        try:
            r = requests.get(f"{self.node_url}/api/chain", timeout=5)
            if r.status_code == 200:
                chain = r.json()
                self.dash_blocks.configure(text=f"Block Height: {len(chain)}")
                self.dash_status.configure(text="🟢 Connected", text_color="green")
                self.status_label.configure(text="🟢 Connected", text_color="green")
                self._update_chain_status(chain)
            else:
                self.dash_status.configure(text="🔴 Disconnected", text_color="red")
                self.status_label.configure(text="🔴 Disconnected", text_color="red")
        except:
            self.dash_status.configure(text="🔴 Disconnected", text_color="red")
            self.status_label.configure(text="🔴 Disconnected", text_color="red")
        try:
            pr = requests.get(f"{self.node_url}/api/peers", timeout=5)
            if pr.status_code == 200:
                peers = pr.json()
                self.dash_peers.configure(text=f"Peers: {len(peers)}")
                self.chain_labels["peers"].configure(text=str(len(peers)))
        except:
            self.dash_peers.configure(text="Peers: --")

    def _update_chain_status(self, chain):
        supply = 0; mined = 0
        wallets = set()
        SYSTEM = {"coinbase", "NFT_MINT", "TREASURY", "REWARDS_POOL", "NFT_ESCROW", "BURN", "RESERVE", "MEME_TREASURY_RESERVE"}
        for b in chain:
            for tx in b.get("transactions", []):
                s = tx.get("sender", "") or ""
                r = tx.get("recipient", "") or ""
                if s == "coinbase":
                    supply += tx.get("amount", 0); mined += 1
                if s and s not in SYSTEM and not s.startswith("STAKE_LOCK:") and not s.startswith("MEMP_POOL:"): wallets.add(s)
                if r and r not in SYSTEM and not r.startswith("STAKE_LOCK:") and not r.startswith("MEMP_POOL:"): wallets.add(r)
        latest = chain[-1] if chain else {}
        latest_hash = latest.get("hash", "")
        difficulty = latest.get("difficulty", "----")
        try: server_name = socket.gethostname()
        except: server_name = "local"
        try: server_ip = socket.gethostbyname(server_name)
        except: server_ip = "127.0.0.1"
        try:
            uptime_seconds = float(open('/proc/uptime').read().split()[0]) if os.name == 'posix' else 0
        except: uptime_seconds = 0
        if uptime_seconds <= 0:
            uptime_seconds = time.time() - self.miner.start_time if self.miner.start_time else 0
        days = int(uptime_seconds // 86400); hours = int((uptime_seconds % 86400) // 3600)
        mins = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{days}d {hours}h {mins}m"
        latest_hash_short = latest_hash[:24] + "..." if len(latest_hash) > 24 else latest_hash
        try:
            mr = requests.get(f"{self.node_url}/api/mempool", timeout=3)
            mempool_count = len(mr.json()) if mr.status_code == 200 else 0
        except: mempool_count = 0
        try:
            ts = requests.get(f"{self.node_url}/api/treasury/stats", timeout=3).json()
            treasury_lbtc = ts.get("balance_lbtc", 0)
            reserve_lbtc = ts.get("reserve_balance_lbtc", 0)
            burned_lbtc = ts.get("burned_total_lbtc", 0)
            revenue_lbtc = ts.get("lifetime_revenue_lbtc", 0)
            meme_reserve_lbtc = ts.get("meme_treasury_reserve_balance_lbtc", 0)
        except:
            treasury_lbtc = reserve_lbtc = burned_lbtc = revenue_lbtc = 0
            meme_reserve_lbtc = 0
        try:
            ss = requests.get(f"{self.node_url}/api/staking/stats", timeout=3).json()
            pending_claims = ss.get("treasury_pending_claims", 0) / COIN
        except:
            pending_claims = 0
        labels = self.chain_labels
        labels["status"].configure(text="🟢 Online")
        labels["server"].configure(text=server_name)
        labels["server_ip"].configure(text=server_ip)
        labels["uptime"].configure(text=uptime_str)
        labels["height"].configure(text=f"#{len(chain)}")
        labels["wallets"].configure(text=str(len(wallets)))
        labels["supply"].configure(text=f"{supply/COIN:.2f} LBTC")
        labels["meme_reserve"].configure(text=f"{meme_reserve_lbtc:.4f} LBTC")
        labels["reserve"].configure(text=f"{reserve_lbtc:.4f} LBTC")
        labels["revenue"].configure(text=f"{revenue_lbtc:.4f} LBTC")
        labels["burned"].configure(text=f"{burned_lbtc:.4f} LBTC")
        labels["mined"].configure(text=str(mined))
        labels["difficulty"].configure(text=difficulty)
        labels["mempool"].configure(text=str(mempool_count))
        labels["latest_hash"].configure(text=latest_hash_short)
        if hasattr(self, 'treasury_rows') and self.treasury_rows:
            self.treasury_rows["treasury"].configure(text=f"{treasury_lbtc:.4f} LBTC")
            self.treasury_rows["meme_reserve"].configure(text=f"{meme_reserve_lbtc:.4f} LBTC")
            self.treasury_rows["reserve"].configure(text=f"{reserve_lbtc:.4f} LBTC")
            self.treasury_rows["revenue"].configure(text=f"{revenue_lbtc:.4f} LBTC")
            self.treasury_rows["burned"].configure(text=f"{burned_lbtc:.4f} LBTC")
            self.treasury_rows["pending"].configure(text=f"{pending_claims:.4f} LBTC")

    # ============================================
    # WALLET
    # ============================================
    def build_wallet(self):
        f = self.tab_view.tab("Wallet")
        ctk.CTkLabel(f, text="Wallet Management", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", pady=5)
        af = ctk.CTkFrame(f); af.pack(fill="x", pady=5)
        ctk.CTkLabel(af, text="Address:").pack(side="left", padx=5)
        self.wallet_addr_entry = ctk.CTkEntry(af, width=350, state="readonly")
        self.wallet_addr_entry.pack(side="left", padx=5)
        self.wallet_addr_entry.insert(0, "(not loaded)")
        self.copy_btn = ctk.CTkButton(af, text="📋 Copy", command=self.copy_address, width=60)
        self.copy_btn.pack(side="left", padx=5)
        acf = ctk.CTkFrame(f); acf.pack(fill="x", pady=5)
        ctk.CTkButton(acf, text="Create Wallet", command=self.create_wallet, width=110,
                      fg_color="#4caf50", hover_color="#388e3c").pack(side="left", padx=5)
        ctk.CTkButton(acf, text="Load Wallet", command=self.load_wallet, width=100,
                      fg_color="#2196f3", hover_color="#1976d2").pack(side="left", padx=5)
        ctk.CTkButton(acf, text="Import Private Key", command=self.import_private_key, width=130,
                      fg_color="#ff9800", hover_color="#f57c00").pack(side="left", padx=5)
        ctk.CTkButton(acf, text="Export Private Key", command=self.export_private_key, width=130,
                      fg_color="#9c27b0", hover_color="#7b1fa2").pack(side="left", padx=5)
        bf = ctk.CTkFrame(f); bf.pack(fill="x", pady=5)
        ctk.CTkLabel(bf, text="Balance:").pack(side="left", padx=5)
        self.wallet_balance = ctk.CTkLabel(bf, text="--", font=ctk.CTkFont(weight="bold"),
                                           text_color="#4fc3f7")
        self.wallet_balance.pack(side="left", padx=5)
        ctk.CTkButton(bf, text="Refresh", command=self.refresh_balance, width=80).pack(side="left", padx=5)
        sf = ctk.CTkFrame(f); sf.pack(fill="x", pady=10)
        ctk.CTkLabel(sf, text="Send LBTC", font=ctk.CTkFont(weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=5)
        ctk.CTkLabel(sf, text="Recipient:").pack(anchor="w", padx=5)
        self.send_recipient = ctk.CTkEntry(sf, width=400); self.send_recipient.pack(anchor="w", padx=5, pady=2)
        ctk.CTkLabel(sf, text="Amount (LBTC):").pack(anchor="w", padx=5)
        self.send_amount = ctk.CTkEntry(sf, width=150); self.send_amount.pack(anchor="w", padx=5, pady=2)
        ctk.CTkLabel(sf, text=f"ℹ️  Fixed network fee: {format_lbtc(FEE_PER_TX)} LBTC",
                     text_color="#ffaa33", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=5, pady=2)
        ctk.CTkButton(sf, text="Send", command=self.send_coins, width=100,
                      fg_color="#f7931a", hover_color="#d97f0f", text_color="#0a0a0a",
                      font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5, pady=5)

    def create_wallet(self):
        pw = ctk.CTkInputDialog(text="Enter passphrase (min 12 chars):", title="Create Wallet").get_input()
        if not pw or len(pw) < 12:
            messagebox.showerror("Error", "Passphrase must be at least 12 characters"); return
        addr, enc = self.miner.create_wallet(pw)
        self.wallet_data = {"address": addr, "encrypted": enc}
        self.save_wallet_file(); self._update_wallet_display(addr)
        self.log("Wallet created: " + addr)
        self.miner.set_node(self.node_url)
        self.refresh_balance(); self.refresh_dashboard(); self.refresh_nfts()
        self.refresh_marketplace(); self.refresh_my_listings()

    def load_wallet(self):
        if not os.path.exists(self.wallet_file):
            messagebox.showinfo("Info", "No wallet file found."); return
        pw = ctk.CTkInputDialog(text="Enter passphrase:", title="Load Wallet").get_input()
        if not pw: return
        addr = self.wallet_data.get("address"); enc = self.wallet_data.get("encrypted")
        if self.miner.load_wallet(addr, enc, pw):
            self._update_wallet_display(addr); self.miner.set_node(self.node_url)
            self.refresh_balance(); self.refresh_dashboard(); self.refresh_nfts()
            self.refresh_marketplace(); self.refresh_my_listings()
            self.log("Wallet loaded: " + addr)
        else: messagebox.showerror("Error", "Wrong passphrase or corrupted wallet")

    def import_private_key(self):
        hexk = ctk.CTkInputDialog(text="Enter private key (hex):", title="Import").get_input()
        if not hexk: return
        pw = ctk.CTkInputDialog(text="Set a new passphrase (min 12 chars):", title="Passphrase").get_input()
        if not pw or len(pw) < 12:
            messagebox.showerror("Error", "Min 12 chars"); return
        try:
            addr, enc = self.miner.load_from_private_key(hexk, pw)
            self.wallet_data = {"address": addr, "encrypted": enc}; self.save_wallet_file()
            self._update_wallet_display(addr); self.miner.set_node(self.node_url)
            self.refresh_balance(); self.refresh_dashboard(); self.refresh_nfts()
            self.refresh_marketplace(); self.refresh_my_listings()
            self.log("Wallet imported: " + addr)
        except Exception as e: messagebox.showerror("Error", f"Invalid private key: {e}")

    def export_private_key(self):
        if not self.miner.sk:
            messagebox.showwarning("Warning", "No wallet loaded."); return
        pw = ctk.CTkInputDialog(text="Enter your passphrase:", title="Export").get_input()
        if not pw: return
        try:
            enc = self.wallet_data.get("encrypted")
            if not enc: messagebox.showerror("Error", "No encrypted key"); return
            kb = decrypt_private_key(enc, pw)
            sk = SigningKey.from_string(kb, curve=SECP256k1)
            hexk = sk.to_string().hex()
            p = ctk.CTkToplevel(self.root); p.title("Private Key"); p.geometry("500x200")
            ctk.CTkLabel(p, text="⚠️ NEVER share this private key!", text_color="red").pack(pady=5)
            ctk.CTkLabel(p, text="Private Key (hex):").pack(pady=2)
            tb = ctk.CTkTextbox(p, height=80); tb.pack(fill="both", expand=True, padx=10, pady=10)
            tb.insert("1.0", hexk); tb.configure(state="disabled")
            ctk.CTkButton(p, text="Copy", command=lambda: self._copy_clip(hexk)).pack(pady=5)
        except Exception as e: messagebox.showerror("Error", f"Decrypt failed: {e}")

    def _copy_clip(self, t):
        self.root.clipboard_clear(); self.root.clipboard_append(t)
        messagebox.showinfo("Copied", "Copied!")

    def _update_wallet_display(self, addr):
        self.wallet_addr_entry.configure(state="normal"); self.wallet_addr_entry.delete(0, "end")
        self.wallet_addr_entry.insert(0, addr); self.wallet_addr_entry.configure(state="readonly")
        self.refresh_dashboard()
        try:
            self.load_proposals()
            self.refresh_marketplace()
            self.refresh_my_listings()
        except: pass

    def refresh_balance(self):
        if not self.miner.address: return
        bal = self.miner.get_balance()
        if bal is not None:
            self.wallet_balance.configure(text=f"{format_lbtc(bal)} LBTC")
            self.dash_wallet_balance.configure(text=f"Balance: {format_lbtc(bal)} LBTC")

    # ============================================
    # MINING
    # ============================================
    def build_mining(self):
        f = self.tab_view.tab("Mining")
        ctk.CTkLabel(f, text="Mining Controls", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", pady=5)
        sf = ctk.CTkFrame(f); sf.pack(fill="x", pady=5)
        ctk.CTkLabel(sf, text="Blocks Mined:").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.mine_blocks = ctk.CTkLabel(sf, text="0", font=ctk.CTkFont(weight="bold", size=16),
                                        text_color="#4fc3f7")
        self.mine_blocks.grid(row=0, column=1, padx=5, pady=2, sticky="w")
        ctk.CTkLabel(sf, text="Hash Rate:").grid(row=0, column=2, padx=5, pady=2, sticky="w")
        self.mine_hashrate = ctk.CTkLabel(sf, text="0 H/s", font=ctk.CTkFont(weight="bold", size=16),
                                          text_color="#4fc3f7")
        self.mine_hashrate.grid(row=0, column=3, padx=5, pady=2, sticky="w")
        ctk.CTkLabel(sf, text="Uptime:").grid(row=1, column=0, padx=5, pady=2, sticky="w")
        self.mine_uptime = ctk.CTkLabel(sf, text="00:00:00", font=ctk.CTkFont(weight="bold", size=16),
                                        text_color="#4fc3f7")
        self.mine_uptime.grid(row=1, column=1, padx=5, pady=2, sticky="w")
        tf = ctk.CTkFrame(f); tf.pack(fill="x", pady=10)
        ctk.CTkLabel(tf, text="Mining Power:", font=ctk.CTkFont(weight="bold"),
                     text_color="#f7931a").pack(side="left", padx=5)
        self.throttle_slider = ctk.CTkSlider(tf, from_=1, to=100, number_of_steps=99,
                                             command=self.update_throttle, fg_color="#4caf50",
                                             button_color="#f7931a", button_hover_color="#d97f0f")
        self.throttle_slider.pack(side="left", fill="x", expand=True, padx=10)
        self.throttle_slider.set(100)
        self.throttle_label = ctk.CTkLabel(tf, text="100%", width=40, font=ctk.CTkFont(weight="bold"))
        self.throttle_label.pack(side="left", padx=5)
        bf = ctk.CTkFrame(f); bf.pack(fill="x", pady=10)
        self.mine_btn = ctk.CTkButton(bf, text="▶ Start Mining", command=self.toggle_mining,
                                      width=150, height=40, fg_color="#f7931a", hover_color="#d97f0f",
                                      text_color="#0a0a0a", font=ctk.CTkFont(weight="bold"))
        self.mine_btn.pack(side="left", padx=10)
        lf = ctk.CTkFrame(f); lf.pack(fill="both", expand=True, pady=10)
        ctk.CTkLabel(lf, text="⛏️ Mining Activity Log", font=ctk.CTkFont(weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=5)
        self.mining_log_box = ctk.CTkTextbox(lf, height=200, font=ctk.CTkFont(family="Consolas", size=11))
        self.mining_log_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.mining_log_box.insert("1.0", "⏳ Start mining to see activity...\n")
        self.mining_log_box.configure(state="disabled")

    def update_throttle(self, v):
        self.miner.throttle = int(v); self.miner._last_reported_hashes = 0
        self.throttle_label.configure(text=f"{int(v)}%")

    def add_mining_log(self, msg):
        self.root.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg):
        self.mining_log_box.configure(state="normal")
        self.mining_log_box.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.mining_log_box.see("end"); self.mining_log_box.configure(state="disabled")

    def toggle_mining(self):
        if not self.miner.running:
            if not self.miner.address:
                messagebox.showwarning("Warning", "Load or create a wallet first"); return
            self.mining_log_box.configure(state="normal")
            self.mining_log_box.delete("1.0", "end"); self.mining_log_box.configure(state="disabled")
            self.add_mining_log("⛏️ Starting local miner...")
            self.add_mining_log(f"📡 Node: {self.node_url}")
            self.miner.set_node(self.node_url); self.miner.start_mining(self.miner_callback)
            self.mine_btn.configure(text="⏹ Stop Mining", fg_color="red")
            self.status_label.configure(text="⛏️ Mining", text_color="#f7931a")
        else:
            self.miner.stop_mining()
            self.mine_btn.configure(text="▶ Start Mining", fg_color="#f7931a")
            self.status_label.configure(text="🟢 Connected", text_color="green")
            self.add_mining_log("⏹ Mining stopped")

    # ============================================
    # TRANSACTIONS
    # ============================================
    def build_transactions(self):
        f = self.tab_view.tab("Transactions")
        ctk.CTkLabel(f, text="Transaction History", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", pady=5)
        bf = ctk.CTkFrame(f); bf.pack(fill="x", pady=5)
        ctk.CTkButton(bf, text="🔄 Refresh", command=self.refresh_transactions,
                      fg_color="#4caf50", hover_color="#388e3c").pack(side="left", padx=5)
        self.tx_list = ctk.CTkTextbox(f, height=300, font=ctk.CTkFont(family="Consolas", size=12))
        self.tx_list.pack(fill="both", expand=True, pady=5)
        self.tx_list.insert("1.0", "No transactions yet.\n")

    def refresh_transactions(self):
        if not self.miner.address: return
        try:
            r = requests.get(f"{self.node_url}/api/chain", timeout=10)
            if r.status_code != 200: return
            chain = r.json(); txs = []
            for block in chain:
                for tx in block.get("transactions", []):
                    if tx.get("sender") == self.miner.address or tx.get("recipient") == self.miner.address:
                        tx['block'] = block.get('index'); tx['timestamp'] = block.get('timestamp')
                        tx['block_hash'] = block.get('hash', ''); txs.append(tx)
            txs.sort(key=lambda x: x.get('block', 0), reverse=True)
            self.tx_list.delete("1.0", "end")
            if not txs: self.tx_list.insert("1.0", "No transactions found.")
            for tx in txs:
                blk = tx.get('block', '?'); s = tx.get('sender', '?'); r_ = tx.get('recipient', '?')
                amt = tx.get('amount', 0); fee = tx.get('fee', 0); ts = tx.get('timestamp', 0)
                bh = tx.get('block_hash', ''); th = tx.get('tx_hash', '')
                memo = tx.get('memo', '')
                tstr = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts)) if ts else 'N/A'
                if s == 'coinbase': d = "⛏️ Coinbase"; det = f"→ {r_}"
                elif s == self.miner.address: d = "⬅️ Sent"; det = f"to {r_} (fee: {format_lbtc(fee)})"
                else: d = "➡️ Received"; det = f"from {s}"
                hd = f"  TxHash: {th[:30]}...\n" if th else ""
                md = f"  Memo: {memo}\n" if memo else ""
                self.tx_list.insert("end", f"[Block {blk}] {tstr}\n  {d} {format_lbtc(amt)} LBTC {det}\n  BlockHash: {bh[:20]}...\n{hd}{md}\n")
        except: pass

    # ============================================
    # PENDING
    # ============================================
    def build_pending(self):
        f = self.tab_view.tab("Pending")
        ctk.CTkLabel(f, text="Pending Transactions (Unconfirmed)",
                     font=ctk.CTkFont(size=16, weight="bold"), text_color="#ffaa33").pack(anchor="w", pady=5)
        bf = ctk.CTkFrame(f); bf.pack(fill="x", pady=5)
        ctk.CTkButton(bf, text="🔄 Refresh", command=self.refresh_pending,
                      fg_color="#f7931a", hover_color="#d97f0f").pack(side="left", padx=5)
        self.pending_list = ctk.CTkTextbox(f, height=300, font=ctk.CTkFont(family="Consolas", size=12))
        self.pending_list.pack(fill="both", expand=True, pady=5)
        self.pending_list.insert("1.0", "No pending transactions.\n")
        self.root.after(10000, self._auto_refresh_pending)

    def _auto_refresh_pending(self):
        self.refresh_pending(); self.root.after(10000, self._auto_refresh_pending)

    def refresh_pending(self):
        if not self.miner.address:
            self.pending_list.delete("1.0", "end")
            self.pending_list.insert("1.0", "Load a wallet to see pending transactions.")
            return
        try:
            confirmed = set()
            try:
                cr = requests.get(f"{self.node_url}/api/chain", timeout=10)
                if cr.status_code == 200:
                    for block in cr.json():
                        for tx in block.get("transactions", []):
                            if tx.get("tx_hash"): confirmed.add(tx["tx_hash"])
            except: pass
            self.pending_sent_txs = [tx for tx in self.pending_sent_txs if tx.get("tx_hash") not in confirmed]
            self.miner.pending_own_txs = [tx for tx in self.miner.pending_own_txs if tx.get("tx_hash") not in confirmed]
            r = requests.get(f"{self.node_url}/api/mempool", timeout=5)
            if r.status_code != 200:
                self.pending_list.delete("1.0", "end")
                self.pending_list.insert("1.0", "Cannot fetch mempool."); return
            mempool = r.json()
            user_txs = [tx for tx in mempool if tx.get("sender") == self.miner.address or tx.get("recipient") == self.miner.address]
            for own in self.miner.pending_own_txs:
                if own not in user_txs: user_txs.append(own)
            for sent in self.pending_sent_txs:
                if not any(tx.get("tx_hash") == sent.get("tx_hash") for tx in user_txs):
                    user_txs.append(sent)
            user_txs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            self.pending_list.delete("1.0", "end")
            if not user_txs:
                self.pending_list.insert("1.0", "No pending transactions.")
            else:
                for tx in user_txs:
                    s = tx.get("sender", "?"); r_ = tx.get("recipient", "?")
                    amt = tx.get("amount", 0); fee = tx.get("fee", 0)
                    th = tx.get("tx_hash", "No hash yet"); ts = tx.get("timestamp", 0)
                    memo = tx.get("memo", "")
                    tstr = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts)) if ts else 'N/A'
                    if s == "coinbase": st = "⛏️ Coinbase (pending)"; det = f"→ {r_}"
                    elif s == self.miner.address: st = "⬅️ Sent (pending)"; det = f"to {r_}"
                    else: st = "➡️ Received (pending)"; det = f"from {s}"
                    md = f"  Memo: {memo}\n" if memo else ""
                    self.pending_list.insert("end", f"[{tstr}]\n  {st} {format_lbtc(amt)} LBTC {det}\n  Fee: {format_lbtc(fee)} LBTC\n{md}  TxHash: {th}\n\n")
        except Exception as e:
            self.pending_list.delete("1.0", "end")
            self.pending_list.insert("1.0", f"Error: {e}")

    # ============================================
    # STAKING
    # ============================================
    def build_staking(self):
        f = self.tab_view.tab("Staking")
        ctk.CTkLabel(f, text="🏦 Staking Pools", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", pady=5)
        ctk.CTkLabel(f, text="Yield is VARIABLE — funded by real app revenue (NFTs, marketplace, memecoins).",
                     text_color="#3fb950", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=5)
        ctk.CTkLabel(f, text="Longer locks get bigger share: 30d = 1× · 60d = 1.5× · 90d = 2.5×",
                     text_color="#8b949e", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=5)
        ctk.CTkLabel(f, text="⚠️  Minimum stake: 10 LBTC  ·  Early unlock: 5 LBTC (2 burned + 3 to stakers)",
                     text_color="#ffaa33", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=5, pady=(2, 8))
        sf = ctk.CTkFrame(f); sf.pack(fill="x", pady=10)
        ctk.CTkLabel(sf, text="Pool:").pack(side="left", padx=5)
        self.pool_select = ctk.CTkOptionMenu(sf, values=["LBTC-30D", "LBTC-60D", "LBTC-90D"])
        self.pool_select.pack(side="left", padx=5)
        ctk.CTkLabel(sf, text="Amount (min 10):").pack(side="left", padx=5)
        self.stake_amount = ctk.CTkEntry(sf, width=100); self.stake_amount.pack(side="left", padx=5)
        ctk.CTkButton(sf, text="Stake", command=self.stake_in_pool,
                      fg_color="#4caf50", hover_color="#388e3c").pack(side="left", padx=5)
        ctk.CTkButton(sf, text="🔄 Refresh", command=self.refresh_stakes,
                      fg_color="#2196f3", hover_color="#1976d2").pack(side="left", padx=5)
        self.pending_rewards_box = ctk.CTkFrame(f, fg_color="#1a2530", corner_radius=10,
                                                border_width=2, border_color="#58a6ff")
        self.pending_rewards_box.pack(fill="x", pady=(6, 4))
        pr_inner = ctk.CTkFrame(self.pending_rewards_box, fg_color="transparent")
        pr_inner.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(pr_inner, text="💰 PENDING STAKING REWARDS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#58a6ff").pack(anchor="w")
        self.pending_rewards_label = ctk.CTkLabel(pr_inner, text="0.00000000 LBTC",
                                                  font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
                                                  text_color="#58a6ff")
        self.pending_rewards_label.pack(anchor="w", pady=(2, 6))
        ctk.CTkButton(pr_inner, text="💰 Claim Rewards", command=self.claim_rewards,
                      fg_color="#238636", hover_color="#2ea043",
                      font=ctk.CTkFont(weight="bold"), width=200).pack(anchor="w")
        ctk.CTkLabel(f, text="📋 My Stakes (each is independent)",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#4fc3f7").pack(anchor="w", padx=5, pady=(10, 5))
        self.stakes_frame = ctk.CTkScrollableFrame(f, height=380, fg_color="#0a0a0a")
        self.stakes_frame.pack(fill="both", expand=True, pady=5)
        self.refresh_stakes()

    def refresh_stakes(self):
        if not self.miner.address:
            for w in list(self.stake_widgets.values()):
                w["frame"].destroy()
            self.stake_widgets = {}
            self._refresh_pending_rewards()
            return
        try:
            r = requests.get(f"{self.node_url}/api/pools/stakes/{self.miner.address}", timeout=10)
            stakes = r.json() if r.status_code == 200 else []
            if not isinstance(stakes, list): stakes = []
        except: stakes = []
        fresh_ids = set(s["id"] for s in stakes)
        for sid in list(self.stake_widgets.keys()):
            if sid not in fresh_ids:
                self.stake_widgets[sid]["frame"].destroy()
                del self.stake_widgets[sid]
        for s in stakes:
            sid = s["id"]
            if sid in self.stake_widgets: self._update_stake_widget(sid, s)
            else: self._create_stake_widget(s)
        self._refresh_pending_rewards()

    def _refresh_pending_rewards(self):
        if not self.miner.address:
            self.pending_rewards_label.configure(text="0.00000000 LBTC")
            return
        try:
            r = requests.get(f"{self.node_url}/api/staking/pending/{self.miner.address}", timeout=5)
            d = r.json() if r.status_code == 200 else {}
            pending = d.get("pending_lbtc", 0)
            self.pending_rewards_label.configure(text=f"{pending:.8f} LBTC")
        except:
            self.pending_rewards_label.configure(text="0.00000000 LBTC")

    def claim_rewards(self):
        if not self.miner.address or not self.miner.sk:
            messagebox.showerror("Error", "Load a wallet first"); return
        try:
            r = requests.get(f"{self.node_url}/api/staking/pending/{self.miner.address}", timeout=5)
            d = r.json()
            pending = d.get("pending_lbtc", 0)
        except:
            pending = 0
        if pending <= 0:
            messagebox.showinfo("Nothing to claim", "You have no pending rewards yet.")
            return
        if not messagebox.askyesno("Claim Rewards", f"Claim {pending:.8f} LBTC to your balance?"):
            return
        ts = int(time.time())
        message = f"CLAIM|{self.miner.address}|{ts}"
        sig = sign_message(self.miner.sk, message)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/staking/claim",
                headers={"Content-Type": "application/json"},
                json={"address": self.miner.address, "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=15)
            d = r.json()
            if d.get("status") == "ok":
                messagebox.showinfo("Claimed", f"✅ Claimed {(d['claimed']/1e8):.8f} LBTC")
            else:
                messagebox.showerror("Failed", d.get("error", "Unknown"))
            self._refresh_pending_rewards(); self.refresh_balance()
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def _create_stake_widget(self, s):
        sid = s["id"]
        card = ctk.CTkFrame(self.stakes_frame, fg_color="#1a1a1a", corner_radius=8)
        card.pack(fill="x", pady=5, padx=5)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(header, text=f"Stake #{sid}",
                     font=ctk.CTkFont(weight="bold", size=15),
                     text_color="#f7931a").pack(side="left")
        status_lbl = ctk.CTkLabel(header, text="", text_color="#ffaa33",
                                  font=ctk.CTkFont(weight="bold"))
        status_lbl.pack(side="right")
        amt_lbl = ctk.CTkLabel(card,
            text=f"Pool: {s['pool']}  |  Amount: {format_lbtc(s['amount'])} LBTC  |  Reward: {format_lbtc(int(s['reward']))} LBTC",
            font=ctk.CTkFont(family="Consolas", size=12))
        amt_lbl.pack(anchor="w", padx=10, pady=2)
        start_lbl = ctk.CTkLabel(card,
            text=f"Started: {s['start_time_readable']}  |  Ends: {s['end_time_readable']}",
            font=ctk.CTkFont(family="Consolas", size=11), text_color="#8b949e")
        start_lbl.pack(anchor="w", padx=10, pady=2)
        cd_lbl = ctk.CTkLabel(card, text="", font=ctk.CTkFont(family="Consolas", size=12),
                              text_color="#ffaa33")
        cd_lbl.pack(anchor="w", padx=10, pady=2)
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(4, 8))
        unlock_free_btn = ctk.CTkButton(btn_row, text="🔓 Unlock Free (0 LBTC)",
                                         fg_color="#238636", hover_color="#2ea043",
                                         width=180, state="disabled",
                                         command=lambda sid=sid: self.unlock_stake_clicked(sid, False))
        unlock_free_btn.pack(side="left", padx=5)
        unlock_early_btn = ctk.CTkButton(btn_row, text="⚡ Early Unlock (5 LBTC)",
                                          fg_color="#da3633", hover_color="#f85149",
                                          width=200, state="disabled",
                                          command=lambda sid=sid: self.unlock_stake_clicked(sid, True))
        unlock_early_btn.pack(side="left", padx=5)
        self.stake_widgets[sid] = {
            "frame": card, "status": status_lbl, "amount": amt_lbl,
            "countdown": cd_lbl, "btn_free": unlock_free_btn, "btn_early": unlock_early_btn,
            "data": s
        }
        self._update_stake_widget(sid, s)

    def _update_stake_widget(self, sid, s):
        w = self.stake_widgets.get(sid)
        if not w: return
        w["data"] = s
        if s.get("active"):
            if s.get("locked"):
                w["status"].configure(text="🔒 Locked", text_color="#ffaa33")
                w["btn_free"].configure(state="disabled", text="🔓 Unlock Free (locked)")
                w["btn_early"].configure(state="normal")
            else:
                w["status"].configure(text="🔓 Ready to Unlock", text_color="#3fb950")
                w["btn_free"].configure(state="normal", text="🔓 Unlock Free (0 LBTC)")
                w["btn_early"].configure(state="disabled")
        else:
            w["status"].configure(text="✅ Closed", text_color="#8b949e")
            w["btn_free"].configure(state="disabled")
            w["btn_early"].configure(state="disabled")

    def update_stake_countdown(self):
        try:
            for sid, w in list(self.stake_widgets.items()):
                s = w["data"]
                if not s.get("active"):
                    w["countdown"].configure(text=""); continue
                remaining = s.get("remaining_seconds", 0)
                if remaining > 0:
                    remaining = max(0, remaining - 1)
                    s["remaining_seconds"] = remaining
                if remaining > 0:
                    d = remaining // 86400; h = (remaining % 86400) // 3600
                    m = (remaining % 3600) // 60; sec = remaining % 60
                    w["countdown"].configure(
                        text=f"⏳ Free unlock in: {d}d {h}h {m}m {sec}s",
                        text_color="#ffaa33")
                    w["btn_free"].configure(state="disabled")
                    w["btn_early"].configure(state="normal")
                else:
                    w["countdown"].configure(
                        text="✅ Lock period ended — unlock for free!",
                        text_color="#3fb950")
                    w["btn_free"].configure(state="normal")
                    w["btn_early"].configure(state="disabled")
        except Exception: pass
        self.root.after(1000, self.update_stake_countdown)

    def unlock_stake_clicked(self, stake_id, is_early):
        if not self.miner.address: return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        if is_early:
            msg = ("Unlock early for 5 LBTC?\n\n"
                   "• 2 LBTC burned (removed from supply)\n"
                   "• 3 LBTC to stakers\n\n"
                   "You still keep your principal + yield earned so far.")
        else:
            msg = "Unlock for free?\n\nYou keep your principal + all yield earned."
        if not messagebox.askyesno("Confirm", msg): return
        ts = int(time.time())
        sign_msg = f"UNLOCK|{self.miner.address}|{stake_id}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/pools/unlock",
                headers={"Content-Type": "application/json"},
                json={"address": self.miner.address, "stake_id": stake_id,
                      "timestamp": ts, "signature": sig, "pubkey": pub}, timeout=15)
            data = r.json()
            if data.get("status") == "ok":
                if data.get("is_early"):
                    messagebox.showinfo("Unlocked (Early)",
                        f"Stake #{stake_id} unlocked early!\n\n"
                        f"Net received: {format_lbtc(data.get('net_received', 0))} LBTC\n\n"
                        f"🔥 Burned: {format_lbtc(data.get('burned', 0))} LBTC\n"
                        f"💰 To stakers: {format_lbtc(data.get('treasury', 0))} LBTC\n"
                        f"💎 To reserve: {format_lbtc(data.get('reserve', 0))} LBTC")
                else:
                    messagebox.showinfo("Unlocked",
                        f"Stake #{stake_id} unlocked!\n\n"
                        f"Full amount: {format_lbtc(data.get('unlocked', 0))} LBTC")
            else:
                messagebox.showerror("Error", data.get("error", "Failed"))
            self.refresh_stakes(); self.refresh_balance()
            self.refresh_pending(); self.refresh_transactions()
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def stake_in_pool(self):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load a wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        pool = self.pool_select.get()
        try: amt = float(self.stake_amount.get())
        except: messagebox.showerror("Error", "Enter a valid amount"); return
        if amt < MIN_STAKE_LBTC:
            messagebox.showerror("Error", f"Minimum stake is {MIN_STAKE_LBTC} LBTC")
            return
        ts = int(time.time())
        au = int(round(amt * COIN))
        sign_msg = f"STAKE|{self.miner.address}|{pool}|{au}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/pools/stake",
                headers={"Content-Type": "application/json"},
                json={"address": self.miner.address, "pool": pool, "amount": amt,
                      "timestamp": ts, "signature": sig, "pubkey": pub}, timeout=15)
            data = r.json()
            if data.get("status") == "ok":
                messagebox.showinfo("Stake",
                    f"Staked {amt} LBTC in {pool}\n"
                    f"Stake ID: #{data.get('stake_id')}\n"
                    f"Tx: {data.get('tx_hash','')[:24]}...")
            else:
                messagebox.showerror("Stake", data.get("error", "Failed"))
            self.refresh_stakes(); self.refresh_balance()
            self.refresh_pending(); self.refresh_transactions()
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    # ============================================
    # NFTs
    # ============================================
    def build_nfts(self):
        f = self.tab_view.tab("NFTs")
        hd = ctk.CTkFrame(f, fg_color="transparent")
        hd.pack(fill="x", pady=(5, 0))
        ctk.CTkLabel(hd, text="🎨 NFT Collection",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(side="left")
        mint_card = ctk.CTkFrame(f, fg_color="#1a1a1a", corner_radius=10,
                                 border_width=1, border_color="#30363d")
        mint_card.pack(fill="x", pady=(8, 8), padx=2)
        ctk.CTkLabel(mint_card, text="✨ Mint New NFT",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(mint_card,
                     text="Common 10 · Rare 25 · Epic 100 · Legendary 200 LBTC  |  72% held in escrow (returned on redeem)",
                     font=ctk.CTkFont(size=11),
                     text_color="#8b949e").pack(anchor="w", padx=14, pady=(0, 8))
        row1 = ctk.CTkFrame(mint_card, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(row1, text="Name:", width=70).pack(side="left")
        self.nft_mint_name = ctk.CTkEntry(row1, width=220, placeholder_text="My NFT")
        self.nft_mint_name.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(row1, text="Rarity:", width=60).pack(side="left")
        self.nft_mint_rarity = ctk.CTkOptionMenu(
            row1, values=["common — 10 LBTC", "rare — 25 LBTC",
                          "epic — 100 LBTC", "legendary — 200 LBTC"], width=200)
        self.nft_mint_rarity.pack(side="left")
        row2 = ctk.CTkFrame(mint_card, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(row2, text="Description:", width=70).pack(side="left")
        self.nft_mint_desc = ctk.CTkEntry(row2, width=520, placeholder_text="optional")
        self.nft_mint_desc.pack(side="left", fill="x", expand=True)
        row3 = ctk.CTkFrame(mint_card, fg_color="transparent")
        row3.pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(row3, text="Image URL:", width=70).pack(side="left")
        self.nft_mint_image = ctk.CTkEntry(row3, width=520, placeholder_text="https://...")
        self.nft_mint_image.pack(side="left", fill="x", expand=True)
        row4 = ctk.CTkFrame(mint_card, fg_color="transparent")
        row4.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(row4, text="✨ Mint NFT", command=self.mint_nft_from_miner,
                      fg_color="#238636", hover_color="#2ea043",
                      font=ctk.CTkFont(weight="bold"),
                      width=180, height=34).pack(side="left")
        btn_row = ctk.CTkFrame(f, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(btn_row, text="🔄 Refresh", command=self.refresh_nfts,
                      fg_color="#9c27b0", hover_color="#7b1fa2",
                      width=110).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="🛒 Go to Marketplace",
                      command=lambda: self.tab_view.set("Marketplace"),
                      fg_color="#2196f3", hover_color="#1976d2",
                      width=200).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="🌐 View All on Explorer",
                      command=self.open_nft_gallery,
                      fg_color="#21262d", hover_color="#30363d",
                      width=200).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="🔥 Burned Collection",
                      command=lambda: webbrowser.open(f"{EXPLORER_URL}/nfts/burned"),
                      fg_color="#da3633", hover_color="#f85149",
                      width=180).pack(side="left")
        self.nfts_frame = ctk.CTkScrollableFrame(f, fg_color="#0a0a0a")
        self.nfts_frame.pack(fill="both", expand=True, pady=4)
        ctk.CTkLabel(self.nfts_frame, text="Load wallet and click Refresh.",
                     text_color="#8b949e").pack(pady=20)

    def _load_thumbnail(self, url, size=(140, 140)):
        try:
            r = requests.get(url, timeout=6, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code != 200: return None
            img = Image.open(BytesIO(r.content)).convert("RGB")
            img.thumbnail(size)
            return ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        except Exception as e:
            print(f"Image load failed: {e}")
            return None

    def _rarity_color(self, rarity):
        return {"common": "#8b949e", "rare": "#58a6ff",
                "epic": "#a371f7", "legendary": "#f7931a"}.get((rarity or "common").lower(), "#8b949e")

    def refresh_nfts(self):
        for w in self.nfts_frame.winfo_children(): w.destroy()
        self.nft_image_refs.clear()
        if not self.miner.address:
            ctk.CTkLabel(self.nfts_frame, text="Load a wallet first.",
                         text_color="#8b949e").pack(pady=20); return
        try:
            r = requests.get(f"{self.node_url}/api/nft/owner/{self.miner.address}", timeout=10)
            nfts = r.json() if r.status_code == 200 else []
            if not isinstance(nfts, list): nfts = []
        except Exception as e:
            ctk.CTkLabel(self.nfts_frame, text=f"Error: {e}",
                         text_color="#f85149").pack(pady=20); return
        if not nfts:
            ctk.CTkLabel(self.nfts_frame,
                         text="🎨 No NFTs owned.\n\nMint one above or from the dashboard.",
                         text_color="#8b949e", font=ctk.CTkFont(size=13),
                         justify="center").pack(pady=40); return
        for nft in nfts: self._create_nft_card(nft)

    def _create_nft_card(self, nft):
        nid = nft["id"]
        card = ctk.CTkFrame(self.nfts_frame, fg_color="#1a1a1a", corner_radius=10)
        card.pack(fill="x", pady=6, padx=4)
        img_frame = ctk.CTkFrame(card, fg_color="#0a0a0a", corner_radius=8,
                                 width=150, height=150)
        img_frame.pack(side="left", padx=10, pady=10)
        img_frame.pack_propagate(False)
        img_url = nft.get("image", "")
        if img_url:
            ctk_img = self._load_thumbnail(img_url, size=(140, 140))
            if ctk_img is not None:
                self.nft_image_refs[nid] = ctk_img
                img_lbl = ctk.CTkLabel(img_frame, image=ctk_img, text="")
                img_lbl.pack(expand=True, fill="both", padx=4, pady=4)
            else:
                ctk.CTkLabel(img_frame, text="[image unavailable]",
                             text_color="#6e7681", font=ctk.CTkFont(size=11),
                             justify="center").pack(expand=True)
        else:
            ctk.CTkLabel(img_frame, text="[no image]",
                         text_color="#6e7681", font=ctk.CTkFont(size=11)).pack(expand=True)
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)
        title_row = ctk.CTkFrame(info, fg_color="transparent")
        title_row.pack(fill="x", anchor="w")
        ctk.CTkLabel(title_row, text=f"#{nid}  {nft.get('name','')}",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#f7931a").pack(side="left")
        rarity = (nft.get("rarity", "common") or "common").lower()
        rcolor = self._rarity_color(rarity)
        ctk.CTkLabel(title_row, text=f" {rarity.upper()} ",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#0a0a0a",
                     fg_color=rcolor, corner_radius=6).pack(side="left", padx=8)
        desc = nft.get("description", "") or ""
        if desc:
            ctk.CTkLabel(info, text=desc, font=ctk.CTkFont(size=12),
                         text_color="#c9d1d9",
                         wraplength=520, justify="left").pack(anchor="w", pady=(4, 0))
        cost = nft.get("mint_cost", 0) / COIN
        escrow = nft.get("escrow_amount", 0) / COIN
        minted = nft.get("mint_time", 0)
        mstr = time.strftime('%Y-%m-%d %H:%M', time.gmtime(minted)) if minted else "N/A"
        ctk.CTkLabel(info, text=f"Mint cost: {cost:.2f} LBTC  ·  Escrow (refundable): {escrow:.2f} LBTC  ·  Minted: {mstr}",
                     font=ctk.CTkFont(family="Consolas", size=11),
                     text_color="#6e7681").pack(anchor="w", pady=(4, 8))
        actions = ctk.CTkFrame(info, fg_color="transparent")
        actions.pack(fill="x")
        ctk.CTkButton(actions, text="🔍  View",
                      command=lambda nid=nid: self.open_nft_detail(nid),
                      fg_color="#21262d", hover_color="#30363d",
                      width=100, height=34).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="📤  Send",
                      command=lambda nid=nid: self.send_nft_clicked(nid),
                      fg_color="#2196f3", hover_color="#1976d2",
                      width=140, height=34).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="💵  Sell",
                      command=lambda nid=nid: self.list_nft_for_sale(nid),
                      fg_color="#f7931a", hover_color="#d97f0f",
                      text_color="#0a0a0a",
                      width=140, height=34).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="🔥  Redeem",
                      command=lambda nid=nid: self.redeem_nft_clicked(nid),
                      fg_color="#da3633", hover_color="#f85149",
                      width=140, height=34).pack(side="left")

    def send_nft_clicked(self, nft_id):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        recipient = ctk.CTkInputDialog(
            text=f"📤 Send NFT #{nft_id} to which wallet address?\n\n"
                 f"Fee: 1 LBTC\n\n"
                 f"Recipient address:",
            title="Send NFT").get_input()
        if not recipient: return
        recipient = recipient.strip()
        if not recipient: return
        if recipient == self.miner.address:
            messagebox.showerror("Error", "Cannot send to yourself"); return
        if len(recipient) < 26:
            messagebox.showerror("Error", "That does not look like a valid address"); return
        if not messagebox.askyesno("Confirm Send",
            f"Send NFT #{nft_id} to:\n\n{recipient}\n\n"
            f"Fee: 1 LBTC\n\nThis is final."):
            return
        ts = int(time.time())
        sign_msg = f"TRANSFER|{self.miner.address}|{recipient}|{nft_id}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/nft/transfer",
                headers={"Content-Type": "application/json"},
                json={"nft_id": nft_id, "from": self.miner.address, "to": recipient,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=15)
            data = r.json()
            if data.get("status") == "ok":
                messagebox.showinfo("NFT Sent ✅",
                    f"NFT #{nft_id} sent to:\n{recipient[:24]}...")
                self.refresh_nfts(); self.refresh_balance()
                self.refresh_pending(); self.refresh_transactions()
                self.refresh_marketplace(); self.refresh_my_listings()
            else:
                messagebox.showerror("Send failed", data.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def mint_nft_from_miner(self):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        name = self.nft_mint_name.get().strip()
        desc = self.nft_mint_desc.get().strip()
        img = self.nft_mint_image.get().strip()
        rarity_label = self.nft_mint_rarity.get()
        rarity = rarity_label.split("—")[0].strip().lower()
        if not name:
            messagebox.showerror("Error", "Enter an NFT name"); return
        cost_lbtc = {"common": 10, "rare": 25, "epic": 100, "legendary": 200}[rarity]
        if not messagebox.askyesno("Confirm Mint",
            f"Mint '{name}' ({rarity})?\n\n"
            f"Cost: {cost_lbtc} LBTC + 0.0001 fee"):
            return
        ts = int(time.time())
        sign_msg = f"MINT|{self.miner.address}|{name}|{desc}|{img}|{rarity}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/nft/mint",
                headers={"Content-Type": "application/json"},
                json={"creator": self.miner.address, "name": name,
                      "description": desc, "image": img, "rarity": rarity,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=15)
            data = r.json()
            if data.get("status") == "ok":
                nft = data.get("nft", {})
                messagebox.showinfo("NFT Minted",
                    f"✅ NFT #{nft.get('id')} minted!\n\n"
                    f"Will confirm in the next block.")
                self.log(f"🎨 Minted NFT #{nft.get('id')} ({rarity})")
                self.nft_mint_name.delete(0, "end")
                self.nft_mint_desc.delete(0, "end")
                self.nft_mint_image.delete(0, "end")
                self.refresh_nfts(); self.refresh_balance()
                self.refresh_pending(); self.refresh_transactions()
            else:
                messagebox.showerror("Mint failed", data.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def redeem_nft_clicked(self, nft_id):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        if not messagebox.askyesno("Confirm Redeem",
            f"Redeem (burn) NFT #{nft_id}?\n\n"
            f"You will receive back 72% of the mint cost minus 1 LBTC fee.\n"
            f"The NFT stays visible forever on the explorer (marked BURNED)."):
            return
        ts = int(time.time())
        sign_msg = f"REDEEM|{self.miner.address}|{nft_id}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/nft/redeem",
                headers={"Content-Type": "application/json"},
                json={"nft_id": nft_id, "address": self.miner.address,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=15)
            data = r.json()
            if data.get("status") == "ok":
                messagebox.showinfo("NFT Redeemed",
                    f"✅ NFT #{nft_id} burned!\n\n"
                    f"Net received: {format_lbtc(data.get('net', 0))} LBTC")
                self.refresh_nfts(); self.refresh_balance()
                self.refresh_pending(); self.refresh_transactions()
                self.refresh_marketplace(); self.refresh_my_listings()
            else:
                messagebox.showerror("Redeem failed", data.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def list_nft_for_sale(self, nft_id):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        price_str = ctk.CTkInputDialog(
            text=f"Set a price for NFT #{nft_id} (in LBTC):\n\nExample: 25",
            title="List NFT for Sale").get_input()
        if not price_str: return
        try: price = float(price_str)
        except: messagebox.showerror("Error", "Invalid price"); return
        if price <= 0:
            messagebox.showerror("Error", "Price must be positive"); return
        if not messagebox.askyesno("Confirm Listing",
            f"List NFT #{nft_id} for {price} LBTC?\n\n2% commission to stakers + reserve"):
            return
        ts = int(time.time())
        sign_msg = f"LIST|{self.miner.address}|{nft_id}|{price}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/market/list",
                headers={"Content-Type": "application/json"},
                json={"nft_id": nft_id, "seller": self.miner.address,
                      "price": price, "timestamp": ts,
                      "signature": sig, "pubkey": pub},
                timeout=10)
            d = r.json()
            if d.get("status") == "ok":
                messagebox.showinfo("Listed", f"✅ NFT #{nft_id} listed for {price} LBTC")
                self.refresh_marketplace(); self.refresh_my_listings()
            else:
                messagebox.showerror("Listing failed", d.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    # ============================================
    # MARKETPLACE
    # ============================================
    def build_marketplace(self):
        f = self.tab_view.tab("Marketplace")
        hd = ctk.CTkFrame(f, fg_color="transparent")
        hd.pack(fill="x", pady=(5, 0))
        ctk.CTkLabel(hd, text="🛒 NFT Marketplace",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(side="left")
        ctk.CTkLabel(f, text="Buy and sell NFTs with other users. 2% commission to stakers + reserve.",
                     font=ctk.CTkFont(size=11), text_color="#8b949e").pack(anchor="w", padx=2, pady=(0, 6))
        brow = ctk.CTkFrame(f, fg_color="transparent")
        brow.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(brow, text="🔄 Refresh All Listings",
                      command=self.refresh_marketplace,
                      fg_color="#f7931a", hover_color="#d97f0f",
                      text_color="#0a0a0a", font=ctk.CTkFont(weight="bold"),
                      width=200).pack(side="left", padx=(0, 6))
        ctk.CTkButton(brow, text="🌐 Open Explorer",
                      command=lambda: webbrowser.open(f"{EXPLORER_URL}/nfts"),
                      fg_color="#21262d", hover_color="#30363d",
                      width=160).pack(side="left")
        ctk.CTkLabel(f, text="📋 My Listings",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#4fc3f7").pack(anchor="w", padx=2, pady=(6, 2))
        self.my_listings_frame = ctk.CTkScrollableFrame(f, fg_color="#0a0a0a", height=180)
        self.my_listings_frame.pack(fill="x", pady=(0, 10), padx=2)
        ctk.CTkLabel(self.my_listings_frame, text="Load wallet to view your listings.",
                     text_color="#8b949e").pack(pady=20)
        ctk.CTkLabel(f, text="🛒 Active Listings (All Users)",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#4fc3f7").pack(anchor="w", padx=2, pady=(6, 2))
        self.market_listings_frame = ctk.CTkScrollableFrame(f, fg_color="#0a0a0a")
        self.market_listings_frame.pack(fill="both", expand=True, pady=(0, 4), padx=2)
        ctk.CTkLabel(self.market_listings_frame, text="Loading listings...",
                     text_color="#8b949e").pack(pady=20)

    def refresh_marketplace(self):
        for w in self.market_listings_frame.winfo_children(): w.destroy()
        self.market_image_refs.clear()
        try:
            r = requests.get(f"{self.node_url}/api/market/listings", timeout=10)
            listings = r.json() if r.status_code == 200 else []
            if not isinstance(listings, list): listings = []
        except Exception as e:
            ctk.CTkLabel(self.market_listings_frame, text=f"Error: {e}",
                         text_color="#f85149").pack(pady=20); return
        if not listings:
            ctk.CTkLabel(self.market_listings_frame,
                         text="🛒 No active listings.\n\nGo to NFTs tab and click '💵 Sell' on any NFT.",
                         text_color="#8b949e", font=ctk.CTkFont(size=12),
                         justify="center").pack(pady=40); return
        for l in listings: self._create_market_card(l)

    def _create_market_card(self, listing):
        nft = listing.get("nft", {}) or {}
        nid = nft.get("id")
        if nid is None: return
        card = ctk.CTkFrame(self.market_listings_frame, fg_color="#1a1a1a", corner_radius=10)
        card.pack(fill="x", pady=6, padx=4)
        img_frame = ctk.CTkFrame(card, fg_color="#0a0a0a", corner_radius=8, width=120, height=120)
        img_frame.pack(side="left", padx=10, pady=10)
        img_frame.pack_propagate(False)
        img_url = nft.get("image", "")
        if img_url:
            ctk_img = self._load_thumbnail(img_url, size=(110, 110))
            if ctk_img is not None:
                self.market_image_refs[nid] = ctk_img
                img_lbl = ctk.CTkLabel(img_frame, image=ctk_img, text="")
                img_lbl.pack(expand=True, fill="both", padx=4, pady=4)
            else:
                ctk.CTkLabel(img_frame, text="[no image]",
                             text_color="#6e7681", font=ctk.CTkFont(size=10)).pack(expand=True)
        else:
            ctk.CTkLabel(img_frame, text="[no image]",
                         text_color="#6e7681", font=ctk.CTkFont(size=10)).pack(expand=True)
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)
        title_row = ctk.CTkFrame(info, fg_color="transparent")
        title_row.pack(fill="x", anchor="w")
        ctk.CTkLabel(title_row, text=f"#{nid}  {nft.get('name','')}",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#f7931a").pack(side="left")
        rarity = (nft.get("rarity", "common") or "common").lower()
        rcolor = self._rarity_color(rarity)
        ctk.CTkLabel(title_row, text=f" {rarity.upper()} ",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#0a0a0a",
                     fg_color=rcolor, corner_radius=6).pack(side="left", padx=8)
        price_lbtc = listing.get("price", 0) / COIN
        seller = listing.get("seller", "")[:16]
        is_mine = (self.miner.address == listing.get("seller"))
        ctk.CTkLabel(info, text=f"💵 Price: {price_lbtc:.4f} LBTC",
                     font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                     text_color="#3fb950").pack(anchor="w", pady=(6, 2))
        ctk.CTkLabel(info, text=f"Seller: {seller}...",
                     font=ctk.CTkFont(family="Consolas", size=10),
                     text_color="#6e7681").pack(anchor="w")
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(side="right", padx=10, pady=10)
        ctk.CTkButton(btn_row, text="🔍 View",
                      command=lambda nid=nid: self.open_nft_detail(nid),
                      fg_color="#21262d", hover_color="#30363d",
                      width=90, height=32).pack(pady=(0, 6))
        if is_mine:
            ctk.CTkButton(btn_row, text="❌ Cancel",
                          command=lambda nid=nid: self.cancel_listing_clicked(nid),
                          fg_color="#da3633", hover_color="#f85149",
                          width=90, height=32).pack()
        else:
            ctk.CTkButton(btn_row, text="🛒 Buy",
                          command=lambda nid=nid, p=price_lbtc: self.buy_nft_clicked(nid, p),
                          fg_color="#238636", hover_color="#2ea043",
                          width=90, height=32).pack()

    def refresh_my_listings(self):
        for w in self.my_listings_frame.winfo_children(): w.destroy()
        if not self.miner.address:
            ctk.CTkLabel(self.my_listings_frame, text="Load wallet to view your listings.",
                         text_color="#8b949e").pack(pady=20); return
        try:
            r = requests.get(f"{self.node_url}/api/market/my-listings/{self.miner.address}", timeout=10)
            listings = r.json() if r.status_code == 200 else []
            if not isinstance(listings, list): listings = []
        except: listings = []
        if not listings:
            ctk.CTkLabel(self.my_listings_frame,
                         text="You have no listings. Go to NFTs tab → click '💵 Sell' on any NFT.",
                         text_color="#8b949e", font=ctk.CTkFont(size=11),
                         justify="center").pack(pady=20); return
        for l in listings:
            nft = l.get("nft", {}) or {}
            status = l.get("status", "active")
            color = "#3fb950" if status == "active" else ("#58a6ff" if status == "sold" else "#8b949e")
            price = l.get("price", 0) / COIN
            row = ctk.CTkFrame(self.my_listings_frame, fg_color="#161b22", corner_radius=6)
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row,
                text=f"#{nft.get('id')} {nft.get('name','')}  ·  {price:.4f} LBTC  ·  [{status.upper()}]",
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=color).pack(side="left", padx=10, pady=6)
            if status == "active":
                ctk.CTkButton(row, text="Cancel",
                              command=lambda nid=nft.get("id"): self.cancel_listing_clicked(nid),
                              fg_color="#da3633", hover_color="#f85149",
                              width=80, height=26).pack(side="right", padx=6, pady=4)

    def cancel_listing_clicked(self, nft_id):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        if not messagebox.askyesno("Cancel Listing", f"Cancel the listing for NFT #{nft_id}?"):
            return
        ts = int(time.time())
        sign_msg = f"UNLIST|{self.miner.address}|{nft_id}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/market/cancel",
                headers={"Content-Type": "application/json"},
                json={"nft_id": nft_id, "seller": self.miner.address,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=10)
            d = r.json()
            if d.get("status") == "ok":
                messagebox.showinfo("Cancelled", f"✅ Listing cancelled")
                self.refresh_marketplace(); self.refresh_my_listings()
                self.refresh_nfts()
            else:
                messagebox.showerror("Cancel failed", d.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def buy_nft_clicked(self, nft_id, price_lbtc):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        if not messagebox.askyesno("Confirm Purchase",
            f"Buy NFT #{nft_id} for {price_lbtc:.4f} LBTC?\n\n"
            f"Plus 0.0001 LBTC network fee."):
            return
        ts = int(time.time())
        sign_msg = f"BUY|{self.miner.address}|{nft_id}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/market/buy",
                headers={"Content-Type": "application/json"},
                json={"nft_id": nft_id, "buyer": self.miner.address,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=15)
            d = r.json()
            if d.get("status") == "ok":
                messagebox.showinfo("Purchased",
                    f"✅ NFT #{nft_id} purchased!")
                self.refresh_marketplace(); self.refresh_my_listings()
                self.refresh_nfts(); self.refresh_balance()
                self.refresh_pending(); self.refresh_transactions()
            else:
                messagebox.showerror("Purchase failed", d.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    # ============================================
    # BRIDGE
    # ============================================
    def build_bridge(self):
        f = self.tab_view.tab("Bridge")
        scroll = ctk.CTkScrollableFrame(f, fg_color="#0a0a0a")
        scroll.pack(fill="both", expand=True)

        hero = ctk.CTkFrame(scroll, fg_color="#0d1a2a", corner_radius=12,
                            border_width=2, border_color="#1f6feb")
        hero.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(hero, text="🌉 LBTC Bridge — Live on Arbitrum Sepolia",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#58a6ff").pack(anchor="w", padx=18, pady=(16, 6))
        ctk.CTkLabel(hero,
                     text=("Move native LBTC to Arbitrum as wLBTC (a wrapped ERC-20).\n"
                           "Two-way. Real. Live. Both directions settle in ~1 block.\n\n"
                           "Bridge fee: 0.1%  ·  Minimum: 10 LBTC"),
                     font=ctk.CTkFont(size=12), text_color="#c9d1d9",
                     justify="left").pack(anchor="w", padx=18, pady=(0, 12))

        brow = ctk.CTkFrame(hero, fg_color="transparent")
        brow.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(brow, text="🌉  Open Bridge (Browser)",
                      command=self.open_bridge,
                      fg_color="#1f6feb", hover_color="#1a60c9",
                      font=ctk.CTkFont(weight="bold", size=14),
                      width=220, height=44).pack(side="left", padx=(0, 10))
        ctk.CTkButton(brow, text="📖  Read Whitepaper",
                      command=lambda: webbrowser.open(f"{WEBSITE_URL}/whitepaper"),
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont(weight="bold"),
                      width=180, height=44).pack(side="left")

        info = ctk.CTkFrame(scroll, fg_color="#1a1a1a", corner_radius=10)
        info.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(info, text="📋 Contract Addresses (Arbitrum Sepolia)",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=16, pady=(12, 8))

        for label, addr, color in [
            ("Bridge Contract", BRIDGE_OWNER_ADDR, "#58a6ff"),
            ("wLBTC Token", WLBTC_ADDR, "#58a6ff"),
            ("LBTC Vault (native)", BRIDGE_VAULT_ADDR, "#3fb950"),
        ]:
            row = ctk.CTkFrame(info, fg_color="#0d1117", corner_radius=6)
            row.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(row, text=label, width=170, anchor="w",
                         font=ctk.CTkFont(size=11), text_color="#8b949e").pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(row, text=addr, anchor="w",
                         font=ctk.CTkFont(family="Consolas", size=11),
                         text_color=color).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="📋 Copy", width=70, height=26,
                          fg_color="#21262d", hover_color="#30363d",
                          font=ctk.CTkFont(size=10),
                          command=lambda a=addr: self._copy_clip(a)).pack(side="right", padx=8, pady=6)

        stat_box = ctk.CTkFrame(scroll, fg_color="#1a1a1a", corner_radius=10)
        stat_box.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(stat_box, text="📊 Bridge Status",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=16, pady=(12, 8))
        self.bridge_status_label = ctk.CTkLabel(stat_box, text="Loading...",
                                                font=ctk.CTkFont(family="Consolas", size=11),
                                                text_color="#8b949e", justify="left")
        self.bridge_status_label.pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkButton(stat_box, text="🔄 Refresh Status",
                      command=self.refresh_bridge_status,
                      fg_color="#21262d", hover_color="#30363d",
                      width=170, height=32).pack(anchor="w", padx=16, pady=(0, 12))

        self.root.after(500, self.refresh_bridge_status)

    def refresh_bridge_status(self):
        try:
            r = requests.get(f"{PUBLIC_NODE_URL}/api/balance/{BRIDGE_VAULT_ADDR}", timeout=8)
            vault_bal = 0
            if r.status_code == 200:
                vault_bal = r.json().get("balance", 0)
            txt = (
                f"Vault LBTC balance: {format_lbtc(vault_bal)} LBTC\n"
                f"Network: Arbitrum Sepolia (chainId 421614)\n\n"
                f"Contract addresses shown above. Click 'Open Bridge' to interact."
            )
            self.bridge_status_label.configure(text=txt, text_color="#c9d1d9")
        except Exception as e:
            self.bridge_status_label.configure(
                text=f"Could not reach node: {e}\n\nNode: {PUBLIC_NODE_URL}",
                text_color="#f85149")

    # ============================================
    # MEMECOINS
    # ============================================
    def build_memecoins(self):
        f = self.tab_view.tab("Memecoins")
        scroll = ctk.CTkScrollableFrame(f, fg_color="#0a0a0a")
        scroll.pack(fill="both", expand=True)

        hero = ctk.CTkFrame(scroll, fg_color="#2a1400", corner_radius=12,
                            border_width=2, border_color="#ff9f2e")
        hero.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(hero, text="🔥 Memecoins on LBTC",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#ff9f2e").pack(anchor="w", padx=18, pady=(16, 6))
        ctk.CTkLabel(hero,
            text=("Launch your own memecoin on LBTC in 60 seconds.\n"
                  "Creation fee: 5,000 LBTC  ·  500 burned · 500 reserve · 4,000 treasury"),
            font=ctk.CTkFont(size=12), text_color="#c9d1d9",
            justify="left").pack(anchor="w", padx=18, pady=(0, 12))

        brow = ctk.CTkFrame(hero, fg_color="transparent")
        brow.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(brow, text="🔥  Browse Memecoins",
                      command=self.open_memecoins,
                      fg_color="#ff9f2e", hover_color="#e0851a",
                      text_color="#0a0a0a",
                      font=ctk.CTkFont(weight="bold", size=14),
                      width=220, height=44).pack(side="left", padx=(0, 10))
        ctk.CTkButton(brow, text="✨  Launch a Coin",
                      command=self.open_memecoins_create,
                      fg_color="#d97706", hover_color="#b45309",
                      text_color="#0a0a0a",
                      font=ctk.CTkFont(weight="bold", size=14),
                      width=200, height=44).pack(side="left", padx=(0, 10))
        ctk.CTkButton(brow, text="🔄  Refresh List",
                      command=self.refresh_memecoins,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont(weight="bold"),
                      width=160, height=44).pack(side="left")

        self.meme_stats_frame = ctk.CTkFrame(scroll, fg_color="#1a1a1a", corner_radius=10)
        self.meme_stats_frame.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(self.meme_stats_frame, text="📊 Platform Stats",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=16, pady=(12, 8))
        self.meme_stats_label = ctk.CTkLabel(self.meme_stats_frame,
                                             text="Loading...",
                                             font=ctk.CTkFont(family="Consolas", size=12),
                                             text_color="#c9d1d9",
                                             justify="left")
        self.meme_stats_label.pack(anchor="w", padx=16, pady=(0, 14))

        list_box = ctk.CTkFrame(scroll, fg_color="#1a1a1a", corner_radius=10)
        list_box.pack(fill="both", expand=True, pady=(0, 12))
        header = ctk.CTkFrame(list_box, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(header, text="🔥 Top Memecoins",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(side="left")
        self.meme_coins_frame = ctk.CTkScrollableFrame(list_box, fg_color="#0a0a0a", height=400)
        self.meme_coins_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        ctk.CTkLabel(self.meme_coins_frame, text="Loading memecoins...",
                     text_color="#8b949e").pack(pady=20)

    def refresh_memecoins(self):
        try:
            sr = requests.get(f"{PUBLIC_NODE_URL}/api/meme2/stats", timeout=8)
            if sr.status_code == 200:
                s = sr.json()
                txt = (
                    f"Total coins:      {s.get('total_coins', 0)}\n"
                    f"Total volume:     {s.get('total_volume_lbtc', 0):.4f} LBTC\n"
                    f"Total trades:     {s.get('total_trades', 0)}\n"
                    f"NFTs minted:      {s.get('total_nfts', 0)}\n"
                    f"Burned from fees: {s.get('total_burned_lbtc', 0):.4f} LBTC\n"
                    f"Graduated:        {s.get('graduated', 0)}"
                )
                self.meme_stats_label.configure(text=txt, text_color="#c9d1d9")
            else:
                self.meme_stats_label.configure(text="Could not load stats",
                                                text_color="#f85149")
        except Exception as e:
            self.meme_stats_label.configure(text=f"Error: {e}",
                                            text_color="#f85149")

        for w in self.meme_coins_frame.winfo_children():
            w.destroy()
        try:
            lr = requests.get(f"{PUBLIC_NODE_URL}/api/meme2/list?sort=market_cap&limit=20", timeout=10)
            if lr.status_code != 200:
                ctk.CTkLabel(self.meme_coins_frame, text="Could not load list.",
                             text_color="#f85149").pack(pady=20); return
            data = lr.json()
            coins = data.get("coins", [])
            if not coins:
                ctk.CTkLabel(self.meme_coins_frame,
                             text="🔥 No memecoins yet.\n\nBe the first to launch one →",
                             text_color="#8b949e", font=ctk.CTkFont(size=13),
                             justify="center").pack(pady=30)
                ctk.CTkButton(self.meme_coins_frame, text="✨ Launch a Coin",
                              command=self.open_memecoins_create,
                              fg_color="#ff9f2e", hover_color="#e0851a",
                              text_color="#0a0a0a",
                              font=ctk.CTkFont(weight="bold"),
                              width=200, height=40).pack(pady=10)
                return
            for c in coins:
                self._create_meme_coin_card(c)
        except Exception as e:
            ctk.CTkLabel(self.meme_coins_frame, text=f"Error: {e}",
                         text_color="#f85149").pack(pady=20)

    def _create_meme_coin_card(self, c):
        card = ctk.CTkFrame(self.meme_coins_frame, fg_color="#161b22", corner_radius=8)
        card.pack(fill="x", pady=4, padx=4)
        left = ctk.CTkFrame(card, fg_color="transparent", width=80)
        left.pack(side="left", padx=10, pady=8)
        left.pack_propagate(False)
        img_url = c.get("image", "")
        if img_url:
            ctk_img = self._load_thumbnail(img_url, size=(60, 60))
            if ctk_img:
                ctk.CTkLabel(left, image=ctk_img, text="").pack()
            else:
                ctk.CTkLabel(left, text=c.get("symbol", "?")[0],
                             font=ctk.CTkFont(size=24, weight="bold"),
                             text_color="#f7931a", width=60, height=60).pack()
        else:
            ctk.CTkLabel(left, text=c.get("symbol", "?")[0],
                         font=ctk.CTkFont(size=24, weight="bold"),
                         text_color="#f7931a", width=60, height=60).pack()
        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=8)
        ctk.CTkLabel(mid, text=c.get("name", ""),
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#ffe0a3", anchor="w").pack(anchor="w")
        ctk.CTkLabel(mid, text=f"${c.get('symbol', '')}",
                     font=ctk.CTkFont(family="Consolas", size=11),
                     text_color="#f7931a", anchor="w").pack(anchor="w")
        desc = (c.get("description") or "")[:80]
        if desc:
            ctk.CTkLabel(mid, text=desc,
                         font=ctk.CTkFont(size=10), text_color="#8b949e",
                         anchor="w").pack(anchor="w", pady=(2, 0))
        right = ctk.CTkFrame(card, fg_color="transparent")
        right.pack(side="right", padx=10, pady=8)
        price = c.get("price", 0)
        mcap = c.get("market_cap", 0)
        price_str = f"{price:.10f}" if price < 1 else f"{price:.6f}"
        ctk.CTkLabel(right, text=f"{price_str} LBTC",
                     font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                     text_color="#3fb950").pack(anchor="e")
        ctk.CTkLabel(right, text=f"MCap: {mcap:.2f}",
                     font=ctk.CTkFont(family="Consolas", size=10),
                     text_color="#8b949e").pack(anchor="e")
        ctk.CTkButton(right, text="View →",
                      command=lambda cid=c.get("id"): webbrowser.open(f"{MEMECOINS_URL}/{cid}"),
                      fg_color="#21262d", hover_color="#30363d",
                      width=90, height=26,
                      font=ctk.CTkFont(size=11)).pack(anchor="e", pady=(4, 0))

    # ============================================
    # GOVERNANCE
    # ============================================
    def build_governance(self):
        f = self.tab_view.tab("Governance")
        cp = ctk.CTkFrame(f, fg_color="#1a1a1a", corner_radius=10)
        cp.pack(fill="x", pady=(5, 10))
        ctk.CTkLabel(cp, text="🗳️ Create Proposal",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=14, pady=(10, 4))
        ctk.CTkLabel(cp, text="Propose a change. Voting lasts 7 days.",
                     font=ctk.CTkFont(size=11), text_color="#8b949e").pack(anchor="w", padx=14)
        r1 = ctk.CTkFrame(cp, fg_color="transparent")
        r1.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(r1, text="Title:", width=45).pack(side="left")
        self.gov_title = ctk.CTkEntry(r1, width=400, placeholder_text="Short title")
        self.gov_title.pack(side="left", padx=(0, 10))
        r2 = ctk.CTkFrame(cp, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(r2, text="Description:", width=90).pack(side="left")
        self.gov_desc = ctk.CTkEntry(r2, width=540, placeholder_text="Long description")
        self.gov_desc.pack(side="left", fill="x", expand=True)
        r3 = ctk.CTkFrame(cp, fg_color="transparent")
        r3.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(r3, text="Create Proposal", command=self.create_proposal,
                      fg_color="#4caf50", hover_color="#388e3c",
                      font=ctk.CTkFont(weight="bold"),
                      width=160, height=32).pack(side="left")
        ap = ctk.CTkFrame(f, fg_color="#1a1a1a", corner_radius=10)
        ap.pack(fill="both", expand=True, pady=(0, 5))
        hdr = ctk.CTkFrame(ap, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(hdr, text="📋 Active Proposals",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f7931a").pack(side="left")
        ctk.CTkButton(hdr, text="🔄 Refresh", command=self.load_proposals,
                      fg_color="#21262d", hover_color="#30363d",
                      width=100, height=28).pack(side="right")
        self.gov_proposals = ctk.CTkScrollableFrame(ap, fg_color="#0a0a0a")
        self.gov_proposals.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        ctk.CTkLabel(self.gov_proposals, text="Loading...",
                     text_color="#8b949e").pack(pady=20)

    def create_proposal(self):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        title = self.gov_title.get().strip()
        desc = self.gov_desc.get().strip()
        if not title:
            messagebox.showerror("Error", "Enter a title"); return
        full_title = title
        full_desc = desc or "Community proposal"
        ts = int(time.time())
        sign_msg = f"PROPOSE|{self.miner.address}|{full_title}|{full_desc}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/governance/propose",
                headers={"Content-Type": "application/json"},
                json={"title": full_title, "description": full_desc,
                      "options": ["Yes", "No"], "creator": self.miner.address,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=10)
            d = r.json()
            if "id" in d:
                messagebox.showinfo("Success", f"Proposal #{d['id']} created!")
                self.gov_title.delete(0, "end")
                self.gov_desc.delete(0, "end")
                self.load_proposals()
            else:
                messagebox.showerror("Failed", d.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    def load_proposals(self):
        for w in self.gov_proposals.winfo_children(): w.destroy()
        try:
            r = requests.get(f"{self.node_url}/api/governance/proposals", timeout=5)
            props = r.json() if r.status_code == 200 else []
        except: props = []
        if not props:
            ctk.CTkLabel(self.gov_proposals, text="No proposals yet.",
                         text_color="#8b949e").pack(pady=20); return
        for p in props:
            card = ctk.CTkFrame(self.gov_proposals, fg_color="#161b22", corner_radius=8)
            card.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(card, text=p.get("title", ""),
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color="#f7931a").pack(anchor="w", padx=12, pady=(8, 2))
            ctk.CTkLabel(card, text=p.get("description", ""),
                         font=ctk.CTkFont(size=11), text_color="#8b949e",
                         wraplength=600, justify="left").pack(anchor="w", padx=12, pady=(0, 4))
            votes = p.get("votes", {})
            total = sum(votes.values()) if votes else 0
            for opt, cnt in votes.items():
                pct = (cnt / total * 100) if total > 0 else 0
                ctk.CTkLabel(card,
                    text=f"  {opt}: {cnt:.2f} ({pct:.1f}%)",
                    font=ctk.CTkFont(family="Consolas", size=11),
                    text_color="#c9d1d9").pack(anchor="w", padx=12)
            btn_row = ctk.CTkFrame(card, fg_color="transparent")
            btn_row.pack(fill="x", padx=12, pady=(6, 10))
            ctk.CTkButton(btn_row, text="Vote Yes",
                          command=lambda pid=p["id"]: self.cast_vote(pid, "Yes"),
                          fg_color="#238636", hover_color="#2ea043",
                          width=100, height=28).pack(side="left", padx=(0, 6))
            ctk.CTkButton(btn_row, text="Vote No",
                          command=lambda pid=p["id"]: self.cast_vote(pid, "No"),
                          fg_color="#da3633", hover_color="#f85149",
                          width=100, height=28).pack(side="left")

    def cast_vote(self, pid, option):
        if not self.miner.address:
            messagebox.showwarning("Warning", "Load wallet first"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet private key not loaded"); return
        ts = int(time.time())
        sign_msg = f"VOTE|{self.miner.address}|{pid}|{option}|{ts}"
        sig = sign_message(self.miner.sk, sign_msg)
        pub = self.miner.vk.to_string().hex()
        try:
            r = requests.post(f"{self.node_url}/api/governance/vote",
                headers={"Content-Type": "application/json"},
                json={"proposal_id": pid, "voter": self.miner.address, "option": option,
                      "timestamp": ts, "signature": sig, "pubkey": pub},
                timeout=10)
            d = r.json()
            if d.get("status") == "ok":
                messagebox.showinfo("Voted", f"✅ Voted {option} on proposal #{pid}")
                self.load_proposals()
            else:
                messagebox.showerror("Vote failed", d.get("error", "Unknown"))
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    # ============================================
    # SETTINGS
    # ============================================
    def build_settings(self):
        f = self.tab_view.tab("Settings")
        ctk.CTkLabel(f, text="Node Connection", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", pady=5)
        ctk.CTkLabel(f, text="Node URL:").pack(anchor="w", padx=5)
        self.node_entry = ctk.CTkEntry(f, width=400); self.node_entry.pack(anchor="w", padx=5, pady=2)
        self.node_entry.insert(0, self.node_url)
        ctk.CTkButton(f, text="Test Connection", command=self.test_connection, width=120).pack(anchor="w", padx=5, pady=5)
        self.settings_status = ctk.CTkLabel(f, text="⚪ Not tested", text_color="gray")
        self.settings_status.pack(anchor="w", padx=5)
        nbf = ctk.CTkFrame(f); nbf.pack(fill="x", pady=10)
        ctk.CTkLabel(nbf, text="Connect to:", font=ctk.CTkFont(weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=5)
        self.public_node_btn = ctk.CTkButton(nbf, text="🔵 Public Node",
                                             command=self.connect_public_node, width=180, height=40,
                                             fg_color="#1a4a6d", hover_color="#123d5a")
        self.public_node_btn.pack(side="left", padx=10, pady=5)
        self.local_node_btn = ctk.CTkButton(nbf, text="🟢 Local Node (127.0.0.1)",
                                            command=self.connect_local_node, width=180, height=40,
                                            fg_color="#2a6d2a", hover_color="#1e4f1e")
        self.local_node_btn.pack(side="left", padx=10, pady=5)
        self.node_status_label = ctk.CTkLabel(f, text="⚪ No node selected", text_color="gray",
                                              font=ctk.CTkFont(size=14))
        self.node_status_label.pack(anchor="w", padx=10, pady=5)

        node_box = ctk.CTkFrame(f, fg_color="#0d1a10", corner_radius=10,
                                border_width=2, border_color="#3fb950")
        node_box.pack(fill="x", pady=10)
        ctk.CTkLabel(node_box, text="🌐 Run as Full Node (help decentralize LBTC)",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#3fb950").pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(node_box,
                     text=("Run a full LBTC node on this computer.\n"
                           "Requirements:\n"
                           "  • node.py in the same folder as this miner\n"
                           "  • Port 8080 open (router/firewall)\n"
                           "  • Python installed on this machine"),
                     font=ctk.CTkFont(size=11), text_color="#c9d1d9",
                     justify="left").pack(anchor="w", padx=14, pady=(0, 10))
        btn_row = ctk.CTkFrame(node_box, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 12))
        self.run_node_btn = ctk.CTkButton(btn_row, text="▶ Start Full Node",
                                          command=self.toggle_full_node,
                                          fg_color="#238636", hover_color="#2ea043",
                                          font=ctk.CTkFont(weight="bold"),
                                          width=180, height=38)
        self.run_node_btn.pack(side="left")
        self.node_status_indicator = ctk.CTkLabel(btn_row,
                                                  text="● Node not running",
                                                  text_color="#8b949e",
                                                  font=ctk.CTkFont(size=13))
        self.node_status_indicator.pack(side="left", padx=14)
        ctk.CTkButton(btn_row, text="🌐 Check Peers",
                      command=self.open_peers_registry,
                      fg_color="#21262d", hover_color="#30363d",
                      width=140, height=38).pack(side="left", padx=(10, 0))

    # ============================================
    # FULL NODE CONTROL
    # ============================================
    def connect_local_node(self):
        try:
            r = requests.get("http://127.0.0.1:8080/api/chain", timeout=3)
            if r.status_code == 200:
                self.node_url = "http://127.0.0.1:8080"
                self.miner.set_node(self.node_url)
                self.node_entry.delete(0, "end")
                self.node_entry.insert(0, self.node_url)
                self.status_label.configure(text="🟢 Connected (local)", text_color="green")
                self.node_status_label.configure(text="🟢 Connected to local node",
                                                 text_color="green")
                self.refresh_dashboard()
                self.log("✅ Connected to local node 127.0.0.1:8080")
            else:
                messagebox.showerror("Local node unavailable",
                    "No local node running on port 8080.")
        except Exception:
            messagebox.showerror("Local node unavailable",
                "No local node running on port 8080.")

    def toggle_full_node(self):
        if self.node_process and self.node_process.poll() is None:
            if not messagebox.askyesno("Stop Full Node",
                "Stop the full node?\n\nYour node will disconnect from peers."):
                return
            try:
                self.node_process.terminate()
                try:
                    self.node_process.wait(timeout=5)
                except Exception:
                    self.node_process.kill()
            except Exception:
                pass
            self.node_process = None
            self.run_node_btn.configure(text="▶ Start Full Node", fg_color="#238636")
            self.node_status_indicator.configure(text="● Node stopped", text_color="#8b949e")
            self.log("⏹ Full node stopped")
            return

        node_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "node.py")
        if not os.path.exists(node_path):
            node_path = os.path.abspath("node.py")
        if not os.path.exists(node_path):
            messagebox.showerror("node.py missing",
                "Could not find node.py.")
            return

        secret_file = os.path.join(DATA_DIR, "api_secret.txt")
        if os.path.exists(secret_file):
            with open(secret_file) as f:
                api_secret = f.read().strip()
        else:
            api_secret = secrets.token_hex(32)
            with open(secret_file, 'w') as f:
                f.write(api_secret)
            self.log("🔐 Generated new API secret for local node")

        env = os.environ.copy()
        env["LBTC_API_SECRET"] = api_secret

        try:
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_CONSOLE
            else:
                creationflags = 0
            self.node_process = subprocess.Popen(
                [sys.executable, node_path],
                env=env,
                creationflags=creationflags,
            )
            self.run_node_btn.configure(text="⏹ Stop Full Node", fg_color="#da3633")
            self.node_status_indicator.configure(text="● Starting node...", text_color="#ffaa33")
            self.log(f"🚀 Starting full node from {node_path}")
            self.root.after(5000, self._check_full_node_ready)
        except Exception as e:
            messagebox.showerror("Failed to start node", f"Error: {e}")
            self.run_node_btn.configure(text="▶ Start Full Node", fg_color="#238636")
            self.node_status_indicator.configure(text="● Node not running", text_color="#8b949e")

    def _check_full_node_ready(self, attempt=1):
        if not self.node_process or self.node_process.poll() is not None:
            self.node_status_indicator.configure(text="● Node crashed", text_color="#f85149")
            self.run_node_btn.configure(text="▶ Start Full Node", fg_color="#238636")
            self.log("❌ Full node process exited unexpectedly")
            return
        try:
            r = requests.get("http://127.0.0.1:8080/api/chain", timeout=3)
            if r.status_code == 200:
                chain = r.json()
                self.node_url = "http://127.0.0.1:8080"
                self.miner.set_node(self.node_url)
                self.node_entry.delete(0, "end")
                self.node_entry.insert(0, self.node_url)
                self.node_status_indicator.configure(
                    text=f"● Node running ({len(chain)} blocks)", text_color="#3fb950")
                self.status_label.configure(text="🟢 Local node", text_color="green")
                self.log(f"✅ Full node ready — {len(chain)} blocks")
                self.refresh_dashboard()
                self._register_with_seed()
                return
        except Exception:
            pass
        if attempt < 20:
            self.root.after(5000, lambda: self._check_full_node_ready(attempt + 1))
        else:
            self.node_status_indicator.configure(text="● Node slow to start", text_color="#ffaa33")
            self.log("⚠️ Node taking longer than expected — check the console window")

    def _register_with_seed(self):
        def worker():
            try:
                try:
                    public_ip = _urllib.urlopen("https://api.ipify.org", timeout=5).read().decode().strip()
                except Exception:
                    return
                if self._is_residential_ip(public_ip):
                    self.log("ℹ️  Home network — skipping peer registration")
                    self.root.after(0, lambda: self.node_status_indicator.configure(
                        text="● Node running (not a public peer)", text_color="#3fb950"))
                    return
                my_url = f"http://{public_ip}:8080"
                chain = requests.get("http://127.0.0.1:8080/api/chain", timeout=5).json()
                proof = chain[-1]["hash"][:16]
                r = requests.post(f"{PUBLIC_NODE_URL}/api/peers/register",
                                  json={"url": my_url, "proof": proof}, timeout=15)
                if r.status_code == 200:
                    self.node_registered = True
                    self.log(f"✅ Registered as public peer: {my_url}")
                    self.root.after(0, lambda: self.node_status_indicator.configure(
                        text="● Node running + public peer", text_color="#3fb950"))
                else:
                    err = r.json().get('error', 'Unknown')
                    self.log(f"⚠️  Registration failed: {err}")
                    self.root.after(0, lambda: self.node_status_indicator.configure(
                        text="● Node running (not registered)", text_color="#ffaa33"))
            except Exception as e:
                self.log(f"ℹ️  Registration skipped: {e}")
                self.root.after(0, lambda: self.node_status_indicator.configure(
                    text="● Node running (not a public peer)", text_color="#3fb950"))
        threading.Thread(target=worker, daemon=True).start()

    def _is_residential_ip(self, ip):
        try:
            parts = ip.split('.')
            if len(parts) != 4: return False
            first = int(parts[0])
            second = int(parts[1])
            if first in (10, 127): return True
            if first == 172 and 16 <= second <= 31: return True
            if first == 192 and second == 168: return True
            if first == 100 and 64 <= second <= 127: return True
            if first in (81, 82, 83, 84, 85, 86, 87, 88, 89,
                         90, 91, 92, 93, 94, 95): return True
            if first in (2, 5, 31, 37, 46, 47, 62, 71,
                         73, 76, 77, 78, 79, 80, 96, 97, 98, 99): return True
            return False
        except Exception:
            return False

    def test_connection(self):
        url = self.node_entry.get()
        try:
            r = requests.get(f"{url}/api/chain", timeout=5)
            if r.status_code == 200:
                self.node_url = url; self.miner.set_node(url)
                self.status_label.configure(text="🟢 Connected", text_color="green")
                self.settings_status.configure(text="✅ Connected", text_color="green")
                self.refresh_dashboard(); return
        except: pass
        self.status_label.configure(text="🔴 Disconnected", text_color="red")
        self.settings_status.configure(text="❌ Failed", text_color="red")

    def connect_public_node(self):
        self.log("🔵 Connecting to public node...")
        self.node_url = PUBLIC_NODE_URL; self.miner.set_node(self.node_url)
        self.node_entry.delete(0, "end"); self.node_entry.insert(0, self.node_url)
        self.status_label.configure(text="🟢 Connected (public)", text_color="green")
        self.node_status_label.configure(text="🟢 Connected to public node", text_color="green")
        self.refresh_dashboard()

    # ============================================
    # SEND LBTC
    # ============================================
    def send_coins(self):
        recipient = self.send_recipient.get().strip()
        amt_str = self.send_amount.get().strip()
        if not recipient or not amt_str:
            messagebox.showerror("Error", "Enter recipient and amount"); return
        try:
            amt_d = float(amt_str)
            if amt_d <= 0: raise ValueError
            amt = int(round(amt_d * COIN))
        except:
            messagebox.showerror("Error", "Invalid amount. Use decimal like 0.5"); return
        if not self.miner.sk:
            messagebox.showerror("Error", "Wallet not loaded"); return
        fee = FEE_PER_TX
        tx = {"sender": self.miner.address, "recipient": recipient, "amount": amt,
              "fee": fee, "timestamp": int(time.time())}
        message = f"{self.miner.address}{recipient}{amt}{fee}{tx['timestamp']}"
        tx["signature"] = sign_message(self.miner.sk, message)
        tx["pubkey"] = self.miner.vk.to_string().hex()
        tx_hash = hashlib.sha256(json.dumps(tx, sort_keys=True).encode()).hexdigest()
        tx["tx_hash"] = tx_hash
        try:
            r = requests.post(f"{self.node_url}/api/transaction", json=tx, timeout=10)
            if r.status_code == 200:
                clean = {k: v for k, v in tx.items() if k != "pubkey"}
                self.miner.pending_own_txs.append(clean)
                self.pending_sent_txs.append(clean)
                messagebox.showinfo("Transfer Placed",
                    f"✅ Sent {format_lbtc(amt)} LBTC\nRecipient: {recipient}\nTx: {tx_hash[:24]}...")
                self.log(f"✅ Sent {format_lbtc(amt)} LBTC to {recipient[:20]}...")
                self.refresh_balance(); self.refresh_pending()
            else:
                messagebox.showerror("Error", f"Transaction failed: {r.text}")
        except Exception as e:
            messagebox.showerror("Error", f"Network error: {e}")

    # ============================================
    # MINER CALLBACKS
    # ============================================
    def miner_callback(self, event, data=None):
        if event == "block_mined":
            self.root.after(0, lambda: self._on_block_mined(data))
        elif event == "stats":
            self.root.after(0, lambda: self._update_stats(data))

    def _on_block_mined(self, block):
        play_coin_sound()
        self.log("⛏️ Block mined and submitted! 💰")
        self.refresh_balance(); self.refresh_dashboard()
        self.refresh_transactions(); self.refresh_pending()

    def _update_stats(self, data):
        self.mine_blocks.configure(text=str(data.get("blocks", 0)))
        hr = data.get("hashrate", 0)
        self.mine_hashrate.configure(text=f"{hr:,.0f} H/s")
        up = data.get("uptime", 0)
        h = int(up // 3600); m = int((up % 3600) // 60); s = int(up % 60)
        self.mine_uptime.configure(text=f"{h:02d}:{m:02d}:{s:02d}")

    def update_uptime(self):
        if self.miner.running and self.miner.start_time:
            up = time.time() - self.miner.start_time
            h = int(up // 3600); m = int((up % 3600) // 60); s = int(up % 60)
            self.mine_uptime.configure(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self.update_uptime)

    def update_status(self):
        try:
            r = requests.get(f"{self.node_url}/api/chain", timeout=3)
            if r.status_code == 200:
                if not self.miner.running:
                    self.status_label.configure(text="🟢 Connected", text_color="green")
            else:
                self.status_label.configure(text="🔴 Disconnected", text_color="red")
        except:
            self.status_label.configure(text="🔴 Disconnected", text_color="red")
        self.root.after(5000, self.update_status)

    def log(self, msg):
        self.add_mining_log(msg)
        try: print(msg)
        except: pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = LBTCApp()
    app.run()