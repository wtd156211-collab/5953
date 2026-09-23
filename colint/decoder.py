"""Random-access reader for colint files.

The block index is loaded into memory once (12 bytes per block, e.g. about
1.4 MB for 30 million points at the default block size). A single point
lookup then costs one index lookup plus a constant number of small reads,
independent of the index: CONST/FOR/RAW blocks all allow extracting the
k-th value directly without decoding the rest of the block.
"""

import struct
import zlib

from .format import (
    BLOCK_HEADER,
    ENC_CONST,
    ENC_FOR,
    ENC_RAW,
    FILE_HEADER,
    INDEX_ENTRY,
    MAGIC,
)
from .errors import CorruptBlockError, FormatError


class Reader:
    """Read a colint file. ``fileobj`` may be a path or a binary file object."""

    def __init__(self, fileobj):
        if isinstance(fileobj, (str, bytes)):
            self._f = open(fileobj, "rb")
            self._owns_file = True
        else:
            self._f = fileobj
            self._owns_file = False
        f = self._f
        header = f.read(FILE_HEADER.size)
        if len(header) < FILE_HEADER.size:
            raise FormatError("file too small for header")
        magic, count, block_size, num_blocks, index_offset = FILE_HEADER.unpack(header)
        if magic != MAGIC:
            raise FormatError("bad magic %r, not a colint file" % (magic,))
        if block_size < 1:
            raise FormatError("invalid block size %d" % block_size)
        self._count = count
        self._block_size = block_size
        self._num_blocks = num_blocks
        f.seek(index_offset)
        raw_index = f.read(INDEX_ENTRY.size * num_blocks)
        if len(raw_index) < INDEX_ENTRY.size * num_blocks:
            raise FormatError("truncated block index")
        self._index = list(INDEX_ENTRY.iter_unpack(raw_index))

    def __len__(self):
        return self._count

    @property
    def block_size(self):
        return self._block_size

    @property
    def num_blocks(self):
        return self._num_blocks

    def close(self):
        if self._owns_file:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _locate(self, i):
        if i < 0:
            i += self._count
        if not 0 <= i < self._count:
            raise IndexError("index %d out of range [0, %d)" % (i, self._count))
        return divmod(i, self._block_size)

    def read(self, i):
        """Return the value at position ``i`` in O(1) block-local work.

        Note: single-point reads do not verify the block checksum for speed;
        use :meth:`read_block`, :meth:`iter_values` or :meth:`check` when
        corruption detection is needed.
        """
        block_no, k = self._locate(i)
        offset, _length = self._index[block_no]
        f = self._f
        f.seek(offset)
        head = f.read(BLOCK_HEADER.size)
        if len(head) < BLOCK_HEADER.size:
            raise FormatError("truncated block %d header" % block_no)
        enc = head[0]
        body = offset + BLOCK_HEADER.size
        if enc == ENC_CONST:
            return struct.unpack("<q", f.read(8))[0]
        if enc == ENC_RAW:
            f.seek(body + 8 * k)
            return struct.unpack("<q", f.read(8))[0]
        if enc == ENC_FOR:
            lo, width = struct.unpack("<qB", f.read(9))
            bit = k * width
            nbytes = (bit % 8 + width + 7) // 8
            f.seek(body + 9 + bit // 8)
            chunk = int.from_bytes(f.read(nbytes), "little")
            return lo + ((chunk >> (bit % 8)) & ((1 << width) - 1))
        raise FormatError("block %d: unknown encoding %d" % (block_no, enc))

    def read_block(self, block_no, verify=True):
        """Decode a whole block, verifying its CRC32 unless ``verify=False``.

        Raises :class:`CorruptBlockError` (carrying ``block_no``) when the
        checksum does not match.
        """
        if not 0 <= block_no < self._num_blocks:
            raise IndexError("block %d out of range" % block_no)
        offset, length = self._index[block_no]
        f = self._f
        f.seek(offset)
        raw = f.read(length)
        if len(raw) < length:
            raise FormatError("truncated block %d" % block_no)
        enc, _flags, count, payload_len, crc = BLOCK_HEADER.unpack(
            raw[: BLOCK_HEADER.size]
        )
        payload = raw[BLOCK_HEADER.size :]
        if len(payload) != payload_len:
            raise CorruptBlockError(block_no, "payload length mismatch")
        if verify:
            actual = zlib.crc32(payload, zlib.crc32(raw[:8]))
            if actual != crc:
                raise CorruptBlockError(block_no)
        if enc == ENC_CONST:
            return [struct.unpack("<q", payload[:8])[0]] * count
        if enc == ENC_RAW:
            return list(struct.unpack("<%dq" % count, payload[: 8 * count]))
        if enc == ENC_FOR:
            lo, width = struct.unpack("<qB", payload[:9])
            bits = int.from_bytes(payload[9:], "little")
            mask = (1 << width) - 1
            return [lo + ((bits >> (j * width)) & mask) for j in range(count)]
        raise FormatError("block %d: unknown encoding %d" % (block_no, enc))

    def iter_values(self, start=0, stop=None, strict=True):
        """Yield values in ``[start, stop)`` block by block.

        With ``strict=False`` corrupted blocks are skipped and iteration
        continues with the next block; with ``strict=True`` the
        :class:`CorruptBlockError` propagates.
        """
        if stop is None or stop > self._count:
            stop = self._count
        if start < 0:
            start = 0
        bs = self._block_size
        block_no = start // bs
        while block_no * bs < stop and block_no < self._num_blocks:
            try:
                values = self.read_block(block_no)
            except CorruptBlockError:
                if strict:
                    raise
                block_no += 1
                continue
            base = block_no * bs
            lo = max(start - base, 0)
            hi = min(stop - base, len(values))
            for j in range(lo, hi):
                yield values[j]
            block_no += 1

    def check(self):
        """Verify every block; return the list of corrupted block numbers."""
        bad = []
        for block_no in range(self._num_blocks):
            try:
                self.read_block(block_no)
            except CorruptBlockError:
                bad.append(block_no)
        return bad

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self._count)
            if step == 1:
                return list(self.iter_values(start, stop))
            return [self.read(i) for i in range(start, stop, step)]
        return self.read(key)
