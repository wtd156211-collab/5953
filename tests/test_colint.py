import io
import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from colint import (
    CorruptBlockError,
    Encoder,
    FormatError,
    Reader,
    encode,
)
from colint.format import BLOCK_HEADER, FILE_HEADER, INDEX_ENTRY

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")

# Size targets from README: encoded file must be <= 20% of 8-bytes-per-point.
SIZE_LIMITS = {
    "ramp.csv": 160000,
    "repeated.csv": 160000,
    "jumps.csv": 80000,
    "negatives.csv": 80000,
    "mixed.csv": 240000,
}


def load_sample(name):
    values = []
    with open(os.path.join(SAMPLES, name), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                values.append(int(line))
    return values


def encode_bytes(values, block_size=256):
    buf = io.BytesIO()
    encode(values, buf, block_size)
    return buf.getvalue()


class RoundTripTest(unittest.TestCase):
    def test_all_samples_roundtrip(self):
        for name in ("tiny.csv", "ramp.csv", "repeated.csv", "jumps.csv",
                     "negatives.csv", "mixed.csv"):
            values = load_sample(name)
            with self.subTest(sample=name):
                reader = Reader(io.BytesIO(encode_bytes(values)))
                self.assertEqual(len(reader), len(values))
                self.assertEqual(list(reader.iter_values()), values)

    def test_random_access_matches(self):
        values = load_sample("mixed.csv")
        reader = Reader(io.BytesIO(encode_bytes(values)))
        rng = random.Random(0)
        for _ in range(2000):
            i = rng.randrange(len(values))
            self.assertEqual(reader.read(i), values[i])
        self.assertEqual(reader.read(0), values[0])
        self.assertEqual(reader.read(len(values) - 1), values[-1])
        self.assertEqual(reader[-1], values[-1])

    def test_slices(self):
        values = load_sample("jumps.csv")
        reader = Reader(io.BytesIO(encode_bytes(values)))
        self.assertEqual(reader[100:5000], values[100:5000])
        self.assertEqual(reader[::997], values[::997])

    def test_edge_cases(self):
        for values in ([], [0], [7] * 1000, [-(1 << 63), (1 << 63) - 1, 0, -1, 1]):
            with self.subTest(n=len(values)):
                reader = Reader(io.BytesIO(encode_bytes(values)))
                self.assertEqual(len(reader), len(values))
                self.assertEqual(list(reader.iter_values()), values)
                for i, v in enumerate(values):
                    self.assertEqual(reader.read(i), v)

    def test_out_of_range(self):
        reader = Reader(io.BytesIO(encode_bytes([1, 2, 3])))
        self.assertRaises(IndexError, reader.read, 3)
        self.assertRaises(IndexError, reader.read, -4)

    def test_value_out_of_int64(self):
        enc = Encoder(io.BytesIO())
        self.assertRaises(ValueError, enc.add, 1 << 63)
        self.assertRaises(ValueError, enc.add, -(1 << 63) - 1)

    def test_custom_block_size(self):
        values = load_sample("negatives.csv")
        for bs in (1, 7, 1000, 65535):
            with self.subTest(bs=bs):
                reader = Reader(io.BytesIO(encode_bytes(values, bs)))
                self.assertEqual(list(reader.iter_values()), values)

    def test_bad_magic(self):
        data = bytearray(encode_bytes([1, 2, 3]))
        data[0] ^= 0xFF
        self.assertRaises(FormatError, Reader, io.BytesIO(bytes(data)))


class DeterminismTest(unittest.TestCase):
    def test_byte_identical_output(self):
        for name in ("tiny.csv", "mixed.csv", "negatives.csv"):
            values = load_sample(name)
            with self.subTest(sample=name):
                self.assertEqual(encode_bytes(values), encode_bytes(values))

    def test_streaming_equals_oneshot(self):
        values = load_sample("ramp.csv")
        one = encode_bytes(values)
        buf = io.BytesIO()
        enc = Encoder(buf)
        for v in values:  # feed one by one
            enc.add(v)
        enc.finish()
        self.assertEqual(buf.getvalue(), one)


class SizeTargetTest(unittest.TestCase):
    def test_size_targets(self):
        total = 0
        for name, limit in SIZE_LIMITS.items():
            values = load_sample(name)
            size = len(encode_bytes(values))
            total += size
            with self.subTest(sample=name, size=size, limit=limit):
                self.assertLessEqual(size, limit)
        self.assertLessEqual(total, 720000)


class CorruptionTest(unittest.TestCase):
    def setUp(self):
        self.values = load_sample("mixed.csv")
        self.data = bytearray(encode_bytes(self.values))
        # locate block 5's payload via the index
        (count, bs, nblocks, index_offset) = struct.unpack_from("<QIIQ", self.data, 8)
        entry = INDEX_ENTRY.unpack_from(self.data, index_offset + 5 * INDEX_ENTRY.size)
        self.block_offset, self.block_len = entry
        self.corrupt = bytearray(self.data)
        self.corrupt[self.block_offset + BLOCK_HEADER.size + 3] ^= 0x01

    def test_corrupt_block_reports_number(self):
        reader = Reader(io.BytesIO(bytes(self.corrupt)))
        with self.assertRaises(CorruptBlockError) as ctx:
            reader.read_block(5)
        self.assertEqual(ctx.exception.block_no, 5)
        self.assertIn("5", str(ctx.exception))

    def test_other_blocks_unaffected(self):
        reader = Reader(io.BytesIO(bytes(self.corrupt)))
        bs = reader.block_size
        self.assertEqual(reader.read_block(4), self.values[4 * bs : 5 * bs])
        self.assertEqual(reader.read_block(6), self.values[6 * bs : 7 * bs])
        self.assertEqual(reader.check(), [5])

    def test_skip_corrupt_iteration(self):
        reader = Reader(io.BytesIO(bytes(self.corrupt)))
        got = list(reader.iter_values(strict=False))
        bs = reader.block_size
        expected = self.values[: 5 * bs] + self.values[6 * bs :]
        self.assertEqual(got, expected)
        # strict mode raises
        self.assertRaises(CorruptBlockError, lambda: list(reader.iter_values()))

    def test_single_bit_flip_detected(self):
        # every single-byte flip inside one block payload must be caught
        for off in (0, 5, self.block_len - 1):
            data = bytearray(self.data)
            data[self.block_offset + off] ^= 0x40
            reader = Reader(io.BytesIO(bytes(data)))
            self.assertRaises(CorruptBlockError, reader.read_block, 5)


class RandomAccessCostTest(unittest.TestCase):
    """read() must do a constant amount of I/O regardless of the index."""

    def test_read_io_is_index_independent(self):
        values = load_sample("ramp.csv")
        raw = encode_bytes(values)

        class Spy(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.reads = 0
                self.bytes_read = 0

            def read(self, n=-1):
                self.reads += 1
                out = super().read(n)
                self.bytes_read += len(out)
                return out

        costs = []
        for i in (0, len(values) // 2, len(values) - 1):
            spy = Spy(raw)
            reader = Reader(spy)
            spy.reads = 0
            spy.bytes_read = 0
            self.assertEqual(reader.read(i), values[i])
            costs.append((spy.reads, spy.bytes_read))
        self.assertEqual(costs[0], costs[1])
        self.assertEqual(costs[1], costs[2])
        self.assertLessEqual(costs[0][0], 4)  # header + at most 2 payload reads


class StreamingTest(unittest.TestCase):
    def test_lazy_generator_input(self):
        def gen():
            for i in range(100000):
                yield (i * 7) % 1000 - 500

        reader = Reader(io.BytesIO(encode_bytes(gen())))
        self.assertEqual(len(reader), 100000)
        self.assertEqual(reader.read(99999), (99999 * 7) % 1000 - 500)

    def test_path_based_encode_and_read(self):
        import tempfile

        values = load_sample("repeated.csv")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.cl")
            stats = encode(iter(values), path)
            self.assertEqual(stats["size"], os.path.getsize(path))
            with Reader(path) as reader:
                self.assertEqual(reader[500:600], values[500:600])


if __name__ == "__main__":
    unittest.main()
