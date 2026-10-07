
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <pthread.h>
#endif

static const uint32_t K256[64] = {
    0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
    0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
    0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
    0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
    0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
    0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
    0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
    0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
};

static const uint32_t H0[8] = {
    0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,
    0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U
};

#define ROTR(x,n)  (((x) >> (n)) | ((x) << (32-(n))))
#define CH(x,y,z)  (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x)   (ROTR(x,2) ^ ROTR(x,13) ^ ROTR(x,22))
#define BSIG1(x)   (ROTR(x,6) ^ ROTR(x,11) ^ ROTR(x,25))
#define SSIG0(x)   (ROTR(x,7) ^ ROTR(x,18) ^ ((x) >> 3))
#define SSIG1(x)   (ROTR(x,17) ^ ROTR(x,19) ^ ((x) >> 10))

static void sha256_compress(uint32_t state[8], const uint8_t block[64]) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)block[i*4] << 24) | ((uint32_t)block[i*4+1] << 16)
             | ((uint32_t)block[i*4+2] << 8) | ((uint32_t)block[i*4+3]);
    }
    for (int i = 16; i < 64; i++) {
        w[i] = SSIG1(w[i-2]) + w[i-7] + SSIG0(w[i-15]) + w[i-16];
    }
    uint32_t a=state[0], b=state[1], c=state[2], d=state[3];
    uint32_t e=state[4], f=state[5], g=state[6], h=state[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + BSIG1(e) + CH(e,f,g) + K256[i] + w[i];
        uint32_t t2 = BSIG0(a) + MAJ(a,b,c);
        h=g; g=f; f=e; e=d+t1;
        d=c; c=b; b=a; a=t1+t2;
    }
    state[0]+=a; state[1]+=b; state[2]+=c; state[3]+=d;
    state[4]+=e; state[5]+=f; state[6]+=g; state[7]+=h;
}

static void sha256_full(const uint8_t* data, size_t len, uint8_t out[32]) {
    uint32_t state[8];
    memcpy(state, H0, sizeof(H0));
    size_t full = len / 64;
    for (size_t i = 0; i < full; i++) sha256_compress(state, data + i*64);
    uint8_t final[128];
    size_t rem = len - full*64;
    memcpy(final, data + full*64, rem);
    final[rem] = 0x80;
    size_t final_len;
    if (rem < 56) {
        final_len = 64;
        memset(final + rem + 1, 0, 64 - rem - 1 - 8);
    } else {
        final_len = 128;
        memset(final + rem + 1, 0, 128 - rem - 1 - 8);
    }
    uint64_t bits = (uint64_t)len * 8;
    for (int i = 0; i < 8; i++) final[final_len - 1 - i] = (bits >> (i*8)) & 0xFF;
    sha256_compress(state, final);
    if (final_len == 128) sha256_compress(state, final + 64);
    for (int i = 0; i < 8; i++) {
        out[i*4]   = (state[i] >> 24) & 0xFF;
        out[i*4+1] = (state[i] >> 16) & 0xFF;
        out[i*4+2] = (state[i] >> 8)  & 0xFF;
        out[i*4+3] = state[i]         & 0xFF;
    }
}

static int u64_to_str(uint64_t v, char* out) {
    char tmp[24];
    int n = 0;
    if (v == 0) { out[0] = '0'; return 1; }
    while (v > 0) {
        tmp[n++] = (char)('0' + (v % 10));
        v /= 10;
    }
    for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
    return n;
}

int lbtc_scan(
    const uint8_t* head, size_t head_len,
    const uint8_t* tail, size_t tail_len,
    uint64_t nonce_start, uint64_t stride, uint64_t count,
    const uint8_t* target,
    uint64_t* processed,
    uint64_t* found_nonce,
    uint8_t* found_hash
) {
    uint8_t buf[65536];
    if (head_len + 24 + tail_len > sizeof(buf)) {
        if (processed) *processed = 0;
        return -1;
    }
    memcpy(buf, head, head_len);
    for (uint64_t i = 0; i < count; i++) {
        uint64_t nonce = nonce_start + i * stride;
        int nlen = u64_to_str(nonce, (char*)(buf + head_len));
        memcpy(buf + head_len + nlen, tail, tail_len);
        uint8_t hash[32];
        sha256_full(buf, head_len + nlen + tail_len, hash);
        if (memcmp(hash, target, 32) < 0) {
            if (processed)     *processed = i + 1;
            if (found_nonce)   *found_nonce = nonce;
            if (found_hash)    memcpy(found_hash, hash, 32);
            return 1;
        }
    }
    if (processed) *processed = count;
    return 0;
}

typedef struct {
    const uint8_t* head;
    size_t head_len;
    const uint8_t* tail;
    size_t tail_len;
    uint64_t nonce_start;
    uint64_t stride;
    const uint8_t* target;
    volatile int* stop_flag;
    volatile int* found_flag;
    uint64_t* found_nonce_out;
    uint8_t* found_hash_out;
    volatile uint64_t* total_hashes;
} WorkerCtx;

static void worker_body(WorkerCtx* ctx) {
    uint8_t buf[65536];
    size_t msg_base = ctx->head_len;
    memcpy(buf, ctx->head, ctx->head_len);
    uint64_t nonce = ctx->nonce_start;
    const uint64_t CHUNK = 10000;
    while (!(*ctx->stop_flag)) {
        for (uint64_t i = 0; i < CHUNK; i++) {
            int nlen = u64_to_str(nonce, (char*)(buf + msg_base));
            memcpy(buf + msg_base + nlen, ctx->tail, ctx->tail_len);
            uint8_t hash[32];
            sha256_full(buf, msg_base + nlen + ctx->tail_len, hash);
            if (memcmp(hash, ctx->target, 32) < 0) {
#ifdef _WIN32
                if (InterlockedCompareExchange((LONG*)ctx->found_flag, 1, 0) == 0) {
#else
                if (__sync_bool_compare_and_swap(ctx->found_flag, 0, 1)) {
#endif
                    *ctx->found_nonce_out = nonce;
                    memcpy(ctx->found_hash_out, hash, 32);
                    *ctx->stop_flag = 1;
                    #ifdef _WIN32
                    InterlockedExchangeAdd64((volatile LONG64*)ctx->total_hashes, i + 1);
#else
                    __sync_fetch_and_add((uint64_t*)ctx->total_hashes, i + 1);
#endif
                    return;
                }
                break;
            }
            nonce += ctx->stride;
        }
        #ifdef _WIN32
        InterlockedExchangeAdd64((volatile LONG64*)ctx->total_hashes, CHUNK);
#else
        __sync_fetch_and_add((uint64_t*)ctx->total_hashes, CHUNK);
#endif
    }
}

#ifdef _WIN32
static DWORD WINAPI thread_entry(LPVOID arg) {
    worker_body((WorkerCtx*)arg);
    return 0;
}
#else
static void* thread_entry(void* arg) {
    worker_body((WorkerCtx*)arg);
    return NULL;
}
#endif

int lbtc_scan_threads(
    const uint8_t* head, size_t head_len,
    const uint8_t* tail, size_t tail_len,
    uint64_t start_nonce,
    const uint8_t* target,
    int num_threads,
    volatile int* stop_flag,
    uint64_t* found_nonce_out,
    uint8_t* found_hash_out,
    volatile uint64_t* total_hashes
) {
    if (head_len + 24 + tail_len > 65536) return -1;
    if (num_threads < 1) num_threads = 1;
    if (num_threads > 256) num_threads = 256;

    volatile int found_flag = 0;
    *total_hashes = 0;

    WorkerCtx ctxs[256];
    for (int i = 0; i < num_threads; i++) {
        ctxs[i].head = head;
        ctxs[i].head_len = head_len;
        ctxs[i].tail = tail;
        ctxs[i].tail_len = tail_len;
        ctxs[i].nonce_start = start_nonce + (uint64_t)i;
        ctxs[i].stride = (uint64_t)num_threads;
        ctxs[i].target = target;
        ctxs[i].stop_flag = stop_flag;
        ctxs[i].found_flag = &found_flag;
        ctxs[i].found_nonce_out = found_nonce_out;
        ctxs[i].found_hash_out = found_hash_out;
        ctxs[i].total_hashes = total_hashes;
    }

#ifdef _WIN32
    HANDLE handles[256];
    for (int i = 0; i < num_threads; i++) {
        handles[i] = CreateThread(NULL, 0, thread_entry, &ctxs[i], 0, NULL);
    }
    for (int i = 0; i < num_threads; i++) {
        WaitForSingleObject(handles[i], INFINITE);
        CloseHandle(handles[i]);
    }
#else
    pthread_t threads[256];
    for (int i = 0; i < num_threads; i++) {
        pthread_create(&threads[i], NULL, thread_entry, &ctxs[i]);
    }
    for (int i = 0; i < num_threads; i++) {
        pthread_join(threads[i], NULL);
    }
#endif

    if (found_flag) return 1;
    return 0;
}
