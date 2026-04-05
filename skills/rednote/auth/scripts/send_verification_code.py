from utils.init_functions.init_chrome_client import get_chrome
import time
import utils.waiting_queue as waiting_queue
def send_verification_code(trace_id: str):
   try:
      waiting_queue.add_queue(trace_id, "send_verification_code")
      while True:
         if waiting_queue.get_queue(trace_id)["completed"]:
            break
         time.sleep(1)
      verify_code = waiting_queue.get_queue(trace_id)["result"]
      return {
         "ok": True,
         "message": "小红书验证码为: " + verify_code,
         "finish": False
      }
   except Exception as e:
      import traceback
      traceback.print_exc()
      return {
         "ok": False,
         "message": f"小红书验证码输入失败: {e}",
         "finish": False
      }