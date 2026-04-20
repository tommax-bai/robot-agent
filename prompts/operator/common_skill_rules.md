## 通用执行规范 (Common Skill Rules)

以下规则适用于所有挂载技能，与特定 SKILL.md 中的规则同时生效。

### 1. 步数效率
- **首选连招**：能在一拍内完成的连续操作（点击 → 粘贴、点击 → 全选 → 删除等），必须使用 `actions` 数组一次性返回。
- **禁止分步**：严禁把"点击输入框"和"粘贴内容"拆成两次 LLM 调用。

### 2. 输入框操作（按 runtime mode 分流）

#### 2.1 local_chrome（桌面浏览器）
- **粘贴前清空**（推荐一把梭）：`clear_input(x,y)` → `paste(text)`
- **等价拆写**：`click` → `hotkey("command+a")` → `hotkey("delete")` → `paste`
- `hotkey` 可用任意 pyautogui 键名或组合（command/ctrl/shift/alt + 其它）。

#### 2.2 cloudmobile / agentbay（阿里无影云手机）
- **首选**：`click` 输入框 → `paste(text)`（Android IME 的 input_text 通常会替换当前 EditText 内容）。
- **`paste` 必须在输入框已获焦时才能工作**：单独调 `paste` 没先 `click` 会报 "No focused editable node found" 直接失败。**保险做法**：`actions` 数组里把 `click(x,y)` 和 `paste(text)` 一次性一起返回。
- **强制清空**：`clear_input(x,y)` —— 此动作发起 tap + 长按（650ms），**会弹出 Android 文本选择菜单**；下一拍必须视觉识别菜单然后：
  1. `click` 菜单中的"全选"（或菜单中出现的等价选项）
  2. 再点键盘上的退格按钮（屏幕底部键盘区）或视觉点击"剪切"/"删除"
- **严禁组合键**：`command+X` / `ctrl+X` / `alt+X` 在云手机一律不支持，SDK 无 meta 键通道。
- **可用 `hotkey`**：仅硬件单键 —— `back` / `esc` / `home` / `menu` / `volume_up` / `volume_down` / `power`。
- **Enter / Tab / 退格 / 方向键**：不是 `hotkey` 能发的；请**视觉 `click`** 屏幕键盘上的对应按钮。`paste` 的 text 里也可以直接带 `\n` 让 IME 自行换行（不保证所有 IME 都识别）。

### 2.x 通用
- **填完即校验**：填入内容后，下一拍必须观察截图确认输入框已显示新内容（而非占位符或旧值）。

### 3. 截图与决策节奏
- **每拍一截**：每次决策前都基于最新的截图。严禁假设页面状态没变。
- **疑问即停**：如果对当前页面状态不确定，先用 `move` 或 `wait` 触发下一次截图，不要盲点。
