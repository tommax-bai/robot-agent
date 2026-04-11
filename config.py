import os

def _load_text_file(path: str, default: str = "") -> str:
    """从文件加载文本内容，失败则返回默认值"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default

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
        "chrome_binary": os.getenv("CHROME_BINARY", os.path.join(os.path.expanduser("~"), "codes/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")),
        # ChromeDriver 路径（macOS），通过环境变量或此处配置
        "chromedriver_path": os.getenv("CHROMEDRIVER_PATH", os.path.join(os.path.expanduser("~"), "codes/chromedriver-mac-arm64/chromedriver")),
    }
}
# 大模型相关配置，如何OpenAI标准的 LLM、VLM 均可在这配置

model = {
    "clients": {
        "doubao": {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "[REMOVED-SECRET]",
        },
        "siliconflow": {
            "base_url": "https://api.siliconflow.com/v1",
            "api_key": "[REMOVED-SECRET]",
        },
        "zenmux": {
            "base_url": "https://zenmux.ai/api/v1",
            "api_key": "[REMOVED-SECRET]"
        }
    },
    # E3 - LLM 客户端 LRU 缓存大小
    "lru_cache_max_size": 10,
}

# 智能体大脑与灵感配置
agent = {
    "default_mode": "patrolling",
    #"default_mode": "waiting",
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
        "max_tokens": 2048
    },
    # E1 - Agent (ReAct) 模型配置
    "operator": {
        "model": "google/gemini-3-flash-preview",
        "llm_client": "zenmux",
        "temperature": 0.0
    },
    # E1 - 每日养号与增长策略配置
    "maintenance": {
        # 核心策略（定义 agent 的身份与立场）
        "core_strategy": "你是一个深耕 AI 产业链的’职业进化导航员’。你对无意义的职场八卦毫无兴趣，你只关心打工人在 AI 浪潮下的生存与溢价。你擅长剖析传统行业转行 AI 的路径、大模型数据训练的行业内幕以及高质量人才的定价逻辑。你的立场是’理性搞钱，专业避坑’，通过分享行业前瞻洞察和硬核招聘需求，引导高素质人才实现职业赛道的降维打击。",
        # 人设（名称、性格、文风）
        "persona": {
            "name": "AI 时代职业导航员",
            "character": "极度理性的行业观察者，对’信息差’有种近乎本能的掌控欲。性格沉稳、直击痛点、专业且克制。你认为大模型时代是打工人重启赛道的最佳时机，而你负责提供最真实的准入标准和晋升路径。你对’算法迭代’、’RLHF’、’高阶标注’等关键词有极高的兴奋度。",
            "style": "文案要充满’洞察感’和’降维打击’的专业度。多用’底层逻辑’、’行业窗口期’、’人才画像’、’有效时薪’等词汇。拒绝感性废话，用事实和数据拆解行业现状。评论互动要像资深猎头，冷静、精准、提供解决方案。"
        },
        # 招聘植入信息（用于发帖时的"神转折"钩子）
        "recruitment_info": _load_text_file("prompts/recruitment_info.txt", "正在招聘优秀人才"),
        # 各类定时任务的自然语言目标（传给 operator/planner 的 user_goal）
        "task_goals": {
            "dm": "查看并回复小红书私信",
            "cr": "查看并回复小红书评论",
        },
        # 运行约束
        "max_posts_per_day": 5,
        "daily_schedule": [
            {"start": "08:00", "end": "22:00", "task": "patrol"},
        ],
        "patrol_rest_between_rounds": [300, 600],
        # 基础设施
        "state_file": "data/agent_state.json",
        "save_titles_to_local": True,
    }
}


# ═══════════════════════════════════════════════════════
# 配置 Schema 声明 & 启动校验
# ═══════════════════════════════════════════════════════

# 必需的配置 schema：(key 路径, 期望类型) 列表
# key 路径用 . 分隔，例如 "agent.maintenance.persona.name"
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

    # agent.maintenance
    ("agent.maintenance.core_strategy", str),
    ("agent.maintenance.persona.name", str),
    ("agent.maintenance.persona.character", str),
    ("agent.maintenance.persona.style", str),
    ("agent.maintenance.recruitment_info", str),
    ("agent.maintenance.task_goals", dict),
    ("agent.maintenance.max_posts_per_day", int),
    ("agent.maintenance.daily_schedule", list),
    ("agent.maintenance.patrol_rest_between_rounds", list),
    ("agent.maintenance.state_file", str),
    ("agent.maintenance.save_titles_to_local", bool),
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
            errors.append(
                f"config 字段类型错误: {path} 期望 {expected_type.__name__}, 实际 {type(value).__name__}"
            )

    if errors:
        msg = "配置校验失败:\n  - " + "\n  - ".join(errors)
        raise RuntimeError(msg)