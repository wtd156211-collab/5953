"""icol 解码器：O(1) 定位的单点随机读、区间读与坏块隔离。

随机访问路径：下标 i -> 块号 i // block_size -> 查内存中的定长索引
（打开文件时一次性读入并校验）-> 按索引里的文件偏移直接 seek 到块头，
校验 CRC32 后只在块内解码出第 k 个点。耗时与下标无关。
"""

import os
import struct
import sys
import zlib
from array import array

from .errors import CorruptBlockError, FormatError
from .format import (
    BLOCK_HEADER,
    CODEC_CONST,
    CODEC_DELTA,
    CODEC_RAW,
    HEADER,
    INDEX_ENTRY,
    MAGIC,
    VERSION,
)

_unpack_q_at = struct.Struct("<q").unpack_from
_unpack_delta_hdr = struct.Struct("<qqB").unpack_from


def _decode_block(codec, count, payload):
    """把整个块解成 int 列表。"""
    if codec == CODEC_CONST:
        (v,) = struct.unpack("<q", payload[:8])
        return [v] * count
    if codec == CODEC_RAW:
        vals = array("q", payload)
        if sys.byteorder == "big":
            vals.byteswap()
        return list(vals)
    if codec == CODEC_DELTA:
        base, min_d, w = _unpack_delta_hdr(payload)
        nbits = w * (count - 1)
        packed = int.from_bytes(payload[17 : 17 + (nbits + 7) // 8], "little")
        out = [base]
        acc = base
        mask = (1 << w) - 1
        shift = 0
        for _ in range(count - 1):
            acc += min_d + ((packed >> shift) & mask)
            shift += w
            out.append(acc)
        return out
    raise FormatError("unknown codec: %d" % codec)


def _value_at(codec, count, payload, k):
    """只解出块内第 k 个点，不解整块。"""
    if codec == CODEC_CONST:
        (v,) = struct.unpack("<q", payload[:8])
        return v
    if codec == CODEC_RAW:
        return _unpack_q_at(payload, k * 8)[0]
    # DELTA_BITPACK：value[k] = base + k*min_d + 前 k 个 offset 之和
    base, min_d, w = _unpack_delta_hdr(payload)
    nbits = w * (count - 1)
    packed = int.from_bytes(payload[17 : 17 + (nbits + 7) // 8], "little")
    mask = (1 << w) - 1
    shift = 0
    acc = 0
    for _ in range(k):
        acc += (packed >> shift) & mask
        shift += w
    return base + k * min_d + acc


class Reader:
    """icol 文件的只读访问器。支持上下文管理器。"""

    def __init__(self, src, block_cache=8):
        own = isinstance(src, (str, bytes, os.PathLike))
        self._f = open(src, "rb") if own else src
        self._own = own
        try:
            self._read_header()
            self._read_index()
        except Exception:
            if own:
                self._f.close()
            raise
        self._cache = {}  # block_no -> list[int]，简单 LRU
        self._cache_order = []
        self._cache_cap = max(0, block_cache)

    # ---- 打开与元数据 -------------------------------------------------

    def _read_header(self):
        raw = self._f.read(HEADER.size)
        if len(raw) != HEADER.size:
            raise FormatError("file too short for header")
        fields = HEADER.unpack(raw)
        (magic, version, header_len, block_size, count,
         num_blocks, index_offset, index_crc, header_crc) = fields
        if magic != MAGIC:
            raise FormatError("bad magic: %r" % (magic,))
        if version != VERSION:
            raise FormatError("unsupported version: %d" % version)
        if header_len != HEADER.size:
            raise FormatError("bad header length: %d" % header_len)
        if zlib.crc32(raw[: HEADER.size - 4]) != header_crc:
            raise FormatError("header CRC32 mismatch")
        if block_size < 1:
            raise FormatError("bad block_size: %d" % block_size)
        self.block_size = block_size
        self.count = count
        self.num_blocks = num_blocks
        self._index_offset = index_offset
        self._index_crc = index_crc

    def _read_index(self):
        size = self.num_blocks * INDEX_ENTRY.size
        self._f.seek(self._index_offset)
        raw = self._f.read(size)
        if len(raw) != size:
            raise FormatError("index truncated")
        if zlib.crc32(raw) != self._index_crc:
            raise FormatError("index CRC32 mismatch")
        self._index = [
            INDEX_ENTRY.unpack_from(raw, i * INDEX_ENTRY.size)
            for i in range(self.num_blocks)
        ]

    def __len__(self):
        return self.count

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        if self._f is not None:
            if self._own:
                self._f.close()
            self._f = None

    # ---- 块读取与校验 --------------------------------------------------

    def _load_block(self, block_no):
        """按索引定位、读入并校验一个块，返回 (codec, count, payload)。"""
        offset, total_len, icount, codec, _ = self._index[block_no]
        self._f.seek(offset)
        raw = self._f.read(total_len)
        if len(raw) != total_len:
            raise CorruptBlockError(block_no, "block truncated")
        h_codec, _, h_count, payload_len, h_crc = BLOCK_HEADER.unpack(
            raw[: BLOCK_HEADER.size]
        )
        if BLOCK_HEADER.size + payload_len != total_len:
            raise CorruptBlockError(block_no, "payload length mismatch")
        crc = zlib.crc32(raw[:8])
        crc = zlib.crc32(raw[BLOCK_HEADER.size:], crc)
        if crc != h_crc:
            raise CorruptBlockError(block_no)
        if h_codec != codec or h_count != icount:
            raise CorruptBlockError(block_no, "header disagrees with index")
        return h_codec, h_count, raw[BLOCK_HEADER.size:]

    def _decoded_block(self, block_no):
        if block_no in self._cache:
            return self._cache[block_no]
        codec, count, payload = self._load_block(block_no)
        vals = _decode_block(codec, count, payload)
        if self._cache_cap:
            self._cache[block_no] = vals
            self._cache_order.append(block_no)
            if len(self._cache_order) > self._cache_cap:
                old = self._cache_order.pop(0)
                del self._cache[old]
        return vals

    # ---- 公开读接口 ----------------------------------------------------

    def read(self, i):
        """读出下标 i 处的值（支持负下标）。耗时与 i 无关。"""
        if i < 0:
            i += self.count
        if not 0 <= i < self.count:
            raise IndexError("index out of range: %d" % i)
        block_no, k = divmod(i, self.block_size)
        cached = self._cache.get(block_no)
        if cached is not None:
            return cached[k]
        codec, count, payload = self._load_block(block_no)
        return _value_at(codec, count, payload, k)

    __getitem__ = read

    def read_range(self, start, stop=None, skip_corrupt=False):
        """读出 [start, stop) 区间，返回 int 列表。

        skip_corrupt=True 时跳过校验失败的块（这些块的点不出现在结果里），
        否则遇到坏块抛 CorruptBlockError（异常里带块号）。
        """
        if stop is None:
            start, stop = 0, start
        if start < 0:
            start += self.count
        if stop < 0:
            stop += self.count
        start = max(0, start)
        stop = min(self.count, stop)
        out = []
        b = start // self.block_size
        while b * self.block_size < stop and b < self.num_blocks:
            lo = b * self.block_size
            hi = min(lo + self.block_size, self.count)
            try:
                vals = self._decoded_block(b)
            except CorruptBlockError:
                if not skip_corrupt:
                    raise
                b += 1
                continue
            out.extend(vals[max(0, start - lo): stop - lo])
            b += 1
        return out

    def blocks(self, skip_corrupt=False):
        """按块迭代，产出 (block_no, values)。"""
        for b in range(self.num_blocks):
            try:
                yield b, self._decoded_block(b)
            except CorruptBlockError:
                if not skip_corrupt:
                    raise

    def values(self, skip_corrupt=False):
        """流式产出全列的值。"""
        for _, vals in self.blocks(skip_corrupt=skip_corrupt):
            yield from vals

    def verify(self):
        """逐块校验，返回未通过校验的块号列表（不抛异常）。"""
        bad = []
        for b in range(self.num_blocks):
            try:
                self._load_block(b)
            except CorruptBlockError:
                bad.append(b)
        return bad
