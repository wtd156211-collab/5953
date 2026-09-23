"""icol 的单元测试（只用标准库 unittest）。"""

import os
import random
import struct
import tempfile
import time
import unittest

from icol import (
    DEFAULT_BLOCK_SIZE,
    CorruptBlockError,
    FormatError,
    Reader,
    encode,
    iter_csv_ints,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(ROOT, "samples")

# README 里的体积目标（不含 tiny）
SIZE_TARGETS = {
    "ramp.csv": 160000,
    "repeated.csv": 160000,
    "jumps.csv": 80000,
    "negatives.csv": 80000,
    "mixed.csv": 240000,
}
SIZE_TOTAL = 720000


def load_csv(name):
    return list(iter_csv_ints(os.path.join(SAMPLES, name)))


def encode_to_tmp(vals, block_size=DEFAULT_BLOCK_SIZE):
    fd, path = tempfile.mkstemp(suffix=".icol")
    os.close(fd)
    try:
        encode(iter(vals), path, block_size=block_size)
        return path
    except Exception:
        os.unlink(path)
        raise


class RoundtripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _path(self, name):
        return os.path.join(self.tmp, name + ".icol")

    def assert_roundtrip(self, vals):
        path = self._path(str(id(vals)))
        encode(iter(vals), path)
        with Reader(path) as r:
            self.assertEqual(len(r), len(vals))
            # 逐点随机访问
            for i in range(0, len(vals), max(1, len(vals) // 37) + 1):
                self.assertEqual(r.read(i), vals[i], "mismatch at %d" % i)
            if vals:
                self.assertEqual(r.read(-1), vals[-1])
            # 整块流式读
            self.assertEqual(list(r.values()), list(vals))
            # 区间读
            if len(vals) >= 10:
                a, b = len(vals) // 3, 2 * len(vals) // 3
                self.assertEqual(r.read_range(a, b), vals[a:b])

    def test_tiny_exact(self):
        vals = load_csv("tiny.csv")
        self.assertEqual(vals, [5, 5, 5, -3, -3, 0, 0, 0, 0, 1000000,
                                -1000000, 7])
        self.assert_roundtrip(vals)

    def test_all_samples_roundtrip(self):
        for name in ["ramp.csv", "repeated.csv", "jumps.csv",
                     "negatives.csv", "mixed.csv"]:
            vals = load_csv(name)
            self.assertEqual(len(vals),
                             {"ramp.csv": 100000, "repeated.csv": 100000,
                              "jumps.csv": 50000, "negatives.csv": 50000,
                              "mixed.csv": 150000}[name])
            self.assert_roundtrip(vals)

    def test_edge_cases(self):
        self.assert_roundtrip([])
        self.assert_roundtrip([42])
        self.assert_roundtrip([-1])
        self.assert_roundtrip([7] * 2000)
        self.assert_roundtrip([0, 0, 0, -1, 1, 0])
        # int64 极值交替：差分必然退化，走 RAW 兜底
        extremes = []
        for i in range(1000):
            extremes.append((1 << 63) - 1 if i % 2 == 0 else -(1 << 63))
        self.assert_roundtrip(extremes)
        # 非块大小整数倍
        self.assert_roundtrip(list(range(1024)))
        self.assert_roundtrip(list(range(1025)))
        self.assert_roundtrip(list(range(1023)))

    def test_value_range_validation(self):
        path = self._path("bad")
        with self.assertRaises(ValueError):
            encode(iter([1 << 63]), path)


class SizeTargetTests(unittest.TestCase):
    def test_each_file_under_target(self):
        total = 0
        for name, limit in SIZE_TARGETS.items():
            vals = load_csv(name)
            fd, path = tempfile.mkstemp(suffix=".icol")
            os.close(fd)
            try:
                encode(iter(vals), path)
                size = os.path.getsize(path)
                total += size
                fixed = len(vals) * 8
                self.assertLessEqual(
                    size, limit,
                    "%s: %d bytes > target %d (fixed int64 is %d)"
                    % (name, size, limit, fixed))
            finally:
                os.unlink(path)
        self.assertLessEqual(total, SIZE_TOTAL)


class DeterminismTests(unittest.TestCase):
    def test_bytes_identical(self):
        vals = load_csv("mixed.csv")
        files = []
        try:
            for _ in range(2):
                fd, path = tempfile.mkstemp(suffix=".icol")
                os.close(fd)
                encode(iter(vals), path)
                files.append(path)
            with open(files[0], "rb") as a, open(files[1], "rb") as b:
                self.assertEqual(a.read(), b.read())
        finally:
            for p in files:
                os.unlink(p)

    def test_list_vs_generator_identical(self):
        vals = load_csv("ramp.csv")
        fd1, p1 = tempfile.mkstemp(suffix=".icol")
        fd2, p2 = tempfile.mkstemp(suffix=".icol")
        os.close(fd1)
        os.close(fd2)
        try:
            encode(vals, p1)
            encode((v for v in vals), p2)
            self.assertEqual(open(p1, "rb").read(), open(p2, "rb").read())
        finally:
            os.unlink(p1)
            os.unlink(p2)

    def test_block_size_changes_bytes_is_ok(self):
        vals = list(range(1000))
        fd1, p1 = tempfile.mkstemp(suffix=".icol")
        fd2, p2 = tempfile.mkstemp(suffix=".icol")
        os.close(fd1)
        os.close(fd2)
        try:
            encode(vals, p1, block_size=256)
            encode(vals, p2, block_size=512)
            self.assertNotEqual(open(p1, "rb").read(), open(p2, "rb").read())
            with Reader(p2) as r:
                self.assertEqual(r.read(999), 999)
        finally:
            os.unlink(p1)
            os.unlink(p2)


class RandomAccessTests(unittest.TestCase):
    def setUp(self):
        self.vals = load_csv("mixed.csv")
        self.path = encode_to_tmp(self.vals)

    def tearDown(self):
        os.unlink(self.path)

    def test_random_indices(self):
        random.seed(1234)
        with Reader(self.path) as r:
            for _ in range(3000):
                i = random.randrange(len(self.vals))
                self.assertEqual(r.read(i), self.vals[i])

    def test_index_errors(self):
        with Reader(self.path) as r:
            with self.assertRaises(IndexError):
                r.read(len(self.vals))
            with self.assertRaises(IndexError):
                r.read(-len(self.vals) - 1)

    def test_random_read_rate(self):
        # 冒烟测试：防回归（真正的 3000 万基准见 bench.py）。
        random.seed(99)
        indices = [random.randrange(len(self.vals)) for _ in range(20000)]
        with Reader(self.path, block_cache=0) as r:
            t0 = time.time()
            for i in indices:
                self.assertEqual(r.read(i), self.vals[i])
            dt = time.time() - t0
        self.assertGreaterEqual(20000 / dt, 5000.0,
                                "random read rate only %.0f/s" % (20000 / dt))


class CorruptionTests(unittest.TestCase):
    def setUp(self):
        self.vals = load_csv("mixed.csv")
        self.path = encode_to_tmp(self.vals)
        with Reader(self.path) as r:
            self.block_size = r.block_size

    def tearDown(self):
        os.unlink(self.path)

    def _flip_byte_at(self, offset):
        with open(self.path, "r+b") as f:
            f.seek(offset)
            b = f.read(1)
            f.seek(offset)
            f.write(bytes([b[0] ^ 0xFF]))

    def test_payload_corruption_reports_block_no(self):
        bad_block = 100
        with Reader(self.path) as r:
            block_offset = r._index[bad_block][0]
        # 翻转该块块体中的一个字节
        self._flip_byte_at(block_offset + 12 + 5)
        with Reader(self.path) as r:
            i = bad_block * self.block_size + 1
            with self.assertRaises(CorruptBlockError) as cm:
                r.read(i)
            self.assertEqual(cm.exception.block_no, bad_block)
            # 坏块之前的点照常读
            self.assertEqual(r.read(0), self.vals[0])
            # 坏块之后的点也照常读
            later = (bad_block + 5) * self.block_size
            self.assertEqual(r.read(later), self.vals[later])

    def test_verify_lists_bad_blocks(self):
        with Reader(self.path) as r:
            off1 = r._index[50][0]
            off2 = r._index[200][0]
        self._flip_byte_at(off1 + 12)
        self._flip_byte_at(off2 + 12 + 3)
        with Reader(self.path) as r:
            self.assertEqual(r.verify(), [50, 200])

    def test_header_corruption_detected(self):
        self._flip_byte_at(2)
        with self.assertRaises(FormatError):
            Reader(self.path)

    def test_index_corruption_detected(self):
        with Reader(self.path) as r:
            index_offset = r._index_offset
        self._flip_byte_at(index_offset)
        with self.assertRaises(FormatError):
            Reader(self.path)

    def test_skip_corrupt_block(self):
        bad_block = 100
        with Reader(self.path) as r:
            block_offset = r._index[bad_block][0]
        self._flip_byte_at(block_offset + 12 + 2)
        with Reader(self.path) as r:
            lo = bad_block * self.block_size
            hi = min(lo + self.block_size, len(self.vals))
            # 不跳过则抛错
            with self.assertRaises(CorruptBlockError):
                r.read_range(lo, hi)
            # 跳过坏块：拿到区间里其余的点
            start = lo - 3
            stop = hi + 3
            got = r.read_range(start, stop, skip_corrupt=True)
            expect = (self.vals[start:lo] + self.vals[hi:stop])
            self.assertEqual(got, expect)

    def test_blocks_iteration_skip(self):
        bad_block = 100
        with Reader(self.path) as r:
            block_offset = r._index[bad_block][0]
        self._flip_byte_at(block_offset + 12 + 1)
        with Reader(self.path) as r:
            block_nos = []
            all_good = True
            for b, vals in r.blocks(skip_corrupt=True):
                block_nos.append(b)
                start = b * self.block_size
                if vals != self.vals[start:start + len(vals)]:
                    all_good = False
            self.assertTrue(all_good)
            self.assertNotIn(bad_block, block_nos)
            self.assertEqual(len(block_nos), r.num_blocks - 1)


class StreamingTests(unittest.TestCase):
    def test_encode_from_generator(self):
        n = 300_000

        def gen():
            v = 0
            for _ in range(n):
                yield v
                v += 1

        fd, path = tempfile.mkstemp(suffix=".icol")
        os.close(fd)
        try:
            count = encode(gen(), path)
            self.assertEqual(count, n)
            with Reader(path) as r:
                self.assertEqual(r.read(n - 1), n - 1)
                self.assertEqual(r.read(0), 0)
                self.assertEqual(r.read(n // 2), n // 2)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
