"""Chrome for Testing 自动下载与安装。

首次启动如果探测不到 chrome_binary / chromedriver，自动从 googlechromelabs.github.io
的 LKGV（last known good versions）清单取匹配平台的压缩包，解压到 ~/codes/ 下。
安装记录写入 data/chrome_install.json。

环境变量：
- CHROME_AUTO_INSTALL=0  关闭自动安装（默认开启）
- CHROME_CFT_CHANNEL     指定通道（Stable|Beta|Dev|Canary；默认 Stable）
"""

from __future__ import annotations

import json
import os
import platform as _platform
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import config
import utils.logger as logger

LKGV_URL = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
DEFAULT_CHANNEL = "Stable"
MANIFEST_PATH = Path("data/chrome_install.json")
AUTO_INSTALL_ENV = "CHROME_AUTO_INSTALL"
CHANNEL_ENV = "CHROME_CFT_CHANNEL"


def ensure_installed() -> dict | None:
    """探测本地 Chrome for Testing + chromedriver 是否就位；不就位则自动下载。"""
    chrome_binary = config.settings.system.chrome.chrome_binary
    chromedriver_path = config.settings.system.chrome.chromedriver_path

    if os.path.exists(chrome_binary) and _driver_executable_exists(chromedriver_path):
        manifest = _load_manifest()
        logger.info(
            {
                "msg": "Chrome for Testing 已就位，跳过自动安装",
                "chrome": chrome_binary,
                "chromedriver": chromedriver_path,
                "version": manifest.get("version") if manifest else "unknown",
            }
        )
        return manifest

    if os.getenv(AUTO_INSTALL_ENV, "1") == "0":
        logger.warning(
            {
                "msg": "Chrome for Testing 缺失但 CHROME_AUTO_INSTALL=0，跳过自动安装",
                "chrome_binary": chrome_binary,
                "chromedriver_path": chromedriver_path,
            }
        )
        return None

    cft_platform = _detect_cft_platform()
    channel = os.getenv(CHANNEL_ENV, DEFAULT_CHANNEL)
    logger.sys(f"Chrome for Testing 未就位，开始自动下载 (platform={cft_platform}, channel={channel})")

    version, chrome_url, driver_url = _fetch_download_urls(channel, cft_platform)
    install_root = _install_root()
    install_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cft-download-") as tmp:
        tmp_path = Path(tmp)
        chrome_zip = tmp_path / f"chrome-{cft_platform}.zip"
        driver_zip = tmp_path / f"chromedriver-{cft_platform}.zip"
        _download(chrome_url, chrome_zip)
        _download(driver_url, driver_zip)
        _extract_zip(chrome_zip, install_root)
        _extract_zip(driver_zip, install_root)

    chrome_dir = install_root / f"chrome-{cft_platform}"
    driver_dir = install_root / f"chromedriver-{cft_platform}"

    if sys.platform != "win32":
        _fix_permissions_unix(chrome_dir)
        _fix_permissions_unix(driver_dir)
    if sys.platform == "darwin":
        _remove_quarantine(chrome_dir)
        _remove_quarantine(driver_dir)

    driver_exe = _resolve_driver_executable(driver_dir)
    if not os.path.exists(chrome_binary):
        raise RuntimeError(
            f"安装完成但未在期望路径找到 chrome_binary={chrome_binary}；"
            f"请检查 ~/codes/chrome-{cft_platform}/ 结构是否符合预期"
        )

    manifest = {
        "version": version,
        "channel": channel,
        "platform": cft_platform,
        "chrome_binary": chrome_binary,
        "chromedriver_path": driver_exe,
        "install_root": str(install_root),
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_manifest(manifest)

    if not os.getenv("CHROMEDRIVER_PATH"):
        config.settings.system.chrome.chromedriver_path = driver_exe

    logger.sys(
        f"Chrome for Testing 安装完成: version={version} chrome={chrome_binary} driver={driver_exe}"
    )
    return manifest


def _detect_cft_platform() -> str:
    if sys.platform == "win32":
        return "win64"
    if sys.platform == "darwin":
        return "mac-arm64" if _platform.machine() == "arm64" else "mac-x64"
    return "linux64"


def _install_root() -> Path:
    return Path(os.path.expanduser("~")) / "codes"


def _driver_executable_exists(driver_path: str) -> bool:
    if not driver_path:
        return False
    if os.path.isfile(driver_path):
        return True
    if os.path.isdir(driver_path):
        name = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
        return os.path.isfile(os.path.join(driver_path, name))
    return False


def _resolve_driver_executable(driver_dir: Path) -> str:
    name = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
    candidate = driver_dir / name
    if candidate.is_file():
        return str(candidate)
    return str(driver_dir)


def _fetch_download_urls(channel: str, cft_platform: str) -> tuple[str, str, str]:
    with urllib.request.urlopen(LKGV_URL, timeout=30) as resp:
        data = json.load(resp)
    channels = data["channels"]
    key = channel.capitalize()
    if key not in channels:
        raise RuntimeError(f"未知 Chrome 通道: {channel} (可选: {list(channels.keys())})")
    entry = channels[key]
    version = entry["version"]
    downloads = entry["downloads"]
    chrome_url = _pick_url(downloads["chrome"], cft_platform)
    driver_url = _pick_url(downloads["chromedriver"], cft_platform)
    return version, chrome_url, driver_url


def _pick_url(entries: list[dict], cft_platform: str) -> str:
    for e in entries:
        if e.get("platform") == cft_platform:
            return e["url"]
    raise RuntimeError(f"未找到 platform={cft_platform} 的下载地址")


def _download(url: str, dest: Path) -> None:
    logger.sys(f"下载 {url}")
    start = time.time()
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        last_logged_pct = -1
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = downloaded * 100 // total
                if pct >= last_logged_pct + 10:
                    logger.sys(
                        f"  进度 {pct}% ({downloaded // (1024 * 1024)}/{total // (1024 * 1024)} MiB)"
                    )
                    last_logged_pct = pct
    elapsed = time.time() - start
    size_mib = dest.stat().st_size // (1024 * 1024)
    logger.sys(f"  完成 → {dest.name} ({size_mib} MiB, {elapsed:.1f}s)")


def _extract_zip(zip_path: Path, target: Path) -> None:
    logger.sys(f"解压 {zip_path.name} → {target}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target)


def _fix_permissions_unix(root: Path) -> None:
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file():
            try:
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass


def _remove_quarantine(root: Path) -> None:
    if not root.exists():
        return
    try:
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", str(root)],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except FileNotFoundError:
        pass


def _load_manifest() -> dict | None:
    if not MANIFEST_PATH.exists():
        return None
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    manifest = ensure_installed()
    if manifest:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
