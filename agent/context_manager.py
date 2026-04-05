from typing import List, Dict, Any

class ContextManager:
    def __init__(self, max_rounds: int = 6):
        self.messages: List[Dict[str, Any]] = []
        self.max_rounds = max_rounds

    def set_system_prompt(self, prompt: str):
        """设置或更新系统提示词"""
        if not self.messages:
            self.messages.append({"role": "system", "content": prompt})
        else:
            self.messages[0] = {"role": "system", "content": prompt}

    def add_user_message(self, content: Any):
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str):
        self.messages.append({"role": "assistant", "content": content})

    def get_messages(self) -> List[Dict[str, Any]]:
        """截断历史，保留 system + 最近 N 轮"""
        if len(self.messages) <= 1:
            return self.messages

        # system(1) + N 轮 * 2 条 = 1 + 2N
        max_len = 1 + self.max_rounds * 2
        if len(self.messages) > max_len:
            return [self.messages[0]] + self.messages[-(self.max_rounds * 2):]
        
        return self.messages

    def clear_conversation(self):
        """清空对话历史，保留 system prompt"""
        if self.messages:
            self.messages = [self.messages[0]]
        else:
            self.messages = []
