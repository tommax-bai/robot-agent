import config
import subprocess
import time
def init_chrome_config():
    if config.global_config["system_info"] == "darwin":

        #pkill -9 Google Chrome
        subprocess.run(["pkill", "-9", "Google Chrome"])
        time.sleep(5)
        # 设置启动参数
        config.global_config["chrome"]["chrome_command"] = [
            "/Users/baitianxing/codes/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            f"--remote-debugging-port={config.global_config['chrome']['debug_port']}",
            #f"--user-data-dir={config.global_config['chrome']['profile_dir']}",
            "--remote-allow-origins=*"
        ]
        
    # TODO 以下代码未经验证
    elif config.global_config["system_info"] == "win32":
        #taskkill /F /IM chrome.exe
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"])
        # 设置启动参数
        config.global_config["chrome"]["chrome_command"] = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            f"--remote-debugging-port={config.global_config['chrome']['debug_port']}",
            f"--user-data-dir={config.global_config['chrome']['profile_dir']}",
            "--remote-allow-origins=*"
        ]