import utils.init_functions.init_screen_size as init_screen_size
import utils.init_functions.init_system_info as init_system_info
import utils.init_functions.init_chrome_client as init_chrome_client
import utils.init_functions.init_chrome_config as init_chrome_config

def init():
    init_screen_size.init_screen_size()
    init_system_info.init_system_info()
    init_chrome_config.init_chrome_config()
    chrome_client = init_chrome_client.init_chrome_client()
