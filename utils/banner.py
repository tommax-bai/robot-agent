"""启动 banner：把 dashboard / API 的可访问地址写进日志，方便复制。"""
from __future__ import annotations

import os
import socket

import utils.logger as logger


def _lan_ips() -> list[str]:
    """枚举本机所有非 loopback 的 IPv4 地址（跨 Mac/Linux/Win 通用）。"""
    ips: set[str] = set()

    # 主路由 IP：指向默认网关那张网卡，通常就是 LAN 地址
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    # 所有网卡 IP：兜底多网段
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if "." in ip and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    return sorted(ips)


def log_dashboard_urls(topology: str | None = None) -> None:
    """启动日志：dashboard URL + LAN 地址 + API 根路径 + 安全提示。"""
    port = os.getenv("AGENT_HTTP_PORT", "6702")
    lines = [
        "═══════════════════════════════════════════════════════",
        f"  📊 Dashboard      http://127.0.0.1:{port}/dashboard",
    ]
    for ip in _lan_ips():
        lines.append(f"  🌐 局域网访问     http://{ip}:{port}/dashboard")
    lines.extend([
        f"  ⚙️  API 根路径     http://127.0.0.1:{port}/api/v1",
        "  ⚠️  当前 API 无认证；开 0.0.0.0 + LAN = 网内任何人都能控制 agent",
    ])
    if topology:
        lines.append(f"  🧭 Topology      {topology}")
    lines.append("═══════════════════════════════════════════════════════")
    for line in lines:
        logger.info({"msg": line})


__all__ = ["log_dashboard_urls"]
