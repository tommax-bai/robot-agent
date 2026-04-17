from __future__ import annotations

import os


def _load_text_file(path: str, default: str = "") -> str:
    """从文件加载文本内容，失败则返回默认值"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


def _env(name: str, default: str = "") -> str:
    """读取环境变量，避免把密钥写进代码仓库。"""
    return os.getenv(name, default).strip()


def _env_executable_path(name: str, default: str, executable_name: str) -> str:
    path = _env(name, default)
    if path and os.path.isdir(path):
        return os.path.join(path, executable_name)
    return path


system = {
    # 屏幕尺寸，无需修改，程序初始化时会自动设置，并打印日志
    "screen_size": {
        "width": 1920,
        "height": 1200,
        "scale": 1.0,
    },
    # 系统信息，无需修改，程序初始化时会自动设置，并打印日志
    "system_info": None,
    "screenshot": {
        # 是否持久化保存，默认关闭，如果开启则需要同时设置save_dir
        # 该配置代表当进行屏幕截图时，本地是否保留一份，如果关闭则不保留
        "persist": True,
        "save_dir": "data",
    },
    # Chrome相关配置信息
    "chrome": {
        # E2 - 调试端口，用于Python服务和 Chrome 通信
        "debug_port": 9222,
        # E3 - 用户数据目录
        "profile_dir": os.path.join(os.path.expanduser("~"), "chrome_debug_profile"),
        # E0 - 用于启动Chrome的命令。
        "chrome_command": None,
        # E3 - Chrome-ip 因chrome安全策略限制，因此无法设置成公网，不建议修改
        "chrome_ip": "127.0.0.1",
        # Chrome 可执行文件路径（macOS），通过环境变量或此处配置
        "chrome_binary": _env(
            "CHROME_BINARY",
            os.path.join(
                os.path.expanduser("~"),
                "codes/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            ),
        ),
        # ChromeDriver 路径（macOS），通过环境变量或此处配置
        "chromedriver_path": _env_executable_path(
            "CHROMEDRIVER_PATH",
            os.path.join(os.path.expanduser("~"), "codes/chromedriver-mac-arm64/chromedriver"),
            "chromedriver.exe" if os.name == "nt" else "chromedriver",
        ),
    },
}
# 大模型相关配置，如何OpenAI标准的 LLM、VLM 均可在这配置

model = {
    "clients": {
        "doubao": {
            "base_url": _env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            "api_key": _env("DOUBAO_API_KEY"),
            "env_var": "DOUBAO_API_KEY",
        },
        "siliconflow": {
            "base_url": _env("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1"),
            "api_key": _env("SILICONFLOW_API_KEY"),
            "env_var": "SILICONFLOW_API_KEY",
        },
        "zenmux": {
            "base_url": _env("ZENMUX_BASE_URL", "https://zenmux.ai/api/v1"),
            "api_key": _env("ZENMUX_API_KEY"),
            "env_var": "ZENMUX_API_KEY",
        },
    },
    # E3 - LLM 客户端 LRU 缓存大小
    "lru_cache_max_size": 10,
}

# 智能体大脑与灵感配置
agent = {
    # "default_mode": "patrolling",
    "default_mode": "waiting",
    # E1 - agent_state 各字段的存储上限
    "state_limits": {
        "inspiration_pool": 30,
        "title_few_shots": 30,
        "hashtags": 50,
        "recent_searches": 30,
        "learning_notes": 20,
        "anxiety_keywords": 30,
        "knowledge_topics": 30,
        "followers_history": 30,
    },
    # E1 - Agent (Planner) 模型配置
    "planner": {
        "model": "google/gemini-3-flash-preview",
        "llm_client": "zenmux",
        "temperature": 0.9,
        "max_tokens": 2048,
    },
    # E1 - Agent (ReAct) 模型配置
    "operator": {
        "model": "google/gemini-3-flash-preview",
        "llm_client": "zenmux",
        "temperature": 0.0,
    },
    # E1 - 页面分类模型配置。用于识别是否遇到新页面，并写入本地页面库。
    "page_classifier": {
        "enabled": True,
        "model": "google/gemini-3-flash-preview",
        "llm_client": "zenmux",
        "temperature": 0.0,
        "max_tokens": 4096,
        "record_min_confidence": 0.72,
        "background_enabled": True,
        "background_workers": 1,
        "local_match_enabled": True,
        "local_match_min_score": 0.75,
        "registry_file": "data/page_registry/pages.json",
    },
    # E1 - 旁路行为总结配置。用于从成功操作轨迹中生成可复用动作候选。
    "behavior_summarizer": {
        "enabled": True,
        "background_enabled": True,
        "background_workers": 1,
        "model": "google/gemini-3-flash-preview",
        "llm_client": "zenmux",
        "temperature": 0.0,
        "max_tokens": 2048,
        "trace_root": "data/action_traces",
        "candidate_root": "data/action_recipe_candidates",
        "max_events": 8,
        "auto_enable": False,
        "auto_enable_min_confidence": 0.92,
    },
    # E1 - Agent 人设与身份
    "persona": {
        "name": "AI 时代职业导航员",
        "character": "极度理性的行业观察者，对’信息差’有种近乎本能的掌控欲。性格沉稳、直击痛点、专业且克制。你认为大模型时代是打工人重启赛道的最佳时机，而你负责提供最真实的准入标准和晋升路径。你对’算法迭代’、’RLHF’、’高阶标注’等关键词有极高的兴奋度。",
        "style": "文案要充满’洞察感’和’降维打击’的专业度。多用’底层逻辑’、’行业窗口期’、’人才画像’、’有效时薪’等词汇。拒绝感性废话，用事实和数据拆解行业现状。评论互动要像资深猎头，冷静、精准、提供解决方案。",
        "strategy": "你是一个深耕 AI 产业链的’职业进化导航员’。你对无意义的职场八卦毫无兴趣，你只关心打工人在 AI 浪潮下的生存与溢价。你擅长剖析传统行业转行 AI 的路径、大模型数据训练的行业内幕以及高质量人才的定价逻辑。你的立场是’理性搞钱，专业避坑’，通过分享行业前瞻洞察和硬核招聘需求，引导高素质人才实现职业赛道的降维打击。",
        "recruitment_info": _load_text_file("prompts/recruitment_info.txt", "正在招聘优秀人才"),
    },
    # E1 - 调度与运行约束
    "schedule": {
        "task_goals": {
            "dm": "查看并回复小红书私信",
            "cr": "查看并回复小红书评论",
        },
        "max_posts_per_day": 5,
        "daily_schedule": [
            {"start": "08:00", "end": "22:00", "task": "patrol"},
        ],
        "patrol_rest_between_rounds": [300, 600],
    },
    # E1 - 存储与持久化
    "storage": {
        "state_file": "data/agent_state.json",
        "save_titles_to_local": True,
        "cleanup": {
            "enabled": _env("AGENT_CLEANUP_ENABLED", "true").lower() != "false",
            "max_age_days": int(_env("AGENT_CLEANUP_MAX_AGE_DAYS", "7")),
        },
    },
    # E1 - 执行运行时（决定 backend / 决策策略的组合，可 per-worker 热切换）
    # mode 可选：
    #   "local_chrome"   — 本地 macOS Chrome（PyAutoGUI + 自家 VLM）
    #   "cloudmobile"   — AgentBay 云手机 + 自家 VLM（VisionActionStep 循环）
    #   "agentbay" — AgentBay 云手机 + AgentBay 内置 mobile_use Agent（委托黑盒）
    "runtime": {
        "mode": _env("AGENT_RUNTIME_MODE", "local_chrome"),
        "agentbay": {
            "api_key": _env("AGENTBAY_API_KEY"),
            "image_id": _env("AGENTBAY_IMAGE_ID", "mobile_latest"),
            # 截图压缩：jpeg 体积小，VLM 输入更省 token;png 无损但更大
            "screenshot_format": _env("AGENTBAY_SCREENSHOT_FORMAT", "jpeg"),
            # 阿里端 idle 自动回收的超时秒数；防止 SIGKILL 留下的孤儿无限计费。
            # None 走阿里默认（套餐定）；自己设建议 600 (10min) 起步。
            "idle_release_timeout": int(_env("AGENTBAY_IDLE_RELEASE_TIMEOUT", "600") or "600"),
        },
        # 启动时自动清理上一次进程遗留的 AgentBay session。
        # ⚠️ 仅在"一个 API key 配一个 agent 进程"时启用；如果同 key 跑多副本会互踢。
        "auto_cleanup_orphans_on_startup": _env("AGENT_AUTO_CLEANUP_ORPHANS", "true").lower() != "false",
        # 拆分部署：Brain Service 通过此 URL 调用 Session Service。
        # 留空 = 单进程模式（向后兼容）。
        "session_service_url": _env("SESSION_SERVICE_URL", ""),
        "session_service_api_key": _env("SESSION_SERVICE_API_KEY", ""),
    },
    # E1 - 多账号矩阵（空列表则退化为单 default worker；dashboard 也支持运行时 POST /agent/workers 动态加）
    # 每个 account 字段：
    #   id (str, 必填)              — 账号唯一标识，影响日志 trace 前缀和 state 路径
    #   display_name (str, 可选)    — dashboard 展示名
    #   image_id (str, 可选)        — AgentBay 镜像 ID，缺省走全局 runtime.agentbay.image_id
    #                                 （通常都是 "mobile_latest"，登录在 session 创建后人工扫码完成）
    #   screenshot_format (可选)    — 覆盖 runtime.agentbay.screenshot_format
    #   state_file (str, 可选)      — 覆盖 state 文件路径，默认 data/accounts/{id}/agent_state.json
    #   mode (str, 可选)            — 覆盖初始 runtime.mode，后续可通过 dashboard 热切换
    #   description (str, 可选)     — 备注
    #
    # 登录流程：每个 worker 第一次发任务时拉起一个全新 AgentBay session（云端 Android）。
    # 通过 dashboard 的 live view 打开该 session 的串流页，**用真机扫码登录小红书**。
    # session 销毁后登录态丢失，下次重新扫码（不做镜像快照、不做自动登录）。
    "accounts": [
        # 示例（注释掉，按需启用）：
        # {"id": "acct-a", "display_name": "导航员-A"},
        # {"id": "acct-b", "display_name": "导航员-B"},
    ],
}


# ═══════════════════════════════════════════════════════
# 配置 Schema 声明 & 启动校验
# ═══════════════════════════════════════════════════════

# 必需的配置 schema：(key 路径, 期望类型) 列表
# key 路径用 . 分隔，例如 "agent.persona.name"
_REQUIRED_SCHEMA = [
    # system
    ("system.screen_size.width", int),
    ("system.screen_size.height", int),
    ("system.screen_size.scale", (int, float)),
    ("system.screenshot.persist", bool),
    ("system.screenshot.save_dir", str),
    ("system.chrome.debug_port", int),
    ("system.chrome.chrome_ip", str),
    ("system.chrome.profile_dir", str),
    ("system.chrome.chrome_binary", str),
    ("system.chrome.chromedriver_path", str),
    # model
    ("model.clients", dict),
    ("model.lru_cache_max_size", int),
    # agent
    ("agent.default_mode", str),
    ("agent.state_limits", dict),
    ("agent.planner.model", str),
    ("agent.planner.llm_client", str),
    ("agent.planner.temperature", (int, float)),
    ("agent.planner.max_tokens", int),
    ("agent.operator.model", str),
    ("agent.operator.llm_client", str),
    ("agent.operator.temperature", (int, float)),
    ("agent.page_classifier.enabled", bool),
    ("agent.page_classifier.model", str),
    ("agent.page_classifier.llm_client", str),
    ("agent.page_classifier.temperature", (int, float)),
    ("agent.page_classifier.max_tokens", int),
    ("agent.page_classifier.record_min_confidence", (int, float)),
    ("agent.page_classifier.background_enabled", bool),
    ("agent.page_classifier.background_workers", int),
    ("agent.page_classifier.local_match_enabled", bool),
    ("agent.page_classifier.local_match_min_score", (int, float)),
    ("agent.page_classifier.registry_file", str),
    ("agent.behavior_summarizer.enabled", bool),
    ("agent.behavior_summarizer.background_enabled", bool),
    ("agent.behavior_summarizer.background_workers", int),
    ("agent.behavior_summarizer.model", str),
    ("agent.behavior_summarizer.llm_client", str),
    ("agent.behavior_summarizer.temperature", (int, float)),
    ("agent.behavior_summarizer.max_tokens", int),
    ("agent.behavior_summarizer.trace_root", str),
    ("agent.behavior_summarizer.candidate_root", str),
    ("agent.behavior_summarizer.max_events", int),
    ("agent.behavior_summarizer.auto_enable", bool),
    ("agent.behavior_summarizer.auto_enable_min_confidence", (int, float)),
    # agent.persona
    ("agent.persona.name", str),
    ("agent.persona.character", str),
    ("agent.persona.style", str),
    ("agent.persona.strategy", str),
    ("agent.persona.recruitment_info", str),
    # agent.schedule
    ("agent.schedule.task_goals", dict),
    ("agent.schedule.max_posts_per_day", int),
    ("agent.schedule.daily_schedule", list),
    ("agent.schedule.patrol_rest_between_rounds", list),
    # agent.storage
    ("agent.storage.state_file", str),
    ("agent.storage.save_titles_to_local", bool),
    ("agent.storage.cleanup.enabled", bool),
    ("agent.storage.cleanup.max_age_days", int),
    # agent.runtime
    ("agent.runtime.mode", str),
    ("agent.runtime.agentbay.api_key", str),
    ("agent.runtime.agentbay.image_id", str),
    ("agent.runtime.agentbay.screenshot_format", str),
    ("agent.runtime.agentbay.idle_release_timeout", int),
    ("agent.runtime.auto_cleanup_orphans_on_startup", bool),
    # agent.accounts (空列表时退化为单 default worker；非空时每项必须含 id)
    ("agent.accounts", list),
]


def _resolve_path(path: str):
    """根据 'a.b.c' 路径从模块级 dict 中取值，缺失抛 KeyError。"""
    parts = path.split(".")
    root_name, rest = parts[0], parts[1:]
    obj = globals().get(root_name)
    if obj is None:
        raise KeyError(f"config 缺少根命名空间: {root_name}")
    for p in rest:
        if not isinstance(obj, dict) or p not in obj:
            raise KeyError(f"config 缺少必需键: {path}")
        obj = obj[p]
    return obj


def _type_name(expected_type) -> str:
    if isinstance(expected_type, tuple):
        return " | ".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def validate():
    """
    校验所有必需配置项存在且类型正确。失败时抛 RuntimeError。
    应在应用启动时调用一次，校验通过后代码可放心直接索引。
    """
    errors = []
    for path, expected_type in _REQUIRED_SCHEMA:
        try:
            value = _resolve_path(path)
        except KeyError as e:
            errors.append(str(e))
            continue
        if not isinstance(value, expected_type):
            errors.append(f"config 字段类型错误: {path} 期望 {_type_name(expected_type)}, 实际 {type(value).__name__}")

    if errors:
        msg = "配置校验失败:\n  - " + "\n  - ".join(errors)
        raise RuntimeError(msg)
