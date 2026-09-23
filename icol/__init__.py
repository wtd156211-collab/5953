"""icol：面向计数器时间序列的列式整数编码库。

- 分块编码（CONST / DELTA_BITPACK / RAW 自适应选择），整列明显小于定长 int64
- 文件尾部定长块索引，任意下标 O(1) 定位、块内直接解码，无需从头解压
- 每块 CRC32 校验，坏块报块号、可跳过，不影响其它块
- 输出确定：同一输入重复编码逐字节一致
- 只用标准库
"""

from .csvutil import iter_csv_ints, write_csv_ints
from .decoder import Reader
from .encoder import encode
from .errors import CorruptBlockError, FormatError, IColError
from .format import CODEC_NAMES, DEFAULT_BLOCK_SIZE

__all__ = [
    "encode",
    "Reader",
    "iter_csv_ints",
    "write_csv_ints",
    "IColError",
    "FormatError",
    "CorruptBlockError",
    "CODEC_NAMES",
    "DEFAULT_BLOCK_SIZE",
]

__version__ = "1.0.0"
