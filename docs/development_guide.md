# 开发规范

本文档面向本项目的日常开发者，尤其是从 PHP 转到 Python 的同学。目标不是追求形式洁癖，而是让 Agent 自动化链路在长期迭代中保持可读、可测、可回退。

## 1. 基本原则

1. 小步改动：优先做可验证的局部改动，避免一次性重写多个层。
2. 显式依赖：业务对象通过构造函数注入，不在函数内部临时 new 一堆全局服务。
3. 结构化数据：跨模块传参优先使用 dataclass / Protocol / typed dict-like contract，少传裸 dict。
4. 可回退：涉及 GUI、LLM、OpenCV、网络调用的改动必须保留失败兜底路径。
5. 日志可追踪：所有任务链路都带 `trace_id`，不要写无法关联到任务的散乱日志。

## 2. Python 版本与格式化

项目使用 Python 3.13，所有 Python 文件统一保留：

```python
from __future__ import annotations
```

这样可以直接写现代类型：

```python
current_run: ActiveRun | None = None
items: list[str] = []
```

静态检查使用 Ruff：

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
```

当前 Ruff 配置在 `pyproject.toml`，只启用低风险规则：语法错误、未使用导入、裸 `except`、import 排序等。后续要扩大规则集时，应先分批修复，不要和业务功能混在一个提交里。

## 3. 导入规范

导入顺序：

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import TaskResult

if TYPE_CHECKING:
    from runtime.context import RunContext
```

使用 `TYPE_CHECKING` 的场景：

1. 只为类型注解引入的类。
2. 该导入会造成循环 import。
3. 运行时不需要这个对象。

不要使用 `TYPE_CHECKING` 的场景：

1. 函数运行时要实例化或调用这个类。
2. 模块加载时需要读取常量或执行初始化逻辑。

PHP 同学习惯对照：`TYPE_CHECKING` 类似“只给 IDE / 静态分析看的 use”，运行时不会真的 import。

## 4. 类型与 dataclass

跨层传递的稳定数据结构使用 dataclass：

```python
@dataclass(frozen=True)
class SubTask:
    id: str
    goal: str
    required_skill: str | None = None
    intent: str = "unknown"
```

推荐：

1. 输入、结果、事件、上下文对象用 dataclass。
2. 不应该修改的值加 `frozen=True`。
3. list / dict 默认值必须用 `field(default_factory=list)`，不要写 `items: list = []`。
4. 函数参数尽量写清楚类型，返回值也写。

PHP 对照：dataclass 接近 PHP 8 的只读 DTO / value object，但 Python 会自动生成 `__init__`、`repr`、比较方法。

## 5. Optional 与 `| None`

项目统一使用现代写法：

```python
current_run: ActiveRun | None = None
```

不再新增：

```python
from typing import Optional

current_run: Optional[ActiveRun] = None
```

原因：Python 3.10+ 原生支持 `| None`，更短，也和 `list[str]`、`dict[str, Any]` 风格一致。

注意：如果变量可能为 `None`，类型里必须写出来：

```python
# 正确
self._current_run: ActiveRun | None = None

# 不推荐，类型和实际值不一致
self._current_run: ActiveRun = None
```

## 6. Protocol 的使用

当代码只依赖“能力”，不依赖具体实现时，使用 `Protocol`：

```python
class AgentStateRepo(Protocol):
    def get(self) -> dict: ...
    def update(self, **kwargs) -> dict: ...
```

这样 `JsonFileStateRepo` 和 `InMemoryStateRepo` 只要实现同样方法，就能被替换。它的价值是降低耦合，方便测试。

PHP 对照：Protocol 接近 interface，但 Python 是结构化类型，不要求显式 `implements`。

## 7. 函数参数里的 `*`

`*` 后面的参数必须用关键字传递：

```python
def update(self, *, followers: int | None = None, trace_id: str = "system") -> dict:
    ...

repo.update(followers=10, trace_id="patrol-xxx")
```

这样能避免参数很多时传错位置。状态更新、配置 patch、LLM 调用参数等都推荐用这个写法。

## 8. 异常处理

不要写裸 `except`：

```python
# 不推荐
try:
    ...
except:
    pass

# 推荐
try:
    ...
except json.JSONDecodeError:
    ...
except Exception as e:
    logger.warning({"msg": "处理失败", "error": str(e)}, trace_id)
```

在 Agent 主链路里优先抛结构化异常，例如 `PlannerError`、`DecisionParseError`、`StepBudgetExceededError`。能恢复的异常要让上层有机会回退到 LLM 或重试，不要悄悄吞掉。

## 9. 异步与阻塞

API、Supervisor、Operator 是 async 链路：

```python
result = await self._operator.run(task, run.ctx)
```

但很多 GUI 工具是同步阻塞的，例如 PyAutoGUI、截图、Selenium。规范：

1. async 函数里可以调用少量同步 GUI 操作，但不要在长循环里无界阻塞。
2. 可取消点要显式调用 `ctx.cancel.raise_if_cancelled()`。
3. `asyncio.create_task()` 创建的后台任务要确保异常被日志记录。
4. 不要把 `time.sleep()` 放进纯调度 async 循环里，调度层使用 `await asyncio.sleep()`。

PHP 对照：这里的 `await` 不等于多线程，它是在事件循环里让出控制权。同步阻塞调用会卡住事件循环。

## 10. 配置与密钥

密钥只允许从环境变量读取，不能硬编码在 `config.py`：

```bash
ZENMUX_API_KEY=...
DOUBAO_API_KEY=...
SILICONFLOW_API_KEY=...
```

本地开发建议放在 `.dev.env`。该文件不应提交。`app.py` 会在非 Docker 环境下根据 `APP_ENV` 加载 `.{APP_ENV}.env`。

如果历史上已经把 `.dev.env` 纳入 Git 跟踪，应在一次专门的安全清理提交中执行：

```bash
git rm --cached .dev.env
```

然后轮换其中出现过的真实密钥。

如果新增 LLM 客户端，按这个结构配置：

```python
"client_name": {
    "base_url": _env("CLIENT_BASE_URL", "https://example.com/v1"),
    "api_key": _env("CLIENT_API_KEY"),
    "env_var": "CLIENT_API_KEY",
}
```

## 11. 日志规范

统一使用 `utils.logger`，日志内容用 dict：

```python
logger.info(
    {"msg": "启动巡逻任务", "goal": goal},
    trace_id,
)
```

要求：

1. `msg` 写人能看懂的中文。
2. 任务链路传 `trace_id`。
3. 不打印 API key、验证码、完整图片 base64。
4. LLM 原始输出只在必要时截断记录。

## 12. LLM 与 Prompt

Prompt 放在 `prompts/` 或 `skills/`，不要硬编码到 Python 文件里。

LLM 输出解析统一放在 `agents/base.py::Decision.parse()` 或对应解析工具中，不要在业务循环里散落字符串修补逻辑。

当 LLM 决策失败时，优先把失败原因结构化反馈给下一轮，而不是重复执行同一个动作。

## 13. GUI Action 规范

GUI 动作分三层：

1. `VisionActionStep`：截图、调用 LLM、得到 `Decision`。
2. `ActionDispatcher`：决定动作发给 skill tool 还是原子 action。
3. `tools/actions.py`：真正执行 PyAutoGUI。

新增原子动作时：

1. 在 `tools/actions.py::execute_action()` 增加 method 分支。
2. 参数用 `get_param()` 做 LLM 脏键名兼容。
3. 返回统一结构：`{"ok": bool, "message": str, "finish": bool}`。
4. 不要让 action 直接读写 Agent 状态。

## 14. 可重复动作 Recipe 规范

Recipe 是“可验证动作链”，不是鼠标轨迹回放。

推荐字段：

```json
{
  "id": "like_note_detail_v1",
  "intent": "open_search",
  "page_state": "rednote_home",
  "enabled": false,
  "trial": true,
  "trial_successes": 0,
  "trial_failures": 0,
  "confidence": 0.65,
  "min_confidence": 0.8,
  "steps": [
    {
      "method": "click",
      "params": {"description": "点赞"},
      "locator": {
        "type": "template",
        "template": "rednote/like_button.png",
        "threshold": 0.8
      }
    }
  ]
}
```

规范：

1. BehaviorSummarizer 产出的候选默认 `enabled=false, trial=true`。候选通过试运行验证后自动提升（成功 ≥ 2 次）或禁用（失败 ≥ 3 次）。
2. 自动执行前必须匹配 `page_state`。匹配优先级：正式 recipe > 试运行候选。
3. `intent` 字段必须使用 `data/intent_registry.json` 中的规范标签。RecipeStore 优先用 `subtask.intent == recipe.intent` 精确匹配，旧 recipe 无 intent 时兜底关键词匹配。
4. 关键坐标优先用 OpenCV locator，不直接复用旧坐标。
5. 执行失败要降级回 LLM，不要循环硬跑。

### 14.1 旁路行为总结

`BehaviorSummarizer` 是动作记忆的 LLM 旁路。主线 subtask 成功后，它会在后台读取 `data/action_traces/<trace_id>.jsonl`，判断连续操作是否能总结成可复用行为。

默认输出位置：

```text
data/action_recipe_candidates/<trace_id>/<subtask_id>_llm.json
```

默认策略：

1. 不阻塞主线 operator。
2. 候选 recipe 默认 `enabled=false`，不会自动执行。
3. 如果动作依赖动态内容、误点、重复点击、页面没有变化，应返回 `reusable=false`。
4. 如果只能复用历史点位，使用 `locator.type=point`，置信度应偏低；稳定图标/按钮后续再人工或评估器升级为 template locator。
5. 是否复用由 `RecipeStore` 和 `RecipeOperator` 决定：`subtask.intent == recipe.intent + page_state 匹配 + confidence >= min_confidence + enabled=true`。
6. `intent` 字段必须从 `data/intent_registry.json` 中选择，由 `IntentRegistry.resolve()` 归一化。Planner 和 Summarizer 共享同一候选列表。

行为总结 prompt 放在 `prompts/action_memory/behavior_summarizer.md`。

## 15. 页面分类规范

页面分类用于判断“当前截图是什么页面、是不是新页面”。默认实现是 `LlmPageClassifier`：

1. 同一 trace、同一张截图先由 `PageContextCache` 精确 hash 缓存，避免重复分类。
2. 默认启用后台分类：缓存未命中时，主线先拿到 `page_classification_pending` 的 `unknown` 页面上下文继续执行，分类任务在后台线程中进行。
3. 后台分类会先由 `LocalPageMatcher` 尝试匹配已知页面的稳定 landmark。
4. 本地 landmark 没命中时，调用 LLM 判断页面语义。
5. LLM 会拿到 `PageRegistry` 中已知页面摘要，并优先复用已有 `page_state`。
6. 置信度达到 `record_min_confidence` 后写入 `data/page_registry/pages.json`，同时保存 stable landmark、dynamic region 和 negative landmark。

后台分类每个 trace 只保留最新截图的待分类任务，避免主线快速推进时堆积大量过期截图分类。

页面分类使用的 LLM prompt 放在 `prompts/vision/page_classifier.md`。改页面识别策略时优先改 prompt 文件，Python 代码只负责注入已知页面库和候选页面状态。

不要用全图 average hash 自动判断页面是否相同。内容社区页面的主要面积由动态内容占据，搜索词、推荐流、个人主页内容一变，全图 hash 就会失真，也容易把布局相似但语义不同的页面混在一起。

LLM 返回的 landmark 要区分三类：

1. `stable_landmarks`：稳定关键元素，例如固定按钮、图标、顶部搜索框布局。只有 `type=template/icon/button` 且带小区域的元素会被裁剪成 OpenCV 模板并参与本地快速命中。
2. `dynamic_regions`：动态内容区域，例如瀑布流、评论区、主页内容网格。它们用于避开不稳定区域，不参与本地页面匹配。
3. `negative_landmarks`：出现后可以否定当前页面状态的元素，例如登录弹窗、验证码、错误页。

本地匹配只验证稳定模板 landmark。文本类、布局类 landmark 会先记录在页面库里，后续可以接 OCR 或更细的布局识别；在这些能力完善前，不要让它们直接决定页面命中。

页面命名统一使用英文 snake_case：

```text
rednote_search_results
rednote_note_detail
rednote_filter_panel
unknown
```

规范：

1. `unknown` 不写入页面库。
2. LLM evidence 必须是视觉依据，不要写业务推测。
3. 页面分类只产出上下文，不直接执行动作。
4. `page_state` 后续会参与 recipe 匹配，所以宁可保守，也不要高置信误判。
5. 如果某个页面状态要长期稳定使用，应在文档或人工审核后的配置里固化说明。

## 16. 测试与验证

当前项目还没有 pytest 测试目录，提交前至少运行：

```bash
source .venv/bin/activate
ruff check .
ruff format --check .
python3 -m compileall -q . -x '(^|/)(\.git|\.venv|data|history|logs|__pycache__)(/|$)'
git diff --check
```

涉及容器连线时再跑：

```bash
.venv/bin/python - <<'PY'
from runtime.container import AppContainer
container = AppContainer()
print(type(container.supervisor).__name__)
PY
```

涉及 OpenCV locator 时，至少用一张小图做模板匹配 smoke test。

涉及 LLM 页面分类时，至少用 `MockLlmTool` 验证“LLM 返回 -> registry 写入 -> 本地命中复用”的链路。

## 17. 提交前检查清单

1. 是否误提交了 `data/`、`history/`、`logs/`、`.venv/`？
2. 是否把 API key、验证码、cookie、完整 base64 图片写进代码或文档？
3. 是否新增了没有 `trace_id` 的任务日志？
4. 是否新增了裸 `except` 或无意义的 `pass`？
5. 是否能在失败时回退到 LLM 或返回结构化错误？
6. 是否更新了对应 prompt / skill / 文档？
