"""On-disk format constants for colint files.

File layout (all integers little-endian):

    +----------------------+
    | file header (32 B)   |  magic, count, block_size, num_blocks, index_offset
    +----------------------+
    | block 0              |  block header (12 B) + payload
    | block 1              |
    | ...                  |
    +----------------------+
    | block index          |  num_blocks x 12 B: (offset, length)
    +----------------------+

Block header (12 B): encoding u8, flags u8, count u16, payload_len u32, crc32 u32.
The CRC32 covers the first 8 header bytes plus the payload.
"""

import struct

MAGIC = b"CLINT001"

# magic(8s), count(Q), block_size(I), num_blocks(I), index_offset(Q)
FILE_HEADER = struct.Struct("<8sQIIQ")

# encoding(B), flags(B), count(H), payload_len(I), crc32(I)
BLOCK_HEADER = struct.Struct("<BBHII")

# CRC covers these leading header bytes (everything except the crc field itself).
BLOCK_HEADER_CRC_PART = struct.Struct("<BBHI")

# file offset(Q), total block length including header(I)
INDEX_ENTRY = struct.Struct("<QI")

ENC_CONST = 0  # payload: value q
ENC_FOR = 1    # payload: min q, width B, bit-packed (value - min)
ENC_RAW = 2    # payload: count x q

DEFAULT_BLOCK_SIZE = 256
MAX_BLOCK_SIZE = 65535

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
