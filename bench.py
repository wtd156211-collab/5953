"""基准：体积目标、3000 万点流式建索引、单点随机读吞吐。

跑法：python3 bench.py
"""

import os
import random
import resource
import tempfile
import time

from icol import DEFAULT_BLOCK_SIZE, Reader, encode, iter_csv_ints

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(ROOT, "samples")

SIZE_TARGETS = {
    "ramp.csv": 160000,
    "repeated.csv": 160000,
    "jumps.csv": 80000,
    "negatives.csv": 80000,
    "mixed.csv": 240000,
}


def bench_sample_sizes():
    print("== 样例体积 ==")
    total, total_fixed = 0, 0
    for name, target in SIZE_TARGETS.items():
        src = os.path.join(SAMPLES, name)
        n = sum(1 for v in iter_csv_ints(src))
        fd, path = tempfile.mkstemp(suffix=".icol")
        os.close(fd)
        t0 = time.time()
        encode(iter_csv_ints(src), path)
        dt = time.time() - t0
        size = os.path.getsize(path)
        fixed = n * 8
        total += size
        total_fixed += fixed
        print("%-14s n=%-7d %8d B  目标 %7d  占定长 %5.1f%%  编码 %.2fs %s"
              % (name, n, size, target, 100.0 * size / fixed, dt,
                 "OK" if size <= target else "FAIL"))
        os.unlink(path)
    print("合计: %d / %d = %.1f%% (目标 %d)\n"
          % (total, total_fixed, 100.0 * total / total_fixed, 720000))


def gen_30m(n=30_000_000):
    """模拟真实计数器：+1 为主，有抖动、长常量段、跳变、负值尖峰。"""
    random.seed(7)
    v = 1_700_000_000
    i = 0
    while i < n:
        if i % 1_000_000 == 500_000:
            run = 200_000  # 一段常量
        else:
            run = 1
        for _ in range(min(run, n - i)):
            if run == 1 and i % 1_000_000 == 0 and i > 0:
                v += random.choice([-500_000, 300_000])  # 跳变
            elif run == 1:
                v += random.choice((1, 1, 1, 1, 0, 2, -1))
            yield v
            i += 1


def bench_30m():
    print("== 3000 万点流式建索引 ==")
    fd, path = tempfile.mkstemp(suffix=".icol")
    os.close(fd)
    n = 30_000_000
    t0 = time.time()
    count = encode(gen_30m(n), path)
    dt = time.time() - t0
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    size = os.path.getsize(path)
    print("点数 %d  耗时 %.1fs  峰值 RSS %d MB  文件 %.1f MB（定长 %.0f MB，%.1f%%）"
          % (count, dt, rss_mb, size / 1e6, n * 8 / 1e6, 100.0 * size / (n * 8)))

    print("\n== 单点随机读 ==")
    with Reader(path, block_cache=0) as r:
        random.seed(1)
        warm = 20000
        idx = [random.randrange(n) for _ in range(warm)]
        t0 = time.time()
        for i in idx:
            r.read(i)
        dt = time.time() - t0
        rate = warm / dt
        print("纯随机单点读 %d 次: %.1fs, %.0f 次/秒 %s"
              % (warm, dt, rate, "OK" if rate >= 20000 else "FAIL"))

        # 与下标无关性：读开头块 vs 最后一块各计时
        for label, ids in (("开头", [0, 1, 2, 3]),
                           ("末尾", [n - 1, n - 2, n - 3, n - 4])):
            t0 = time.time()
            for i in ids:
                r.read(i)
            print("  %s块 4 次读取: %.3f ms" % (label, (time.time() - t0) * 1e3))
    os.unlink(path)


if __name__ == "__main__":
    bench_sample_sizes()
    bench_30m()
