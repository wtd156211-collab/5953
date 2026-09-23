"""icol 文件格式的常量与二进制布局定义（全部小端）。

文件整体布局::

    +----------------------+
    | 文件头 HEADER         |  固定 44 字节
    +----------------------+
    | 数据块 0              |  块头 12 字节 + 块体
    +----------------------+
    | 数据块 1              |
    +----------------------+
    | ...                   |
    +----------------------+
    | 块索引 INDEX          |  num_blocks * 16 字节
    +----------------------+

文件头 HEADER（44 字节，struct "<8sHHIQIQII"）::

    magic        8s   固定为 b"ICOLINT1"
    version      H    格式版本，当前为 1
    header_len   H    文件头长度（字节），当前为 44
    block_size   I    每块最大点数（最后一块可以不足）
    count        Q    全列总点数
    num_blocks   I    块数量
    index_offset Q    块索引在文件中的起始偏移
    index_crc    I    块索引区整体的 CRC32
    header_crc   I    文件头前 40 字节的 CRC32

块头 BLOCK_HEADER（12 字节，struct "<BBHII"）::

    codec        B    块内编码：0=RAW, 1=CONST, 2=DELTA_BITPACK
    reserved     B    保留，写 0
    count        H    本块点数
    payload_len  I    块体字节数
    crc32        I    对 (codec..payload_len 这 8 字节 + 块体) 的 CRC32

块体（依 codec 而定）::

    RAW:           count 个 int64 小端原样存放
    CONST:         1 个 int64（块内 count 个点全是它）
    DELTA_BITPACK: base(int64) + min_delta(int64) + w(uint8)
                   + ceil((count-1)*w/8) 字节的位打包数据。
                   第 i 个点 = base + i*min_delta + 前 i 个 offset 之和，
                   其中 offset[j] = delta[j] - min_delta（无符号 w 位），
                   delta[j] = value[j+1] - value[j]。
                   offset 按 LSB 在前打包进一个大整数后小端存盘。

块索引 INDEX：num_blocks 个定长条目（16 字节，struct "<QIHBB"）::

    file_offset  Q    块头在文件中的偏移
    total_len    I    块头 + 块体的总字节数（用于跳块扫描）
    count        H    本块点数（冗余，便于校验）
    codec        B    块内编码（冗余，便于校验）
    reserved     B    保留，写 0
"""

import struct

MAGIC = b"ICOLINT1"
VERSION = 1

HEADER = struct.Struct("<8sHHIQIQII")
HEADER_LEN = HEADER.size  # 44

BLOCK_HEADER = struct.Struct("<BBHII")
BLOCK_HEADER_PRE = struct.Struct("<BBHI")  # crc 之前的部分

INDEX_ENTRY = struct.Struct("<QIHBB")

CODEC_RAW = 0
CODEC_CONST = 1
CODEC_DELTA = 2

CODEC_NAMES = {
    CODEC_RAW: "RAW",
    CODEC_CONST: "CONST",
    CODEC_DELTA: "DELTA_BITPACK",
}

DEFAULT_BLOCK_SIZE = 512
