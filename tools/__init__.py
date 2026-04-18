"""
tools/ 层：Agent 与外部世界交互的两类门面。

- environment.Environment  — 执行环境协议（capture + perform）
- llm.LlmTool              — LLM 调用门面

三个内置 Environment 实现：
- tools.macos_chrome.MacOSChromeEnv  — 本地 macOS Chrome（PyAutoGUI）
- tools.cloud_mobile.CloudMobileEnv  — 阿里无影云手机（AgentBay SDK）
- tools.remote.RemoteEnv             — 通过 HTTP 代理到 Session Service

一个 Strategy 实现（不是 env）：
- tools.remote.RemoteDelegateStrategy — agentbay 模式下的远程委托
"""

from __future__ import annotations
