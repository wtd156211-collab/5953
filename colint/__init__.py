"""colint: a columnar integer encoding library with O(1) random access.

Self-contained single-file format: file header, fixed-size data blocks
(each with its own CRC32), and a block index appended at the end.
"""

from .decoder import Reader
from .encoder import Encoder, encode
from .errors import ColintError, CorruptBlockError, FormatError
from .format import DEFAULT_BLOCK_SIZE

__version__ = "1.0.0"

__all__ = [
    "Encoder",
    "Reader",
    "encode",
    "ColintError",
    "CorruptBlockError",
    "FormatError",
    "DEFAULT_BLOCK_SIZE",
    "__version__",
]
