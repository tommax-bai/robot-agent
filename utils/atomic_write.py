"""原子文件写入：write → fsync → rename，防止 crash 导致数据丢失。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, content: str) -> None:
    """原子写入文本文件。先写临时文件并 fsync，再 rename 覆盖目标。

    POSIX rename 是原子的：要么完整替换，要么不动。即使 crash 也不会产生半写文件。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
