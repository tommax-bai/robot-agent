"""
旧文件清理工具：定期删除过期的 action trace / history / screenshot 文件，
防止磁盘无限增长。
"""

from __future__ import annotations

import os
import time


def cleanup_old_files(directory: str, max_age_days: int = 7, extensions: tuple = ()) -> int:
    """Delete files older than max_age_days. If extensions specified, only delete matching files.
    Returns count of deleted files."""
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
