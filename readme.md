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