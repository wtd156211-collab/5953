"""Scale benchmark: build a 30M-point colint file, measure encode time,
peak memory and random-read throughput.

Usage: python3 tools/bench.py [--points N] [--reads R]
"""

import argparse
import array
import os
import random
import resource
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from colint import Reader, encode


def make_values(n):
    """Ramp with jitter, constant stretches, jumps and negatives."""
    out = array.array("q")
    v = 1700000000
    rng = random.Random(1234)
    for i in range(n):
        if i % 500000 == 499999:
            v += rng.choice((-500000, 300000))
        elif i % 10000 < 100:
            pass  # constant stretch
        else:
            v += rng.choice((-1, 0, 1, 1, 1, 2))
        out.append(v)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--points", type=int, default=30_000_000)
    parser.add_argument("--reads", type=int, default=100_000)
    args = parser.parse_args()

    print("generating %d values..." % args.points)
    values = make_values(args.points)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bench.cl")
        start = time.perf_counter()
        stats = encode(values, path)
        elapsed = time.perf_counter() - start
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        raw = args.points * 8
        print(
            "encode: %d points in %.1fs (%.0f pts/s), file %d bytes (%.1f%% of raw), peak RSS %.0f MB"
            % (args.points, elapsed, args.points / elapsed, stats["size"],
               100.0 * stats["size"] / raw, peak_mb)
        )

        reader = Reader(path)
        rng = random.Random(7)
        indices = [rng.randrange(args.points) for _ in range(args.reads)]
        start = time.perf_counter()
        for i in indices:
            reader.read(i)
        elapsed = time.perf_counter() - start
        print("random read: %d ops in %.2fs -> %.0f reads/s" % (args.reads, elapsed, args.reads / elapsed))

        # spot-check correctness
        for i in indices[:1000]:
            assert reader.read(i) == values[i], i
        print("spot check: ok")
        reader.close()


if __name__ == "__main__":
    main()
