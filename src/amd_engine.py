"""
amd_engine.py — OpenCL SHA-256 miner for AMD (and other OpenCL) GPUs.  (v62)

v61 shipped a placeholder here that did no hashing at all and reported a made-up
hashrate. This is a real engine with the same streaming kernel as the CUDA one.
"""
import time
import numpy as np

try:
    import pyopencl as cl
    _CL_OK = True
    _CL_ERR = None
except Exception as _e:  # pyopencl missing
    cl = None
    _CL_OK = False
    _CL_ERR = f"{type(_e).__name__}: {_e}"

_KERNEL_SRC = r"""
__constant uint K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

#define ROTR(x,n) rotate((uint)(x), (uint)(32 - (n)))

void sha256_compress(uint *state, const uchar *block) {
    uint w[64];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint)block[i*4] << 24) | ((uint)block[i*4+1] << 16) | ((uint)block[i*4+2] << 8) | (uint)block[i*4+3];
    for (int i = 16; i < 64; i++) {
        uint s0 = ROTR(w[i-15], 7) ^ ROTR(w[i-15], 18) ^ (w[i-15] >> 3);
        uint s1 = ROTR(w[i-2], 17) ^ ROTR(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint a = state[0], b = state[1], c = state[2], d = state[3];
    uint e = state[4], f = state[5], g = state[6], h = state[7];
    for (int i = 0; i < 64; i++) {
        uint S1 = ROTR(e, 6) ^ ROTR(e, 11) ^ ROTR(e, 25);
        uint ch = (e & f) ^ ((~e) & g);
        uint t1 = h + S1 + ch + K[i] + w[i];
        uint S0 = ROTR(a, 2) ^ ROTR(a, 13) ^ ROTR(a, 22);
        uint maj = (a & b) ^ (a & c) ^ (b & c);
        uint t2 = S0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

#define PUSH(byte) { blk[bp++] = (uchar)(byte); if (bp == 64) { sha256_compress(state, blk); bp = 0; } }

__kernel void mine_kernel(
    __global const uchar* hp, int hp_len, int prefix_len,
    __global const uint* midstate,
    __global const uchar* tail, int tail_len,
    __global const uchar* target,
    ulong base_nonce,
    __global ulong* out_nonce, __global uchar* out_hash, __global volatile uint* out_found)
{
    ulong nonce = base_nonce + (ulong)get_global_id(0);
    uint state[8];
    for (int i = 0; i < 8; i++) state[i] = midstate[i];
    uchar blk[64];
    int bp = 0;
    for (int i = 0; i < hp_len; i++) PUSH(hp[i]);
    uchar tmp[24]; int tl = 0;
    ulong n = nonce;
    if (n == 0) tmp[tl++] = '0';
    else { while (n > 0) { tmp[tl++] = (uchar)('0' + (n % 10)); n /= 10; } }
    for (int i = tl - 1; i >= 0; i--) PUSH(tmp[i]);
    for (int i = 0; i < tail_len; i++) PUSH(tail[i]);
    ulong total_bits = (ulong)(prefix_len + hp_len + tl + tail_len) * 8;
    PUSH(0x80);
    while (bp != 56) PUSH(0);
    for (int i = 7; i >= 0; i--) PUSH((total_bits >> (i * 8)) & 0xff);

    uchar out[32];
    for (int i = 0; i < 8; i++) {
        out[i*4] = (state[i] >> 24) & 0xff; out[i*4+1] = (state[i] >> 16) & 0xff;
        out[i*4+2] = (state[i] >> 8) & 0xff; out[i*4+3] = state[i] & 0xff;
    }
    int less = 1;
    for (int i = 0; i < 32; i++) {
        if (out[i] < target[i]) break;
        if (out[i] > target[i]) { less = 0; break; }
    }
    if (less && atomic_cmpxchg(out_found, 0u, 1u) == 0u) {
        *out_nonce = nonce;
        for (int i = 0; i < 32; i++) out_hash[i] = out[i];
    }
}
"""

_IV = np.array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19], dtype=np.uint32)
TAIL_MAX = 262144
WORK_PER_LAUNCH = 8 * 1024 * 1024


def _py_midstate(head):
    """SHA-256 state after the full 64-byte blocks of `head` (pure Python, run once per job)."""
    import struct
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
    st = [int(x) for x in _IV]
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
    return np.array(st, dtype=np.uint32)


class AMDMiner:
    def __init__(self, log_func=print, prefer=("AMD", "ADVANCED MICRO")):
        self.log = log_func
        self.ready = False
        self.name = "OpenCL GPU"
        self.last_rate_mhs = 0.0
        self._prefix_len = 0
        self._midstate = _IV.copy()
        if not _CL_OK:
            self.log(f"[gpu] pyopencl unavailable: {_CL_ERR}")
            return
        try:
            dev = None
            for p in cl.get_platforms():
                if any(k in p.name.upper() for k in prefer):
                    gpus = p.get_devices(cl.device_type.GPU)
                    if gpus:
                        dev = gpus[0]
                        break
            if dev is None:
                self.log("[gpu] No AMD OpenCL GPU found.")
                return
            self.device = dev
            self.name = dev.name.strip()
            self.ctx = cl.Context([dev])
            self.queue = cl.CommandQueue(self.ctx)
            self.prg = cl.Program(self.ctx, _KERNEL_SRC).build()
            self.kernel = cl.Kernel(self.prg, "mine_kernel")
            mf = cl.mem_flags
            self.b_head = cl.Buffer(self.ctx, mf.READ_ONLY, 4096)
            self.b_tail = cl.Buffer(self.ctx, mf.READ_ONLY, TAIL_MAX)
            self.b_target = cl.Buffer(self.ctx, mf.READ_ONLY, 32)
            self.b_mid = cl.Buffer(self.ctx, mf.READ_ONLY, 32)
            self.b_nonce = cl.Buffer(self.ctx, mf.READ_WRITE, 8)
            self.b_hash = cl.Buffer(self.ctx, mf.READ_WRITE, 32)
            self.b_found = cl.Buffer(self.ctx, mf.READ_WRITE, 4)
            self.ready = True
            self.log(f"[gpu] OpenCL ready: {self.name}")
        except Exception as e:
            self.log(f"[gpu] OpenCL setup failed: {type(e).__name__}: {str(e)[:200]}")
            self.ready = False

    def duty_cycle_sleep(self, last_batch_seconds, target_percent):
        if target_percent >= 100 or target_percent <= 0 or last_batch_seconds <= 0:
            return
        time.sleep(min(last_batch_seconds * (100.0 - target_percent) / target_percent, 0.25))

    def prepare_midstate(self, head):
        n_full = len(head) // 64
        if not self.ready or n_full == 0:
            self._prefix_len = 0
            self._midstate = _IV.copy()
            return None, head
        self._midstate = _py_midstate(head)
        self._prefix_len = n_full * 64
        return True, head[n_full * 64:]

    def mine_batch_midstate(self, midstate, head_partial, tail, target, base_nonce, threads=256, blocks=4096):
        if not self.ready:
            return None, 0, 0, None
        hp = np.frombuffer(head_partial, dtype=np.uint8).copy()
        tl = np.frombuffer(tail, dtype=np.uint8).copy()
        tg = np.frombuffer(target, dtype=np.uint8).copy()
        if hp.nbytes > 4000 or tl.nbytes > TAIL_MAX - 64:
            return None, 0, 0, None
        n_comp = (hp.nbytes + 20 + tl.nbytes + 9) // 64 + 1
        blocks = max(16, min(int(blocks), WORK_PER_LAUNCH // (int(threads) * n_comp)))
        total = int(threads) * blocks
        try:
            q = self.queue
            cl.enqueue_copy(q, self.b_head, hp if hp.nbytes else np.zeros(1, np.uint8))
            if tl.nbytes:
                cl.enqueue_copy(q, self.b_tail, tl)
            cl.enqueue_copy(q, self.b_target, tg)
            cl.enqueue_copy(q, self.b_mid, self._midstate if midstate is not None else _IV)
            cl.enqueue_copy(q, self.b_found, np.zeros(1, np.uint32))
            t0 = time.time()
            self.kernel(q, (total,), (int(threads),),
                        self.b_head, np.int32(hp.nbytes), np.int32(self._prefix_len if midstate is not None else 0),
                        self.b_mid, self.b_tail, np.int32(tl.nbytes), self.b_target, np.uint64(base_nonce),
                        self.b_nonce, self.b_hash, self.b_found)
            q.finish()
            dt = time.time() - t0
            if dt > 0:
                self.last_rate_mhs = total / dt / 1e6
            flag = np.zeros(1, np.uint32)
            cl.enqueue_copy(q, flag, self.b_found)
            q.finish()
            if flag[0] == 1:
                nn = np.zeros(1, np.uint64); hh = np.zeros(32, np.uint8)
                cl.enqueue_copy(q, nn, self.b_nonce); cl.enqueue_copy(q, hh, self.b_hash); q.finish()
                return int(nn[0]), total, dt, bytes(hh)
            return None, total, dt, None
        except Exception as e:
            self.log(f"[gpu] OpenCL mine: {type(e).__name__}: {str(e)[:120]}")
            self.ready = False
            raise

    def mine_batch(self, head, tail, target, base_nonce, threads=256, blocks=4096):
        self._prefix_len = 0
        return self.mine_batch_midstate(None, head, tail, target, base_nonce, threads, blocks)
