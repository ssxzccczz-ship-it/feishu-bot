"""
飞书 AI 机器人服务端

启动后作为飞书机器人后端，接收用户消息 → Gemini/Claude API → 回复。
所有对话自动存储在本地 memory 目录中，下次对话保留上下文。

飞书端配置：
  1. 创建企业自建应用 → 添加"机器人"能力
  2. 事件订阅 → 订阅 im.message.receive_v1
  3. 请求网址: http://你的IP:7897/feishu/webhook
  4. 权限: im:message, im:message:send_as_bot
  5. 发布版本并审核通过

启动: python feishu_server.py
"""
import json
import os
import hashlib
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

from memory_store import init_memory, get_memory

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("feishu-bot")

# ============================================================
# 配置加载
# ============================================================
def load_config():
    config_path = os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(__file__), "config.json"))
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

cfg = load_config()

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", cfg["feishu"]["app_id"])
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", cfg["feishu"]["app_secret"])
FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFY_TOKEN", cfg["feishu"]["verification_token"])

AI_PROVIDER = os.environ.get("AI_PROVIDER", cfg["ai"]["provider"])
AI_API_KEY = os.environ.get("AI_API_KEY", cfg["ai"]["api_key"])
AI_MODEL = os.environ.get("AI_MODEL", cfg["ai"]["model"])
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", cfg["ai"]["max_tokens"]))

SERVER_HOST = os.environ.get("SERVER_HOST", cfg["server"]["host"])
SERVER_PORT = int(os.environ.get("PORT", cfg["server"]["port"]))
MEMORY_DIR = os.environ.get("MEMORY_DIR", cfg["memory"]["memory_dir"])
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", cfg["memory"]["max_history_messages"]))

# 消息去重：飞书会在 1s 内未收到 200 时重试，导致同一条消息被处理多次
_PROCESSED_MSG_IDS: set[str] = set()

# ============================================================
# 飞书 Token 管理
# ============================================================
_tenant_token: Optional[str] = None
_token_expires_at: float = 0


async def get_tenant_token() -> str:
    """获取 tenant_access_token，自动缓存和刷新"""
    global _tenant_token, _token_expires_at

    if _tenant_token and time.time() < _token_expires_at - 60:
        return _tenant_token

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    body = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, timeout=15)
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败: {data}")

    _tenant_token = data["tenant_access_token"]
    _token_expires_at = time.time() + data.get("expire", 7200)
    log.info("飞书 tenant token 已刷新")
    return _tenant_token


# ============================================================
# 飞书 API
# ============================================================
async def send_feishu_message(open_id: str, msg_type: str, content: str) -> dict:
    """发送消息给飞书用户"""
    token = await get_tenant_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": "open_id"}

    body = {
        "receive_id": open_id,
        "msg_type": msg_type,
        "content": json.dumps({"text": content}, ensure_ascii=False),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        return resp.json()


async def get_message_content(message_id: str) -> str:
    """获取飞书消息的文本内容"""
    token = await get_tenant_token()
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
        data = resp.json()

    if data.get("code") != 0:
        log.error(f"获取消息失败: {data}")
        return ""

    items = data.get("data", {}).get("items", [])
    if not items:
        return ""

    msg = items[0]
    body = msg.get("body", {})
    content = body.get("content", "")

    # 飞书文本消息的 content 是 JSON string
    try:
        parsed = json.loads(content)
        return parsed.get("text", content)
    except (json.JSONDecodeError, TypeError):
        return content


# ============================================================
# AI API (支持 Gemini / Claude)
# ============================================================
SYSTEM_PROMPT = """你是豪昇新材（软石/柔性石材）产品顾问，通过飞书与用户对话。你有完整的产品目录，回答产品参数问题时要引用具体数据。

## 身份与风格
- 你是建材行业软石产品专家
- 用户是公司内部人员，熟悉产品
- 回复简洁直接，用中文
- 不确定的参数说不确定，不编造

## 完整产品目录（尺寸×宽 mm / 厚度 / 重量 kg/㎡）

### 夯土系列（9款）
| 款式 | 尺寸 | 厚度 | 重量 |
|------|------|------|------|
| 夯土A | 2360×580 / 2360×1160 | 4mm | 6.5 |
| 夯土A渐变色 | 2360×580 / 2360×1160 | 4mm | 6.5 |
| 夯土B | 2680×930 / 2800×1030 / 2900×1160 | 4mm | 6.5 |
| 夯土B渐变色 | 2680×930 / 2800×1030 / 2900×1160 | 4mm | 6.5 |
| 夯土D | 2800×560 | 4mm | 6.5 |
| 夯土D渐变色 | 2800×560 | 4mm | 6.5 |
| 夯土C | 2800×530 / 2800×1060 | 5mm | 7.5 |
| 夯土A7 | 2800×1030 | 4mm | 6.5 |
| 夯土砖 | 2850×1000 | 3-7mm | 9.5 |

### 线石系列（24款）
| 款式 | 尺寸 | 厚度 | 重量 |
|------|------|------|------|
| 圆线石（脉络石） | 4050×630 / 2800×990 / 2800×580 | 3-8mm | 10 |
| 中线石 | 3000×1200 | 3-8mm | 9.5 |
| 条纹板（星云石） | 2830×1150 | 3-8mm | 9 |
| 星云石B款 | 3000×1200 | 3-8mm | 9 |
| 波浪石 | 2750×980 | 3-7mm | 7.5 |
| 5号波浪石 | 3000×1200 | 4-8mm | 7.5 |
| 粗纹线石 | 2380×1180 / 2360×580 | 3-5mm | 8 |
| 粗纹线石B款 | 2800×1200 | 4-8mm | 8.5 |
| 布纹石 | 2500×1160 | 3-5mm | 6.5 |
| 粗布纹 | 2800×1200 | 4-6mm | 7 |
| 方线石（阡陌石） | 2800×1000 / 2800×580 | 3-8mm | 9.5 |
| 新线石 | 2950×1200 / 2700×1200 | 3-5mm | 7 |
| 双线石 | 2650×1180 | 3-8mm | 9 |
| 环形石 | 3000×1200 | 4-6mm | 8 |
| 脊线石 | 3000×1120 | 4mm | 6.5 |
| 流水石 | 1180×580 | 3-8mm | 9 |
| 15线石 | 2950×1180 | 3-6mm | 8.5 |
| 23线石 | 2950×1180 | 3-6mm | 9.5 |
| 35线石 | 2950×1180 | 3-6mm | 9 |
| 71线石 | 2950×1160 | 3-5mm | 8.5 |
| 凸纹20内圆 | 3000×1200 | 3-8mm | 8.5 |
| 凹纹30内圆 | 3000×1200 | 3-8mm | 8.5 |
| 100内圆 | 3000×590 | 3-10mm | 待确认 |
| 岁月痕 | 3000×1200 | 3-7mm | 待确认 |

### 岩石系列（17款）
| 款式 | 尺寸 | 厚度 | 重量 |
|------|------|------|------|
| 星月石 | 2830×1150 / 3100×1160 | 3-8mm | 10 |
| 新花岗岩（安英石） | 3000×600 / 3000×1150 | 3-5mm | 7.5 |
| 玄武岩 | 3000×600 | 3-5mm | 7.5 |
| 斧开石 | 2300×560 | 3-8mm | 10 |
| 大斧开石 | 3000×1200 | 3-8mm | 10 |
| 黑山岩 | 3100×1160 | 3-10mm | 10 |
| 叠纹石 | 3100×1160 | 3-12mm | 10 |
| 溶积岩 | 2950×1160 | 3-8mm | 11 |
| 山岩石 | 3000×1200 | 3-10mm | 9 |
| 沉积岩 | 2850×1100 | 3-12mm | 13 |
| 页岩 | 3100×1160 | 3-16mm | 15 |
| 峭壁岩 | 1060×590 | 4mm | 9 |
| 大板岩 | 3000×1200 | 3-5mm | 6.5 |
| 花岗岩拼接 | 2700×1200 | 4mm | 待确认 |
| 云丘 | 2950×1180 | 4mm | 7 |
| 莱姆石 | 2800×1200 | 4mm | 6.5 |
| 斧凿石 | 3000×1200 | 3-6mm | 待确认 |

### 艺术浇筑系列（27款）
| 款式 | 尺寸 | 厚度 | 重量 |
|------|------|------|------|
| 水泥浇筑板 | 3100×1180 | 4mm | 7 |
| 木纹板 | 2830×1140 / 2830×570 | 4mm | 7 |
| 大波纹板 | 3000×1150 / 2800×540 / 2750×1060 | 3-8mm | 9 |
| 小波纹板 | 3000×1200 / 2850×990 | 3-8mm | 7.5 |
| 麻编 | 1420×560 / 2650×1160 | 4mm | 8 |
| 人字编 | 2360×1160 | 3-5mm | 6.5 |
| 竹纹（凸） | 2880×580 / 2950×1000 | 3-8mm | 9 |
| 竹纹（凹） | 2800×980 | 3-8mm | 11 |
| 锯木板 | 3100×1160 | 3-6mm | 9 |
| 积木纹 | 2950×1180 | 4mm | 7 |
| 50马赛克 | 1190×590 | 3-7mm | 待确认 |
| 22马赛克 | 1190×590 | 3-7mm | 待确认 |
| 水立方 | 3000×1140 | 3-7mm | 7 |
| 山竹 | 2950×1160 | 3-7mm | 7 |
| 古木纹板（细纹） | 2950×1200 | 4mm | 7 |
| 古木纹板（粗纹） | 3000×1200 | 4mm | 7 |
| 方型马赛克 | 2980×1180 | 3-6mm | 7 |
| 洞石马赛克 | 2990×1190 | 3-6mm | 9 |
| 苹果叶 | 2950×1160 | 3-5mm | 7 |
| 芭蕉叶 | 2900×1150 | 3-7mm | 7 |
| 齿木纹 | 2950×1180 | 4mm | 7 |
| 碳化木 | 2850×930 | 4mm | 6.5 |
| 摩洛石 | 2950×1180 | 3-5mm | 8 |
| 铝板（泡沫铝） | 2800×1130 | 4mm | 8.5 |
| 洞石拼接 | 3000×1200 | 3-6mm | 待确认 |
| 藤编 | 3000×1200 | 3-5mm | 待确认 |
| 新款麻编 | 3000×1200 | 4mm | 待确认 |

### 洞石系列（3款底材，99色印刷）
| 款式 | 尺寸 | 厚度 | 重量 |
|------|------|------|------|
| 大板洞石 | 2800×1200 / 2400×1200 | 待确认 | 6.5 |
| 新版洞石 | 3000×1200 / 2800×1200 / 2400×1200 | 待确认 | 6.5 |
| 小板洞石 | 1200×600 | 待确认 | 5 |

### 鎏金系列（2款底材，75色）
| 款式 | 尺寸 | 厚度 | 重量 |
|------|------|------|------|
| 硬质鎏金板 | 3000×1220 / 2440×1220 | 待确认 | 11 |
| 软质鎏金板 | 2950×1160 / 2400×1200 | 待确认 | 6.5 |

### 3D打印系列（3D涂装，多色）
平板涂装大板规格：2950×1160 / 2850×1160，3-4mm，6.5kg/㎡

## 关键别名
- 阡陌石 = 方线石（线石系列）
- 脉络石 = 圆线石（线石系列）
- 星云石 = 条纹板（线石系列）
- 安英石 = 新花岗岩（岩石系列）
- 泡沫铝 = 仿铝板 = 铝板（艺术浇筑系列）
- 麻编 归 艺术浇筑系列（非线石系列）
- 星月石 归 岩石系列

## 记忆
- 用户是建材行业从业者，产品为软石（柔性石材）
- 之前尝试微信4.1数据库导出未成功
- 知识库和素材在 F:\\sucaizhengli\\
- 产品目录来源：豪昇新材软石简化版2026.6.6

## 规则
- 问产品参数时引用上表数据
- 不确定的说"待确认"，不编造
- 回复用中文，简洁直接"""


async def call_gemini(messages: list[dict]) -> str:
    """调用 Gemini API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent?key={AI_API_KEY}"

    # 构建 Gemini 格式的 contents
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({
            "role": role,
            "parts": [{"text": m["content"]}]
        })

    body = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "generationConfig": {
            "maxOutputTokens": AI_MAX_TOKENS,
            "temperature": 0.7,
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, timeout=120)
        data = resp.json()

    if resp.status_code != 200:
        log.error(f"Gemini API 错误: {data}")
        return f"[Gemini 错误] {data.get('error', {}).get('message', str(data))}"

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return "[Gemini 返回格式异常]"


async def call_ark(messages: list[dict]) -> str:
    """调用火山引擎 Ark API (OpenAI 兼容)"""
    url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

    # 构建 OpenAI 格式的消息
    api_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        api_messages.append({"role": role, "content": m["content"]})

    body = {
        "model": AI_MODEL,
        "messages": api_messages,
        "max_tokens": AI_MAX_TOKENS,
        "temperature": 0.7,
    }

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, headers=headers, timeout=120)
        data = resp.json()

    if resp.status_code != 200:
        log.error(f"Ark API 错误: {data}")
        return f"[Ark 错误] {data.get('error', {}).get('message', str(data))}"

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return "[Ark 返回格式异常]"


async def call_deepseek(messages: list[dict]) -> str:
    """调用 DeepSeek API (OpenAI 兼容)"""
    url = "https://api.deepseek.com/v1/chat/completions"

    api_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        api_messages.append({"role": role, "content": m["content"]})

    body = {
        "model": AI_MODEL,
        "messages": api_messages,
        "max_tokens": AI_MAX_TOKENS,
        "temperature": 0.7,
    }

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, headers=headers, timeout=120)
        data = resp.json()

    if resp.status_code != 200:
        log.error(f"DeepSeek API 错误: {data}")
        return f"[DeepSeek 错误] {data.get('error', {}).get('message', str(data))}"

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return "[DeepSeek 返回格式异常]"


async def call_ai(messages: list[dict]) -> str:
    """统一 AI 调用入口"""
    if AI_PROVIDER == "gemini":
        return await call_gemini(messages)
    elif AI_PROVIDER == "ark":
        return await call_ark(messages)
    elif AI_PROVIDER == "deepseek":
        return await call_deepseek(messages)
    else:
        return f"[不支持的 AI provider: {AI_PROVIDER}]"


# ============================================================
# FastAPI 应用
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_memory(MEMORY_DIR, MAX_HISTORY)
    log.info(f"飞书 Claude 机器人启动 → http://{SERVER_HOST}:{SERVER_PORT}")
    log.info(f"记忆目录: {MEMORY_DIR}")
    yield

app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping():
    return {"status": "ok", "time": time.time()}


@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件回调入口"""
    body = await request.body()
    body_str = body.decode("utf-8")

    # 记录所有请求
    log.info(f"收到请求: {body_str[:500]}")

    try:
        data = json.loads(body_str)
    except json.JSONDecodeError:
        log.error(f"JSON 解析失败: {body_str[:200]}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # URL 验证（飞书配置回调地址时会发送 challenge）
    if data.get("type") == "url_verification":
        challenge = data.get("challenge", "")
        token = data.get("token", "")
        # 验证 verification_token
        if FEISHU_VERIFY_TOKEN and FEISHU_VERIFY_TOKEN != "YOUR_VERIFICATION_TOKEN":
            if token != FEISHU_VERIFY_TOKEN:
                raise HTTPException(status_code=403, detail="Token mismatch")
        log.info(f"URL 验证请求，token={token}")
        return JSONResponse({"challenge": challenge})

    # 事件回调
    header = data.get("header", {})
    event_type = header.get("event_type", "")

    if event_type == "im.message.receive_v1":
        event = data.get("event", {})
        message_data = event.get("message", {})
        message_id = message_data.get("message_id", "")
        chat_type = message_data.get("chat_type", "")
        sender = event.get("sender", {})
        sender_id = sender.get("sender_id", {})
        open_id = sender_id.get("open_id", "")

        # 过滤机器人自己的消息
        if chat_type == "bot":
            return JSONResponse({"code": 0})

        log.info(f"收到消息: sender={open_id}, msg_id={message_id}")

        # 去重：相同 message_id 已处理过则跳过
        if message_id in _PROCESSED_MSG_IDS:
            log.info(f"重复消息，跳过: {message_id}")
            return JSONResponse({"code": 0})
        _PROCESSED_MSG_IDS.add(message_id)
        # 限制 set 大小，防止内存泄漏
        if len(_PROCESSED_MSG_IDS) > 1000:
            _PROCESSED_MSG_IDS.clear()

        if not open_id or not message_id:
            return JSONResponse({"code": 0})

        # 优先从事件体中直接提取消息内容（无需额外 API 权限）
        msg_content_raw = message_data.get("content", "")
        user_text = ""
        try:
            parsed = json.loads(msg_content_raw)
            user_text = parsed.get("text", msg_content_raw)
        except (json.JSONDecodeError, TypeError):
            user_text = msg_content_raw

        # 如果事件体中没有内容，则通过 API 获取
        if not user_text or not user_text.strip():
            user_text = await get_message_content(message_id)

        if not user_text or not user_text.strip():
            log.warning("消息内容为空，跳过")
            return JSONResponse({"code": 0})

        log.info(f"消息内容: {user_text[:100]}")

        # 加载历史 → 追加上一条用户消息 → 调 AI → 回复
        memory = get_memory()
        memory.append(open_id, "user", user_text)
        context = memory.get_context_messages(open_id)

        log.info(f"上下文消息数: {len(context)}")

        # 调用 AI
        reply = await call_ai(context)

        # 保存回复到记忆
        memory.append(open_id, "assistant", reply)

        # 发送回复
        result = await send_feishu_message(open_id, "text", reply)
        log.info(f"回复结果: {result}")

    return JSONResponse({"code": 0})


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
    )
