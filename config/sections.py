"""Pydantic 配置模型：类型化、自校验、可在启动后继续修改（部分字段由 bootstrap 回填）。

命名约定：
  `XxxCfg`  — 子节点配置
  `AppSettings` — 根对象
  顶层属性 `system` / `agent` / `llm` 分别对应旧 dict 的三大命名空间
  （旧 `config.model` 里除了客户端其实只有 `lru_cache_max_size`，命名成 `llm` 更贴切）
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# 让所有 BaseModel 允许以 `model_*` 命名的字段不报 protected_namespace 警告。
_model_config = ConfigDict(protected_namespaces=())


# ── System ──────────────────────────────────────────────────

class ScreenSize(BaseModel):
    model_config = _model_config
    width: int = 1920
    height: int = 1200
    scale: float = 1.0


class ScreenshotProfileCfg(BaseModel):
    """给 LLM 的截图后处理参数。两档：chromelocal（激进省 token）/ wuyingcloud（保守保字形）。

    处理管线顺序（按需开启）：
      调色板量化(palette_colors) → 饱和度调整(saturation) → posterize → JPEG/PNG 编码

    字段语义：
      - format: 输出格式，jpeg 省流，png 无损
      - quality: 仅 JPEG 有效（1-100）
      - palette_colors: None=关，2-256=量化到 N 色再转回 RGB（桌面可开，移动端别开）
      - posterize_bits: None=关，1-8=每通道保留位数（5 即 32 级）
      - saturation: 1.0=原色，<1.0 降饱和（进一步省 JPEG 体积）
    """
    model_config = _model_config
    format: str = "jpeg"
    quality: int = 75
    palette_colors: int | None = 128
    posterize_bits: int | None = 5
    saturation: float = 1.0


class ScreenshotCfg(BaseModel):
    model_config = _model_config
    persist: bool = True
    save_dir: str = "data"
    chromelocal: ScreenshotProfileCfg = Field(
        default_factory=lambda: ScreenshotProfileCfg(
            format="jpeg",
            quality=75,
            palette_colors=128,
            posterize_bits=5,
            saturation=0.7,
        )
    )
    wuyingcloud: ScreenshotProfileCfg = Field(
        default_factory=lambda: ScreenshotProfileCfg(
            format="jpeg",
            quality=85,
            palette_colors=None,
            posterize_bits=None,
            saturation=1.0,
        )
    )


class InputHumanizeCfg(BaseModel):
    """wuyingcloud 端的拟人化输入参数。默认全关 → 等价旧行为；按需开启。

    字段语义：
      - paste_per_char: True=逐字符 input_text + 随机停顿；False=一次性 input_text
      - paste_min_delay_ms / paste_max_delay_ms: 单字符之间的均匀抖动区间（毫秒）
      - tap_jitter_px: tap 坐标 ±N 像素抖动；0=关
      - pre_action_pause: 每个动作前后插入随机停顿（与 chromelocal 对齐）
    """
    model_config = _model_config
    paste_per_char: bool = False
    paste_min_delay_ms: int = 60
    paste_max_delay_ms: int = 180
    tap_jitter_px: int = 0
    pre_action_pause: bool = False


class ChromeCfg(BaseModel):
    model_config = _model_config
    debug_port: int = 9222
    chrome_ip: str = "127.0.0.1"
    profile_dir: str
    chrome_binary: str
    chromedriver_path: str
    # chrome_command 在 bootstrap.chrome_launch.prepare() 里按平台拼好后回填。
    chrome_command: list[str] | None = None


class SystemCfg(BaseModel):
    """基础设施。`system_info` / `screen_size` / `chrome.chrome_command` / `chrome.chromedriver_path`
    会在 bootstrap 阶段由平台探测结果回填。"""
    model_config = _model_config
    screen_size: ScreenSize = Field(default_factory=ScreenSize)
    system_info: str | None = None
    screenshot: ScreenshotCfg = Field(default_factory=ScreenshotCfg)
    input: InputHumanizeCfg = Field(default_factory=InputHumanizeCfg)
    chrome: ChromeCfg


# ── LLM clients ──────────────────────────────────────────────

class LlmClientCfg(BaseModel):
    model_config = _model_config
    base_url: str
    api_key: str
    env_var: str


class LlmCfg(BaseModel):
    model_config = _model_config
    clients: dict[str, LlmClientCfg]
    lru_cache_max_size: int = 10


# ── Agent sub-sections ───────────────────────────────────────

class LlmTaskCfg(BaseModel):
    """Planner / Operator / ... 这类 LLM 子任务共用的字段。"""
    model_config = _model_config
    model: str
    llm_client: str
    temperature: float = 0.0
    max_tokens: int | None = None


class OperatorCfg(LlmTaskCfg):
    """Operator 额外带 vision loop 参数。"""
    max_steps: int = 40
    history_rounds: int = 6


class PageClassifierCfg(LlmTaskCfg):
    enabled: bool = True
    max_tokens: int = 4096
    record_min_confidence: float = 0.72
    background_enabled: bool = True
    background_workers: int = 1
    local_match_enabled: bool = True
    local_match_min_score: float = 0.75
    registry_file: str = "data/page_registry/pages.json"


class BehaviorSummarizerCfg(LlmTaskCfg):
    enabled: bool = True
    max_tokens: int = 2048
    background_enabled: bool = True
    background_workers: int = 1
    trace_root: str = "data/action_traces"
    candidate_root: str = "data/action_recipe_candidates"
    max_events: int = 8
    auto_enable: bool = False
    auto_enable_min_confidence: float = 0.92


class PersonaCfg(BaseModel):
    model_config = _model_config
    name: str
    character: str
    style: str
    strategy: str
    recruitment_info: str


class ScheduleCfg(BaseModel):
    model_config = _model_config
    task_goals: dict[str, str]
    max_posts_per_day: int
    daily_schedule: list[dict]
    patrol_rest_between_rounds: list[int]


class CleanupCfg(BaseModel):
    model_config = _model_config
    enabled: bool = True
    max_age_days: int = 7


class StorageCfg(BaseModel):
    model_config = _model_config
    state_file: str = "data/agent_state.json"
    save_titles_to_local: bool = True
    cleanup: CleanupCfg = Field(default_factory=CleanupCfg)


class AgentBayCfg(BaseModel):
    model_config = _model_config
    api_key: str = ""
    image_id: str = "mobile_latest"
    idle_release_timeout: int | None = 600


class RuntimeCfg(BaseModel):
    model_config = _model_config
    mode: str = "wuyingcloud"
    agentbay: AgentBayCfg = Field(default_factory=AgentBayCfg)
    auto_cleanup_orphans_on_startup: bool = True
    session_service_url: str = ""
    session_service_api_key: str = ""


class AccountCfg(BaseModel):
    """每个账号的运行参数。`extra=allow` 让老配置里未声明的字段不会被拒。"""
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    id: str
    display_name: str = ""
    image_id: str | None = None
    state_file: str | None = None
    mode: str | None = None
    description: str = ""


class StateLimitsCfg(BaseModel):
    model_config = _model_config
    inspiration_pool: int = 30
    title_few_shots: int = 30
    hashtags: int = 50
    recent_searches: int = 30
    learning_notes: int = 20
    anxiety_keywords: int = 30
    knowledge_topics: int = 30
    followers_history: int = 30


class AgentCfg(BaseModel):
    model_config = _model_config
    default_mode: str = "waiting"
    state_limits: StateLimitsCfg = Field(default_factory=StateLimitsCfg)
    planner: LlmTaskCfg
    operator: OperatorCfg
    page_classifier: PageClassifierCfg
    behavior_summarizer: BehaviorSummarizerCfg
    persona: PersonaCfg
    schedule: ScheduleCfg
    storage: StorageCfg = Field(default_factory=StorageCfg)
    runtime: RuntimeCfg = Field(default_factory=RuntimeCfg)
    accounts: list[AccountCfg] = Field(default_factory=list)


class AppSettings(BaseModel):
    """应用配置根对象。通过 `config.settings` 访问。"""
    model_config = _model_config
    system: SystemCfg
    llm: LlmCfg
    agent: AgentCfg
