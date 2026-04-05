import time
import json

waiting_queue = {

}

def add_queue(trace_id: str, task_name: str):
    waiting_queue[trace_id] = {
        "completed": False,
        "created_at": time.time(),
        "updated_at": None,
        "task_name": task_name,
        "result": None,
    }
    # 打印当前完整队列日志
    print(f"\n[QUEUE LOG] Added: {trace_id} | Full Queue: {json.dumps(waiting_queue, indent=2, ensure_ascii=False)}\n")

def get_queue(trace_id: str):
    return waiting_queue[trace_id]

def update_queue(trace_id: str, result: dict):
    if trace_id not in waiting_queue:
        return {
            "code": 400,
            "msg": f"{trace_id}不存在于等待队列中",
            "data": None
        }
    waiting_queue[trace_id]["result"] = result
    waiting_queue[trace_id]["completed"] = True
    waiting_queue[trace_id]["updated_at"] = time.time()
    return {
        "code": 200,
        "msg": f"{trace_id}回调成功",
        "data": None
    }

def remove_queue(trace_id: str):
    del waiting_queue[trace_id]