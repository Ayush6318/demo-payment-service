import argparse
import random
import threading
import time

import requests


def worker(url, stop, results, lock, think):
    s = requests.Session()
    while not stop.is_set():
        body = {"customer_email": f"user{random.randint(1, 500)}@example.com",
                "amount_cents": random.randint(500, 50_000)}
        t0 = time.perf_counter()
        try:
            code = s.post(f"{url}/payments", json=body, timeout=20).status_code
        except requests.RequestException:
            code = 0
        with lock:
            results.append((code, time.perf_counter() - t0))
        time.sleep(random.uniform(0.8 * think, 1.2 * think))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--think", type=float, default=1.2, help="seconds between requests per worker")
    args = ap.parse_args()

    stop, lock, results = threading.Event(), threading.Lock(), []
    for _ in range(args.workers):
        threading.Thread(target=worker, args=(args.url, stop, results, lock, args.think), daemon=True).start()
    print(f"load: {args.workers} workers against {args.url}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(5)
            with lock:
                batch, results[:] = results[:], []
            if not batch:
                continue
            ok = sum(1 for c, _ in batch if c == 201)
            errors = sum(1 for c, _ in batch if c >= 500 or c == 0)
            avg = sum(d for _, d in batch) / len(batch)
            print(f"{time.strftime('%H:%M:%S')}  rps={len(batch)/5:4.1f}  ok={ok:3d}  "
                  f"errors={errors:3d}  avg_latency={avg:5.2f}s")
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
