## 文档入口

- 新人架构说明：`docs/architecture_onboarding.md`
- 开发规范：`docs/development_guide.md`
- AI 助手协作说明：`CLAUDE.md`

## 执行模式（agent.runtime.mode）

通过环境变量 `AGENT_RUNTIME_MODE` 在三种模式间切换。三种模式正交于"环境（Backend）"和"决策（Strategy）"两个维度：

| mode | Backend | Strategy | 适用 |
|------|---------|----------|------|
| `local_chrome`（默认） | `MacOSChromeBackend`：PyAutoGUI + ImageGrab | `VisionActionStep`：自家 VLM 视觉循环 | 本地开发/调试 |
| `cloudmobile` | `AgentBayBackend`：阿里无影云手机 session | `VisionActionStep`：同上 | 矩阵账号生产、防风控 |
| `agentbay` | `AgentBayBackend` | `AgentBayDelegateStrategy`：委托给 AgentBay 内置 mobile_use Agent | 任务原型/兜底，决策黑盒 |

三种模式都先尝试 `RecipeOperator` 快路径（命中即跳过 Strategy）。云端模式下 recipe 的 tap/swipe 自动走 AgentBay。

### 切换示例

```bash
# 默认本地（无需设置）
uvicorn app:app --port 6702

# 切到云手机 + 自家 VLM
export AGENT_RUNTIME_MODE=cloudmobile
export AGENTBAY_API_KEY=<你的阿里云 AccessKey>
export AGENTBAY_IMAGE_ID=mobile_latest        # 可选，默认 mobile_latest
export AGENTBAY_SCREENSHOT_FORMAT=jpeg        # 可选，jpeg|png
uvicorn app:app --port 6702

# 切到云手机 + AgentBay 内置 mobile_use Agent（委托黑盒）
export AGENT_RUNTIME_MODE=agentbay
export AGENTBAY_API_KEY=<...>
uvicorn app:app --port 6702
```

### 查看当前模式

```bash
curl http://127.0.0.1:6702/api/v1/agent/runtime
```

返回示例（cloudmobile 模式，session 尚未建立）：

```json
{
  "mode": "cloudmobile",
  "backend": "AgentBayBackend",
  "strategy": {"type": "VisionActionStep", "name": "vision_action"},
  "agentbay": {
    "image_id": "mobile_latest",
    "screenshot_format": "jpeg",
    "session_active": false,
    "screen_size": {"width": 0, "height": 0}
  }
}
```

云端 session 是**懒加载**的——发起第一次任务前不会有任何云端实例计费。`session_active=true` 表示已建立。

## 监控看板（Dashboard）

启动服务后浏览器打开 `http://127.0.0.1:6702/dashboard`，可以看到：

- 当前 runtime / backend / strategy / supervisor 状态
- 云端模式：iframe 嵌入阿里 `live_view_url`，**实时看云手机屏幕**
- 本地模式：`/api/v1/agent/screen.jpg` 每 2s 刷新，看本机 Chrome 截图
- 操作面板：debug/waiting 切换、patrol 开关、提交任务、取消任务
- SSE 事件流：mode_changed / task_started / task_completed 实时滚动

### 让局域网其他电脑访问

启动时绑定 `0.0.0.0` 并设置 `AGENT_HTTP_PORT`（让 banner 打印准确 URL）：

```bash
AGENT_HTTP_PORT=6702 uvicorn app:app --host 0.0.0.0 --port 6702
```

启动时会打印类似：

```
═══════════════════════════════════════════════════════
  📊 Dashboard      http://127.0.0.1:6702/dashboard
  🌐 局域网访问     http://10.2.1.57:6702/dashboard
  ⚙️  API 根路径     http://127.0.0.1:6702/api/v1
  ⚠️  当前 API 无认证；开 0.0.0.0 + LAN = 网内任何人都能控制 agent
═══════════════════════════════════════════════════════
```

⚠️ **安全提示**：所有 API 端点（包括 dashboard 操作面板、cloud phone 串流 URL）当前**无任何认证**。绑 `0.0.0.0` 暴露到 LAN 等于让网内每一台机器都能：
- 发送任务、取消任务、切换 mode
- 通过 `/api/v1/agent/runtime` 拿到 `live_view_url` → 直接接管你的云手机
- 触发新的云端 session（产生计费）

仅在你**完全信任** LAN 内所有设备时启用。生产部署请在反向代理（Nginx / Caddy）上加 basic auth 或 IP 白名单。

## 多账号矩阵（WorkerPool）

`config.agent["accounts"]` 配置一个 account 列表，每项一个独立 worker：

```python
"accounts": [
    {"id": "acct-a", "display_name": "导航员-A"},
    {"id": "acct-b", "display_name": "导航员-B"},
],
```

留空 → 单 default worker（保持单账号项目兼容）。

每个 worker 拥有：
- 独立的 `data/accounts/{id}/agent_state.json` 状态（知识 / daily_stats / inspiration_pool）
- 独立的 AgentBay session（云手机实例，按时长各自计费）
- 独立的 supervisor / backend / strategy / runtime mode 切换（互不干扰）
- 共享：LLM 客户端、技能词表、recipe 库、page registry、event bus、Planner

### 登录流程（每 session 扫码一次）

**不做镜像快照、不做自动登录**——出错风险高、维护成本大。每次 worker 创建新 session 都从空白 Android 镜像启动，需人工：

1. dashboard 切到该 worker 的 tab
2. 触发任意一个任务（或 patrol-once），后端创建 AgentBay session
3. dashboard 的 Live View iframe 会显示云手机屏幕
4. **用真机扫码**完成小红书登录
5. 再次发任务，agent 在已登录态下执行

session 销毁（mode 切换、服务重启、idle 超时）后登录态丢失，下次重新扫码。

### Dashboard 多 worker UI

左栏顶部 worker tab 条显示所有 worker，每个 tab 带状态色点（灰=waiting / 绿=patrolling / 黄=executing / 红=debug），带右上角 × 可移除。末尾有蓝色 `+` 按钮展开"+1 / +3 / +5" 动态加 worker。点击 tab 切换"激活 worker"，所有面板（Runtime / Supervisor / Tasks / Modes / Live View）都跟着切。

Live View 支持两种视图：
- **单视图**：只看 active worker 的云手机画面。右上角 `← 返回网格` 回到网格。
- **网格视图**（多 worker 默认）：所有 worker 同屏并排，每 cell 头部有 worker 名 + 关机 ⏻ 图标。cell 未 active 时有透明 overlay 接住"第一次点击切换 active"；active 后 overlay 隐藏，iframe 正常操作云手机。**双击** cell 头部 = 切换 + 进单视图。

日志面板全宽置底，trace_id 区分任务，支持过滤和 level 筛选。

### 运行时动态增删 worker

Dashboard 上 "+N" 按钮；或 API：

```bash
# 新增 3 个临时 worker（id 自动生成，进程重启即消失）
curl -X POST http://localhost:6702/api/v1/agent/workers \
  -H 'Content-Type: application/json' -d '{"count": 3}'

# 移除某个 worker（自动 release session）
curl -X DELETE http://localhost:6702/api/v1/agent/workers/worker-abc12345
```

要永久命名的账号（重启后保留）→ 写进 `config.agent.accounts`，不要用动态方式。

### API 多租户

所有 `/api/v1/agent/*` endpoint 接受可选 `account_id`：

```bash
# 给 acct-b 发任务
curl -X POST http://localhost:6702/api/v1/agent/actions \
  -H 'Content-Type: application/json' \
  -d '{"user_goal": "...", "account_id": "acct-b"}'

# 列所有 worker（dashboard 用此渲染 tab）
curl http://localhost:6702/api/v1/agent/workers

# 单 worker runtime 视图
curl 'http://localhost:6702/api/v1/agent/runtime?account_id=acct-b'

# 切换某个 worker 的 runtime.mode（per-worker，不影响其他 worker）
curl -X POST http://localhost:6702/api/v1/agent/runtime/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode": "cloudmobile", "account_id": "acct-b"}'

# 释放某 worker 的 session（支持 force=true 先取消任务再释放）
curl -X POST http://localhost:6702/api/v1/agent/session/release \
  -H 'Content-Type: application/json' \
  -d '{"account_id": "acct-b", "force": true}'
```

省略 `account_id` 时全部走 default worker，老脚本零改动。

### 孤儿 session 防护

进程异常退出（SIGKILL / OOM）会留下云端 session 继续计费。两道防线：

1. **启动时自动清**：`AppContainer` 初始化时调 `AgentBay.list()` 检测上次残留的 session 全部 delete。  
   关闭开关：`AGENT_AUTO_CLEANUP_ORPHANS=false`（多副本同 API key 部署时必须关）
2. **云端 idle 回收**：CreateSession 带 `idle_release_timeout=600`，10 分钟无操作云端自动销毁。  
   调整：`AGENTBAY_IDLE_RELEASE_TIMEOUT=3600`

## 本地开发环境

项目要求 Python 3.13+。首次 setup：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

本地模式（`local_chrome`）还需要 Chrome for Testing + ChromeDriver，[下载地址](https://googlechromelabs.github.io/chrome-for-testing/)。配置环境变量：

```bash
export CHROME_BINARY=/path/to/chrome-for-testing/Google\ Chrome\ for\ Testing.app/Contents/MacOS/Google\ Chrome\ for\ Testing
export CHROMEDRIVER_PATH=/path/to/chromedriver
```

云端模式（`cloudmobile` / `agentbay`）只需要 `AGENTBAY_API_KEY`，不依赖 Chrome。

### 启动

```bash
# 开发（热重载）
uvicorn app:app --host 0.0.0.0 --port 6702 --reload

# 生产（单 worker，无热重载）
AGENT_HTTP_PORT=6702 gunicorn -w 1 -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:6702 --timeout 120
```

### 常用调试 curl

```bash
# 健康检查
curl http://127.0.0.1:6702/health

# 当前状态（mode / active task / trace_id）
curl http://127.0.0.1:6702/api/v1/agent/status

# 停止所有自动活动（开发时先做这步）
curl -X POST http://127.0.0.1:6702/api/v1/agent/mode/debug

# 异步发任务（dashboard 默认走这个）
curl -X POST http://127.0.0.1:6702/api/v1/agent/actions \
  -H 'Content-Type: application/json' \
  -d '{"user_goal": "打开小红书首页"}'

# 同步发任务（脚本集成想拿 summary 用这个；dashboard 不用）
curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{"user_goal": "打开小红书首页"}'

# 按人设触发一次 patrol（Strategist 合成 goal）
curl -X POST http://127.0.0.1:6702/api/v1/agent/scheduled/patrol-once

# 取消当前任务
curl -X POST http://127.0.0.1:6702/api/v1/agent/cancel

# Chrome DevTools 代理（local_chrome 模式）
curl http://127.0.0.1:6702/api/v1/agent/chrome/json
```
