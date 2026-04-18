"""文件系统小工具：原子写入 + 旧文件清理。"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def atomic_write_text(path: str | Path, content: str) -> None:
    """原子写入文本文件：write → fsync → rename，crash 不会产生半写文件。

    POSIX rename 是原子的：要么完整替换，要么不动。
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


def cleanup_old_files(directory: str, max_age_days: int = 7, extensions: tuple = ()) -> int:
    """递归删除 directory 下 mtime 超过 max_age_days 的文件。

    extensions 给定时只删后缀匹配的文件。返回实际删除数。
    """
    if not os.path.isdir(directory):
        return 0

    cutoff = time.time() - max_age_days * 86400
    deleted = 0

    for root, _dirs, files in os.walk(directory):
        for name in files:
            if extensions and not name.endswith(extensions):
                continue
            filepath = os.path.join(root, name)
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    deleted += 1
            except OSError:
                pass

    return deleted
