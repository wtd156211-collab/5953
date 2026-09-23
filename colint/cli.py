"""Command line interface: encode/decode/verify/bench-read."""

import argparse
import random
import sys
import time

from .decoder import Reader
from .encoder import encode
from .errors import ColintError


def read_csv_ints(path):
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                yield int(line)


def cmd_encode(args):
    stats = encode(read_csv_ints(args.input), args.output, args.block_size)
    print(
        "encoded %d points into %d blocks, %d bytes -> %s"
        % (stats["count"], stats["num_blocks"], stats["size"], args.output)
    )


def cmd_decode(args):
    with Reader(args.input) as reader:
        with open(args.output, "w", encoding="utf-8") as out:
            for v in reader.iter_values(strict=not args.skip_corrupt):
                out.write("%d\n" % v)


def cmd_verify(args):
    with Reader(args.input) as reader:
        bad = reader.check()
        if bad:
            print("corrupted blocks: %s" % ", ".join(map(str, bad)))
            return 1
        print("ok: %d blocks, %d points" % (reader.num_blocks, len(reader)))
        return 0


def cmd_bench_read(args):
    with Reader(args.input) as reader:
        n = len(reader)
        rng = random.Random(42)
        indices = [rng.randrange(n) for _ in range(args.ops)]
        start = time.perf_counter()
        for i in indices:
            reader.read(i)
        elapsed = time.perf_counter() - start
        print(
            "%d random reads in %.3fs -> %.0f reads/s" % (args.ops, elapsed, args.ops / elapsed)
        )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="colint")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("encode", help="encode a CSV of ints into a colint file")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--block-size", type=int, default=256)
    p.set_defaults(func=cmd_encode)

    p = sub.add_parser("decode", help="decode a colint file back to CSV ints")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--skip-corrupt", action="store_true")
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("verify", help="verify all block checksums")
    p.add_argument("input")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("bench-read", help="benchmark random single-point reads")
    p.add_argument("input")
    p.add_argument("--ops", type=int, default=100000)
    p.set_defaults(func=cmd_bench_read)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except ColintError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
