"""Exception types raised by colint."""


class ColintError(Exception):
    """Base class for all colint errors."""


class FormatError(ColintError):
    """The file is not a valid colint file (bad magic, truncation, ...)."""


class CorruptBlockError(ColintError):
    """A data block failed its CRC32 check.

    Attributes:
        block_no: zero-based number of the corrupted block.
    """

    def __init__(self, block_no, detail="checksum mismatch"):
        self.block_no = block_no
        super().__init__("block %d corrupted: %s" % (block_no, detail))
