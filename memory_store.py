"""
会话记忆管理 — 按飞书用户存储对话历史，启动时加载已有记忆。
"""
import json
import os
from datetime import datetime
from typing import Optional


class MemoryStore:
    def __init__(self, memory_dir: str, max_history: int = 50):
        self.memory_dir = memory_dir
        self.max_history = max_history
        os.makedirs(memory_dir, exist_ok=True)
        self._cache: dict[str, list[dict]] = {}

    def _user_file(self, user_id: str) -> str:
        safe = user_id.replace("/", "_").replace("\\", "_")
        return os.path.join(self.memory_dir, f"{safe}.json")

    def load(self, user_id: str) -> list[dict]:
        """加载用户对话历史"""
        if user_id in self._cache:
            return self._cache[user_id]

        path = self._user_file(user_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache[user_id] = data
                return data
            except Exception:
                pass
        self._cache[user_id] = []
        return []

    def save(self, user_id: str, messages: list[dict]):
        """保存用户对话历史"""
        # 只保留最近 N 轮
        if len(messages) > self.max_history * 2:
            messages = messages[-(self.max_history * 2):]

        path = self._user_file(user_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        self._cache[user_id] = messages

    def append(self, user_id: str, role: str, content: str, msg_id: str = ""):
        """追加一条消息"""
        history = self.load(user_id)
        history.append({
            "role": role,
            "content": content,
            "time": datetime.now().isoformat(),
            "message_id": msg_id,
        })
        self.save(user_id, history)

    def get_context_messages(self, user_id: str) -> list[dict]:
        """获取可直接发送给 Claude API 的消息列表（不含 time 字段）"""
        history = self.load(user_id)
        return [{"role": m["role"], "content": m["content"]} for m in history]


# 全局实例
_memory: Optional[MemoryStore] = None


def get_memory() -> MemoryStore:
    global _memory
    if _memory is None:
        raise RuntimeError("MemoryStore not initialized. Call init_memory() first.")
    return _memory


def init_memory(memory_dir: str, max_history: int = 50) -> MemoryStore:
    global _memory
    _memory = MemoryStore(memory_dir, max_history)
    return _memory
