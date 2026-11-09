"""
gpu_engine.py — CUDA C kernel via cuda-python + NVRTC.  (v62)
v62: the kernel streams the block through SHA-256 64 bytes at a time, so blocks of
any size work (v60/v61 crashed with CUDA_ERROR_ILLEGAL_ADDRESS on blocks > 2 KB),
work per launch is sized to the block, and a GPU error resets the device so the
miner can recover instead of logging the same error forever.
"""
import ctypes
import time
import numpy as np

try:
    from cuda.bindings import driver as cu
    from cuda.bindings import nvrtc
    _CUDA_OK = True
    _CUDA_ERR = None
except Exception as _e:
    _CUDA_OK = False
    _CUDA_ERR = f"{type(_e).__name__}: {_e}"

# ═══════════════════════════════════════════════════════════════════
# CUDA kernel source (runtime-compiled by NVRTC)
# ═══════════════════════════════════════════════════════════════════
_KERNEL_SRC = r"""
typedef unsigned int       uint32_t;
typedef unsigned long long uint64_t;
typedef unsigned char      uint8_t;

__device__ __constant__ uint32_t K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

__device__ __forceinline__ uint32_t rotr(uint32_t x, int n) {
    return (x >> n) | (x << (32 - n));
}

__device__ __forceinline__ uint32_t be32(const uint8_t* p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

__device__ void sha256_compress(uint32_t state[8], const uint8_t block[64]) {
    uint32_t w[64];
    #pragma unroll
    for (int i = 0; i < 16; i++) w[i] = be32(&block[i*4]);
    #pragma unroll
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
        uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
    #pragma unroll
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t t1 = h + S1 + ch + K[i] + w[i];
        uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = S0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

extern "C" __global__ void midstate_kernel(const uint8_t* head, int n_full, uint32_t* out_state) {
    if (threadIdx.x != 0 || blockIdx.x != 0) return;
    uint32_t state[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                          0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19 };
    for (int blk = 0; blk < n_full; blk++) {
        sha256_compress(state, &head[blk * 64]);
    }
    for (int i = 0; i < 8; i++) out_state[i] = state[i];
}

__device__ __forceinline__ void sha_push(uint32_t st[8], uint8_t blk[64], int &bp, uint8_t b) {
    blk[bp++] = b;
    if (bp == 64) { sha256_compress(st, blk); bp = 0; }
}

// v62: streams the message through a 64-byte block, so any block size works
// (v60/v61 copied the whole block into a 2 KB buffer and crashed on big blocks).
extern "C" __global__ void mine_kernel(
    const uint8_t* hp, int hp_len,
    int prefix_len,
    const uint32_t* midstate,
    const uint8_t* tail, int tail_len,
    const uint8_t* target,
    uint64_t base_nonce,
    uint64_t* out_nonce, uint8_t* out_hash, uint32_t* out_found
) {
    uint64_t tid = (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;
    uint64_t nonce = base_nonce + tid;

    uint32_t state[8];
    #pragma unroll
    for (int i = 0; i < 8; i++) state[i] = midstate[i];
    uint8_t blk[64];
    int bp = 0;

    for (int i = 0; i < hp_len; i++) sha_push(state, blk, bp, hp[i]);
    char tmp[24]; int tl = 0;
    uint64_t n = nonce;
    if (n == 0) tmp[tl++] = '0';
    else { while (n > 0) { tmp[tl++] = '0' + (n % 10); n /= 10; } }
    for (int i = tl - 1; i >= 0; i--) sha_push(state, blk, bp, (uint8_t)tmp[i]);
    for (int i = 0; i < tail_len; i++) sha_push(state, blk, bp, tail[i]);

    uint64_t total_bits = (uint64_t)(prefix_len + hp_len + tl + tail_len) * 8;
    sha_push(state, blk, bp, 0x80);
    while (bp != 56) sha_push(state, blk, bp, 0);
    for (int i = 7; i >= 0; i--) sha_push(state, blk, bp, (uint8_t)((total_bits >> (i * 8)) & 0xff));

    uint8_t out[32];
    #pragma unroll
    for (int i = 0; i < 8; i++) {
        out[i*4]   = (state[i] >> 24) & 0xff;
        out[i*4+1] = (state[i] >> 16) & 0xff;
        out[i*4+2] = (state[i] >> 8) & 0xff;
        out[i*4+3] = state[i] & 0xff;
    }

    int less = 1;
    for (int i = 0; i < 32; i++) {
        if (out[i] < target[i]) break;
        if (out[i] > target[i]) { less = 0; break; }
    }
    if (less && atomicCAS(out_found, 0u, 1u) == 0u) {
        *out_nonce = nonce;
        for (int i = 0; i < 32; i++) out_hash[i] = out[i];
    }
}
"""


def _nvrtc_compile(src: str, arch_sm: str):
    """Compile source to PTX using NVRTC. Returns PTX bytes."""
    err, prog = nvrtc.nvrtcCreateProgram(src.encode(), b"mine.cu", 0, [], [])
    if err != nvrtc.nvrtcResult.NVRTC_SUCCESS:
        raise RuntimeError(f"nvrtcCreateProgram: {err}")
    opts = [f"--gpu-architecture={arch_sm}".encode(), b"--std=c++14"]
    err, = nvrtc.nvrtcCompileProgram(prog, len(opts), opts)
    if err != nvrtc.nvrtcResult.NVRTC_SUCCESS:
        err, log_size = nvrtc.nvrtcGetProgramLogSize(prog)
        log = b""
        if err == nvrtc.nvrtcResult.NVRTC_SUCCESS and log_size > 1:
            buf = b" " * log_size
            nvrtc.nvrtcGetProgramLog(prog, buf)
            log = bytes(buf).decode(errors="replace")
        raise RuntimeError(f"nvrtcCompileProgram failed: {err}\n{log}")
    err, ptx_size = nvrtc.nvrtcGetPTXSize(prog)
    if err != nvrtc.nvrtcResult.NVRTC_SUCCESS:
        raise RuntimeError(f"nvrtcGetPTXSize: {err}")
    ptx = b" " * ptx_size
    err, = nvrtc.nvrtcGetPTX(prog, ptx)
    if err != nvrtc.nvrtcResult.NVRTC_SUCCESS:
        raise RuntimeError(f"nvrtcGetPTX: {err}")
    nvrtc.nvrtcDestroyProgram(prog)
    return bytes(ptx)


def detect_cuda():
    if not _CUDA_OK:
        return False, 0, [_CUDA_ERR or "cuda-python not loaded"]
    try:
        err, = cu.cuInit(0)
        if err != cu.CUresult.CUDA_SUCCESS:
            return False, 0, [str(err)]
        err, count = cu.cuDeviceGetCount()
        if err != cu.CUresult.CUDA_SUCCESS:
            return False, 0, [str(err)]
        return True, int(count), ["cuda"]
    except Exception as e:
        return False, 0, [f"{type(e).__name__}: {e}"]


TAIL_MAX = 262144            # largest block tail the GPU accepts (256 KB)
WORK_PER_LAUNCH = 8 * 1024 * 1024   # SHA-256 compressions per kernel launch (keeps launches short)


class GpuEngine:
    def __init__(self, device_id=0, log=print):
        self.log = log
        self.ready = False
        self.cumulative = 0
        self.last_rate_mhs = 0.0
        self._prefix_len = 0
        self.name = "cuda-python"
        self._ctx = None
        self._module = None
        self._fn_mid = None
        self._fn_mine = None
        self._buf_head = None
        self._buf_tail = None
        self._buf_target = None
        self._buf_midstate = None
        self._buf_found_nonce = None
        self._buf_found_hash = None
        self._buf_found_flag = None

        if not _CUDA_OK:
            self.log(f"[gpu] cuda-python unavailable: {_CUDA_ERR}")
            return
        try:
            err, = cu.cuInit(0)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"cuInit: {err}")
            err, self._dev = cu.cuDeviceGet(device_id)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"cuDeviceGet: {err}")
            try:
                cu.cuDevicePrimaryCtxReset(self._dev)   # v62: start from a clean context
            except Exception:
                pass
            err, self._ctx = cu.cuDevicePrimaryCtxRetain(self._dev)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"cuDevicePrimaryCtxRetain: {err}")
            err, = cu.cuCtxSetCurrent(self._ctx)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"cuCtxSetCurrent: {err}")
            # Query compute capability for arch string
            err, major = cu.cuDeviceGetAttribute(
                cu.CUdevice_attribute.CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR,
                self._dev)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"attr major: {err}")
            err, minor = cu.cuDeviceGetAttribute(
                cu.CUdevice_attribute.CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR,
                self._dev)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"attr minor: {err}")
            arch = f"sm_{int(major)}{int(minor)}"
            # Compile kernel with NVRTC
            ptx = _nvrtc_compile(_KERNEL_SRC, arch)
            # Load module
            err, self._module = cu.cuModuleLoadData(ptx)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"cuModuleLoadData: {err}")
            err, self._fn_mid = cu.cuModuleGetFunction(self._module, b"midstate_kernel")
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"get midstate_kernel: {err}")
            err, self._fn_mine = cu.cuModuleGetFunction(self._module, b"mine_kernel")
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"get mine_kernel: {err}")
            # Pre-allocate device buffers (ONE TIME)
            err, self._buf_head = cu.cuMemAlloc(4096)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc head: {err}")
            err, self._buf_tail = cu.cuMemAlloc(TAIL_MAX)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc tail: {err}")
            err, self._buf_target = cu.cuMemAlloc(32)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc target: {err}")
            err, self._buf_midstate = cu.cuMemAlloc(32)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc midstate: {err}")
            err, self._buf_found_nonce = cu.cuMemAlloc(8)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc fn: {err}")
            err, self._buf_found_hash = cu.cuMemAlloc(32)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc fh: {err}")
            err, self._buf_found_flag = cu.cuMemAlloc(4)
            if err != cu.CUresult.CUDA_SUCCESS: raise RuntimeError(f"alloc ff: {err}")
            # Default midstate = SHA-256 IV
            iv = np.array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                           0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19], dtype=np.uint32)
            self._memcpy_htod(self._buf_midstate, iv)
            # Get device name
            err, name_b = cu.cuDeviceGetName(128, self._dev)
            if err == cu.CUresult.CUDA_SUCCESS:
                self.name = name_b.decode() if isinstance(name_b, bytes) else str(name_b)
            self.ready = True
            self.log(f"[gpu] native CUDA ready: {self.name} ({arch})")
        except Exception as e:
            self.log(f"[gpu] init failed: {type(e).__name__}: {e}")

    def _fatal(self):
        """v62: a CUDA error poisons the context; reset it so a new engine can start."""
        self.ready = False
        try:
            cu.cuDevicePrimaryCtxReset(self._dev)
        except Exception:
            pass

    def _memcpy_htod(self, dptr, arr):
        if not isinstance(arr, np.ndarray):
            arr = np.asarray(arr)
        err, = cu.cuMemcpyHtoD(dptr, arr.ctypes.data, arr.nbytes)
        if err != cu.CUresult.CUDA_SUCCESS:
            raise RuntimeError(f"cuMemcpyHtoD: {err}")

    def _memcpy_dtoh(self, arr, dptr):
        err, = cu.cuMemcpyDtoH(arr.ctypes.data, dptr, arr.nbytes)
        if err != cu.CUresult.CUDA_SUCCESS:
            raise RuntimeError(f"cuMemcpyDtoH: {err}")

    def duty_cycle_sleep(self, last_batch_seconds, target_percent):
        if target_percent >= 100 or target_percent <= 0 or last_batch_seconds <= 0:
            return
        off_time = last_batch_seconds * (100.0 - target_percent) / target_percent
        if off_time > 0:
            time.sleep(min(off_time, 0.25))

    def prepare_midstate(self, head):
        if not self.ready:
            return None, head
        n_full = len(head) // 64
        if n_full == 0:
            self._prefix_len = 0
            return None, head
        try:
            head_arr = np.frombuffer(head, dtype=np.uint8).copy()
            self._memcpy_htod(self._buf_head, head_arr)
            # launch: grid(1), block(1)
            # args: (head_ptr, n_full, midstate_ptr)
            d_head_c = ctypes.c_void_p(int(self._buf_head))
            n_full_c = ctypes.c_int(int(n_full))
            d_mid_c = ctypes.c_void_p(int(self._buf_midstate))
            params = (ctypes.c_void_p * 3)(
                ctypes.cast(ctypes.pointer(d_head_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(n_full_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_mid_c), ctypes.c_void_p),
            )
            err, = cu.cuLaunchKernel(self._fn_mid, 1, 1, 1, 1, 1, 1, 0, 0, params, 0)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"launch midstate: {err}")
            err, = cu.cuCtxSynchronize()
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"sync midstate: {err}")
            self._prefix_len = n_full * 64
            return True, head[n_full * 64:]
        except Exception as e:
            self.log(f"[gpu] prepare_midstate: {type(e).__name__}: {e}")
            self._prefix_len = 0
            return None, head

    def mine_batch_midstate(self, midstate, head_partial, tail, target,
                            base_nonce, threads=256, blocks=2048):
        if not self.ready:
            return None, 0, 0, None
        try:
            hp = np.frombuffer(head_partial, dtype=np.uint8).copy()
            tl = np.frombuffer(tail, dtype=np.uint8).copy()
            tg = np.frombuffer(target, dtype=np.uint8).copy()
            if hp.nbytes > 4000 or tl.nbytes > TAIL_MAX - 64:
                return None, 0, 0, None
            # v62: size the launch to the block so each launch stays well under the
            # Windows GPU watchdog, even for very large blocks
            n_comp = (hp.nbytes + 20 + tl.nbytes + 9) // 64 + 1
            blocks = max(16, min(int(blocks), WORK_PER_LAUNCH // (int(threads) * n_comp)))
            self._memcpy_htod(self._buf_head, hp)
            self._memcpy_htod(self._buf_tail, tl)
            self._memcpy_htod(self._buf_target, tg)
            zero4 = np.zeros(1, dtype=np.uint32)
            self._memcpy_htod(self._buf_found_flag, zero4)

            d_hp_c = ctypes.c_void_p(int(self._buf_head))
            hp_len_c = ctypes.c_int(int(hp.nbytes))
            pref_c = ctypes.c_int(int(self._prefix_len))
            d_mid_c = ctypes.c_void_p(int(self._buf_midstate))
            d_tl_c = ctypes.c_void_p(int(self._buf_tail))
            tl_len_c = ctypes.c_int(int(tl.nbytes))
            d_tg_c = ctypes.c_void_p(int(self._buf_target))
            nonce_c = ctypes.c_uint64(int(base_nonce))
            d_on_c = ctypes.c_void_p(int(self._buf_found_nonce))
            d_oh_c = ctypes.c_void_p(int(self._buf_found_hash))
            d_of_c = ctypes.c_void_p(int(self._buf_found_flag))
            params = (ctypes.c_void_p * 11)(
                ctypes.cast(ctypes.pointer(d_hp_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(hp_len_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(pref_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_mid_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_tl_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(tl_len_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_tg_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(nonce_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_on_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_oh_c), ctypes.c_void_p),
                ctypes.cast(ctypes.pointer(d_of_c), ctypes.c_void_p),
            )
            t0 = time.time()
            err, = cu.cuLaunchKernel(self._fn_mine, blocks, 1, 1, threads, 1, 1, 0, 0, params, 0)
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"launch mine: {err}")
            err, = cu.cuCtxSynchronize()
            if err != cu.CUresult.CUDA_SUCCESS:
                raise RuntimeError(f"sync mine: {err}")
            dt = time.time() - t0
            hashes = threads * blocks
            self.cumulative += hashes
            if dt > 0:
                self.last_rate_mhs = hashes / dt / 1e6
            flag_arr = np.zeros(1, dtype=np.uint32)
            self._memcpy_dtoh(flag_arr, self._buf_found_flag)
            if flag_arr[0] == 1:
                nonce_arr = np.zeros(1, dtype=np.uint64)
                hash_arr = np.zeros(32, dtype=np.uint8)
                self._memcpy_dtoh(nonce_arr, self._buf_found_nonce)
                self._memcpy_dtoh(hash_arr, self._buf_found_hash)
                return int(nonce_arr[0]), hashes, dt, bytes(hash_arr)
            return None, hashes, dt, None
        except Exception as e:
            self.log(f"[gpu] mine: {type(e).__name__}: {str(e)[:120]} — resetting GPU")
            self._fatal()
            raise

    def mine_batch(self, head, tail, target, base_nonce, threads=256, blocks=2048):
        # Without midstate — prefix_len=0, full head hashed
        self._prefix_len = 0
        return self.mine_batch_midstate(None, head, tail, target, base_nonce, threads, blocks)