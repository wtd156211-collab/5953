"""icol 的异常类型。"""


class IColError(Exception):
    """icol 库所有异常的基类。"""


class FormatError(IColError):
    """文件不是合法的 icol 文件（魔数、版本、头校验或索引校验失败）。"""


class CorruptBlockError(IColError):
    """某个数据块未通过 CRC32 校验，或块体长度与块头不符。"""

    def __init__(self, block_no, detail="CRC32 mismatch"):
        self.block_no = block_no
        super().__init__("block %d corrupted: %s" % (block_no, detail))
