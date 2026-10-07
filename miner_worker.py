import sys, json, ctypes, time, threading

def main():
    worker_count = int(sys.argv[1])
    dll_path = sys.argv[2]

    block_line = sys.stdin.readline()
    if not block_line:
        print(json.dumps({"type": "error", "msg": "no block"}), flush=True)
        return 1

    try:
        block = json.loads(block_line)
    except Exception as e:
        print(json.dumps({"type": "error", "msg": "bad json: " + str(e)}), flush=True)
        return 1

    try:
        lib = ctypes.CDLL(dll_path)
    except Exception as e:
        print(json.dumps({"type": "error", "msg": "dll load: " + str(e)}), flush=True)
        return 1

    if not hasattr(lib, "lbtc_scan_threads"):
        print(json.dumps({"type": "error", "msg": "lbtc_scan_threads missing"}), flush=True)
        return 1

    U8 = ctypes.c_ubyte
    lib.lbtc_scan_threads.argtypes = [
        ctypes.POINTER(U8), ctypes.c_size_t,
        ctypes.POINTER(U8), ctypes.c_size_t,
        ctypes.c_uint64,
        ctypes.POINTER(U8),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(U8),
        ctypes.POINTER(ctypes.c_uint64),
    ]
    lib.lbtc_scan_threads.restype = ctypes.c_int

    src = json.dumps({**block, "nonce": 0}, sort_keys=True)
    needle = '"nonce": 0'
    if needle not in src:
        print(json.dumps({"type": "error", "msg": "no nonce placeholder"}), flush=True)
        return 1
    head_str, tail_str = src.split(needle, 1)
    head = (head_str + '"nonce": ').encode()
    tail = tail_str.encode()

    target_hex = str(block["difficulty"]).lower()
    if len(target_hex) != 64:
        print(json.dumps({"type": "error", "msg": "bad difficulty len=" + str(len(target_hex))}), flush=True)
        return 1
    target_bytes = bytes.fromhex(target_hex)

    head_buf = (U8 * len(head)).from_buffer_copy(head)
    tail_buf = (U8 * len(tail)).from_buffer_copy(tail)
    target_buf = (U8 * 32).from_buffer_copy(target_bytes)
    found_hash = (U8 * 32)()
    found_nonce = ctypes.c_uint64(0)
    total_hashes = ctypes.c_uint64(0)
    stop_flag = ctypes.c_int(0)

    def stdin_watch():
        try:
            while True:
                line = sys.stdin.readline()
                if not line or line.strip() == "stop":
                    stop_flag.value = 1
                    return
        except Exception:
            stop_flag.value = 1
    threading.Thread(target=stdin_watch, daemon=True).start()

    def report():
        last_h = 0
        last_t = time.time()
        while not stop_flag.value:
            time.sleep(2.0)
            now_t = time.time()
            cur = total_hashes.value
            dt = now_t - last_t
            rate = (cur - last_h) / dt if dt > 0 else 0
            print(json.dumps({"type": "progress", "hashes": cur, "rate": rate}), flush=True)
            last_h = cur
            last_t = now_t
    threading.Thread(target=report, daemon=True).start()

    print(json.dumps({"type": "started", "workers": worker_count, "head_len": len(head), "tail_len": len(tail)}), flush=True)

    res = lib.lbtc_scan_threads(
        head_buf, len(head),
        tail_buf, len(tail),
        0,
        target_buf,
        worker_count,
        ctypes.byref(stop_flag),
        ctypes.byref(found_nonce),
        found_hash,
        ctypes.byref(total_hashes),
    )

    stop_flag.value = 1

    if res == 1:
        print(json.dumps({"type": "found", "nonce": found_nonce.value, "hash": bytes(found_hash).hex(), "hashes": total_hashes.value}), flush=True)
        return 0
    else:
        print(json.dumps({"type": "stopped", "hashes": total_hashes.value}), flush=True)
        return 0

if __name__ == "__main__":
    sys.exit(main())
