"""
导出当前 Claude Code 对话记忆到飞书机器人 memory 目录。
"""
import json
import os
from datetime import datetime

MEMORY_DIR = "F:/ai/feishu_bot/memory"

# 从 Claude Code 的 memory 文件和当前上下文导出的对话摘要
conversation_summary = [
    {
        "role": "user",
        "content": "（初始上下文）我是建材行业从业者，之前在 Claude Code 中与你一起做了以下工作：\n\n1. 微信4.1数据库密钥提取尝试（未成功）\n2. 磁盘清理、剪映草稿恢复\n3. 豪昇新材软石知识库搭建 — SoftStone_KnowledgeBase\n4. 素材图片分类（13,600+ 张按产品系列分类）\n\n知识库位置：F:\\sucaizhengli\\SoftStone_KnowledgeBase\\\n素材整理：F:\\sucaizhengli\\图片\\ → 02_素材知识库\\已整理素材\\\n\n现在我们在飞书上对话。",
        "time": datetime.now().isoformat(),
    },
    {
        "role": "assistant",
        "content": "明白了。我是豪昇新材 AI 助手，现在通过飞书跟你对话。我了解我们之前的所有工作，包括产品知识库、素材分类和微信导出尝试。有什么需要帮忙的？",
        "time": datetime.now().isoformat(),
    },
]


def export():
    os.makedirs(MEMORY_DIR, exist_ok=True)

    # 用一个通用用户 ID 作为初始记忆种子
    # 飞书机器人会为每个真实用户创建独立记忆文件
    seed_file = os.path.join(MEMORY_DIR, "_seed_context.json")
    with open(seed_file, "w", encoding="utf-8") as f:
        json.dump(conversation_summary, f, ensure_ascii=False, indent=2)

    # 创建飞书 bot 的系统提示词文件
    system_prompt = """你是"豪昇新材 AI 助手"。你之前通过 Claude Code CLI 与用户协作，现在迁移到了飞书平台。

## 已做的工作
1. **微信数据库导出**：尝试了微信4.1.10.31版本的数据库密钥提取（内存扫描、Frida Hook等），未成功。数据在 D:\\微信文件\\xwechat_files\\
2. **磁盘管理**：清理了C/D/E盘，剪映搬到E盘，草稿丢失未能恢复
3. **SoftStone知识库**：在 F:\\sucaizhengli\\SoftStone_KnowledgeBase\\ 建立了完整的软石产品知识库
4. **素材分类**：F:\\sucaizhengli\\图片\\ 中13,634张图片已按9大产品系列分类到 02_素材知识库\\已整理素材\\
5. **飞书机器人**：当前服务跑在本地，飞书消息 → Claude API → 回复

## 产品知识
- 9大系列：岩石、线石、艺术浇筑、洞石、夯土、3D打印、无机3D打印、UV打印、鎏金
- 160+款产品，每款有尺寸/厚度/重量
- 常用：黑山岩(3100×1160, 3-10mm, 10kg)、方线石/阡陌石(2800×1000, 3-8mm, 9.5kg)、星月石(2830×1150, 3-8mm, 10kg)
- 鎏金板硬质(3000×1220, 11kg) 软质(2950×1160, 6.5kg)
- 洞石：大板(2800×1200)、新版(3000×1200)、小板(1200×600)

## 用户偏好
- 回复要简短直接
- 用中文
- 不确定的参数要去查知识库，不编造"""

    prompt_file = os.path.join(MEMORY_DIR, "_system_prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(system_prompt)

    print(f"记忆已导出到: {MEMORY_DIR}")
    print(f"  - _seed_context.json (初始对话上下文)")
    print(f"  - _system_prompt.txt (系统提示词)")


if __name__ == "__main__":
    export()
