"""
这个文件是一个单文件版的 Z.ai 代理服务。

它把外部传进来的 OpenAI 风格请求，转成 Z.ai 网页接口需要的格式，
再把 Z.ai 返回的流式事件，重新整理成 OpenAI 风格的响应。

你可以把它理解成 4 段连续动作：

1. HTTP 路由收到请求事件
2. 路由把请求交给队列指令
3. 队列指令调用上游客户端去拿数据
4. 数据再被转换成 OpenAI 格式反馈给调用方

这个文件里主要有这些内容：

- 服务配置，直接写在文件顶部，方便新手集中修改
- 服务状态，记录当前任务、等待队列、统计数据、面板日志
- 上游客户端，专门负责登录、查模型、创建 chat、打开上游流
- 协议转换，把消息和 SSE 事件改造成 OpenAI 兼容格式
- 串行队列，保证同一时间只处理一个请求
- FastAPI 路由，对外提供 /v1/models 和 /v1/chat/completions
- 启动入口，直接 python main.py 就能跑

最常见的修改入口有这几个：

- 改监听端口：改 servicePort
- 改默认模型：改 defaultModel
- 改固定 token：改 fixedToken
- 改是否显示思考过程：改 showReasoning
- 改是否把工具返回内容直接塞进正文：改 exposeToolResponseAsContent
- 改启用的 mcp 服务：改 enableMcpServers

真实调用路径示例：

- GET  /v1/models
- POST /v1/chat/completions 传 {"model":"GLM-5.1","messages":[{"role":"user","content":"你好"}]}
- POST /v1/chat/completions 传 {"model":"GLM-5.1","stream":true,"messages":[{"role":"user","content":"讲个笑话"}]}
- python main.py
"""


import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text


# =========================
# 配置数据
# =========================

"""
这里放服务最常改的配置。

全部集中写在一起的好处是：
新接手的人不需要到处翻文件，打开顶部就知道这个服务怎么调。
"""

baseUrl = "https://chat.z.ai"
apiHost = "chat.z.ai"
servicePort = 46325

fixedToken = ""

defaultModel = "GLM-5.1"
userAgent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"
)
feVersion = "prod-fe-1.1.12"

requestTimeout = 300
showReasoning = True
exposeToolResponseAsContent = False
enableMcpServers = ["deep-web-search"]
panelEnabled = True
queueMaxLog = 30


"""
这个字典模拟网页访问时常见的浏览器环境信息。

上游接口会依赖这些字段来判断请求是不是来自正常网页。
如果以后上游接口变了，最容易出问题的地方通常就在这里。
"""
browserProfile = {
    "language": "zh-CN",
    "languages": "zh-CN,en,en-GB,en-US",
    "timezone": "Asia/Shanghai",
    "cookie_enabled": "true",
    "screen_width": "1280",
    "screen_height": "800",
    "screen_resolution": "1280x800",
    "viewport_height": "676",
    "viewport_width": "589",
    "viewport_size": "589x676",
    "color_depth": "30",
    "pixel_ratio": "2.4000000953674316",
    "host": apiHost,
    "hostname": apiHost,
    "protocol": "https:",
    "referrer": "",
    "title": "Z.ai - Free AI Chatbot & Agent powered by GLM-5.1 & GLM-5",
    "timezone_offset": "-480",
    "is_mobile": "false",
    "is_touch": "false",
    "max_touch_points": "0",
    "browser_name": "Chrome",
    "os_name": "Windows",
}


# =========================
# 服务状态数据
# =========================

"""
这里是这个服务真正关心的运行时数据。

它们不是业务逻辑，只是状态存储：
- 当前有没有任务在跑
- 还有多少任务在等
- 已经发生过哪些日志
- 成功失败取消了多少次
"""

console = Console()

queueLog = []

currentTask = None
waitingTasks = []

stats = {
    "totalRequests": 0,
    "finishedRequests": 0,
    "failedRequests": 0,
    "cancelledRequests": 0,
}

panelLive = None
panelLock = asyncio.Lock()

app = FastAPI(title="ZAI OpenAI Compatible Proxy")


# =========================
# 任务数据
# =========================

"""
ChatTask 表示一个完整的聊天请求任务。

它会经历这些状态：
排队中 -> 执行中 -> 已完成
               -> 已取消
               -> 失败

这样设计的好处是：
无论你在面板、日志、还是路由里看这个对象，
都能快速知道这次请求现在走到了哪一步。
"""


@dataclass
class ChatTask:
    """
    这个对象保存一次聊天请求从进入服务到返回结果的全部关键状态。

    示例：
    - ChatTask(id="a1b2c3d4", model="GLM-5.1", messages=[...], stream=True, tools=None, clientIp="127.0.0.1")
    - ChatTask(id="task0001", model="GLM-5.1", messages=[{"role":"user","content":"你好"}], stream=False, tools=[], clientIp="10.0.0.8")
    - ChatTask(id="demo1234", model="GLM-5.1", messages=[{"role":"user","content":"写首诗"}], stream=True, tools=None, clientIp="unknown")
    """

    id: str
    model: str
    messages: list
    stream: bool
    tools: list | None
    clientIp: str

    path: str = "/v1/chat/completions"
    createdAt: float = field(default_factory=time.time)
    startedAt: float = 0
    finishedAt: float = 0
    status: str = "排队中"
    note: str = "等待执行"
    outputPreview: str = ""
    cancelEvent: asyncio.Event = field(default_factory=asyncio.Event)
    outputQueue: asyncio.Queue = field(default_factory=asyncio.Queue)
    resultFuture: asyncio.Future | None = None


# =========================
# 小工具
# =========================

def trimText(text, maxLen=80):
    """把长文本压成一行短预览，方便面板和日志显示。"""
    if text is None:
        return ""

    text = str(text).replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= maxLen:
        return text

    return text[:maxLen] + "..."


def getClientIp(request: Request):
    """优先取代理头里的真实 IP，没有就退回到 FastAPI 提供的客户端地址。"""
    forwardedFor = request.headers.get("x-forwarded-for", "")
    if forwardedFor:
        return forwardedFor.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def addCors(response):
    """给所有响应补上最基础的 CORS 头，让浏览器和脚本都更容易调用。"""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


def makeError(message, errorType="server_error", statusCode=500):
    """构造 OpenAI 风格的错误响应。"""
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": errorType,
            }
        },
        status_code=statusCode,
    )


def log(level, title, message):
    """
    记录一条服务日志，同时打印到控制台，并放进最近日志列表里。

    示例：
    - log("INFO", "Queue", "任务已进入等待队列")
    - log("OK", "Startup", "服务启动完成")
    - log("ERROR", "Worker", "上游请求失败")
    """
    nowText = datetime.now().strftime("%H:%M:%S")
    row = {
        "time": nowText,
        "level": level,
        "title": title,
        "message": message,
    }

    queueLog.insert(0, row)
    if len(queueLog) > queueMaxLog:
        queueLog.pop()

    print(f"[{nowText}] [{level}] {title}: {message}", flush=True)


# =========================
# 面板反馈
# =========================

def buildPanel():
    """
    构建 Rich 面板内容。

    面板只负责“反馈”，不负责业务。
    它从当前任务、等待队列、统计数据、最近日志里读数据，
    再把这些数据排版成表格。
    """
    nowTs = time.time()

    queueItems = []
    if currentTask:
        queueItems.append(currentTask)
    queueItems.extend(waitingTasks)

    modelCount = {}
    ipCount = {}

    for item in queueItems:
        modelCount[item.model] = modelCount.get(item.model, 0) + 1
        ipCount[item.clientIp] = ipCount.get(item.clientIp, 0) + 1

    summaryTable = Table(title="服务总览", expand=True)
    summaryTable.add_column("项目", style="cyan", width=16)
    summaryTable.add_column("值", style="green")
    summaryTable.add_row("监听地址", f"http://0.0.0.0:{servicePort}")
    summaryTable.add_row("当前执行数", "1" if currentTask else "0")
    summaryTable.add_row("等待队列数", str(len(waitingTasks)))
    summaryTable.add_row("总请求数", str(stats["totalRequests"]))
    summaryTable.add_row("已完成", str(stats["finishedRequests"]))
    summaryTable.add_row("已失败", str(stats["failedRequests"]))
    summaryTable.add_row("已取消", str(stats["cancelledRequests"]))

    modelSummary = " / ".join([f"{name}:{count}" for name, count in modelCount.items()]) if modelCount else "-"
    ipSummary = " / ".join([f"{ip}:{count}" for ip, count in list(ipCount.items())[:5]]) if ipCount else "-"
    summaryTable.add_row("模型分布", modelSummary)
    summaryTable.add_row("来源IP", ipSummary)

    queueTable = Table(title="请求队列主表", expand=True)
    queueTable.add_column("顺序", style="cyan", width=6, no_wrap=True)
    queueTable.add_column("状态", width=8, no_wrap=True)
    queueTable.add_column("模型", width=14, overflow="fold")
    queueTable.add_column("IP", width=15, no_wrap=True)
    queueTable.add_column("模式", width=6, no_wrap=True)
    queueTable.add_column("秒数", width=8, justify="right", no_wrap=True)
    queueTable.add_column("输出预览", overflow="fold")

    if queueItems:
        for index, item in enumerate(queueItems, start=1):
            if currentTask and item.id == currentTask.id:
                durationSec = nowTs - item.startedAt if item.startedAt else 0
                orderText = f"{index}*"
            else:
                durationSec = nowTs - item.createdAt
                orderText = str(index)

            statusText = item.status or "-"
            if item.status == "执行中":
                statusText = "[green]执行中[/green]"
            elif item.status == "排队中":
                statusText = "[yellow]排队中[/yellow]"
            elif item.status == "已取消":
                statusText = "[red]已取消[/red]"
            elif item.status == "失败":
                statusText = "[red]失败[/red]"
            elif item.status == "已完成":
                statusText = "[green]已完成[/green]"

            queueTable.add_row(
                orderText,
                statusText,
                item.model,
                item.clientIp,
                "流式" if item.stream else "非流式",
                f"{durationSec:.1f}",
                trimText(item.outputPreview, 80),
            )
    else:
        queueTable.add_row("-", "空闲", "-", "-", "-", "0", "-")

    logTable = Table(title="最近日志", expand=True)
    logTable.add_column("时间", style="cyan", width=10)
    logTable.add_column("级别", style="magenta", width=8)
    logTable.add_column("标题", style="yellow", width=16)
    logTable.add_column("内容")

    if queueLog:
        for row in queueLog[:8]:
            logTable.add_row(
                row["time"],
                row["level"],
                row["title"],
                trimText(row["message"], 100),
            )
    else:
        logTable.add_row("-", "-", "-", "暂无日志")

    return Group(summaryTable, Text(""), queueTable, Text(""), logTable)


async def panelLoop():
    """
    持续刷新控制台面板。

    这是纯反馈逻辑，所以就算面板挂了，也不应该影响主服务。
    因此这里故意把异常吞掉并继续重试，避免界面问题拖垮接口服务。
    """
    global panelLive

    if not panelEnabled:
        return

    with Live(buildPanel(), console=console, refresh_per_second=4, screen=False) as live:
        panelLive = live
        while True:
            try:
                live.update(buildPanel(), refresh=True)
                await asyncio.sleep(0.25)
            except Exception as error:
                print("面板刷新异常:", error, flush=True)
                await asyncio.sleep(1)


# =========================
# OpenAI 消息 -> 上游提示词
# =========================

def flattenMessages(messages):
    """
    把 OpenAI 风格 messages 压平为一个文本提示词，交给上游网页接口。

    这个转换比较“朴素”，核心目标不是 100% 还原结构，
    而是尽量把上下文保留下来，让上游还能看懂对话角色和内容。

    示例：
    - [{"role":"user","content":"你好"}]
    - [{"role":"system","content":"你是助手"},{"role":"user","content":"讲笑话"}]
    - [{"role":"user","content":[{"type":"text","text":"写一首诗"}]}]
    """
    parts = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        if isinstance(content, list):
            textParts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    textParts.append(item.get("text", ""))
            content = "".join(textParts)

        if content is None:
            content = ""

        parts.append(f"<{role.upper()}>{content}</{role.upper()}>")

    text = "\n".join(parts).strip()
    return text or ""


def mergeUsage(oldUsage, newUsage):
    """
    用新 usage 覆盖旧 usage 中已有的统计字段。

    这里不用简单相加，是因为上游有时返回的是“最新总量”，
    不是“本次增量”。如果盲目相加，就会把 token 数累计错。
    """
    if not newUsage:
        return oldUsage

    return {
        "prompt_tokens": int(newUsage.get("prompt_tokens", oldUsage.get("prompt_tokens", 0)) or 0),
        "completion_tokens": int(newUsage.get("completion_tokens", oldUsage.get("completion_tokens", 0)) or 0),
        "total_tokens": int(newUsage.get("total_tokens", oldUsage.get("total_tokens", 0)) or 0),
    }


def makeChunk(completionId, model, delta=None, finishReason=None):
    """生成一个 OpenAI 流式 chunk。"""
    return {
        "id": completionId,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta or {},
                "finish_reason": finishReason,
            }
        ],
    }


# =========================
# 上游 SSE -> 统一事件
# =========================

async def parseSseLines(response):
    """
    从上游 HTTP 流里逐行读取 SSE 数据，只保留 data: 开头的有效内容。

    返回的是“原始 JSON 事件”或者 {"done": True} 这样的结束标记。
    """
    async for line in response.aiter_lines():
        if not line:
            continue

        if not line.startswith("data: "):
            continue

        payload = line[6:].strip()

        if payload == "[DONE]":
            yield {"done": True}
            continue

        try:
            yield json.loads(payload)
        except Exception:
            continue


def normalizeEvent(raw):
    """
    把上游多种 phase 事件，统一整理成更容易处理的小事件。

    统一后的事件种类有：
    - done
    - usage
    - thinking
    - answer
    - toolName
    - toolArgs
    - toolResponse

    这样后面的流式和非流式处理函数就可以共用一套判断逻辑。
    """
    if raw.get("done"):
        return {"kind": "done"}

    data = raw.get("data", {}) if isinstance(raw, dict) else {}
    phase = data.get("phase")

    if data.get("done"):
        return {"kind": "done"}

    if phase == "thinking":
        text = data.get("delta_content", "")
        return {"kind": "thinking", "text": text} if text else None

    if phase == "answer":
        text = data.get("delta_content", "")
        return {"kind": "answer", "text": text} if text else None

    if phase == "other" and data.get("usage"):
        return {"kind": "usage", "usage": data.get("usage")}

    if phase == "tool_call":
        if data.get("delta_name"):
            meta = data.get("metadata", {}) or {}
            return {
                "kind": "toolName",
                "name": data.get("delta_name", ""),
                "toolCallId": meta.get("tool_call_id", ""),
            }

        if data.get("delta_arguments") is not None:
            return {
                "kind": "toolArgs",
                "text": data.get("delta_arguments", ""),
            }

        return None

    if phase == "tool_response":
        return {
            "kind": "toolResponse",
            "toolName": data.get("tool_name", ""),
            "text": data.get("delta_content", ""),
            "status": data.get("status", ""),
            "metadata": data.get("metadata", {}) or {},
        }

    return None


# =========================
# 上游客户端指令
# =========================

class ZaiClient:
    """
    这个类只负责和上游 Z.ai 通信。

    它不处理 HTTP 路由，不处理队列，不处理 FastAPI 响应。
    它只做这些事情：
    - 登录
    - 刷新登录
    - 获取模型列表
    - 创建 chat
    - 生成签名
    - 打开上游 completion 流

    这样职责单一，出了问题也更容易定位。
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=requestTimeout,
            follow_redirects=True,
            headers={
                "Accept": "*/*",
                "Accept-Language": "zh-CN",
                "Content-Type": "application/json",
                "Origin": baseUrl,
                "Referer": baseUrl + "/",
                "User-Agent": userAgent,
                "X-FE-Version": feVersion,
            },
        )
        self.authToken = None
        self.userId = None
        self.modelsCache = None

    async def ensureAuth(self):
        """确保当前客户端已经拿到 token 和 userId，没有就去登录一次。"""
        if self.authToken and self.userId:
            return

        headers = {}
        if fixedToken:
            headers["Authorization"] = "Bearer " + fixedToken

        response = await self.client.get(baseUrl + "/api/v1/auths/", headers=headers)
        response.raise_for_status()
        data = response.json()

        self.authToken = fixedToken or data.get("token")
        self.userId = data.get("id")

        if not self.authToken or not self.userId:
            raise RuntimeError("登录失败，拿不到 token 或 userId")

        self.client.headers["Authorization"] = "Bearer " + self.authToken
        log("OK", "Auth", f"登录成功 userId={self.userId}")

    async def refreshAuth(self):
        """清空当前登录状态并重新登录，适合上游 token 失效后重试。"""
        self.authToken = None
        self.userId = None
        self.modelsCache = None

        if "Authorization" in self.client.headers:
            del self.client.headers["Authorization"]

        self.client.cookies.clear()
        await self.ensureAuth()

    async def getModels(self):
        """获取上游模型列表，并缓存结果，避免每次都重复请求。"""
        await self.ensureAuth()

        if self.modelsCache:
            return self.modelsCache

        response = await self.client.get(baseUrl + "/api/models")
        response.raise_for_status()
        data = response.json()

        rawList = data.get("data", []) if isinstance(data, dict) else data
        items = []

        for item in rawList or []:
            info = item.get("info", {})
            if not info.get("is_active", True):
                continue

            modelId = item.get("id") or item.get("name") or "unknown"
            items.append(
                {
                    "id": modelId,
                    "object": "model",
                    "created": 0,
                    "owned_by": "z.ai",
                }
            )

        self.modelsCache = {
            "object": "list",
            "data": items,
        }
        return self.modelsCache

    def buildNewChatBody(self, promptText, model):
        """构造新建 chat 的请求体。"""
        messageId = str(uuid.uuid4())
        nowSec = int(time.time())
        nowMs = int(time.time() * 1000)

        return {
            "chat": {
                "id": "",
                "title": "新聊天",
                "models": [model],
                "params": {},
                "history": {
                    "messages": {
                        messageId: {
                            "id": messageId,
                            "parentId": None,
                            "childrenIds": [],
                            "role": "user",
                            "timestamp": nowSec,
                            "content": promptText,
                            "models": [model],
                        }
                    },
                    "currentId": messageId,
                },
                "tags": [],
                "flags": [],
                "features": [
                    {"type": "mcp", "server": "vibe-coding", "status": "hidden"},
                    {"type": "mcp", "server": "ppt-maker", "status": "hidden"},
                    {"type": "mcp", "server": "image-search", "status": "hidden"},
                    {"type": "mcp", "server": "deep-research", "status": "hidden"},
                    {"type": "mcp", "server": "deep-web-search", "status": "hidden"},
                    {"type": "tool_selector", "server": "tool_selector_h", "status": "hidden"},
                ],
                "mcp_servers": [],
                "enable_thinking": True,
                "auto_web_search": False,
                "message_version": 1,
                "timestamp": nowMs,
                "extra": {},
            }
        }

    async def createChat(self, promptText, model):
        """先创建一个 chat，后面的 completion 流需要带着这个 chatId。"""
        await self.ensureAuth()

        body = self.buildNewChatBody(promptText, model)
        response = await self.client.post(baseUrl + "/api/v1/chats/new", json=body)
        response.raise_for_status()
        data = response.json()

        chatId = data.get("id") or data.get("chat", {}).get("id")
        if not chatId:
            raise RuntimeError("创建 chat 失败，没有拿到 chatId")

        return chatId

    def buildSignatureAndQuery(self, promptText, chatId):
        """
        生成上游 completion 接口所需的查询参数和签名。

        这里是最像“兼容层”的代码。
        之所以保留这种写法，是因为上游接口就是要求这些字段和签名算法。
        这类代码不是为了优雅，而是为了兼容。
        """
        timestampMs = str(int(time.time() * 1000))
        requestId = str(uuid.uuid4())

        core = {
            "timestamp": timestampMs,
            "requestId": requestId,
            "user_id": self.userId or "",
        }

        sortedPayload = ",".join(f"{key},{value}" for key, value in sorted(core.items()))
        promptB64 = base64.b64encode(promptText.encode("utf-8")).decode("ascii")
        timeBucket = str(int(timestampMs) // (5 * 60 * 1000))

        derivedKey = hmac.new(
            b"key-@@@@)))()((9))-xxxx&&&%%%%%",
            timeBucket.encode(),
            hashlib.sha256,
        ).hexdigest()

        message = f"{sortedPayload}|{promptB64}|{timestampMs}"
        xSignature = hmac.new(
            derivedKey.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        nowUtc = datetime.now(timezone.utc)
        currentUrl = baseUrl + "/c/" + chatId

        extra = {
            "version": "0.0.1",
            "platform": "web",
            "token": self.authToken or "",
            "user_agent": userAgent,
            "language": browserProfile["language"],
            "languages": browserProfile["languages"],
            "timezone": browserProfile["timezone"],
            "cookie_enabled": browserProfile["cookie_enabled"],
            "screen_width": browserProfile["screen_width"],
            "screen_height": browserProfile["screen_height"],
            "screen_resolution": browserProfile["screen_resolution"],
            "viewport_height": browserProfile["viewport_height"],
            "viewport_width": browserProfile["viewport_width"],
            "viewport_size": browserProfile["viewport_size"],
            "color_depth": browserProfile["color_depth"],
            "pixel_ratio": browserProfile["pixel_ratio"],
            "current_url": currentUrl,
            "pathname": "/c/" + chatId,
            "search": "",
            "hash": "",
            "host": browserProfile["host"],
            "hostname": browserProfile["hostname"],
            "protocol": browserProfile["protocol"],
            "referrer": browserProfile["referrer"],
            "title": browserProfile["title"],
            "timezone_offset": browserProfile["timezone_offset"],
            "local_time": nowUtc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{nowUtc.microsecond // 1000:03d}Z",
            "utc_time": nowUtc.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "is_mobile": browserProfile["is_mobile"],
            "is_touch": browserProfile["is_touch"],
            "max_touch_points": browserProfile["max_touch_points"],
            "browser_name": browserProfile["browser_name"],
            "os_name": browserProfile["os_name"],
            "signature_timestamp": timestampMs,
        }

        query = urlencode({**core, **extra})
        return query, xSignature

    def buildCompletionBody(self, chatId, promptText, model):
        """构造上游 completion 请求体。"""
        nowLocal = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))

        return {
            "stream": True,
            "model": model,
            "messages": [{"role": "user", "content": promptText}],
            "signature_prompt": promptText,
            "params": {},
            "extra": {},
            "mcp_servers": enableMcpServers[:],
            "features": {
                "image_generation": False,
                "web_search": False,
                "auto_web_search": False,
                "preview_mode": True,
                "flags": [],
                "vlm_tools_enable": False,
                "vlm_web_search_enable": False,
                "vlm_website_mode": False,
                "enable_thinking": True,
            },
            "variables": {
                "{{USER_NAME}}": "Guest",
                "{{USER_LOCATION}}": "Unknown",
                "{{CURRENT_DATETIME}}": nowLocal.strftime("%Y-%m-%d %H:%M:%S"),
                "{{CURRENT_DATE}}": nowLocal.strftime("%Y-%m-%d"),
                "{{CURRENT_TIME}}": nowLocal.strftime("%H:%M:%S"),
                "{{CURRENT_WEEKDAY}}": nowLocal.strftime("%A"),
                "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
                "{{USER_LANGUAGE}}": "zh-CN",
            },
            "chat_id": chatId,
            "id": str(uuid.uuid4()),
            "current_user_message_id": str(uuid.uuid4()),
            "current_user_message_parent_id": None,
            "background_tasks": {
                "title_generation": True,
                "tags_generation": True,
            },
        }

    async def openCompletionStream(self, promptText, model):
        """
        打开上游流式 completion。

        返回：
        - chatId
        - response 流对象
        """
        await self.ensureAuth()

        chatId = await self.createChat(promptText, model)
        query, xSignature = self.buildSignatureAndQuery(promptText, chatId)
        body = self.buildCompletionBody(chatId, promptText, model)

        url = baseUrl + "/api/v2/chat/completions?" + query
        headers = {
            "Accept": "*/*",
            "X-Signature": xSignature,
            "Referer": baseUrl + "/c/" + chatId,
            "Origin": baseUrl,
        }

        log("INFO", "Upstream", f"打开上游流 chatId={chatId} model={model}")

        response = await self.client.send(
            self.client.build_request("POST", url, json=body, headers=headers),
            stream=True,
        )
        response.raise_for_status()

        return chatId, response

    async def close(self):
        """关闭底层 HTTP 客户端连接池。"""
        await self.client.aclose()


# =========================
# 队列指令
# =========================

class SerialDispatcher:
    """
    串行调度器。

    它的职责非常明确：
    同一时间只跑一个任务，后来的请求先进等待队列，
    当前任务结束后再取下一个。

    这样做虽然吞吐量低，但行为非常稳定，也更容易追问题。
    """

    def __init__(self, zaiClient):
        self.zaiClient = zaiClient
        self.queue = asyncio.Queue()
        self.workerTask = None
        self.currentTask = None

    async def start(self):
        """启动后台 worker，只启动一次。"""
        if not self.workerTask:
            self.workerTask = asyncio.create_task(self.worker())

    async def submit(self, task):
        """
        把任务放进等待队列。

        这是“事件进入指令层”的第一步：
        路由不直接操作上游，而是统一交给调度器。
        """
        global waitingTasks
        global stats

        task.status = "排队中"
        task.note = "等待前方请求完成"

        waitingTasks.append(task)
        stats["totalRequests"] += 1

        await self.queue.put(task)
        log("INFO", "Queue", f"任务入队 id={task.id} ip={task.clientIp} model={task.model} stream={task.stream}")

    async def worker(self):
        """
        后台常驻 worker。

        读取一个任务 -> 标记执行中 -> 调上游 -> 写回结果/流 -> 更新统计。
        """
        global currentTask
        global waitingTasks
        global stats

        while True:
            task = await self.queue.get()

            waitingTasks = [item for item in waitingTasks if item.id != task.id]

            self.currentTask = task
            currentTask = task
            task.startedAt = time.time()
            task.status = "执行中"
            task.note = "正在请求上游"
            task.outputPreview = ""

            log("INFO", "Queue", f"开始执行 id={task.id} ip={task.clientIp} model={task.model}")

            try:
                if task.stream:
                    await self.handleStreamTask(task)
                    if task.status not in ["已取消", "失败"]:
                        task.status = "已完成"
                        task.note = "流式请求完成"
                        stats["finishedRequests"] += 1
                else:
                    await self.handleNonStreamTask(task)
                    if task.status not in ["已取消", "失败"]:
                        task.status = "已完成"
                        task.note = "非流式请求完成"
                        stats["finishedRequests"] += 1

            except Exception as error:
                task.status = "失败"
                task.note = str(error)
                stats["failedRequests"] += 1

                log("ERROR", "WorkerError", f"id={task.id} err={error}")

                if task.stream:
                    try:
                        errorBody = {
                            "error": {
                                "message": str(error),
                                "type": "server_error",
                            }
                        }
                        await task.outputQueue.put("data: " + json.dumps(errorBody, ensure_ascii=False) + "\n\n")
                        await task.outputQueue.put("data: [DONE]\n\n")
                    except Exception:
                        pass

                    await task.outputQueue.put(None)
                else:
                    if task.resultFuture and not task.resultFuture.done():
                        task.resultFuture.set_exception(error)

            finally:
                task.finishedAt = time.time()
                spent = task.finishedAt - task.startedAt if task.startedAt else 0

                log("INFO", "Queue", f"执行结束 id={task.id} status={task.status} spent={spent:.1f}s")

                self.currentTask = None
                currentTask = None
                self.queue.task_done()

    async def handleStreamTask(self, task):
        """
        处理流式任务。

        事件流向是：
        上游 SSE -> normalizeEvent -> OpenAI chunk -> task.outputQueue -> StreamingResponse
        """
        completionId = "chatcmpl-" + uuid.uuid4().hex[:29]
        promptText = flattenMessages(task.messages)
        model = task.model

        _, response = await self.zaiClient.openCompletionStream(promptText, model)

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        toolIndex = 0
        currentToolId = None
        currentToolName = ""
        currentToolArguments = ""

        await task.outputQueue.put(
            "data: " + json.dumps(makeChunk(completionId, model, {"role": "assistant"}), ensure_ascii=False) + "\n\n"
        )

        try:
            async for raw in parseSseLines(response):
                if task.cancelEvent.is_set():
                    task.status = "已取消"
                    task.note = "客户端已断开，上游已停止"
                    log("WARN", "Cancel", f"流式任务被取消 id={task.id}")
                    await response.aclose()
                    await task.outputQueue.put(None)
                    return

                event = normalizeEvent(raw)
                if not event:
                    continue

                if event["kind"] == "usage":
                    usage = mergeUsage(usage, event.get("usage"))
                    continue

                if event["kind"] == "thinking":
                    if showReasoning:
                        chunk = makeChunk(completionId, model, {"reasoning_content": event["text"]})
                        await task.outputQueue.put("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
                    continue

                if event["kind"] == "answer":
                    text = event.get("text", "")
                    if text:
                        task.outputPreview = trimText(task.outputPreview + text, 120)
                    chunk = makeChunk(completionId, model, {"content": text})
                    await task.outputQueue.put("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
                    continue

                if event["kind"] == "toolName":
                    currentToolId = event.get("toolCallId") or ("call_" + uuid.uuid4().hex[:24])
                    currentToolName = event.get("name", "")
                    currentToolArguments = ""

                    chunk = makeChunk(
                        completionId,
                        model,
                        {
                            "tool_calls": [
                                {
                                    "index": toolIndex,
                                    "id": currentToolId,
                                    "type": "function",
                                    "function": {
                                        "name": currentToolName,
                                        "arguments": "",
                                    },
                                }
                            ]
                        },
                    )
                    await task.outputQueue.put("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
                    continue

                if event["kind"] == "toolArgs":
                    currentToolArguments += event.get("text", "")
                    chunk = makeChunk(
                        completionId,
                        model,
                        {
                            "tool_calls": [
                                {
                                    "index": toolIndex,
                                    "id": currentToolId or ("call_" + uuid.uuid4().hex[:24]),
                                    "type": "function",
                                    "function": {
                                        "name": currentToolName,
                                        "arguments": event.get("text", ""),
                                    },
                                }
                            ]
                        },
                    )
                    await task.outputQueue.put("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
                    continue

                if event["kind"] == "toolResponse":
                    if exposeToolResponseAsContent and event.get("text"):
                        toolText = event.get("text", "")
                        task.outputPreview = trimText(task.outputPreview + toolText, 120)
                        chunk = makeChunk(completionId, model, {"content": toolText})
                        await task.outputQueue.put("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")

                    toolIndex += 1
                    currentToolId = None
                    currentToolName = ""
                    currentToolArguments = ""
                    continue

                if event["kind"] == "done":
                    break

            finishReason = "tool_calls" if toolIndex > 0 else "stop"

            await task.outputQueue.put(
                "data: " + json.dumps(makeChunk(completionId, model, {}, finishReason), ensure_ascii=False) + "\n\n"
            )

            await task.outputQueue.put(
                "data: " + json.dumps(
                    {
                        "id": completionId,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [],
                        "usage": usage,
                    },
                    ensure_ascii=False,
                ) + "\n\n"
            )

            await task.outputQueue.put("data: [DONE]\n\n")
            await task.outputQueue.put(None)

        finally:
            await response.aclose()

    async def handleNonStreamTask(self, task):
        """
        处理非流式任务。

        做法不是单独请求一个非流式上游接口，
        而是依旧读取上游流，然后在本地把所有片段拼成一个完整结果。

        这种方式的好处是：
        流式和非流式共用同一条上游通路，逻辑一致，兼容性也更稳定。
        """
        completionId = "chatcmpl-" + uuid.uuid4().hex[:29]
        promptText = flattenMessages(task.messages)
        model = task.model

        _, response = await self.zaiClient.openCompletionStream(promptText, model)

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        contentParts = []
        reasoningParts = []
        toolCalls = []

        currentToolId = None
        currentToolName = ""
        currentToolArguments = ""

        try:
            async for raw in parseSseLines(response):
                if task.cancelEvent.is_set():
                    task.status = "已取消"
                    task.note = "客户端已断开，上游已停止"
                    log("WARN", "Cancel", f"非流式任务被取消 id={task.id}")
                    await response.aclose()
                    raise RuntimeError("客户端已断开，任务取消")

                event = normalizeEvent(raw)
                if not event:
                    continue

                if event["kind"] == "usage":
                    usage = mergeUsage(usage, event.get("usage"))
                    continue

                if event["kind"] == "thinking":
                    if showReasoning:
                        reasoningParts.append(event["text"])
                    continue

                if event["kind"] == "answer":
                    contentParts.append(event["text"])
                    task.outputPreview = trimText("".join(contentParts), 120)
                    continue

                if event["kind"] == "toolName":
                    if currentToolName or currentToolArguments:
                        toolCalls.append(
                            {
                                "id": currentToolId or ("call_" + uuid.uuid4().hex[:24]),
                                "type": "function",
                                "function": {
                                    "name": currentToolName,
                                    "arguments": currentToolArguments,
                                },
                            }
                        )

                    currentToolId = event.get("toolCallId") or ("call_" + uuid.uuid4().hex[:24])
                    currentToolName = event.get("name", "")
                    currentToolArguments = ""
                    continue

                if event["kind"] == "toolArgs":
                    currentToolArguments += event.get("text", "")
                    continue

                if event["kind"] == "toolResponse":
                    if exposeToolResponseAsContent and event.get("text"):
                        contentParts.append(event.get("text", ""))
                        task.outputPreview = trimText("".join(contentParts), 120)

                    if currentToolName or currentToolArguments:
                        toolCalls.append(
                            {
                                "id": currentToolId or ("call_" + uuid.uuid4().hex[:24]),
                                "type": "function",
                                "function": {
                                    "name": currentToolName,
                                    "arguments": currentToolArguments,
                                },
                            }
                        )
                        currentToolId = None
                        currentToolName = ""
                        currentToolArguments = ""
                    continue

                if event["kind"] == "done":
                    break

            if currentToolName or currentToolArguments:
                toolCalls.append(
                    {
                        "id": currentToolId or ("call_" + uuid.uuid4().hex[:24]),
                        "type": "function",
                        "function": {
                            "name": currentToolName,
                            "arguments": currentToolArguments,
                        },
                    }
                )

            message = {
                "role": "assistant",
                "content": "".join(contentParts),
            }

            if showReasoning and reasoningParts:
                message["reasoning_content"] = "".join(reasoningParts)

            if toolCalls:
                message["tool_calls"] = toolCalls

            result = {
                "id": completionId,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if toolCalls else "stop",
                    }
                ],
                "usage": usage,
            }

            if task.resultFuture and not task.resultFuture.done():
                task.resultFuture.set_result(result)

        finally:
            await response.aclose()


# =========================
# 全局指令实例
# =========================

zaiClient = ZaiClient()
dispatcher = SerialDispatcher(zaiClient)


# =========================
# HTTP 事件
# =========================

@app.on_event("startup")
async def startupEvent():
    """服务启动时启动队列 worker 和面板刷新任务。"""
    await dispatcher.start()
    asyncio.create_task(panelLoop())
    log("OK", "Startup", f"服务启动完成 http://0.0.0.0:{servicePort}")


@app.on_event("shutdown")
async def shutdownEvent():
    """服务关闭时释放上游 HTTP 连接。"""
    await zaiClient.close()
    log("OK", "Shutdown", "服务已关闭")


@app.options("/v1/models")
async def optionsModels():
    """浏览器预检请求。"""
    return addCors(JSONResponse({}))


@app.get("/v1/models")
async def listModels():
    """
    查询可用模型列表。

    调用示例：
    - GET /v1/models
    - curl http://127.0.0.1:46325/v1/models
    - 浏览器打开 http://127.0.0.1:46325/v1/models
    """
    try:
        data = await zaiClient.getModels()
        return addCors(JSONResponse(data))
    except Exception as error:
        return addCors(makeError(str(error)))


@app.options("/v1/chat/completions")
async def optionsChat():
    """浏览器预检请求。"""
    return addCors(JSONResponse({}))


async def streamTaskOutput(task, request):
    """
    把队列任务产出的 chunk 一段一段推给客户端。

    这里是“反馈出口”：
    队列 worker 往 task.outputQueue 写数据，
    StreamingResponse 再从这里把数据送到网络上。
    """
    global stats

    try:
        while True:
            if await request.is_disconnected():
                task.cancelEvent.set()
                task.status = "已取消"
                task.note = "客户端断开连接"
                stats["cancelledRequests"] += 1
                log("WARN", "Disconnect", f"客户端断开 id={task.id} ip={task.clientIp}")
                break

            item = await task.outputQueue.get()
            if item is None:
                break

            yield item

    except asyncio.CancelledError:
        task.cancelEvent.set()
        task.status = "已取消"
        task.note = "流式响应被取消"
        stats["cancelledRequests"] += 1
        raise

    finally:
        task.cancelEvent.set()


@app.post("/v1/chat/completions")
async def chatCompletions(request: Request):
    """
    OpenAI 兼容聊天接口。

    调用示例：
    - {"model":"GLM-5.1","messages":[{"role":"user","content":"你好"}]}
    - {"model":"GLM-5.1","stream":true,"messages":[{"role":"user","content":"讲个故事"}]}
    - {"messages":[{"role":"system","content":"你是助手"},{"role":"user","content":"介绍一下你自己"}]}
    - {"model":"GLM-5.1","stream":false,"messages":[{"role":"user","content":"写一首五言诗"}]}
    """
    global stats

    try:
        body = await request.json()
    except Exception:
        return addCors(makeError("请求体不是合法 JSON", "invalid_request_error", 400))

    messages = body.get("messages") or []
    model = body.get("model") or defaultModel
    stream = bool(body.get("stream", False))
    tools = body.get("tools")

    # 这里先做最基础的输入检查，避免无效请求进入队列浪费资源。
    if not messages:
        return addCors(makeError("messages 不能为空", "invalid_request_error", 400))

    taskId = uuid.uuid4().hex[:8]
    clientIp = getClientIp(request)

    task = ChatTask(
        id=taskId,
        model=model,
        messages=messages,
        stream=stream,
        tools=tools,
        clientIp=clientIp,
    )

    if not stream:
        loop = asyncio.get_running_loop()
        task.resultFuture = loop.create_future()

    await dispatcher.submit(task)

    if stream:
        response = StreamingResponse(
            streamTaskOutput(task, request),
            media_type="text/event-stream",
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["X-Accel-Buffering"] = "no"
        return addCors(response)

    try:
        while True:
            if await request.is_disconnected():
                task.cancelEvent.set()
                task.status = "已取消"
                task.note = "客户端在等待结果时断开"
                stats["cancelledRequests"] += 1
                log("WARN", "Disconnect", f"非流式客户端断开 id={task.id} ip={task.clientIp}")
                return addCors(makeError("客户端已断开", "client_disconnect", 499))

            if task.resultFuture.done():
                result = await task.resultFuture
                return addCors(JSONResponse(result))

            await asyncio.sleep(0.05)

    except Exception as error:
        return addCors(makeError(str(error)))


# =========================
# 直接启动
# =========================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=servicePort)