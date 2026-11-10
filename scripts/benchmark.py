"""HTTP load test against a fixture seeded with seed_benchmark.py.

32 total client threads: 24 writers and 8 readers, active concurrently.
Every write is a new item, so duplicate/old-event no-ops cannot inflate ingest rate.
"""
import argparse
import concurrent.futures
import json
import math
import sys
import threading
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "starter-kit"))
from harness_lib.service import http

parser = argparse.ArgumentParser()
parser.add_argument("prefix")
parser.add_argument("--events", type=int, default=4000)
args = parser.parse_args()
base = "http://localhost:8080"
stop = threading.Event()
recall_times, write_times, errors = [], [], []
barrier = threading.Barrier(32)
run_id = str(time.time_ns())


def writer(slot):
    barrier.wait()
    for i in range(slot, args.events, 24):
        start = time.monotonic()
        response = http("POST", base + "/v1/events", {
            "user_id": f"{args.prefix}-{i % 500}", "source_id": "live", "item_id": f"{run_id}-{i}",
            "version": 1, "kind": "upsert", "content": "Ana is leading the Atlas project.",
            "occurred_at": "2026-01-01T00:00:00Z"})
        write_times.append(time.monotonic() - start)
        if response.status != 202:
            errors.append(f"write {response.status}")


def reader(slot):
    barrier.wait()
    i = slot
    while not stop.is_set():
        start = time.monotonic()
        response = http("GET", f"{base}/v1/users/{args.prefix}-{i % 500}/recall?q=project&k=10")
        recall_times.append(time.monotonic() - start)
        if response.status != 200 or len(response.body.get("memories", [])) != 10:
            errors.append(f"recall {response.status}")
        i += 8


start = time.monotonic()
with concurrent.futures.ThreadPoolExecutor(32) as pool:
    readers = [pool.submit(reader, i) for i in range(8)]
    writers = [pool.submit(writer, i) for i in range(24)]
    try:
        for future in writers:
            future.result()
        duration = time.monotonic() - start
    finally:
        stop.set()
    for future in readers:
        future.result()


def p95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1] * 1000


result = {"fixture_prefix": args.prefix, "events": len(write_times), "seconds": round(duration, 3),
          "accepted_events_per_second": round(len(write_times) / duration, 3),
          "write_p95_ms": round(p95(write_times), 3), "recall_requests": len(recall_times),
          "recall_p95_ms": round(p95(recall_times), 3), "errors": errors[:10]}
print(json.dumps(result, indent=2))
sys.exit(0 if not errors and result["accepted_events_per_second"] >= 200 and result["recall_p95_ms"] < 150 else 1)
