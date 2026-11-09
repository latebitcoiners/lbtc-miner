"""
fast_engine.py: fast OpenCL SHA-256 engine for LBTC-PRO (v69). Runs on NVIDIA, AMD and Intel GPUs.

The GPU kernel below comes from lackminer-lbtc (github.com/Knop3dev/lackminer-lbtc), used under its license:

    Community Software License
    
    Copyright (c) 2026 Late Bitcoiners
    
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:
    
    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.
    
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
    THE SOFTWARE.

What makes it fast: the counter (work_nonce) is always 13 digits at byte 40 of the last 64-byte block, so each
attempt is one block; the digits are made with 32-bit maths; and the first 10 of the 64 rounds only depend on bytes
that never change, so they are computed once here on the CPU and the GPU starts at round 10.
"""
import hashlib, struct, time
import numpy as np
try:
    import pyopencl as cl
    _CL_OK, _CL_ERR = True, None
except Exception as _e:
    cl, _CL_OK, _CL_ERR = None, False, f"{type(_e).__name__}: {_e}"

DIGITS = 13
NONCE_MIN, NONCE_MAX = 10 ** 12, 10 ** 13          # the counter must stay 13 digits
COUNTER_AT = 40                                     # the counter starts at byte 40 of the last block
_KERNEL = r"""


__constant uint K[64]={
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define R(x,n) rotate((uint)(x),(uint)(32-(n)))
#define S0(x) (R(x,2)^R(x,13)^R(x,22))
#define S1(x) (R(x,6)^R(x,11)^R(x,25))
#define s0(x) (R(x,7)^R(x,18)^((x)>>3))
#define s1(x) (R(x,17)^R(x,19)^((x)>>10))
static inline void compress(uint st[8],uint w[16]) {
    uint a=st[0],b=st[1],c=st[2],d=st[3],e=st[4],f=st[5],g=st[6],h=st[7];
    #pragma unroll 64
    for(uint i=0;i<64;i++) {
        if(i>=16)w[i&15]+=s0(w[(i+1)&15])+w[(i+9)&15]+s1(w[(i+14)&15]);
        uint t1=h+S1(e)+bitselect(g,f,e)+K[i]+w[i&15];uint t2=S0(a)+bitselect(a,b,a^c);
        h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
    }
    st[0]+=a;st[1]+=b;st[2]+=c;st[3]+=d;st[4]+=e;st[5]+=f;st[6]+=g;st[7]+=h;
}
static inline void compress_after10(uint st[8],uint w[16],__constant uint *initial) {
    uint a=initial[0],b=initial[1],c=initial[2],d=initial[3],e=initial[4],f=initial[5],g=initial[6],h=initial[7];
    #pragma unroll 64
    for(uint i=10;i<64;i++) {
        if(i>=16)w[i&15]+=s0(w[(i+1)&15])+w[(i+9)&15]+s1(w[(i+14)&15]);
        uint t1=h+S1(e)+bitselect(g,f,e)+K[i]+w[i&15];uint t2=S0(a)+bitselect(a,b,a^c);
        h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
    }
    st[0]+=a;st[1]+=b;st[2]+=c;st[3]+=d;st[4]+=e;st[5]+=f;st[6]+=g;st[7]+=h;
}
static inline void compress_fixed(uint st[8],__constant uint *w) {
    uint a=st[0],b=st[1],c=st[2],d=st[3],e=st[4],f=st[5],g=st[6],h=st[7];
    #pragma unroll 64
    for(uint i=0;i<64;i++) {
        uint t1=h+S1(e)+bitselect(g,f,e)+w[i];uint t2=S0(a)+bitselect(a,b,a^c);
        h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
    }
    st[0]+=a;st[1]+=b;st[2]+=c;st[3]+=d;st[4]+=e;st[5]+=f;st[6]+=g;st[7]+=h;
}
static inline void digest_decimal(__constant uint *job,uchar decimal[20],uint st[8]) {
    uint offset=job[8],digits=job[9],blocks=job[10];
    for(uint i=0;i<8;i++)st[i]=job[i];
    for(uint block=0;block<blocks;block++) {
        if(block*64+64<=offset||block*64>=offset+digits) {
            compress_fixed(st,job+12+block*64);
            continue;
        }
        uint w[16];
        for(uint i=0;i<16;i++) {
            uint pos=block*64+4*i;uint word=job[12+block*64+i];
            for(uint k=0;k<4;k++) if(pos+k>=offset&&pos+k<offset+digits) {
                uint shift=24-8*k;
                word=(word&~(255u<<shift))|((uint)decimal[pos+k-offset]<<shift);
            }
            w[i]=word;
        }
        compress(st,w);
    }
}
static inline void digest(__constant uint *job,ulong nonce,uint st[8]) {
    uint offset=job[8],digits=job[9],blocks=job[10];
    uchar decimal[20];
    uint pos=digits;
    while(pos){ulong high=nonce/1000000000ul;uint chunk=(uint)(nonce-high*1000000000ul);
        uint take=min(pos,9u);
        for(uint j=0;j<take;j++){decimal[--pos]=(uchar)(48+chunk%10u);chunk/=10u;}
        nonce=high;}
    digest_decimal(job,decimal,st);
}
static inline void digest_batch(__constant uint *job,ulong base,uint gid,uint st[8]) {
    uint digits=job[9];
    if(digits>18){digest(job,base+gid,st);return;}
    
    uint high=(uint)(base/1000000000ul);
    uint low=(uint)(base%1000000000ul)+gid;
    if(low>=1000000000u){low-=1000000000u;high++;}
    uchar decimal[20];uint pos=digits;
    for(uint j=0;j<min(digits,9u);j++){
        decimal[--pos]=(uchar)(48+low%10u);low/=10u;
    }
    while(pos){decimal[--pos]=(uchar)(48+high%10u);high/=10u;}
    digest_decimal(job,decimal,st);
}
static inline void digest_short(__constant uint *job,ulong base,uint gid,uint st[8]) {
if((job[8]!=42&&job[8]!=43)||job[9]!=13||(job[10]!=5&&job[10]!=6&&job[10]!=7&&job[10]!=8)){digest_batch(job,base,gid,st);return;}
uint high=(uint)(base/1000000000ul),low=(uint)(base%1000000000ul)+gid;
if(low>=1000000000u){low-=1000000000u;high++;}
uint d0=48u+(high/1000u)%10u;
uint d1=48u+(high/100u)%10u;
uint d2=48u+(high/10u)%10u;
uint d3=48u+(high/1u)%10u;
uint d4=48u+(low/100000000u)%10u;
uint d5=48u+(low/10000000u)%10u;
uint d6=48u+(low/1000000u)%10u;
uint d7=48u+(low/100000u)%10u;
uint d8=48u+(low/10000u)%10u;
uint d9=48u+(low/1000u)%10u;
uint d10=48u+(low/100u)%10u;
uint d11=48u+(low/10u)%10u;
uint d12=48u+(low/1u)%10u;
uint w[16];
st[0]=job[0];
st[1]=job[1];
st[2]=job[2];
st[3]=job[3];
st[4]=job[4];
st[5]=job[5];
st[6]=job[6];
st[7]=job[7];
w[0]=job[12];
w[1]=job[13];
w[2]=job[14];
w[3]=job[15];
w[4]=job[16];
w[5]=job[17];
w[6]=job[18];
w[7]=job[19];
w[8]=job[20];
w[9]=job[21];
if(job[8]==42){
w[10]=(job[22]&0xffff0000u)|(d0<<8)|d1;
w[11]=(d2<<24)|(d3<<16)|(d4<<8)|d5;
w[12]=(d6<<24)|(d7<<16)|(d8<<8)|d9;
w[13]=(d10<<24)|(d11<<16)|(d12<<8)|(job[25]&255u);
}else{
w[10]=(job[22]&0xffffff00u)|d0;
w[11]=(d1<<24)|(d2<<16)|(d3<<8)|d4;
w[12]=(d5<<24)|(d6<<16)|(d7<<8)|d8;
w[13]=(d9<<24)|(d10<<16)|(d11<<8)|d12;
}
w[14]=job[26];
w[15]=job[27];
compress_after10(st,w,job+12+job[10]*64);
compress_fixed(st,job+76);
compress_fixed(st,job+140);
compress_fixed(st,job+204);
compress_fixed(st,job+268);
if(job[10]>=6)compress_fixed(st,job+332);
if(job[10]>=7)compress_fixed(st,job+396);
if(job[10]==8)compress_fixed(st,job+460);
}

static inline void digest_work_end(__constant uint *job,ulong base,uint gid,uint st[8]){
 if(job[8]!=40||job[9]!=13||job[10]!=1){digest_short(job,base,gid,st);return;}
 uint high=(uint)(base/1000000000ul),low=(uint)(base%1000000000ul)+gid;
 if(low>=1000000000u){low-=1000000000u;high++;}
 uint d0=48u+(high/1000u)%10u,d1=48u+(high/100u)%10u,d2=48u+(high/10u)%10u,d3=48u+high%10u;
 uint d4=48u+(low/100000000u)%10u,d5=48u+(low/10000000u)%10u,d6=48u+(low/1000000u)%10u,d7=48u+(low/100000u)%10u;
 uint d8=48u+(low/10000u)%10u,d9=48u+(low/1000u)%10u,d10=48u+(low/100u)%10u,d11=48u+(low/10u)%10u,d12=48u+low%10u;
 uint w[16];
 for(uint i=0;i<8;i++)st[i]=job[i];
 for(uint i=0;i<10;i++)w[i]=job[12+i];
 w[10]=(d0<<24)|(d1<<16)|(d2<<8)|d3;
 w[11]=(d4<<24)|(d5<<16)|(d6<<8)|d7;
 w[12]=(d8<<24)|(d9<<16)|(d10<<8)|d11;
 w[13]=(d12<<24)|(job[25]&0xffffffu);w[14]=job[26];w[15]=job[27];
 compress_after10(st,w,job+76);
}
__kernel void search(__constant uint *job,ulong base,__constant uint *target,
                     __global uint *count,__global ulong *nonces,uint capacity) {
    ulong nonce=base+get_global_id(0);uint st[8];digest_work_end(job,base,(uint)get_global_id(0),st);
    bool valid=false;
    for(uint i=0;i<8;i++){if(st[i]<target[i]){valid=true;break;}if(st[i]>target[i])break;}
    if(valid){uint index=atomic_inc(count);if(index<capacity)nonces[index]=nonce;}
}
__kernel void test(__constant uint *job,ulong base,__global uint *out) {
    uint st[8];digest_work_end(job,base,(uint)get_global_id(0),st);
    for(uint i=0;i<8;i++)out[get_global_id(0)*8+i]=st[i];
}
"""

_K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]
_IV = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
_M = 0xffffffff
def _r(x, n): return ((x >> n) | (x << (32 - n))) & _M

def _rounds(st, w16, upto=64):
    """run the SHA-256 rounds 0..upto-1 of one block from state st; returns the working variables a..h"""
    w = list(w16)
    for i in range(16, 64):
        w.append((w[i-16] + (_r(w[i-15],7) ^ _r(w[i-15],18) ^ (w[i-15] >> 3)) + w[i-7] + (_r(w[i-2],17) ^ _r(w[i-2],19) ^ (w[i-2] >> 10))) & _M)
    a, b, c, d, e, f, g, h = st
    for i in range(upto):
        t1 = (h + (_r(e,6) ^ _r(e,11) ^ _r(e,25)) + ((e & f) ^ (~e & g)) + _K[i] + w[i]) & _M
        t2 = ((_r(a,2) ^ _r(a,13) ^ _r(a,22)) + ((a & b) ^ (a & c) ^ (b & c))) & _M
        h, g, f, e, d, c, b, a = g, f, e, (d + t1) & _M, c, b, a, (t1 + t2) & _M
    return [a, b, c, d, e, f, g, h]

def midstate(data):
    st = list(_IV)
    for off in range(0, (len(data) // 64) * 64, 64):
        v = _rounds(st, struct.unpack('>16I', data[off:off+64]))
        st = [(x + y) & _M for x, y in zip(st, v)]
    return st

def pack_job(head):
    """head = the block's JSON up to and including '"work_nonce": ' (its length must be 40 mod 64).
    Returns the uint32 job the kernel reads (layout: state 0-7, offset/digits/blocks 8-10, block words 12-27,
    state after 10 rounds 76-83)."""
    if len(head) % 64 != COUNTER_AT: raise ValueError('head not aligned for the fast engine')
    n_full = len(head) // 64
    st = midstate(head[:n_full * 64])
    last = head[n_full * 64:] + b'0' * DIGITS + b'}'
    total_bits = (len(head) + DIGITS + 1) * 8
    last += b'\x80' + b'\x00' * (56 - len(last) - 1) + total_bits.to_bytes(8, 'big')
    words = list(struct.unpack('>16I', last))
    job = [0] * 84
    job[0:8] = st; job[8], job[9], job[10] = COUNTER_AT, DIGITS, 1
    job[12:28] = words
    job[76:84] = _rounds(st, words, upto=10)
    return np.array(job, dtype=np.uint32)

def full_hash(head, nonce):
    return hashlib.sha256(head + str(nonce).encode() + b'}').digest()

class FastCLMiner:
    def __init__(self, log=print, self_test=True):
        self.log, self.ready, self.name, self.last_rate_mhs = log, False, 'OpenCL GPU', 0.0
        self._head = None; self._job = None
        if not _CL_OK:
            self.log(f'[fast] pyopencl unavailable: {_CL_ERR}'); return
        try:
            dev = None
            for want in ('NVIDIA', 'ADVANCED MICRO', 'AMD'):          # a real graphics card first, never the CPU
                for p in cl.get_platforms():
                    if want in p.name.upper() or want in p.vendor.upper():
                        g = p.get_devices(cl.device_type.GPU)
                        if g: dev = g[0]; break
                if dev: break
            if dev is None:                                            # Mac (Apple) / Intel GPUs: only used if the self-test passes
                for p in cl.get_platforms():
                    try: g = p.get_devices(cl.device_type.GPU)
                    except Exception: g = []
                    if g: dev = g[0]; break
            if dev is None: self.log('[fast] no OpenCL GPU found - mining on CPU'); return
            self.device, self.name = dev, dev.name.strip()
            self.ctx = cl.Context([dev]); self.q = cl.CommandQueue(self.ctx)
            self.prg = cl.Program(self.ctx, _KERNEL).build()
            mf = cl.mem_flags
            self.b_job = cl.Buffer(self.ctx, mf.READ_ONLY, 84 * 4)
            self.b_target = cl.Buffer(self.ctx, mf.READ_ONLY, 32)
            self.b_count = cl.Buffer(self.ctx, mf.READ_WRITE, 4)
            self.capacity = 64
            self.b_nonces = cl.Buffer(self.ctx, mf.READ_WRITE, 8 * self.capacity)
            self.batch = 1 << 22
            if self_test and not self.self_test():
                self.log('[fast] self-test FAILED: not using the fast engine'); return
            self.ready = True
            self.log(f'[fast] fast OpenCL engine ready on {self.name} (self-test passed)')
        except Exception as e:
            self.log(f'[fast] setup failed: {type(e).__name__}: {str(e)[:200]}')

    def self_test(self):
        """the kernel's own test entry: 64 attempts on the GPU must equal Python's SHA-256 exactly"""
        def tmpl(k):
            return (b'{"difficulty": "00000000ffff", "index": 1, "mining_pad": "' + b'0' * k + b'", "nonce": 0, '
                    b'"previous_hash": "ab", "timestamp": 1, "transactions": [], "work_nonce": ')
        head = tmpl((COUNTER_AT - len(tmpl(0)) % 64) % 64)
        if len(head) % 64 != COUNTER_AT: return False
        job = pack_job(head); base = 1234567890123
        out = np.zeros(64 * 8, dtype=np.uint32); b_out = cl.Buffer(self.ctx, cl.mem_flags.WRITE_ONLY, out.nbytes)
        cl.enqueue_copy(self.q, self.b_job, job)
        self.prg.test(self.q, (64,), None, self.b_job, np.uint64(base), b_out); cl.enqueue_copy(self.q, out, b_out); self.q.finish()
        for i in range(64):
            gpu = b''.join(int(x).to_bytes(4, 'big') for x in out[i*8:(i+1)*8])
            if gpu != full_hash(head, base + i): return False
        return True

    def prepare(self, head):
        self._head = head; self._job = pack_job(head)
        cl.enqueue_copy(self.q, self.b_job, self._job)
        return True, b''

    def duty_cycle_sleep(self, last_batch_seconds, target_percent):
        if 0 < target_percent < 100 and last_batch_seconds > 0:
            time.sleep(min(last_batch_seconds * (100.0 - target_percent) / target_percent, 0.25))

    def mine_batch_midstate(self, midstate_, head_partial, tail, target, base_nonce, threads=256, blocks=4096):
        if not self.ready or self._job is None: return None, 0, 0, None
        if base_nonce < NONCE_MIN or base_nonce + self.batch >= NONCE_MAX: return None, 0, 0, None   # keep 13 digits
        tg = np.array(struct.unpack('>8I', bytes(target)), dtype=np.uint32)
        cl.enqueue_copy(self.q, self.b_target, tg); cl.enqueue_copy(self.q, self.b_count, np.zeros(1, np.uint32))
        t0 = time.time()
        self.prg.search(self.q, (self.batch,), (min(256, self.device.max_work_group_size),), self.b_job, np.uint64(base_nonce), self.b_target, self.b_count, self.b_nonces, np.uint32(self.capacity))
        cnt = np.zeros(1, np.uint32); cl.enqueue_copy(self.q, cnt, self.b_count); self.q.finish()
        dt = time.time() - t0
        used = int(self.batch)                                         # hashes done in THIS launch
        if dt > 0:
            self.last_rate_mhs = used / dt / 1e6
            # keep each launch about 50-150 ms: fast enough to stop quickly, big enough to keep the GPU busy
            if dt < 0.05 and self.batch < (1 << 28): self.batch <<= 1
            elif dt > 0.15 and self.batch > (1 << 18): self.batch >>= 1
        hashes = used
        if cnt[0]:
            ns = np.zeros(self.capacity, np.uint64); cl.enqueue_copy(self.q, ns, self.b_nonces); self.q.finish()
            for n in ns[:min(int(cnt[0]), self.capacity)]:
                h = full_hash(self._head, int(n))                       # double-check every find on the CPU
                if h < bytes(target): return int(n), hashes, dt, h
        return None, hashes, dt, None
