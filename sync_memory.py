"""记忆同步：本地 ↔ 云端 Render"""
import json
import os
import sys
import httpx

CLOUD_URL = "https://feishu-bot-2onq.onrender.com"
LOCAL_MEMORY_DIR = "F:/ai/feishu_bot/memory"
USER_ID = "ou_68ee1b76d9b1ed17147d8aae42fab717"  # 你的飞书 open_id


def load_local_memory():
    path = os.path.join(LOCAL_MEMORY_DIR, f"{USER_ID}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_local_memory(messages):
    os.makedirs(LOCAL_MEMORY_DIR, exist_ok=True)
    path = os.path.join(LOCAL_MEMORY_DIR, f"{USER_ID}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


def fetch_cloud_memory():
    try:
        r = httpx.get(f"{CLOUD_URL}/memory/{USER_ID}", timeout=30)
        if r.status_code == 200:
            return r.json().get("messages", [])
        print(f"Cloud returned {r.status_code}")
    except Exception as e:
        print(f"Cannot reach cloud: {e}")
    return []


def push_to_cloud(messages):
    try:
        r = httpx.post(
            f"{CLOUD_URL}/memory/{USER_ID}/merge",
            json={"messages": messages},
            timeout=30,
        )
        if r.status_code == 200:
            print(f"Pushed: {r.json().get('merged_count', '?')} total messages")
    except Exception as e:
        print(f"Push failed: {e}")


def sync():
    print("Syncing memory...")
    print(f"  Cloud: {CLOUD_URL}")
    print(f"  Local: {LOCAL_MEMORY_DIR}")
    print(f"  User:  {USER_ID}")

    local = load_local_memory()
    cloud = fetch_cloud_memory()

    print(f"  Local: {len(local)} messages")
    print(f"  Cloud: {len(cloud)} messages")

    # Merge: dedup by message_id
    seen = set()
    merged = []
    for m in local + cloud:
        key = m.get("message_id", m.get("content", "")[:40])
        if key not in seen:
            seen.add(key)
            merged.append(m)

    merged.sort(key=lambda x: x.get("time", ""))

    if len(merged) > len(local):
        save_local_memory(merged)
        print(f"  Local updated: {len(local)} -> {len(merged)} messages")

    if len(merged) > len(cloud):
        push_to_cloud(merged)

    print(f"  Final: {len(merged)} messages synced")
    print("Done!")


if __name__ == "__main__":
    sync()
