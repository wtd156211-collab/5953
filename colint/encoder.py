"""Streaming encoder for colint files.

Values are buffered one block at a time; finished blocks are written out
immediately and their index entries are spilled to a temporary file, so
memory usage stays bounded regardless of how many values are encoded.
The output is a pure function of the input values and the block size:
encoding the same data twice yields byte-identical files.
"""

import shutil
import struct
import tempfile
import zlib

from .format import (
    BLOCK_HEADER,
    BLOCK_HEADER_CRC_PART,
    DEFAULT_BLOCK_SIZE,
    ENC_CONST,
    ENC_FOR,
    ENC_RAW,
    FILE_HEADER,
    INDEX_ENTRY,
    INT64_MAX,
    INT64_MIN,
    MAGIC,
    MAX_BLOCK_SIZE,
)


def encode_block(values):
    """Encode one block of int64 values, returning (encoding_id, payload)."""
    n = len(values)
    first = values[0]
    lo = first
    hi = first
    constant = True
    for v in values[1:]:
        if v < lo:
            lo = v
        elif v > hi:
            hi = v
        if v != first:
            constant = False

    if constant:
        return ENC_CONST, struct.pack("<q", first)

    width = (hi - lo).bit_length()
    packed_len = (n * width + 7) // 8
    if 9 + packed_len < 8 * n:
        acc = 0
        shift = 0
        base = lo
        for v in values:
            acc |= (v - base) << shift
            shift += width
        payload = struct.pack("<qB", lo, width) + acc.to_bytes(packed_len, "little")
        return ENC_FOR, payload

    return ENC_RAW, struct.pack("<%dq" % n, *values)


class Encoder:
    """Incrementally encode int64 values into a colint file.

    ``fileobj`` must be a binary, seekable, writable file object.
    """

    def __init__(self, fileobj, block_size=DEFAULT_BLOCK_SIZE):
        if not 1 <= block_size <= MAX_BLOCK_SIZE:
            raise ValueError("block_size must be in [1, %d]" % MAX_BLOCK_SIZE)
        self._f = fileobj
        self._block_size = block_size
        self._buf = []
        self._count = 0
        self._num_blocks = 0
        self._offset = FILE_HEADER.size
        self._index_tmp = tempfile.TemporaryFile()
        self._finished = False
        fileobj.write(b"\x00" * FILE_HEADER.size)

    def add(self, value):
        """Append a single int64 value."""
        if self._finished:
            raise ValueError("encoder already finished")
        if not INT64_MIN <= value <= INT64_MAX:
            raise ValueError("value out of int64 range: %r" % (value,))
        self._buf.append(value)
        self._count += 1
        if len(self._buf) == self._block_size:
            self._flush()

    def add_all(self, values):
        """Append every value from an iterable (may be a lazy generator)."""
        for v in values:
            self.add(v)

    def _flush(self):
        vals = self._buf
        if not vals:
            return
        enc, payload = encode_block(vals)
        head = BLOCK_HEADER_CRC_PART.pack(enc, 0, len(vals), len(payload))
        crc = zlib.crc32(payload, zlib.crc32(head))
        block = head + struct.pack("<I", crc) + payload
        self._f.write(block)
        self._index_tmp.write(INDEX_ENTRY.pack(self._offset, len(block)))
        self._offset += len(block)
        self._num_blocks += 1
        self._buf = []

    def finish(self):
        """Flush the last block, write the index and patch the file header."""
        if self._finished:
            return
        self._flush()
        index_offset = self._offset
        self._index_tmp.seek(0)
        shutil.copyfileobj(self._index_tmp, self._f)
        self._index_tmp.close()
        self._f.seek(0)
        self._f.write(
            FILE_HEADER.pack(
                MAGIC, self._count, self._block_size, self._num_blocks, index_offset
            )
        )
        self._f.flush()
        self._finished = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.finish()

    def close(self):
        """Release the temporary index spill file (idempotent)."""
        if not self._index_tmp.closed:
            self._index_tmp.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def encode(values, out, block_size=DEFAULT_BLOCK_SIZE):
    """Encode an iterable of int64 values.

    ``out`` may be a path or a binary seekable file object. Returns a dict
    with count, num_blocks and the encoded file size in bytes.
    """
    if isinstance(out, (str, bytes)):
        with open(out, "w+b") as fh:
            return encode(values, fh, block_size)
    enc = Encoder(out, block_size)
    enc.add_all(values)
    enc.finish()
    return {
        "count": enc._count,
        "num_blocks": enc._num_blocks,
        "size": enc._offset + INDEX_ENTRY.size * enc._num_blocks,
    }
