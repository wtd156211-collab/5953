"""icol 编码器：把整数列流式写成一个自包含文件。

内存占用与点数无关：每次只在内存里缓冲一个块（block_size 个点），
编码完立即落盘，索引条目每块只有 16 字节。
"""

import os
import struct
import sys
import zlib
from array import array

from .format import (
    BLOCK_HEADER,
    BLOCK_HEADER_PRE,
    CODEC_CONST,
    CODEC_DELTA,
    CODEC_RAW,
    DEFAULT_BLOCK_SIZE,
    HEADER,
    INDEX_ENTRY,
    MAGIC,
    VERSION,
)

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
# delta 偏移量（max_delta - min_delta）超过这个宽度就不如按原样存
_MAX_PACK_WIDTH = 56

_pack_q = struct.Struct("<q").pack
_pack_i = struct.Struct("<I").pack


def _encode_block(vals):
    """为一个块选择编码并返回 (codec, payload)。

    选择规则：
    - 全块同值 -> CONST
    - 差分偏移位宽不超过 56 且位打包后比 RAW 小 -> DELTA_BITPACK
    - 其余（含 int64 极值导致差分溢出 Python 无符号 63 位的情况）-> RAW
    """
    n = len(vals)
    first = vals[0]
    if n == 1 or vals.count(first) == n:
        return CODEC_CONST, _pack_q(first)

    base = first
    prev = first
    min_d = None
    max_d = None
    deltas = []
    for v in vals[1:]:
        d = v - prev
        prev = v
        deltas.append(d)
        if min_d is None or d < min_d:
            min_d = d
        if max_d is None or d > max_d:
            max_d = d

    spread = max_d - min_d  # 非负
    if spread < (1 << _MAX_PACK_WIDTH):
        w = spread.bit_length()
        payload_len = 17 + (w * (n - 1) + 7) // 8
        if payload_len < 8 * n:
            packed = 0
            shift = 0
            for d in deltas:
                packed |= (d - min_d) << shift
                shift += w
            payload = struct.pack("<qqB", base, min_d, w)
            if w:
                payload += packed.to_bytes((w * (n - 1) + 7) // 8, "little")
            return CODEC_DELTA, payload

    raw = array("q", vals)
    if sys.byteorder == "big":
        raw.byteswap()
    return CODEC_RAW, raw.tobytes()


def _write_block(f, vals, index):
    codec, payload = _encode_block(vals)
    pre = BLOCK_HEADER_PRE.pack(codec, 0, len(vals), len(payload))
    crc = zlib.crc32(pre)
    crc = zlib.crc32(payload, crc)
    offset = f.tell()
    f.write(pre)
    f.write(_pack_i(crc))
    f.write(payload)
    index.append((offset, BLOCK_HEADER.size + len(payload), len(vals), codec, 0))


def encode(values, out, block_size=DEFAULT_BLOCK_SIZE):
    """把整数列 values（任意可迭代对象）编码写入 out。

    out 可以是路径，也可以是已打开的二进制可写文件对象（需支持 seek）。
    返回写入的总点数。输出的字节序列只取决于输入数据与 block_size，
    不含时间戳或随机内容，同一输入重复编码结果逐字节一致。
    """
    if not 2 <= block_size <= 65535:
        raise ValueError("block_size must be in [2, 65535]")

    own = isinstance(out, (str, bytes, os.PathLike))
    f = open(out, "wb") if own else out
    try:
        f.write(bytes(HEADER.size))  # 头部占位，收尾时回填

        index = []
        count = 0
        buf = []
        for v in values:
            if not INT64_MIN <= v <= INT64_MAX:
                raise ValueError("value out of int64 range: %r" % (v,))
            buf.append(v)
            if len(buf) == block_size:
                _write_block(f, buf, index)
                count += len(buf)
                buf.clear()
        if buf:
            _write_block(f, buf, index)
            count += len(buf)

        index_offset = f.tell()
        index_bytes = b"".join(INDEX_ENTRY.pack(*e) for e in index)
        f.write(index_bytes)
        index_crc = zlib.crc32(index_bytes)

        header = HEADER.pack(
            MAGIC, VERSION, HEADER.size, block_size,
            count, len(index), index_offset, index_crc, 0,
        )
        header = header[: HEADER.size - 4] + _pack_i(
            zlib.crc32(header[: HEADER.size - 4])
        )
        f.seek(0)
        f.write(header)
    finally:
        if own:
            f.close()
    return count
