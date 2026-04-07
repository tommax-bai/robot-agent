import config
import subprocess
import time
def init_chrome_config():
    if config.system["system_info"] == "darwin":

        #pkill -9 Google Chrome
        subprocess.run(["pkill", "-9", "Google Chrome"])
        time.sleep(5)
        # 设置启动参数
        config.system["chrome"]["chrome_command"] = [
            "/Users/baitianxing/codes/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            f"--remote-debugging-port={config.system['chrome']['debug_port']}",
            #f"--user-data-dir={config.system['chrome']['profile_dir']}",
            "--remote-allow-origins=*"
        ]
        
    # TODO 以下代码未经验证
    elif config.system["system_info"] == "win32":
        #taskkill /F /IM chrome.exe
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"])
        # 设置启动参数
        config.system["chrome"]["chrome_command"] = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            f"--remote-debugging-port={config.system['chrome']['debug_port']}",
            f"--user-data-dir={config.system['chrome']['profile_dir']}",
            "--remote-allow-origins=*"
        ]