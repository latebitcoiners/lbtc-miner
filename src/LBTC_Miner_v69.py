#!/usr/bin/env python3
"""
LBTC-PRO v69 · Pure Miner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pure mining. No tabs, no wallet UI, no explorer. Just hashing, done right.

Phase 1: CPU engine (native library) + clean dashboard.
Phase 2: GPU engine (CUDA + OpenCL) — coming next.
"""
import os, sys, json, time, threading, base64, hashlib, ctypes, queue
import miner_config

# ─── Pool + Node integration ───
SOLO_NODE_URL   = "https://latebitcoiners.com"
POOL_NODE_URL   = "https://pool.latebitcoiners.com"
POOL_STATS_URL  = "https://pool.latebitcoiners.com/pool"
LOCAL_NODE_URL  = "http://127.0.0.1:8080"
PUBLIC_NODE_URL = os.environ.get("LBTC_NODE_URL", SOLO_NODE_URL)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Mac/Linux: the app folder can be read-only (.app bundle, AppImage, /opt), so settings live in ~/LBTC
_USER_DIR = os.path.join(os.path.expanduser("~"), "LBTC")
try:
    os.makedirs(_USER_DIR, exist_ok=True)
except Exception:
    pass
_MODE_FILE = os.path.join(_USER_DIR, "mining_mode.json")
if not os.path.exists(_MODE_FILE) and os.path.exists(os.path.join(_BASE_DIR, "mining_mode.json")):
    _MODE_FILE_OLD = os.path.join(_BASE_DIR, "mining_mode.json")
    try:
        import shutil as _sh; _sh.copyfile(_MODE_FILE_OLD, _MODE_FILE)
    except Exception:
        pass

def _nocache(url):
    """v66: a unique stamp on every request, so no cache between the miner and the node can return an old copy."""
    return url + ('&' if '?' in url else '?') + '_=' + str(int(time.time() * 1000))

# ── v68: SHA-256 state after the full 64-byte blocks of `head` (run once per job, on the CPU) ──
def _v68_midstate(head):
    import struct
    import numpy as _np
    K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
         0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
         0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
         0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
         0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
         0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
         0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
         0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]
    M = 0xffffffff
    r = lambda x, n: ((x >> n) | (x << (32 - n))) & M
    st = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
    for off in range(0, (len(head) // 64) * 64, 64):
        w = list(struct.unpack(">16I", head[off:off + 64]))
        for i in range(16, 64):
            s0 = r(w[i-15], 7) ^ r(w[i-15], 18) ^ (w[i-15] >> 3)
            s1 = r(w[i-2], 17) ^ r(w[i-2], 19) ^ (w[i-2] >> 10)
            w.append((w[i-16] + s0 + w[i-7] + s1) & M)
        a, b, c, d, e, f, g, h = st
        for i in range(64):
            t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) & M
            t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) & M
            h, g, f, e, d, c, b, a = g, f, e, (d + t1) & M, c, b, a, (t1 + t2) & M
        st = [(x + y) & M for x, y in zip(st, (a, b, c, d, e, f, g, h))]
    return _np.array(st, dtype=_np.uint32)

# ── v68: the counter goes at the END of the block, so each attempt hashes one 64-byte block, whatever the block holds ──
V68_HEAD_REMAINDER = 40     # v69: the counter starts at byte 40 of the last block (13 digits + "}" + padding still fit in one block)
def v68_split(block):
    """Set nonce=0, add work_nonce (sorts last) and a mining_pad that aligns the head. Returns (head, tail) bytes."""
    block['nonce'] = 0
    block['work_nonce'] = 0
    block['mining_pad'] = ''
    src = json.dumps(block, sort_keys=True)
    end = '"work_nonce": 0}'
    if not src.endswith(end):
        return None, None
    head_len = len(src.encode()) - 2                     # everything up to and including '"work_nonce": '
    block['mining_pad'] = '0' * ((V68_HEAD_REMAINDER - head_len) % 64)
    src = json.dumps(block, sort_keys=True)
    return src[:-2].encode(), b'}'

def load_saved_mode():
    try:
        if os.path.exists(_MODE_FILE):
            with open(_MODE_FILE) as f:
                return json.load(f).get("mode", "solo")
    except Exception:
        pass
    return "solo"

def save_mode(mode):
    try:
        with open(_MODE_FILE, "w") as f:
            json.dump({"mode": mode, "saved_at": int(time.time())}, f)
    except Exception:
        pass

def get_mode_url(mode, custom=None):
    if mode == "pool":   return POOL_NODE_URL
    if mode == "local":  return LOCAL_NODE_URL
    if mode == "custom": return custom or SOLO_NODE_URL
    return SOLO_NODE_URL
# ───────────────────────────────

import tkinter.messagebox as messagebox
import webbrowser
import customtkinter as ctk
import requests
from datetime import datetime
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ecdsa import SigningKey, SECP256k1

# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS — must match node consensus
# ═══════════════════════════════════════════════════════════════════════════
COIN                = 100_000_000
BASE_REWARD         = 50 * COIN
HALVING_INTERVAL    = 210_000
FEE_PER_TX          = 10_000
MAX_TXS_PER_BLOCK   = 50
FOUNDER_ADDR        = "1AvFeYZhJtxL71yjuG7vvBiJKYcuEDS6XP"
FOUNDER_PCT         = 0.01
MINER_VERSION       = 69
JOB_REFRESH_SEC     = 120   # v64: rebuild the job every 2 min (fresh timestamp, difficulty, transactions)
MINER_HEADERS       = {"X-LBTC-Miner": str(MINER_VERSION)}   # node sends full blocks to v63+
DATA_DIR            = os.path.join(os.path.expanduser('~'), 'LBTC')
CONFIG_FILE         = os.path.join(DATA_DIR, 'v60_config.json')
WALLET_FILE         = os.path.join(DATA_DIR, 'wallet_gui.json')
NODE_URL_DEFAULT    = "https://latebitcoiners.com"

# Difficulty bounds — must match node (Hybrid C rules)
DIFF_HARD_FLOOR     = 2 ** 216          # hardest cap
DIFF_EASY_CEILING   = 2 ** 224          # v61c: hashrate race (was 2^248)
TARGET_BLOCK_TIME   = 300               # 5-min target (min-gap stays 180s)

# ═══════════════════════════════════════════════════════════════════════════
# NATIVE LIBRARY LOADER
# ═══════════════════════════════════════════════════════════════════════════
def load_native():
    """Locate and load the native SHA-256 library."""
    if sys.platform == "win32":
        name = "lbtc_scan.dll"
    elif sys.platform == "darwin":
        name = "liblbtc_scan.dylib"
    else:
        name = "liblbtc_scan.so"

    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    search = [
        getattr(sys, "_MEIPASS", here),
        os.path.dirname(os.path.abspath(sys.executable)),
        here,
        os.path.join(here, "native"),
        parent,
        os.path.join(parent, "native"),
        os.getcwd(),
    ]
    for p in search:
        path = os.path.join(p, name)
        if os.path.exists(path):
            try:
                lib = ctypes.CDLL(path)
                return lib, path
            except Exception as e:
                print(f"[native] load {path} failed: {e}")
    return None, None

# ═══════════════════════════════════════════════════════════════════════════
# CRYPTO
# ═══════════════════════════════════════════════════════════════════════════
def decrypt_private_key(enc, pw):
    salt  = base64.b64decode(enc["salt"])
    nonce = base64.b64decode(enc["nonce"])
    ct    = base64.b64decode(enc["ct"])
    kdf = PBKDF2HMAC(hashes.SHA256(), 32, salt, 600_000)
    key = kdf.derive(pw.encode())
    return AESGCM(key).decrypt(nonce, ct, None)

def sign_message(sk, msg):
    return sk.sign(msg.encode(), hashfunc=hashlib.sha256).hex()

# ═══════════════════════════════════════════════════════════════════════════
# MINER CORE
# ═══════════════════════════════════════════════════════════════════════════
class PureMiner:
    def __init__(self):
        self.running        = False
        self.node_url       = NODE_URL_DEFAULT
        self.address        = ""
        self.sk             = None
        self.vk             = None
        self.blocks_mined   = 0
        self.current_hashes = 0
        self.last_peak_hr   = 0
        self.stale          = True
        self.start_time     = 0
        self.workers        = 0
        self.log            = print
        self.stats_cb       = None
        self.thread         = None
        self.stats_thread   = None
        self._hr_window     = []
        if not hasattr(self, 'config'):
            import miner_config as _mc
            self.config = _mc.load()

    # ─────── Mining loop ───────
    def _mine_loop(self):
        self.log("⛏  Mining loop started")
        while self.running:
            try:
                self._mine_once()   # v65: a block is counted once, when the node accepts it
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout, ConnectionError) as e:
                self.log(f"⚠  Node connection: {e}")
                time.sleep(3)
            except Exception as e:
                self.log(f"⚠  Mining error: {type(e).__name__}: {e}")
                time.sleep(2)

    def _mine_once(self):
        if not self.sk:
            raise Exception("Wallet not loaded")

        # ── Fetch tip ──
        r = requests.get(_nocache(f"{self.node_url}/api/chain?limit=3"), timeout=10)
        if r.status_code != 200:
            raise ConnectionError(f"HTTP {r.status_code}")
        chain = r.json()
        if not isinstance(chain, list) or not chain:
            return False
        chain = sorted(chain, key=lambda b: b.get('index', 0))
        if not isinstance(chain[0], dict):
            return False

        # ── Fetch mempool ──
        try:
            mr = requests.get(_nocache(f"{self.node_url}/api/mempool"), timeout=5, headers=MINER_HEADERS)
            mempool = mr.json() if mr.status_code == 200 else []
            if isinstance(mempool, dict):
                mempool = mempool.get("mempool", mempool.get("transactions", []))
            mempool = [tx for tx in mempool if isinstance(tx, dict)]
        except Exception:
            mempool = []

        # ── Build block ──
        last = chain[-1]
        index       = last['index'] + 1
        timestamp   = int(time.time())
        prev_hash   = last['hash']
        difficulty  = self._calc_difficulty(chain)
        reward      = self._block_reward(index)

        # v63: the node says exactly what the founder grant is for the next block
        # (1% until the 10,000 LBTC cap or the end date, then 0) and to which address.
        founder_addr, founder_cut = FOUNDER_ADDR, 0
        try:
            fr = requests.get(_nocache(f"{self.node_url}/api/founder/stats"), timeout=5, headers=MINER_HEADERS).json()
            if "next_block_founder_units" not in fr:
                raise ValueError("node too old")
            founder_cut = int(fr["next_block_founder_units"])
            founder_addr = fr.get("address") or FOUNDER_ADDR
        except Exception as e:
            self.log(f"[v63] founder info unavailable ({e}) - retrying")
            time.sleep(3)
            return False
        miner_cut   = reward - founder_cut

        self._job_gen = getattr(self, '_mode_gen', 0)     # v67: this job belongs to the current server
        # v66: pool mode: the block's reward pays the pool, the coinbase names this miner, and we mine to the
        # pool's share target (easier); a share that also meets the network target is a full block
        pay_to, share_target = self.address, None
        if self._is_pool_mode():
            try:
                pi = getattr(self, '_pool_info', None)
                if not pi or time.time() - pi.get('_at', 0) > 300:
                    pi = requests.get(_nocache(f"{self.node_url}/api/pool/info"), timeout=5).json(); pi['_at'] = time.time(); self._pool_info = pi
                pay_to = pi['pool_address']
                share_target = format(min(2 ** 256 - 1, int(difficulty, 16) * int(pi.get('share_factor', 16))), '064x')
            except Exception as e:
                self.log(f"[pool] pool info unavailable ({e}) - retrying")
                time.sleep(5)
                return False

        txs = []
        if founder_cut > 0:
            txs.append({
                'sender': 'coinbase', 'recipient': founder_addr,
                'amount': founder_cut, 'fee': 0, 'timestamp': timestamp,
                'memo': f'founder_grant_block_{index}',
            })

        mp_take = mempool[:MAX_TXS_PER_BLOCK]
        total_fees = sum(tx.get('fee', 0) for tx in mp_take if tx.get('sender') != 'coinbase')
        cb = {
            'sender': 'coinbase', 'recipient': pay_to,
            'amount': miner_cut + total_fees, 'fee': total_fees,
            'timestamp': timestamp,
        }
        if share_target:
            cb['memo'] = 'pool:' + self.address          # v66: proves whose work this is
            cb['extra'] = os.urandom(6).hex()            # v66: extra nonce, so every pool job is unique (no duplicate shares)
        txs.append(cb)
        txs += mp_take

        block = {
            'index': index, 'timestamp': timestamp, 'transactions': txs,
            'previous_hash': prev_hash, 'nonce': 0, 'difficulty': difficulty,
        }

        workers = self.workers if self.workers > 0 else max(1, os.cpu_count() or 4)
        if not getattr(self, '_mode_logged_for', None) == self.node_url:
            self._mode_logged_for = self.node_url
            self.log(f"[mode] mining {'in the LBTC Fair Pool' if self._is_pool_mode() else 'solo'} · {self.node_url}")
        self.log(f"[v68] #{index}  ·  {len(txs)} txs  ·  diff {difficulty[:16]}...  (full-speed hashing · v69 fast engine)")
        self.log(f"[5A] difficulty source: node")

        # ── Mine ──
        result = self._parallel_mine(block, workers, target_hex=share_target)
        if result is None:
            return False
        nonce, block_hash, _ = result
        block['work_nonce'] = nonce      # v68: the counter lives at the end of the block; 'nonce' stays 0
        block['hash']  = block_hash

        # ── Submit ──
        return self._submit_block(block)

    def _native_mine_core(self, head, tail, target_bytes, block, stop_event, workers):
        lib, _path = load_native()
        if not lib:
            self.log("❌ Native library (lbtc_scan.dll / liblbtc_scan.so / .dylib) not found")
            return None

        lib.lbtc_scan_threads.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_uint64),
        ]
        lib.lbtc_scan_threads.restype = ctypes.c_int

        # head/tail/target_bytes passed in from caller
        U8  = ctypes.c_ubyte
        U64 = ctypes.c_uint64

        head_buf   = (U8 * len(head)).from_buffer_copy(head)
        tail_buf   = (U8 * len(tail)).from_buffer_copy(tail)
        target_buf = (U8 * 32).from_buffer_copy(target_bytes)
        found_hash  = (U8 * 32)()
        found_nonce = U64(0)

        next_nonce = 0
        cumulative = 0
        SESSION_SEC = 3.0

        while self.running and not stop_event.is_set():
            # Stale check before starting a fresh window
            if self._is_stale(block['previous_hash']):
                self.log("[v63] chain tip moved — refetching job")
                return None
            if time.time() - block['timestamp'] > JOB_REFRESH_SEC or self._job_outdated():
                stop_event.set()      # v64: the GPU refreshes too (v67: also after a Solo/Pool switch)
                return None

            stop = ctypes.c_int(0)
            window = U64(0)
            session_end = time.time() + SESSION_SEC

            def _stopper(_sf=stop, _end=session_end):
                while time.time() < _end:
                    time.sleep(0.05)
                    if not self.running:
                        break
                _sf.value = 1
            threading.Thread(target=_stopper, daemon=True).start()

            t0 = time.time()
            res = lib.lbtc_scan_threads(
                head_buf, len(head),
                tail_buf, len(tail),
                next_nonce,
                target_buf,
                workers,
                ctypes.byref(stop),
                ctypes.byref(found_nonce),
                found_hash,
                ctypes.byref(window),
            )
            dt = time.time() - t0
            w = window.value
            cumulative += w
            next_nonce += w + workers
            self.cpu_hashes += w
            self.current_hashes = self.cpu_hashes + self.gpu_hashes

            if w > 0 and dt > 0:
                rate = w / dt
                if rate > self.last_peak_hr:
                    self.last_peak_hr = rate
                self.stale = False

            if res == 1:
                return (found_nonce.value, bytes(found_hash).hex(), 0)
        return None

    def _v68_prepare_midstate(self, head):
        """v68: the head can be the whole block (several KB); the CUDA engine's head buffer is 4 KB, so the midstate is
        calculated here and only its 32 bytes are copied to the GPU. The AMD engine already works this way."""
        eng = self.gpu_engine
        if hasattr(eng, 'prepare'):                       # v69: the fast engine packs its own job
            return eng.prepare(head)
        n_full = len(head) // 64
        if n_full and hasattr(eng, '_buf_midstate') and hasattr(eng, '_memcpy_htod'):
            eng._memcpy_htod(eng._buf_midstate, _v68_midstate(head))
            eng._prefix_len = n_full * 64
            return True, head[n_full * 64:]
        return eng.prepare_midstate(head)

    def _gpu_mine_core(self, head, tail, target_bytes, block, stop_event):
        """v61: GPU mining with midstate + auto-recovery + periodic reset."""
        # ── Lazy-init (retried after each failure) ──
        if getattr(self, 'gpu_engine', None) is None:
            if getattr(self, 'gpu_disabled', False):
                return None
            # Exponential backoff after repeated failures
            fail_n = getattr(self, '_gpu_fail_count', 0)
            if fail_n > 0:
                delay = min(30, 2 ** fail_n)
                if self.log:
                    self.log(f'[gpu] retry init in {delay}s (fail #{fail_n})')
                time.sleep(delay)
            
            engine_ready = False
            
            # 0. v69: the fast OpenCL engine (lackminer kernel), only if its self-test on this GPU passes
            try:
                from fast_engine import FastCLMiner as _GE_FAST
                tmp_fast = _GE_FAST(log=self.log or print)
                if getattr(tmp_fast, 'ready', False):
                    self.gpu_engine = tmp_fast
                    engine_ready = True
                elif self.log:
                    self.log("[gpu] fast engine not available here: using the CUDA engine")
            except Exception as e:
                if self.log: self.log(f"[gpu] fast engine load failed ({type(e).__name__}): using the CUDA engine")

            # 1. Try NVIDIA CUDA
            if not engine_ready:
              try:
                from gpu_engine import GpuEngine as _GE_CUDA
                tmp_cuda = _GE_CUDA(log=self.log or print)
                if getattr(tmp_cuda, 'ready', False):
                    self.gpu_engine = tmp_cuda
                    engine_ready = True
                else:
                    if self.log: self.log("[gpu] CUDA failed to initialize. Trying AMD...")
              except Exception as e:
                if self.log: self.log(f"[gpu] CUDA load failed. Trying AMD...")

            # 2. Try AMD OpenCL
            if not engine_ready:
                try:
                    from amd_engine import AMDMiner as _GE_AMD
                    tmp_amd = _GE_AMD(log_func=self.log or print)
                    if getattr(tmp_amd, 'ready', False):
                        self.gpu_engine = tmp_amd
                        engine_ready = True
                except Exception as e:
                    if self.log: self.log(f"[gpu] AMD load failed: {e}")

            if not engine_ready:
                self._gpu_fail_count = fail_n + 1
                self.gpu_engine = None
                if self._gpu_fail_count >= 5:
                    self.gpu_disabled = True
                    if self.log:
                        self.log('[gpu] 5 init fails — GPU disabled for this session')
                return None

            # Successful reinit — log if we were recovering
            if fail_n > 0 and self.log:
                self.log(f'[gpu] recovered after {fail_n} failure(s)')
            self._gpu_fail_count = 0
            self._gpu_logged = False

        if not getattr(self.gpu_engine, 'ready', False):
            return None

        next_nonce = 1 << 40   # v63: the GPU searches a different nonce range than the CPU
        SESSION_SEC = 3.0

        # ── Midstate prep (with recovery) ──
        midstate = None
        head_partial = None
        try:
            midstate, head_partial = self._v68_prepare_midstate(head)
        except Exception as e:
            self._gpu_on_error(e, 'midstate-prep')
            return None

        if self.log and not getattr(self, '_gpu_logged', False):
            md_on = 'on' if midstate is not None else 'off'
            self.log(f'[gpu] mining on {self.gpu_engine.name} (midstate={md_on})')
            self._gpu_logged = True

        while self.running and not stop_event.is_set():
            # v63: stop working on a block someone else already mined
            if self._is_stale(block['previous_hash']):
                if self.log:
                    self.log('[gpu] chain tip moved — refetching job')
                return None
            if self._job_outdated():
                if self.log:
                    self.log('[v67] mode switched: starting a new job on the new server')
                return None
            if time.time() - block['timestamp'] > JOB_REFRESH_SEC:
                # v64: a job older than 2 min gets a fresh timestamp (the node refuses blocks stamped >15 min ago)
                if self.log:
                    self.log('[v64] refreshing the job: new timestamp, difficulty and transactions')
                return None
            session_end = time.time() + SESSION_SEC
            while time.time() < session_end and self.running and not stop_event.is_set():
                try:
                    if midstate is not None:
                        found, hashes, dt, hash_bytes = self.gpu_engine.mine_batch_midstate(
                            midstate, head_partial, tail, target_bytes, next_nonce,
                            threads=256, blocks=4096,
                        )
                    else:
                        found, hashes, dt, hash_bytes = self.gpu_engine.mine_batch(
                            head, tail, target_bytes, next_nonce,
                            threads=256, blocks=4096,
                        )
                except Exception as e:
                    self._gpu_on_error(e, 'batch')
                    return None

                self.gpu_hashes += hashes
                self.current_hashes = self.cpu_hashes + self.gpu_hashes
                next_nonce += hashes
                if found is not None and hash_bytes is not None:
                    if self.log:
                        self.log(f'[gpu] FOUND nonce={found}')
                    return (found, hash_bytes.hex(), 0)

                pct = self.config.get('gpu_percent', 100)
                if pct < 100 and dt > 0:
                    try:
                        self.gpu_engine.duty_cycle_sleep(dt, pct)
                    except Exception:
                        pass
        return None

    def _gpu_on_error(self, exc, where='batch'):
        """Log GPU error, tear down engine so next call re-inits it."""
        self._gpu_fail_count = getattr(self, '_gpu_fail_count', 0) + 1
        if self.log:
            self.log(f'[gpu] error ({where}) #{self._gpu_fail_count}: {type(exc).__name__}: {str(exc)[:80]}')
        # Tear down so lazy-init at top of _gpu_mine_core re-creates it
        self.gpu_engine = None
        self._gpu_logged = False
        # Small pause to let driver recover
        time.sleep(2)



    def _parallel_mine(self, block, workers, target_hex=None):
        """v63: GPU runs on THIS thread (persistent context).
        CPU runs on sub-thread. First to find wins.
        v66: target_hex = the pool's share target in pool mode (easier than the block's difficulty)."""

        head, tail = v68_split(block)          # v68: counter at the end (work_nonce); block gets nonce=0 + mining_pad
        if head is None:
            return None
        target_hex = str(target_hex or block["difficulty"]).lower()
        if len(target_hex) != 64:
            return None
        target_bytes = bytes.fromhex(target_hex)

        cpu_pct = self.config.get("cpu_percent", 100)
        gpu_pct = self.config.get("gpu_percent", 100)
        cpu_on = cpu_pct > 0 and workers > 0
        gpu_on = gpu_pct > 0 and not getattr(self, 'gpu_disabled', False)

        if not cpu_on and not gpu_on:
            time.sleep(2)
            return None

        stop_event = threading.Event()
        result_holder = {'r': None, 'source': None}

        def cpu_worker():
            try:
                r = self._native_mine_core(
                    head, tail, target_bytes, block, stop_event, workers,
                )
                if r is not None and not stop_event.is_set():
                    if result_holder['r'] is None:
                        result_holder['r'] = r
                        result_holder['source'] = 'CPU'
                        stop_event.set()
                elif r is None and self._is_stale(block['previous_hash']):
                    stop_event.set()   # v63: job is stale - stop the GPU too
            except Exception as e:
                if self.log:
                    self.log(f"[cpu] {type(e).__name__}: {e}")

        if cpu_on:
            t = threading.Thread(target=cpu_worker, daemon=True)
            t.start()

        # GPU runs on THIS thread — context persists across calls
        if gpu_on:
            try:
                r = self._gpu_mine_core(head, tail, target_bytes, block, stop_event)
                if r is not None and result_holder['r'] is None:
                    result_holder['r'] = r
                    result_holder['source'] = 'GPU'
                    stop_event.set()
            except Exception as e:
                if self.log:
                    self.log(f"[gpu] {type(e).__name__}: {str(e)[:80]}")

        elif cpu_on:
            # v64: CPU-only mining: wait for the CPU workers (before, they were stopped at once)
            t.join()
            if self.log and result_holder['r'] is None and time.time() - block['timestamp'] > JOB_REFRESH_SEC:
                self.log('[v64] refreshing the job: new timestamp, difficulty and transactions')

        stop_event.set()
        if result_holder['r'] is not None:
            if self.log:
                self.log(f"[{result_holder['source']}] first to find")
            return result_holder['r']
        return None


    def set_node(self, url):
        new = url.rstrip('/') if url else ''
        if new != getattr(self, 'node_url', None):
            self._mode_gen = getattr(self, '_mode_gen', 0) + 1     # v67: running jobs for the old server stop at once
        self.node_url = new

    def _job_outdated(self):
        return getattr(self, '_job_gen', 0) != getattr(self, '_mode_gen', 0)

    def _is_pool_mode(self):
        return 'pool' in (self.node_url or '').lower()

    def _submit_pool_share(self, block):
        """v66: send a share (the whole block, so the pool can check the work) to the LBTC Fair Pool."""
        try:
            r = requests.post(f"{self.node_url}/api/pool/share", json={"miner": self.address, "block": block}, timeout=10)
            d = {}
            try: d = r.json()
            except Exception: pass
            if r.status_code == 200 and d.get('ok'):
                self.pool_shares = getattr(self, 'pool_shares', 0) + 1
                if d.get('block'):
                    self.blocks_mined += 1
                    if self.log: self.log(f"\U0001F389 [pool] share #{self.pool_shares} is a BLOCK (#{block.get('index')}): the pool is submitting it")
                    return True
                if self.log and (self.pool_shares <= 3 or self.pool_shares % 10 == 0):
                    self.log(f"[pool] share accepted ({self.pool_shares} so far)")
                return False
            if self.log: self.log(f"[pool] share refused: {d.get('error', r.status_code)}")
            return False
        except Exception as e:
            if self.log: self.log(f"[pool] share failed: {type(e).__name__}: {e}")
            time.sleep(2)
            return False

    def _is_stale(self, expected_prev_hash):
        # v61: try /api/chain/tip first, fall back to /api/chain?limit=1
        try:
            r = requests.get(_nocache(f"{self.node_url}/api/chain/tip"), timeout=2)
            if r.status_code == 200:
                try:
                    tip = r.json()
                    if isinstance(tip, dict) and 'hash' in tip:
                        return tip.get('hash') != expected_prev_hash
                except Exception:
                    pass
        except Exception:
            pass
        # Fallback for older nodes
        try:
            r = requests.get(_nocache(f"{self.node_url}/api/chain?limit=1"), timeout=2)
            if r.status_code != 200:
                return False
            chain = r.json()
            if not isinstance(chain, list) or not chain:
                return False
            return chain[-1].get('hash') != expected_prev_hash
        except Exception:
            return False


    def _submit_block(self, block, _attempt=0):
        # v61: pool mode intercepts here
        if self._is_pool_mode():
            return self._submit_pool_share(block)

        # ─── Solo / Local node: submit full block ───
        msg = f"BLOCK|{block['index']}|{block['hash']}|{self.address}"
        sig = sign_message(self.sk, msg)
        pub = self.vk.to_string().hex()
        # v61 DEBUG — recompute what node will hash
        try:
            import hashlib as _hl
            _body = json.dumps({k: v for k, v in block.items() if k != 'hash'}, sort_keys=True)
            _recomputed = _hl.sha256(_body.encode()).hexdigest()
            self.log(f"[DBG] claimed: {block.get('hash','')[:24]}...")
            self.log(f"[DBG] node computes: {_recomputed[:24]}...")
            self.log(f"[DBG] MATCH: {block.get('hash') == _recomputed}")
            if block.get('hash') != _recomputed:
                self.log(f"[DBG] body: {_body[:400]}")
        except Exception as _e:
            self.log(f"[DBG] err: {_e}")

        try:
            r = requests.post(
                f"{self.node_url}/api/block",
                json=block, timeout=30,
                headers={'X-Block-Signature': sig, 'X-Block-Pubkey': pub, **MINER_HEADERS},
            )
            resp = {}
            try: resp = r.json()
            except Exception: pass

            if r.status_code == 200 and resp.get('status') == 'ok':
                self.blocks_mined += 1
                self.last_block_hash = block.get('hash', '')
                self.last_block = block
                if self.log:
                    self.log(f"\u2713 Block #{block.get('index')} ACCEPTED")
                return True
            if resp.get('status') == 'already exists':
                return False
            if r.status_code == 429:
                err = str(resp.get('error', '') or r.text)
                wait = 180
                # v65: the fair-mining rule says how long other miners get first
                if 'other miners have' in err:
                    try:
                        wait = int(err.split('other miners have')[1].strip().split('s')[0]) + 3
                    except Exception:
                        pass
                if 's since prev' in err:
                    try:
                        pre = err.split('s since prev')[0]
                        num = ''.join(c for c in reversed(pre) if c.isdigit())
                        if num:
                            elapsed = int(num[::-1])
                            wait = max(1, 180 - elapsed + 5)
                    except Exception:
                        pass
                if self.log:
                    self.log(f"[429] block too soon \u2014 sleeping {wait}s")
                end = time.time() + wait
                while self.running and time.time() < end:
                    time.sleep(0.5)
                if self.running and self.log:
                    self.log("[429] cooldown ended \u2014 resuming")
                # v65: the block is still valid if nobody mined this height meanwhile: send it again
                if self.running and _attempt < 3 and not self._is_stale(block['previous_hash']):
                    if self.log:
                        self.log(f"[v65] re-sending block #{block.get('index')} (still valid)")
                    return self._submit_block(block, _attempt + 1)
                return False
            if self.log:
                self.log(f"\u2717 Block rejected: HTTP {r.status_code} {resp or r.text[:100]}")
            return False
        except Exception as e:
            if self.log:
                self.log(f"\u2717 Submit error: {type(e).__name__}: {e}")
            return False


    def _calc_difficulty(self, chain):
        """Phase 5A: fetch from node /api/difficulty/next — node is truth."""
        try:
            r = requests.get(_nocache(f"{self.node_url}/api/difficulty/next"), timeout=5)
            if r.status_code == 200:
                d = r.json().get("difficulty")
                if d and len(d) == 64:
                    return d
        except Exception:
            pass
        # Fallback: exact same math as node's smooth_difficulty.py
        if not chain:
            return "0000ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        current = int(chain[-1].get("difficulty", ""), 16) if chain[-1].get("difficulty") else 2**232
        if len(chain) < 2:
            return format(current, '064x')
        actual = chain[-1].get('timestamp', 0) - chain[-2].get('timestamp', 0)
        if actual <= 0:
            actual = 1
        ratio = max(0.5, min(2.0, actual / 180))
        new_target = int(current * ratio)
        max_t = 2**256 - 1
        min_t = 2**216
        new_target = min(max_t, max(min_t, new_target))
        return format(new_target, '064x')

    def _block_reward(self, height):
        halvings = height // HALVING_INTERVAL
        if halvings >= 64:
            return 0
        return BASE_REWARD // (2 ** halvings)

    # ─────── Stats loop ───────
    def _stats_loop(self):
        while self.running:
            time.sleep(1.0)
            if not self.running:
                break
            now = time.time()
            # Separate windows for total / CPU / GPU
            self._hr_window.append((now, self.current_hashes))
            self._hr_window = [(t, h) for t, h in self._hr_window if now - t <= 10]
            self._hr_window_cpu.append((now, self.cpu_hashes))
            self._hr_window_cpu = [(t, h) for t, h in self._hr_window_cpu if now - t <= 10]
            self._hr_window_gpu.append((now, self.gpu_hashes))
            self._hr_window_gpu = [(t, h) for t, h in self._hr_window_gpu if now - t <= 10]

            def _rate(window):
                if len(window) >= 2:
                    t0, h0 = window[0]
                    t1, h1 = window[-1]
                    dt = t1 - t0
                    return (h1 - h0) / dt if dt > 0 else 0
                return 0

            hr = _rate(self._hr_window)
            self.cpu_rate = _rate(self._hr_window_cpu)
            self.gpu_rate = _rate(self._hr_window_gpu)
            self.total_hashes = self.current_hashes
            if hr > self.best_rate:
                self.best_rate = hr
            if hr > 0:
                self.last_peak_hr = hr
                self.stale = False
            else:
                self.stale = True
            if self.stats_cb:
                try:
                    self.stats_cb({
                        "blocks":     self.blocks_mined,
                        "hashrate":   hr,
                "cpu_rate": self.cpu_rate,
                "best_rate": self.best_rate,
                "total_hashes": self.total_hashes,
                "elapsed": (time.time() - self.start_time) if self.start_time else 0,
                "gpu_rate": self.gpu_rate,
                        "last_peak":  self.last_peak_hr,
                        "stale":      self.stale,
                        "uptime":     now - self.start_time,
                    })
                except Exception:
                    pass

    def fetch_balance(self):
        if not self.address:
            return None
        try:
            r = requests.get(_nocache(f"{self.node_url}/api/balance/{self.address}"), timeout=5)
            if r.status_code == 200:
                return r.json().get("balance", 0)
        except Exception:
            pass
        return None

    # ─────── Public ───────
    def start(self, workers=0):
        self.workers = workers
        self.running = True
        self.blocks_mined = 0
        self.current_hashes = 0
        self.last_peak_hr = 0
        self.stale = True
        self.start_time = time.time()
        self._hr_window = []
        self._hr_window_cpu = []
        self._hr_window_gpu = []
        self.cpu_hashes = 0
        self.gpu_hashes = 0
        self.cpu_rate = 0.0
        self.gpu_rate = 0.0
        self.config = miner_config.load()
        self.best_rate = 0.0
        self.total_hashes = 0
        self.thread = threading.Thread(target=self._mine_loop, daemon=True)
        self.thread.start()
        self.stats_thread = threading.Thread(target=self._stats_loop, daemon=True)
        self.stats_thread.start()

    def stop(self):
        self.running = False
        if self.thread:        self.thread.join(timeout=3)
        if self.stats_thread:  self.stats_thread.join(timeout=1)

# ═══════════════════════════════════════════════════════════════════════════
# UI — single window, no tabs
# ═══════════════════════════════════════════════════════════════════════════
class V60App:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.root = ctk.CTk()
        self.root.title("LBTC-PRO v69 · Pure Miner")
        self.root.geometry("1200x820")
        self.root.minsize(1000, 720)
        self.miner = PureMiner()
        self.miner.log = self._log_safe
        self.miner.stats_cb = self._on_stats
        # v61: restore saved mining mode
        try:
            _saved_mode = load_saved_mode()
            _saved_url = get_mode_url(_saved_mode)
            if _saved_url:
                self.miner.set_node(_saved_url)
        except Exception:
            pass
        self._load_config()
        # v69: the saved mode decides the server (the settings file may still hold an older address)
        try:
            _m = load_saved_mode()
            if _m in ("pool", "solo"):
                self.miner.set_node(get_mode_url(_m))
                self.cfg["node_url"] = self.miner.node_url
        except Exception:
            pass
        self._build_ui()
        self._load_wallet()
        self.root.after(1000, self._tick_uptime)

    # ── Config ──
    def _load_config(self):
        self.cfg = {
            "node_url":     NODE_URL_DEFAULT,
            "cpu_threads":  0,
            "wallet_address": "",
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    self.cfg.update(json.load(f))
            except Exception:
                pass
        self.miner.node_url = self.cfg["node_url"]

    def _save_config(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception:
            pass

    # ── Wallet ──
    def _load_wallet(self):
        self._wallet_data = None
        if not os.path.exists(WALLET_FILE):
            self._set_status("No wallet file — add ~/LBTC/wallet_gui.json", "#f85149")
            return
        try:
            with open(WALLET_FILE) as f:
                self._wallet_data = json.load(f)
            self.cfg["wallet_address"] = self._wallet_data.get("address", "")
            self._set_status("Wallet found — click Unlock", "#f7931a")
        except Exception as e:
            self._set_status(f"Wallet error: {e}", "#f85149")

    def _unlock_wallet(self):
        if not self._wallet_data:
            messagebox.showerror("Wallet", "No wallet file found")
            return
        pw = ctk.CTkInputDialog(
            text="Enter wallet passphrase:", title="Unlock Wallet"
        ).get_input()
        if not pw:
            return
        try:
            kb = decrypt_private_key(self._wallet_data["encrypted"], pw)
            self.miner.sk = SigningKey.from_string(kb, curve=SECP256k1)
            self.miner.vk = self.miner.sk.get_verifying_key()
            self.miner.address = self._wallet_data["address"]
            if hasattr(self, 'address_label'):
                self.address_label.configure(text=self.miner.address, text_color="#c9d1d9")
            self._refresh_balance()
            self._set_status(f"Ready · {self.miner.address[:16]}…", "#3fb950")
            self._log(f"✓ Wallet unlocked · {self.miner.address}")
        except Exception:
            self._set_status("Wrong passphrase", "#f85149")
            messagebox.showerror("Unlock failed", "Wrong passphrase")

    # ── UI ──
    def _copy_address(self):
        if self.miner.address:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.miner.address)
            self._log('Address copied: ' + self.miner.address)

    def _logout_wallet(self):
        self.miner.sk = None
        self.miner.vk = None
        self.miner.address = ''
        self._set_status('No wallet', '#8b949e')
        if hasattr(self, 'address_label'):
            self.address_label.configure(text='-', text_color='#8b949e')
        if hasattr(self, 'balance_label'):
            self.balance_label.configure(text='-')
        self._log('Wallet cleared')

    def _refresh_balance(self):
        if not self.miner.address:
            if hasattr(self, 'balance_label'):
                self.balance_label.configure(text='-')
            return
        def w():
            b = self.miner.fetch_balance()
            def u():
                if hasattr(self, 'balance_label'):
                    self.balance_label.configure(
                        text=('%s LBTC' % (b/1e8)) if b is not None else 'unavailable')
            self.root.after(0, u)
        threading.Thread(target=w, daemon=True).start()

    def _import_private_key(self):
        hk = ctk.CTkInputDialog(text='Paste private key (64 hex):', title='Import').get_input()
        if not hk: return
        hk = hk.strip().replace('0x', '')
        if len(hk) != 64:
            messagebox.showerror('Import', 'Must be 64 hex chars'); return
        try:
            from ecdsa import SigningKey, SECP256k1
            import hashlib as _hashlib, base64 as _b64
            sk = SigningKey.from_string(bytes.fromhex(hk), curve=SECP256k1)
            vk = sk.get_verifying_key()
            pub = vk.to_string()
            sha = _hashlib.sha256(pub).digest()
            h = _hashlib.new('ripemd160'); h.update(sha)
            v = b'\x00' + h.digest()
            chk = _hashlib.sha256(_hashlib.sha256(v).digest()).digest()[:4]
            payload = v + chk
            A = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
            num = int.from_bytes(payload, 'big'); addr = ''
            while num > 0:
                num, r = divmod(num, 58); addr = A[r] + addr
            for byte in payload:
                if byte == 0: addr = '1' + addr
                else: break
            pw = ctk.CTkInputDialog(text='Set passphrase (12+ chars):', title='Passphrase').get_input()
            if not pw or len(pw) < 12:
                messagebox.showerror('Import', 'Passphrase 12+ chars'); return
            from cryptography.hazmat.primitives import hashes as _h
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            salt = os.urandom(32)
            kdf = PBKDF2HMAC(_h.SHA256(), 32, salt, 600000)
            key = kdf.derive(pw.encode())
            nonce = os.urandom(12)
            ct = AESGCM(key).encrypt(nonce, bytes.fromhex(hk), None)
            enc = {'salt': _b64.b64encode(salt).decode(),
                   'nonce': _b64.b64encode(nonce).decode(),
                   'ct': _b64.b64encode(ct).decode()}
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(WALLET_FILE, 'w') as f:
                json.dump({'address': addr, 'encrypted': enc}, f, indent=2)
            self._wallet_data = {'address': addr, 'encrypted': enc}
            self.miner.sk = sk; self.miner.vk = vk; self.miner.address = addr
            if hasattr(self, 'address_label'):
                self.address_label.configure(text=addr, text_color='#c9d1d9')
            self._set_status('Ready ' + addr[:16], '#3fb950')
            self._log('Imported wallet: ' + addr)
            self._refresh_balance()
        except Exception as e:
            messagebox.showerror('Import failed', str(e))

    def _export_private_key(self):
        if not self.miner.sk:
            messagebox.showerror('Export', 'Unlock wallet first')
            return
        try:
            pk_hex = self.miner.sk.to_string().hex()
            self.root.clipboard_clear()
            self.root.clipboard_append(pk_hex)
            messagebox.showwarning(
                'Private Key Exported',
                'Your Private Key has been copied to your clipboard.\n\n'
                'WARNING: Never share this key with anyone!\n\n'
                f'Key Preview: {pk_hex[:8]}...{pk_hex[-8:]}'
            )
        except Exception as e:
            messagebox.showerror('Export Failed', str(e))

    def _build_ui(self):
        # Top bar
        top = ctk.CTkFrame(self.root, fg_color="#0a0a0a", height=70)
        top.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(top, text="⛏  LBTC-PRO v69",
                     font=ctk.CTkFont(size=26, weight="bold"),
                     text_color="#f7931a").pack(side="left", padx=8)
        ctk.CTkLabel(top, text="Pure Miner",
                     font=ctk.CTkFont(size=14), text_color="#8b949e"
                     ).pack(side="left", padx=(6, 0))
        self.status_label = ctk.CTkLabel(top, text="● Idle",
                                          font=ctk.CTkFont(size=14, weight="bold"),
                                          text_color="#8b949e")
        self.status_label.pack(side="right", padx=8)

        # v67: the LBTC Fair Pool banner with a quick Solo / Pool switch
        pb = ctk.CTkFrame(self.root, fg_color="#1a1206", corner_radius=12, border_width=1, border_color="#f7931a")
        pb.pack(fill="x", padx=16, pady=(0, 8))
        ptxt = ctk.CTkFrame(pb, fg_color="transparent")
        ptxt.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        ctk.CTkLabel(ptxt, text="\U0001F91D  Mine together in the LBTC Fair Pool",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#f7931a").pack(anchor="w")
        ctk.CTkLabel(ptxt, text="0% fee \u00b7 rewards split fairly by your real work \u00b7 every block and payout on-chain",
                     font=ctk.CTkFont(size=11), text_color="#c9d1d9").pack(anchor="w")
        ctk.CTkButton(pb, text="Pool stats \u2197", width=110, height=32, fg_color="#21262d", hover_color="#30363d",
                      command=lambda: webbrowser.open("https://latebitcoiners.com/pool")).pack(side="right", padx=(6, 14), pady=10)
        self._quick_mode = ctk.CTkSegmentedButton(pb, values=["\U0001F3AF Solo", "\U0001F91D Pool"],
                                                  command=self._quick_mode_changed, selected_color="#f7931a",
                                                  selected_hover_color="#ffa94d", unselected_color="#21262d",
                                                  font=ctk.CTkFont(size=12, weight="bold"))
        self._quick_mode.set("\U0001F91D Pool" if load_saved_mode() == "pool" else "\U0001F3AF Solo")
        self._quick_mode.pack(side="right", padx=6, pady=10)

        # Panel 1 — Dashboard
        dash = ctk.CTkFrame(self.root, fg_color="#111111", corner_radius=12)
        dash.pack(fill="x", padx=16, pady=8)

        stats_row = ctk.CTkFrame(dash, fg_color="transparent")
        stats_row.pack(fill="x", padx=20, pady=16)

        for key, label, color in [
            ("blocks",   "Blocks Mined", "#4fc3f7"),
            ("hashrate", "Hashrate",     "#7ee787"),
            ("uptime",   "Uptime",       "#f7931a"),
        ]:
            col = ctk.CTkFrame(stats_row, fg_color="#0a0a0a", corner_radius=10)
            col.pack(side="left", expand=True, fill="both", padx=6)
            ctk.CTkLabel(col, text=label.upper(),
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="#6e7681").pack(anchor="w", padx=16, pady=(14, 2))
            lbl = ctk.CTkLabel(col, text="—",
                                font=ctk.CTkFont(size=22, weight="bold"),
                                text_color=color)
            lbl.pack(anchor="w", padx=16, pady=(0, 14))
            setattr(self, f"stat_{key}", lbl)

        action = ctk.CTkFrame(dash, fg_color="transparent")
        action.pack(fill="x", padx=20, pady=(0, 16))
        self.start_btn = ctk.CTkButton(
            action, text="▶  START MINING",
            command=self._toggle_mining,
            fg_color="#f7931a", hover_color="#d97f0f",
            text_color="#0a0500",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=48, width=240,
        )
        self.start_btn.pack(side="left")
        ctk.CTkButton(action, text="🔓 Unlock Wallet",
                       command=self._unlock_wallet,
                       fg_color="#238636", hover_color="#2ea43b",
                       font=ctk.CTkFont(weight="bold"),
                       height=48, width=180).pack(side="left", padx=10)
        ctk.CTkButton(action, text="⚙  Settings",
                       command=self._open_settings,
                       fg_color="#21262d", hover_color="#30363d",
                       height=48, width=140).pack(side="right")

        # Panel 2 — Hardware# Wallet card
        # ─── Split row: Left = Wallet + Engine Control | Right = Log ───
        split_row = ctk.CTkFrame(self.root, fg_color="transparent")
        split_row.pack(fill="both", expand=True, padx=16, pady=8)
        split_row.grid_columnconfigure(0, weight=1, uniform="cols")
        split_row.grid_columnconfigure(1, weight=1, uniform="cols")
        split_row.grid_rowconfigure(0, weight=1)

        left_col = ctk.CTkFrame(split_row, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        right_col = ctk.CTkFrame(split_row, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        wal = ctk.CTkFrame(left_col, fg_color='#111111', corner_radius=12)
        wal.pack(fill="x", pady=(0, 6))
        r1 = ctk.CTkFrame(wal, fg_color='transparent')
        r1.pack(fill='x', padx=20, pady=(14, 4))
        ctk.CTkLabel(r1, text='WALLET', font=ctk.CTkFont(size=11, weight='bold'),
                     text_color='#f7931a').pack(side='left')
        self.balance_label = ctk.CTkLabel(r1, text='-',
            font=ctk.CTkFont(family='Consolas', size=16, weight='bold'),
            text_color='#7ee787')
        self.balance_label.pack(side='right')
        r2 = ctk.CTkFrame(wal, fg_color='transparent')
        r2.pack(fill='x', padx=20, pady=(0, 14))
        self.address_label = ctk.CTkLabel(r2, text='-',
            font=ctk.CTkFont(family='Consolas', size=12),
            text_color='#8b949e')
        self.address_label.pack(side='left')
        
        # Added Export button, adjusted widths to fit
        for _txt, _cmd, _col in [
            ('Copy',    self._copy_address,       '#21262d'),
            ('Refresh', self._refresh_balance,    '#21262d'),
            ('Export',  self._export_private_key, '#f7931a'),
            ('Import',  self._import_private_key, '#a371f7'),
            ('Clear',   self._logout_wallet,      '#da3633'),
        ]:
            ctk.CTkButton(r2, text=_txt, command=_cmd,
                          fg_color=_col, hover_color=_col,
                          height=30, width=70,
                          font=ctk.CTkFont(size=11, weight='bold')
                          ).pack(side='right', padx=(4, 0))
        # ─── Engine Control ───

        ctrl = ctk.CTkFrame(left_col, fg_color="#0d1117", corner_radius=12)
        ctrl.pack(fill="x", pady=(0, 6))

        hdr = ctk.CTkFrame(ctrl, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(14, 10))
        ctk.CTkLabel(hdr, text="\u2699  ENGINE CONTROL",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f0f6fc").pack(side="left")
        self.total_rate_label = ctk.CTkLabel(hdr, text="0.0 MH/s",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color="#6e7681")
        self.total_rate_label.pack(side="right")

        # ─── CPU card ───
        cpu_card = ctk.CTkFrame(ctrl, fg_color="#0a0a0a", corner_radius=8)
        cpu_card.pack(fill="x", padx=20, pady=(0, 6))

        cpu_top = ctk.CTkFrame(cpu_card, fg_color="transparent")
        cpu_top.pack(fill="x", padx=14, pady=(10, 2))

        self.cpu_dot = ctk.CTkLabel(cpu_top, text="\u25CF",
            font=ctk.CTkFont(size=14), text_color="#6e7681")
        self.cpu_dot.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(cpu_top, text="CPU",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#7ee787").pack(side="left")

        _cpu_name = self._get_cpu_name()
        if len(_cpu_name) > 48:
            _cpu_name = _cpu_name[:48] + "\u2026"
        ctk.CTkLabel(cpu_top, text=f"\u00b7 {_cpu_name} \u00b7 {os.cpu_count()} threads",
            font=ctk.CTkFont(size=10), text_color="#8b949e").pack(side="left", padx=(6, 0))

        self.cpu_hr_label = ctk.CTkLabel(cpu_top, text="0.0 MH/s",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#7ee787")
        self.cpu_hr_label.pack(side="right", padx=(0, 12))

        self.cpu_pct_badge = ctk.CTkLabel(cpu_top, text="100%",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#7ee787")
        self.cpu_pct_badge.pack(side="right", padx=(0, 16))

        cpu_sl = ctk.CTkFrame(cpu_card, fg_color="transparent")
        cpu_sl.pack(fill="x", padx=14, pady=(0, 12))

        self.cpu_pct_label = ctk.CTkLabel(cpu_sl, text="", width=0)
        self.cpu_slider = ctk.CTkSlider(cpu_sl, from_=0, to=100, number_of_steps=20,
                                         command=self._on_cpu_slider,
                                         progress_color="#7ee787",
                                         button_color="#7ee787",
                                         button_hover_color="#a5d6a7")
        self.cpu_slider.set(self.miner.config.get("cpu_percent", 100))
        self.cpu_slider.pack(fill="x")
        self.cpu_slider.bind("<ButtonRelease-1>", lambda e: self._save_config_debounced())

        # ─── GPU card ───
        gpu_card = ctk.CTkFrame(ctrl, fg_color="#0a0a0a", corner_radius=8)
        gpu_card.pack(fill="x", padx=20, pady=(0, 10))

        gpu_top = ctk.CTkFrame(gpu_card, fg_color="transparent")
        gpu_top.pack(fill="x", padx=14, pady=(10, 2))

        self.gpu_dot = ctk.CTkLabel(gpu_top, text="\u25CF",
            font=ctk.CTkFont(size=14), text_color="#6e7681")
        self.gpu_dot.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(gpu_top, text="GPU",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#58a6ff").pack(side="left")

        self.gpu_status_label = ctk.CTkLabel(gpu_top, text="\u00b7 detecting\u2026",
            font=ctk.CTkFont(size=10), text_color="#8b949e")
        self.gpu_status_label.pack(side="left", padx=(6, 0))

        self.gpu_rate_label = ctk.CTkLabel(gpu_top, text="0.0 MH/s",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#58a6ff")
        self.gpu_rate_label.pack(side="right", padx=(0, 12))

        self.gpu_pct_badge = ctk.CTkLabel(gpu_top, text="100%",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#58a6ff")
        self.gpu_pct_badge.pack(side="right", padx=(0, 16))

        gpu_sl = ctk.CTkFrame(gpu_card, fg_color="transparent")
        gpu_sl.pack(fill="x", padx=14, pady=(0, 12))

        self.gpu_pct_label = ctk.CTkLabel(gpu_sl, text="", width=0)
        self.gpu_slider = ctk.CTkSlider(gpu_sl, from_=0, to=100, number_of_steps=20,
                                         command=self._on_gpu_slider,
                                         progress_color="#58a6ff",
                                         button_color="#58a6ff",
                                         button_hover_color="#a5d6ff")
        self.gpu_slider.set(self.miner.config.get("gpu_percent", 100))
        self.gpu_slider.pack(fill="x")
        self.gpu_slider.bind("<ButtonRelease-1>", lambda e: self._save_config_debounced())

        def _gpu_tick():
            try:
                eng = getattr(self.miner, "gpu_engine", None) or getattr(self.miner, "_gpu_engine", None)
                if eng is not None and getattr(eng, "ready", False):
                    name = eng.name
                    if len(name) > 32:
                        name = name[:32] + "\u2026"
                    self.gpu_status_label.configure(text=f"\u00b7 {name} \u00b7 CUDA")
                elif getattr(self.miner, "_gpu_disabled", False):
                    self.gpu_status_label.configure(text="\u00b7 not available")
            except Exception:
                pass
            self.root.after(1000, _gpu_tick)
        _gpu_tick()

        # ─── Presets ───
        preset_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        preset_row.pack(fill="x", padx=20, pady=(0, 14))
        ctk.CTkLabel(preset_row, text="PRESETS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#6e7681").pack(side="left", padx=(0, 10))
        for _name, _cv, _gv, _em in [("Silent", 25, 25, "\U0001F507"),
                                       ("Balanced", 75, 75, "\u2696"),
                                       ("Max", 100, 100, "\u26A1")]:
            ctk.CTkButton(preset_row, text=f"{_em} {_name}",
                          width=100, height=28,
                          fg_color="#21262d", hover_color="#30363d",
                          font=ctk.CTkFont(size=11, weight="bold"),
                          command=lambda c=_cv, g=_gv: self._apply_preset(c, g)
                          ).pack(side="left", padx=4)

        # ─── Session Stats ───

        log_frame = ctk.CTkFrame(right_col, fg_color="#0d1117", corner_radius=12)
        log_frame.pack(fill="both", expand=True, padx=0, pady=0)

        log_hdr = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_hdr.pack(fill="x", padx=20, pady=(14, 6))
        ctk.CTkLabel(log_hdr, text="\U0001F4CB  ACTIVITY LOG",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f0f6fc").pack(side="left")

        ctk.CTkButton(log_hdr, text="Clear", width=70, height=24,
                      fg_color="#21262d", hover_color="#30363d",
                      font=ctk.CTkFont(size=10),
                      command=self._clear_log
                      ).pack(side="right", padx=(4, 0))

        self.autoscroll_var = ctk.BooleanVar(value=True)
        self.autoscroll_btn = ctk.CTkButton(log_hdr, text="\u2193 Auto",
                                             width=75, height=24,
                                             fg_color="#238636", hover_color="#2ea43b",
                                             font=ctk.CTkFont(size=10, weight="bold"),
                                             command=self._toggle_autoscroll)
        self.autoscroll_btn.pack(side="right", padx=(4, 0))

        self.log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0a0a0a", text_color="#c9d1d9",
        )
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        sess = ctk.CTkFrame(self.root, fg_color="#0d1117", corner_radius=12)
        sess.pack(fill="x", padx=16, pady=8)

        ctk.CTkLabel(sess, text="\U0001F4CA  SESSION",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#f0f6fc").pack(anchor="w", padx=20, pady=(14, 8))

        sess_row = ctk.CTkFrame(sess, fg_color="transparent")
        sess_row.pack(fill="x", padx=20, pady=(0, 14))

        def _sess_col(parent, label, color):
            col = ctk.CTkFrame(parent, fg_color="#0a0a0a", corner_radius=8)
            col.pack(side="left", expand=True, fill="both", padx=4)
            ctk.CTkLabel(col, text=label,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="#6e7681").pack(anchor="w", padx=12, pady=(10, 2))
            lbl = ctk.CTkLabel(col, text="\u2014",
                                font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                                text_color=color)
            lbl.pack(anchor="w", padx=12, pady=(0, 10))
            return lbl

        self.sess_hashes_lbl = _sess_col(sess_row, "TOTAL HASHES",  "#7ee787")
        self.sess_best_lbl   = _sess_col(sess_row, "PEAK RATE",     "#f7931a")
        self.sess_avg_lbl    = _sess_col(sess_row, "AVG RATE",      "#58a6ff")
        self.sess_eta_lbl    = _sess_col(sess_row, "NEXT BLOCK ETA", "#f0f6fc")

        # Track a rolling average
        if not hasattr(self, "_session_hist"):
            self._session_hist = []


        self._log("\u26CF  LBTC-PRO v69 Pure Miner ready")
        self._log("    Click 'Unlock Wallet' then 'Start Mining'")
        self._log("    Click 'Unlock Wallet' then 'Start Mining'")

    def _set_status(self, txt, color):
        self.root.after(0, lambda: self.status_label.configure(
            text=f"● {txt}", text_color=color))

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        if not hasattr(self, "autoscroll_var") or self.autoscroll_var.get():
            self.log_box.see("end")

    def _log_safe(self, msg):
        self.root.after(0, lambda: self._log(msg))

    def _toggle_mining(self):
        if self.miner.running:
            self.miner.stop()
            self.start_btn.configure(text="▶  START MINING", fg_color="#f7931a")
            self._set_status("Idle", "#8b949e")
        else:
            if not self.miner.sk:
                messagebox.showwarning("Wallet", "Unlock wallet first")
                return
            self.miner.start(workers=self.cfg.get("cpu_threads", 0))
            self.start_btn.configure(text="⏹  STOP MINING", fg_color="#da3633")
            self._set_status("Mining", "#f7931a")

    def _on_stats(self, data):
        def _upd():
            self.stat_blocks.configure(text=str(data['blocks']))
            hr    = data['hashrate']
            peak  = data['last_peak']
            stale = data['stale']
            cpu_r = data.get('cpu_rate', 0)
            gpu_r = data.get('gpu_rate', 0)

            if hr > 0:
                self.stat_hashrate.configure(
                    text=f"{hr/1e6:,.1f} MH/s", text_color="#7ee787")
            elif peak > 0 and stale:
                self.stat_hashrate.configure(
                    text=f"{peak/1e6:,.1f} MH/s \u00b7 cooling",
                    text_color="#8b949e")
            else:
                self.stat_hashrate.configure(
                    text="\U0001F50D Looking for block\u2026",
                    text_color="#f7931a")

            if hasattr(self, 'total_rate_label'):
                self.total_rate_label.configure(
                    text=f"{hr/1e6:,.1f} MH/s" if hr > 0 else "0.0 MH/s",
                    text_color="#7ee787" if hr > 0 else "#6e7681")

            # Session stats
            try:
                if hasattr(self, 'sess_hashes_lbl'):
                    th = data.get('total_hashes', 0)
                    if th >= 1e9:
                        self.sess_hashes_lbl.configure(text=f"{th/1e9:.2f} G")
                    elif th >= 1e6:
                        self.sess_hashes_lbl.configure(text=f"{th/1e6:.2f} M")
                    else:
                        self.sess_hashes_lbl.configure(text=f"{th:,}")

                    best = data.get('best_rate', 0)
                    self.sess_best_lbl.configure(
                        text=f"{best/1e6:.1f} MH/s" if best > 0 else "0.0 MH/s")

                    # Avg = total_hashes / elapsed
                    elapsed = data.get('elapsed', 0)
                    if elapsed > 0 and th > 0:
                        avg = th / elapsed
                        self.sess_avg_lbl.configure(text=f"{avg/1e6:.1f} MH/s")
                        self._session_hist.append(avg)
                        if len(self._session_hist) > 30:
                            self._session_hist = self._session_hist[-30:]

                    # ETA: difficulty target vs current rate
                    # Assume ~180s block target; rough estimate
                    if hr > 0:
                        # Difficulty bits: how many leading zeros approx.
                        # For 2^240 target: ~1 in 2^16 nonces wins
                        # At 30 MH/s: ~50ms per find if target is easy
                        # We use empirical: current best hashes-per-second
                        # Rough: assume 1 in N hashes wins, N = 2^(256-240) = 65536
                        # time_to_find = N / hr
                        expected = 65536 / hr if hr > 0 else 0
                        if expected < 60:
                            self.sess_eta_lbl.configure(text=f"~{expected:.0f}s")
                        elif expected < 3600:
                            self.sess_eta_lbl.configure(text=f"~{expected/60:.1f}m")
                        else:
                            self.sess_eta_lbl.configure(text=">1h")
                    else:
                        self.sess_eta_lbl.configure(text="\u2014")
            except Exception:
                pass

            if cpu_r > 0:
                self.cpu_hr_label.configure(
                    text=f"{cpu_r/1e6:,.1f} MH/s", text_color="#7ee787")
                if hasattr(self, 'cpu_dot'):
                    self.cpu_dot.configure(text_color="#7ee787")
            else:
                self.cpu_hr_label.configure(
                    text="0.0 MH/s", text_color="#6e7681")
                if hasattr(self, 'cpu_dot'):
                    self.cpu_dot.configure(text_color="#6e7681")

            if gpu_r > 0:
                self.gpu_rate_label.configure(
                    text=f"{gpu_r/1e6:,.1f} MH/s", text_color="#58a6ff")
                if hasattr(self, 'gpu_dot'):
                    self.gpu_dot.configure(text_color="#58a6ff")
            elif getattr(self.miner, '_gpu_disabled', False):
                self.gpu_rate_label.configure(text="\u2014", text_color="#6e7681")
                if hasattr(self, 'gpu_dot'):
                    self.gpu_dot.configure(text_color="#6e7681")
            else:
                self.gpu_rate_label.configure(text="0.0 MH/s", text_color="#6e7681")
                if hasattr(self, 'gpu_dot'):
                    self.gpu_dot.configure(text_color="#6e7681")
        self.root.after(0, _upd)



    def _on_cpu_slider(self, value):
        v = int(round(value / 5) * 5)
        if hasattr(self, 'cpu_pct_label'):
            self.cpu_pct_label.configure(text=f'{v}%')
        if hasattr(self, 'cpu_pct_badge'):
            self.cpu_pct_badge.configure(text=f'{v}%')
        self.miner.config['cpu_percent'] = v

    def _on_gpu_slider(self, value):
        v = int(round(value / 5) * 5)
        if hasattr(self, 'gpu_pct_label'):
            self.gpu_pct_label.configure(text=f'{v}%')
        if hasattr(self, 'gpu_pct_badge'):
            self.gpu_pct_badge.configure(text=f'{v}%')
        self.miner.config['gpu_percent'] = v

    def _save_config_debounced(self):
        if hasattr(self, '_save_timer') and self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.5, miner_config.save, args=(self.miner.config,))
        self._save_timer.daemon = True
        self._save_timer.start()

    def _apply_preset(self, cpu_v, gpu_v):
        self.cpu_slider.set(cpu_v)
        self.gpu_slider.set(gpu_v)
        self._on_cpu_slider(cpu_v)
        self._on_gpu_slider(gpu_v)
        miner_config.save(self.miner.config)
        self._log(f'[config] preset: CPU {cpu_v}% / GPU {gpu_v}%')

    def _clear_log(self):
        try:
            self.log_box.delete('1.0', 'end')
        except Exception:
            pass

    def _toggle_autoscroll(self):
        self.autoscroll_var.set(not self.autoscroll_var.get())
        if self.autoscroll_var.get():
            self.autoscroll_btn.configure(text='Auto', fg_color='#238636')
            self.log_box.see('end')
        else:
            self.autoscroll_btn.configure(text='Manual', fg_color='#6e7681')

    def _get_cpu_name(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0")
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if name and name.strip():
                return name.strip()
        except Exception:
            pass
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        try:
            import subprocess
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL, timeout=2)
            return out.decode().strip()
        except Exception:
            pass
        try:
            import platform
            p = platform.processor()
            if p and "Family" not in p:
                return p
        except Exception:
            pass
        return "CPU"

    def _tick_uptime(self):
        if self.miner.running:
            up = time.time() - self.miner.start_time
            h, m, s = int(up//3600), int((up%3600)//60), int(up%60)
            self.stat_uptime.configure(text=f"{h:02d}:{m:02d}:{s:02d}")
        if not hasattr(self, "_last_bal"):
            self._last_bal = 0
        if self.miner.address and (time.time() - self._last_bal) >= 30:
            self._last_bal = time.time()
            self._refresh_balance()
        self.root.after(1000, self._tick_uptime)

    def _open_settings(self):
        """Settings modal — Mining Mode + Local Node controls."""
        win = ctk.CTkToplevel(self.root)
        win.title("Settings")
        win.geometry("640x680")
        win.configure(fg_color="#0d1117")
        win.grab_set()

        ctk.CTkLabel(win, text="\u2699  Settings",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#f7931a").pack(anchor="w", padx=24, pady=(20, 12))

        # ─── Mining Mode ───
        ctk.CTkLabel(win, text="\u26CF  MINING MODE",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#58a6ff").pack(anchor="w", padx=24, pady=(4, 4))

        mode_card = ctk.CTkFrame(win, fg_color="#0a0a0a", corner_radius=10)
        mode_card.pack(fill="x", padx=24, pady=(0, 12))

        ctk.CTkLabel(mode_card, text="Choose how to mine. Takes effect next Start Mining.",
                     font=ctk.CTkFont(size=11), text_color="#8b949e",
                     justify="left", wraplength=560).pack(anchor="w", padx=14, pady=(12, 6))

        self._mode_select = ctk.CTkOptionMenu(
            mode_card,
            values=[
                "\U0001F3AF Solo (50 LBTC per block)",
                "\U0001F91D Pool (steady income)",
                "\U0001F5A5 Local Node (127.0.0.1)",
                "\U0001F310 Custom URL",
            ],
            width=400,
            command=self._on_mode_changed,
        )
        current_mode = load_saved_mode()
        if current_mode == "pool":
            self._mode_select.set("\U0001F91D Pool (steady income)")
        elif current_mode == "local":
            self._mode_select.set("\U0001F5A5 Local Node (127.0.0.1)")
        elif current_mode == "custom":
            self._mode_select.set("\U0001F310 Custom URL")
        else:
            self._mode_select.set("\U0001F3AF Solo (50 LBTC per block)")
        self._mode_select.pack(anchor="w", padx=14, pady=(0, 8))

        self._mode_help = ctk.CTkLabel(mode_card, text="", text_color="#c9d1d9",
                                        font=ctk.CTkFont(size=11), justify="left",
                                        wraplength=560, anchor="w")
        self._mode_help.pack(fill="x", padx=14, pady=(0, 12))
        self._refresh_mode_help()

        # ─── Node URL ───
        url_row = ctk.CTkFrame(win, fg_color="transparent")
        url_row.pack(fill="x", padx=24, pady=(0, 12))
        ctk.CTkLabel(url_row, text="Node URL:", width=80, anchor="w",
                     font=ctk.CTkFont(size=11), text_color="#8b949e").pack(side="left")
        self._node_entry = ctk.CTkEntry(url_row, width=380)
        self._node_entry.insert(0, self.miner.node_url or SOLO_NODE_URL)
        self._node_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(url_row, text="Test", width=70, height=28,
                      fg_color="#21262d", hover_color="#30363d",
                      command=self._test_node_url).pack(side="left")

        # ─── Local Node ───
        ctk.CTkLabel(win, text="\U0001F5A5  LOCAL NODE",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#3fb950").pack(anchor="w", padx=24, pady=(8, 4))

        node_card = ctk.CTkFrame(win, fg_color="#0a0a0a", corner_radius=10)
        node_card.pack(fill="x", padx=24, pady=(0, 12))
        ctk.CTkLabel(node_card,
                     text="Run your own full LBTC node locally. 0ms latency \u2014 wins races.",
                     font=ctk.CTkFont(size=11), text_color="#8b949e",
                     justify="left", wraplength=560).pack(anchor="w", padx=14, pady=(12, 8))

        node_btn_row = ctk.CTkFrame(node_card, fg_color="transparent")
        node_btn_row.pack(fill="x", padx=14, pady=(0, 12))
        self._node_btn = ctk.CTkButton(node_btn_row, text="\u25B6 Start Local Node",
                                        command=self._toggle_local_node,
                                        fg_color="#238636", hover_color="#2ea43b",
                                        font=ctk.CTkFont(weight="bold"),
                                        width=200, height=40)
        self._node_btn.pack(side="left")
        self._node_status = ctk.CTkLabel(node_btn_row, text="Node not running",
                                          text_color="#8b949e",
                                          font=ctk.CTkFont(size=12))
        self._node_status.pack(side="left", padx=14)

        ctk.CTkButton(win, text="Close", width=120, height=36,
                      fg_color="#21262d", hover_color="#30363d",
                      command=win.destroy).pack(pady=(4, 18))

    def _refresh_mode_help(self):
        if not hasattr(self, '_mode_help'):
            return
        mode = load_saved_mode()
        texts = {
            "pool":  "Pool mode: latebitcoiners.com pool, 0% fee, steady payouts.",
            "local": "Local Node: your own 127.0.0.1:8080 node \u2014 0ms latency.",
            "custom":"Custom URL: any node you trust.",
            "solo":  "Solo mode: main node, 50 LBTC per block, high variance.",
        }
        self._mode_help.configure(text=texts.get(mode, texts["solo"]))

    def _quick_mode_changed(self, value):
        """v67: the banner switch: Solo or Pool, saved like Settings and applied at once."""
        mode = "pool" if "Pool" in value else "solo"
        save_mode(mode)
        url = get_mode_url(mode)
        self.miner.set_node(url)
        try:
            self.cfg["node_url"] = url; self._save_config()      # v69: settings and mode always agree
        except Exception:
            pass
        try:
            if hasattr(self, '_node_entry') and self._node_entry.winfo_exists():
                self._node_entry.delete(0, "end"); self._node_entry.insert(0, url)
            if hasattr(self, '_mode_select') and self._mode_select.winfo_exists():
                self._mode_select.set("\U0001F91D Pool (steady income)" if mode == "pool" else "\U0001F3AF Solo (50 LBTC per block)")
            self._refresh_mode_help()
        except Exception:
            pass
        msg = ("\U0001F91D Pool: mining together in the LBTC Fair Pool" if mode == "pool" else "\U0001F3AF Solo: mining on your own")
        if getattr(self.miner, 'running', False):
            msg += " (switching now)"
        self._log("[mode] " + msg)

    def _on_mode_changed(self, value):
        if "Pool" in value:
            mode = "pool"
        elif "Local" in value:
            mode = "local"
        elif "Custom" in value:
            mode = "custom"
        else:
            mode = "solo"
        save_mode(mode)
        url = get_mode_url(mode)
        try:
            if hasattr(self, '_quick_mode'):
                self._quick_mode.set("\U0001F91D Pool" if mode == "pool" else "\U0001F3AF Solo")   # v67: keep the banner in step
        except Exception:
            pass
        if url:
            self._node_entry.delete(0, "end")
            self._node_entry.insert(0, url)
            self.miner.set_node(url)
            try:
                self.cfg["node_url"] = url; self._save_config()  # v69: settings and mode always agree
            except Exception:
                pass
        self._refresh_mode_help()
        self._log(f"[settings] mode = {mode}")

    def _test_node_url(self):
        url = self._node_entry.get().strip().rstrip("/")
        if not url:
            return
        try:
            r = requests.get(f"{url}/api/chain/tip", timeout=5)
            if r.status_code == 200:
                tip = r.json()
                self._log(f"Node OK \u2014 height {tip.get('height','?')}")
                self.miner.set_node(url)
            else:
                self._log(f"Node error: HTTP {r.status_code}")
        except Exception as e:
            self._log(f"Node unreachable: {type(e).__name__}: {e}")

    def _toggle_local_node(self):
        if getattr(self, "_node_proc", None) and self._node_proc.poll() is None:
            try:
                self._node_proc.terminate()
            except Exception:
                pass
            self._node_proc = None
            if hasattr(self, "_node_btn"):
                self._node_btn.configure(text="\u25B6 Start Local Node", fg_color="#238636")
                self._node_status.configure(text="Node stopped", text_color="#8b949e")
            return
        node_py = None
        for _d in (_BASE_DIR, os.path.dirname(os.path.abspath(sys.executable)), _USER_DIR, os.path.join(_USER_DIR, "node")):
            if os.path.exists(os.path.join(_d, "node.py")):
                node_py = os.path.join(_d, "node.py"); break
        if not node_py:
            self._log("node.py not found \u2014 download from /download/node_bundle.zip")
            return
        import subprocess, secrets, sys as _sys
        env = os.environ.copy()
        try:
            env["LBTC_API_SECRET"] = secrets.token_hex(32)
        except Exception:
            pass
        try:
            creationflags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
            log_path = os.path.join(_USER_DIR, "node_launch.log")
            py = _sys.executable
            if getattr(_sys, "frozen", False):
                import shutil as _sh
                py = _sh.which("python3") or _sh.which("python")
                if not py:
                    self._log("Local node needs Python 3 installed (python3 not found)")
                    return
            self._node_log_fp = open(log_path, "wb")
            self._node_proc = subprocess.Popen(
                [py, node_py], env=env, cwd=os.path.dirname(node_py),
                stdout=self._node_log_fp, stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            if hasattr(self, "_node_btn"):
                self._node_btn.configure(text="\u23F9 Stop Local Node", fg_color="#da3633")
                self._node_status.configure(text="Starting...", text_color="#f7931a")
            self._log("Local node starting...")
            self.root.after(4000, self._check_node_ready)
        except Exception as e:
            self._log(f"Node launch failed: {type(e).__name__}: {e}")

    def _check_node_ready(self, attempt=1):
        if not getattr(self, "_node_proc", None) or self._node_proc.poll() is not None:
            if hasattr(self, "_node_status"):
                self._node_status.configure(text="Node crashed", text_color="#da3633")
            return
        try:
            r = requests.get("http://127.0.0.1:8080/api/chain/tip", timeout=3)
            if r.status_code == 200:
                self._node_status.configure(text="Node ready \u2014 127.0.0.1:8080",
                                            text_color="#3fb950")
                self.miner.set_node("http://127.0.0.1:8080")
                if hasattr(self, "_node_entry"):
                    self._node_entry.delete(0, "end")
                    self._node_entry.insert(0, "http://127.0.0.1:8080")
                self._log("Local node ready")
                return
        except Exception:
            pass
        if attempt < 15:
            self.root.after(3000, lambda: self._check_node_ready(attempt + 1))
        else:
            if hasattr(self, "_node_status"):
                self._node_status.configure(text="Slow start \u2014 check node_launch.log",
                                            text_color="#f7931a")


    def run(self):
        self.root.mainloop()


def _selftest():
    """--selftest: checks the CPU library and the GPU engines without a wallet or network. Prints PASS/FAIL."""
    import platform
    print(f"LBTC-PRO v{MINER_VERSION} self-test - {platform.system()} {platform.machine()} - Python {platform.python_version()}")
    ok = True
    lib, path = load_native()
    if not lib:
        print("CPU native library: FAIL (not found)"); ok = False
    else:
        U8, U64 = ctypes.c_ubyte, ctypes.c_uint64
        lib.lbtc_scan_threads.argtypes = [ctypes.POINTER(U8), ctypes.c_size_t, ctypes.POINTER(U8), ctypes.c_size_t,
            U64, ctypes.POINTER(U8), ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(U64),
            ctypes.POINTER(U8), ctypes.POINTER(U64)]
        lib.lbtc_scan_threads.restype = ctypes.c_int
        head, tail = b'{"selftest":"lbtc","work_nonce":', b'}'
        target = bytes([0, 0x0f]) + b'\xff' * 30
        stop, fn, tot = ctypes.c_int(0), U64(0), U64(0)
        fh = (U8 * 32)()
        t0 = time.time()
        r = lib.lbtc_scan_threads((U8 * len(head)).from_buffer_copy(head), len(head), (U8 * len(tail)).from_buffer_copy(tail),
                                  len(tail), 0, (U8 * 32).from_buffer_copy(target), max(1, os.cpu_count() or 1),
                                  ctypes.byref(stop), ctypes.byref(fn), fh, ctypes.byref(tot))
        dt = max(time.time() - t0, 1e-6)
        good = r == 1 and hashlib.sha256(head + str(fn.value).encode() + tail).digest() == bytes(fh) and bytes(fh) < target
        print(f"CPU native library: {'PASS' if good else 'FAIL'} ({os.path.basename(path)}, {tot.value/dt/1e6:.2f} MH/s)")
        ok = ok and good
    gpu = "none (CPU mining only - this is fine)"
    try:
        import fast_engine
        e = fast_engine.FastCLMiner(log=lambda *a: None)
        if e.ready: gpu = f"PASS - {e.name} (OpenCL)"
    except Exception as ex:
        gpu = f"none ({type(ex).__name__})"
    print(f"GPU: {gpu}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    app = V60App()
    app.run()