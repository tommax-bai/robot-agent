VERSION="145.0.7632.76"
PLATFORM="mac-arm64"

curl -L -o chrome.zip "https://storage.googleapis.com/chrome-for-testing-public/145.0.7632.76/mac-arm64/chrome-mac-arm64.zip"
curl -L -o chromedriver.zip "https://storage.googleapis.com/chrome-for-testing-public/145.0.7632.76/mac-arm64/chromedriver-mac-arm64.zip"


unzip -o chrome.zip
unzip -o chromedriver.zip

cd ./python-agent-social-media-renote
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

#### run 
```bash
export APP_ENV=dev && gunicorn -w 1 -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:6702 --timeout 120

uvicorn app:app --host 0.0.0.0 --port 6702 --reload
```


#### test shell
'''
cd /Users/baitianxing/codes/python-agent-social-media-renote
source .venv/bin/activate
python - <<'PY'
from core.screenshot import get_screenshot_base64
import base64

img_base64, x, y = get_screenshot_base64("debug", include_cursor=True)
with open("debug_screenshot.jpg", "wb") as f:
    f.write(base64.b64decode(img_base64))

print("saved: debug_screenshot.jpg")
print("cursor:", x, y)
PY
'''

1. 确认服务活着

curl http://127.0.0.1:6702/health
预期返回：{"status":"ok"}

2. 看当前 Agent 状态

curl http://127.0.0.1:6702/api/v1/agent/status

这个最适合排查：
当前 mode 是什么
有没有任务在跑
当前 trace_id 是什么

3. 切到 debug 模式，避免自动任务干扰
调试时我建议先执行这个：

curl -X POST http://127.0.0.1:6702/api/v1/agent/mode/debug
这样自动化和调度会停下来，避免你手动调试时被后台任务打断。

4. 同步执行一个最小任务
这个最适合看一条完整链路有没有跑通：

curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "user_goal": "打开小红书首页",
    "max_steps": 10,
    "checker_delay": 0.3
  }'

5. 调试截图+定位类问题
这种目标适合看 Agent 是否能识别页面，不一定真的复杂操作：

curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "user_goal": "观察当前页面，并说明页面中央区域是什么，不做多余操作",
    "max_steps": 5,
    "checker_delay": 0.3
  }'

6. 调试输入框连招
这个适合验证你刚改的 action_prompt.py 是否会一次返回一组动作：

curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "user_goal": "在当前页面找到搜索框，输入关键词 AI 标注 并提交",
    "max_steps": 10,
    "checker_delay": 0.3
  }'

7. 异步任务调试
如果你不想一直阻塞等结果，用异步接口：

curl -X POST http://127.0.0.1:6702/api/v1/agent/actions \
  -H 'Content-Type: application/json' \
  -d '{
    "user_goal": "打开小红书首页并检查是否已登录",
    "max_steps": 20,
    "checker_delay": 0.3
  }'
它会先返回一个 trace_id。然后你可以继续查状态：

curl http://127.0.0.1:6702/api/v1/agent/status

8. 关闭自动巡逻
如果不是用 debug 模式，也可以单独关掉 patrol：

curl -X POST http://127.0.0.1:6702/api/v1/agent/patrol \
  -H 'Content-Type: application/json' \
  -d '{"enable": false}'
重新打开：

curl -X POST http://127.0.0.1:6702/api/v1/agent/patrol \
  -H 'Content-Type: application/json' \
  -d '{"enable": true}'


9. 强制触发一次 maintenance
这个适合单测 supervisor 的养号任务入口：

curl -X POST http://127.0.0.1:6702/api/v1/agent/maintenance/trigger

10. 调试 Chrome DevTools 代理
先看 Chrome targets：

curl http://127.0.0.1:6702/api/v1/agent/chrome/json
如果返回页面列表，你可以拿其中某个 id 去打开调试页：

open "http://127.0.0.1:6702/api/v1/agent/debug/<page_id>"