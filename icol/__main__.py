"""icol 命令行入口。

用法：
    python -m icol encode input.csv output.icol [--block-size N]
    python -m icol decode input.icol [output.csv]
    python -m icol get    input.icol INDEX
    python -m icol verify input.icol
    python -m icol info   input.icol
"""

import argparse
import sys
import time

from . import encode, iter_csv_ints, write_csv_ints
from .decoder import Reader
from .errors import CorruptBlockError
from .format import CODEC_NAMES


def cmd_encode(args):
    t0 = time.time()
    n = encode(iter_csv_ints(args.input), args.output,
               block_size=args.block_size)
    print("encoded %d points in %.2fs -> %s" % (n, time.time() - t0, args.output))
    return 0


def cmd_decode(args):
    with Reader(args.input) as r:
        if args.output:
            write_csv_ints(r.values(skip_corrupt=True), args.output)
            print("decoded %d points -> %s" % (r.count, args.output))
        else:
            out = sys.stdout
            for v in r.values(skip_corrupt=True):
                out.write("%d\n" % v)
    return 0


def cmd_get(args):
    with Reader(args.input) as r:
        print(r.read(args.index))
    return 0


def cmd_verify(args):
    with Reader(args.input) as r:
        bad = r.verify()
    if bad:
        print("corrupt blocks: %s" % ",".join(str(b) for b in bad))
        return 1
    print("ok: %d blocks, %d points" % (r.num_blocks, r.count))
    return 0


def cmd_info(args):
    with Reader(args.input) as r:
        print("points:      %d" % r.count)
        print("block_size:  %d" % r.block_size)
        print("blocks:      %d" % r.num_blocks)
        counts = {}
        for _off, _total_len, _count, codec, _resv in r._index:
            name = CODEC_NAMES.get(codec, str(codec))
            counts[name] = counts.get(name, 0) + 1
        for name, n in sorted(counts.items()):
            print("codec %-13s %d blocks" % (name, n))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m icol")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("encode", help="CSV -> icol")
    pe.add_argument("input")
    pe.add_argument("output")
    pe.add_argument("--block-size", type=int, default=512)
    pe.set_defaults(func=cmd_encode)

    pd = sub.add_parser("decode", help="icol -> CSV (坏块自动跳过)")
    pd.add_argument("input")
    pd.add_argument("output", nargs="?")
    pd.set_defaults(func=cmd_decode)

    pg = sub.add_parser("get", help="随机读一个下标")
    pg.add_argument("input")
    pg.add_argument("index", type=int)
    pg.set_defaults(func=cmd_get)

    pv = sub.add_parser("verify", help="逐块校验，报告坏块号")
    pv.add_argument("input")
    pv.set_defaults(func=cmd_verify)

    pi = sub.add_parser("info", help="打印文件元信息")
    pi.add_argument("input")
    pi.set_defaults(func=cmd_info)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except CorruptBlockError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
