#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
# 负责什么：
# - 把 chat.z.ai 包装成只支持 OpenAI 协议的单文件代理。
# - 用全局串行队列把聊天请求排队处理，避免并发请求同时打到上游后触发空流或风控。
# - 用 Rich 在终端实时展示队列、当前执行中的请求、最近完成的请求，并统一输出中文彩色日志。
#
# 主要数据：
# - Config：运行配置、默认请求头、端口、重试次数。
# - zai：唯一上游会话，所有聊天请求按队列串行复用它。
# - queue_board：全局排队状态、当前执行中的请求、最近日志和 Rich 实时面板。
#
# 可直接调用的方法：
# - GET  /v1/models
# - POST /v1/chat/completions
# - zai.get_models()
# - zai.create_completion(...)
# - queue_board.enter_request(...)
# - queue_board.finish_request(...)
#
# 修改影响：
# - 会直接影响模型列表、聊天补全、流式输出、重试策略、串行队列行为和终端日志展示。
# - 这个文件就是单文件入口，改这里等于改完整代理服务行为。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Generator
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, make_response, request
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

load_dotenv()


class Config:
    """
    # 负责什么：
    # - 集中管理运行配置和默认请求头。
    #
    # 主要数据：
    # - base_url / api_host：上游地址。
    # - default_model：默认模型。
    # - chat_retry_count：空流重试次数。
    # - headers：模拟浏览器请求头。
    #
    # 可直接调用的方法：
    # - Config.make_headers()
    #
    # 修改影响：
    # - 会影响所有上游请求、重试次数、队列展示和服务端口。
    # """

    base_url = os.getenv("BASE_URL", "https://chat.z.ai").rstrip("/")
    api_host = os.getenv("BASE", "chat.z.ai").strip()
    token = os.getenv("TOKEN", "").strip()
    port = int(os.getenv("PORT", "46325"))
    debug = os.getenv("DEBUG", "false").strip().lower() == "true"
    debug_http = os.getenv("DEBUG_MSG", "false").strip().lower() == "true"
    default_model = os.getenv("MODEL", "glm-5").strip() or "glm-5"
    hmac_secret = "key-@@@@)))()((9))-xxxx&&&%%%%%"
    fe_version = os.getenv("ZAI_FE_VERSION", "prod-fe-1.0.231")
    client_version = os.getenv("ZAI_CLIENT_VERSION", "0.0.1")
    user_agent = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    )
    chat_retry_count = int(os.getenv("ZAI_EMPTY_RETRY_COUNT", "3"))
    queue_history_size = int(os.getenv("ZAI_QUEUE_HISTORY_SIZE", "20"))

    @classmethod
    def make_headers(cls) -> dict:
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-CN",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Origin": cls.base_url,
            "Pragma": "no-cache",
            "Referer": f"{cls.base_url}/",
            "User-Agent": cls.user_agent,
            "X-FE-Version": cls.fe_version,
        }


console = Console()
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


@dataclass
class QueueRequest:
    """
    # 负责什么：
    # - 表达一个进入全局聊天队列的请求。
    #
    # 主要数据：
    # - request_id：本地请求标识。
    # - ticket：排队号。
    # - model：当前请求模型。
    # - stream：是否流式。
    # - entered_at：进入队列的时间。
    #
    # 可直接调用的方法：
    # - QueueRequest(...)
    #
    # 修改影响：
    # - 会影响终端队列展示和排队顺序。
    # """

    request_id: str
    ticket: int
    model: str
    stream: bool
    entered_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    status: str = "排队中"
    note: str = "等待前方请求完成"


class QueueBoard:
    """
    # 负责什么：
    # - 用一个全局条件变量把聊天请求串行化。
    # - 用 Rich Live 面板实时展示等待队列、当前执行中的请求和最近完成记录。
    # - 输出中文彩色日志，替代 print 和普通 logging。
    #
    # 主要数据：
    # - self.waiting：等待中的请求列表。
    # - self.current：当前正在执行的请求。
    # - self.history：最近完成的请求和事件摘要。
    # - self.next_ticket：递增排队号。
    #
    # 可直接调用的方法：
    # - queue_board.enter_request(...)
    # - queue_board.finish_request(...)
    # - queue_board.log_info(...)
    #
    # 修改影响：
    # - 会直接影响并发处理方式和终端可视化体验。
    # """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.waiting: list[QueueRequest] = []
        self.current: QueueRequest | None = None
        self.history: deque[tuple[str, str, str]] = deque(maxlen=Config.queue_history_size)
        self.next_ticket = 1
        self.live_enabled = console.is_terminal
        self.live: Live | None = None
        if self.live_enabled:
            self.live = Live(self.make_layout(), console=console, refresh_per_second=4, transient=False)
            self.live.start()
        self.log_info("系统", "串行队列已启动，聊天请求将按顺序逐个执行")

    # 目的：输出中文彩色日志
    # 输入：级别、标题、内容
    # 输出：无
    # 会修改：history、终端面板
    # 结果：日志统一由 Rich 输出，便于观察队列和请求状态
    def log(self, level: str, title: str, message: str) -> None:
        level_style = {
            "信息": "bold cyan",
            "成功": "bold green",
            "警告": "bold yellow",
            "错误": "bold red",
            "调试": "bold magenta",
        }.get(level, "bold white")
        with self.lock:
            now_text = datetime.now().strftime("%H:%M:%S")
            self.history.appendleft((now_text, level, f"{title}：{message}"))
            console.log(f"[{level_style}]{level}[/] [{title}] {message}")
            self.refresh_live()

    def log_info(self, title: str, message: str) -> None:
        self.log("信息", title, message)

    def log_success(self, title: str, message: str) -> None:
        self.log("成功", title, message)

    def log_warning(self, title: str, message: str) -> None:
        self.log("警告", title, message)

    def log_error(self, title: str, message: str) -> None:
        self.log("错误", title, message)

    def log_debug(self, title: str, message: str) -> None:
        if Config.debug_http:
            self.log("调试", title, message)

    # 目的：让一个聊天请求进入全局队列并等待轮到自己
    # 输入：模型名、是否流式
    # 输出：当前请求的队列对象
    # 会修改：waiting、current、排队号
    # 结果：多个聊天请求不会再并发打上游
    def enter_request(self, model: str, stream: bool) -> QueueRequest:
        with self.cond:
            request_state = QueueRequest(
                request_id=uuid.uuid4().hex[:8],
                ticket=self.next_ticket,
                model=model,
                stream=stream,
            )
            self.next_ticket += 1
            self.waiting.append(request_state)
            self.log_info(
                "队列",
                f"请求 #{request_state.ticket} 已入队，模式={'流式' if stream else '非流式'}，模型={model}，前方等待 {max(len(self.waiting) - 1, 0)} 个请求",
            )

            while True:
                is_first = self.waiting and self.waiting[0] is request_state
                is_idle = self.current is None
                if is_first and is_idle:
                    self.waiting.pop(0)
                    request_state.started_at = time.time()
                    request_state.status = "执行中"
                    request_state.note = "已获取全局执行权，开始请求上游"
                    self.current = request_state
                    self.log_success("队列", f"请求 #{request_state.ticket} 开始执行")
                    return request_state
                self.cond.wait(timeout=0.3)

    # 目的：结束当前请求并唤醒下一个排队请求
    # 输入：请求对象、结果说明
    # 输出：无
    # 会修改：current、请求状态、等待条件
    # 结果：后续请求可以继续执行
    def finish_request(self, request_state: QueueRequest, result_text: str) -> None:
        with self.cond:
            request_state.finished_at = time.time()
            request_state.status = "已完成"
            request_state.note = result_text
            if self.current is request_state:
                self.current = None
            spent = request_state.finished_at - request_state.started_at if request_state.started_at else 0.0
            self.log_info("队列", f"请求 #{request_state.ticket} 已结束，耗时 {spent:.1f}s，结果：{result_text}")
            self.cond.notify_all()

    # 目的：构建 Rich 终端面板
    # 输入：无
    # 输出：Rich 可渲染对象
    # 会修改：无
    # 结果：终端可以实时看到队列、当前请求和最近日志
    def make_layout(self):
        queue_table = Table(title="聊天请求队列", expand=True)
        queue_table.add_column("排队号", style="bold cyan", width=8)
        queue_table.add_column("请求ID", style="white", width=10)
        queue_table.add_column("模型", style="green", width=12)
        queue_table.add_column("模式", style="yellow", width=8)
        queue_table.add_column("状态", style="magenta", width=10)
        queue_table.add_column("等待秒数", style="blue", width=10)
        queue_table.add_column("说明", style="white")

        current_items = [self.current] if self.current else []
        waiting_items = list(self.waiting)
        all_items = current_items + waiting_items
        now = time.time()
        if not all_items:
            queue_table.add_row("-", "-", "-", "-", "空闲", "0.0", "当前没有排队请求")
        else:
            for item in all_items:
                waited = now - item.entered_at
                mode_text = "流式" if item.stream else "非流式"
                queue_table.add_row(
                    str(item.ticket),
                    item.request_id,
                    item.model,
                    mode_text,
                    item.status,
                    f"{waited:.1f}",
                    item.note,
                )

        current_table = Table(title="当前执行中的请求", expand=True)
        current_table.add_column("字段", style="bold cyan", width=16)
        current_table.add_column("值", style="white")
        if self.current is None:
            current_table.add_row("状态", "当前没有请求在执行")
        else:
            running = time.time() - self.current.started_at if self.current.started_at else 0.0
            current_table.add_row("排队号", str(self.current.ticket))
            current_table.add_row("请求ID", self.current.request_id)
            current_table.add_row("模型", self.current.model)
            current_table.add_row("模式", "流式" if self.current.stream else "非流式")
            current_table.add_row("已执行秒数", f"{running:.1f}")
            current_table.add_row("说明", self.current.note)

        history_table = Table(title="最近日志", expand=True)
        history_table.add_column("时间", style="cyan", width=10)
        history_table.add_column("级别", style="magenta", width=8)
        history_table.add_column("内容", style="white")
        if not self.history:
            history_table.add_row("-", "-", "暂无日志")
        else:
            for time_text, level, message in list(self.history)[:10]:
                history_table.add_row(time_text, level, message)

        summary = Text()
        summary.append("串行策略", style="bold green")
        summary.append("：聊天请求严格排队，一个接一个执行。  ")
        summary.append("等待数", style="bold yellow")
        summary.append(f"：{len(self.waiting)}  ")
        summary.append("当前执行", style="bold cyan")
        summary.append(f"：{'有' if self.current else '无'}")

        return Group(
            Panel(summary, title="代理状态总览", border_style="green"),
            queue_table,
            current_table,
            history_table,
        )

    def refresh_live(self) -> None:
        if self.live:
            self.live.update(self.make_layout(), refresh=True)


queue_board = QueueBoard()


def make_error_response(message: str, error_type: str = "server_error") -> dict:
    return {"error": {"message": message, "type": error_type}}


class ZaiServer:
    """
    # 负责什么：
    # - 管理一个可复用的上游会话。
    # - 负责 guest 登录、模型列表、创建聊天、发起对话。
    # - 把 z.ai 的事件整理成 OpenAI 需要的数据。
    #
    # 主要数据：
    # - self.http：复用的 requests session。
    # - self.token / self.user_id：当前上游身份。
    # - self.models_cache：模型列表缓存。
    #
    # 可直接调用的方法：
    # - zai.get_models()
    # - zai.create_completion(...)
    #
    # 修改影响：
    # - 会直接影响上游登录、签名和所有 OpenAI 接口输出。
    # """

    def __init__(self) -> None:
        self.http = requests.Session()
        self.http.headers.update(Config.make_headers())
        self.token: str | None = None
        self.user_id: str | None = None
        self.models_cache: dict | None = None

    def add_cors(self, resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    # 目的：确保当前会话已经拿到 token 和 user_id
    # 输入：无
    # 输出：无
    # 会修改：self.token、self.user_id、self.http.headers
    # 结果：后续请求能直接带上 Bearer Token
    def ensure_auth(self) -> None:
        if self.token and self.user_id:
            return

        headers = dict(Config.make_headers())
        if Config.token:
            headers["Authorization"] = f"Bearer {Config.token}"

        response = self.http.get(f"{Config.base_url}/api/v1/auths/", headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()

        self.token = Config.token or data.get("token")
        self.user_id = data.get("id")
        if not self.token or not self.user_id:
            raise RuntimeError("上游登录成功，但没有拿到 token 或 user_id")

        self.http.headers["Authorization"] = f"Bearer {self.token}"
        queue_board.log_success("登录", f"guest 登录成功，user_id={self.user_id}")

    # 目的：强制丢弃当前 guest 会话并重新登录
    # 输入：无
    # 输出：无
    # 会修改：self.token、self.user_id、self.models_cache、self.http.headers、cookies
    # 结果：后续请求会使用一份新的 guest 身份继续重试
    def force_refresh_auth(self) -> None:
        self.token = None
        self.user_id = None
        self.models_cache = None
        self.http.headers.pop("Authorization", None)
        self.http.cookies.clear()
        queue_board.log_info("登录", "已丢弃当前 guest 会话，准备重新登录")
        self.ensure_auth()

    def get_models(self) -> dict:
        self.ensure_auth()
        if self.models_cache:
            return self.models_cache

        response = self.http.get(f"{Config.base_url}/api/models", timeout=60)
        response.raise_for_status()
        payload = response.json()

        source_models = payload.get("data", []) if isinstance(payload, dict) else payload
        model_items = []
        for item in source_models or []:
            if not item.get("info", {}).get("is_active", True):
                continue
            model_id = item.get("id") or item.get("name") or "unknown"
            model_items.append(
                {
                    "id": model_id,
                    "object": "model",
                    "created": item.get("info", {}).get("created_at", 0) or 0,
                    "owned_by": "z.ai",
                }
            )

        self.models_cache = {"object": "list", "data": model_items}
        queue_board.log_info("模型", f"已刷新模型列表，共 {len(model_items)} 个模型")
        return self.models_cache

    def get_prompt(self, messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts = [item.get("text", "") for item in content if item.get("type") == "text"]
                return "".join(text_parts)
        return ""

    def build_prompt_text(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for message in messages:
            role = message.get("role", "user").upper()
            if role == "TOOL":
                content = message.get("content", "") or ""
                lines.append(f"<TOOL>{content}</TOOL>")
                continue

            content = message.get("content", "")
            if isinstance(content, list):
                text = "".join(item.get("text", "") for item in content if item.get("type") == "text")
            else:
                text = content or ""

            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                text = f"{text}\n{json.dumps(tool_calls, ensure_ascii=False)}".strip()

            lines.append(f"<{role}>{text}</{role}>")

        return "\n".join(lines).strip() or self.get_prompt(messages)

    def build_flat_messages(self, messages: list[dict]) -> list[dict]:
        flat_content = self.build_prompt_text(messages)
        return [{"role": "user", "content": flat_content}]

    def create_chat(self, user_message: str, model: str) -> str:
        self.ensure_auth()
        if not user_message:
            raise ValueError("messages 里至少要有一条 user 文本")

        message_id = str(uuid.uuid4())
        message_time = int(time.time())
        body = {
            "chat": {
                "id": "",
                "title": "新聊天",
                "models": [model],
                "params": {},
                "history": {
                    "messages": {
                        message_id: {
                            "id": message_id,
                            "parentId": None,
                            "childrenIds": [],
                            "role": "user",
                            "content": user_message,
                            "timestamp": message_time,
                            "models": [model],
                        }
                    },
                    "currentId": message_id,
                },
                "tags": [],
                "flags": [],
                "features": [{"type": "tool_selector", "server": "tool_selector_h", "status": "hidden"}],
                "mcp_servers": [],
                "enable_thinking": True,
                "auto_web_search": False,
                "message_version": 1,
                "extra": {},
                "timestamp": int(time.time() * 1000),
            }
        }

        response = self.http.post(f"{Config.base_url}/api/v1/chats/new", json=body, timeout=60)
        response.raise_for_status()
        chat_id = response.json().get("id")
        if not chat_id:
            raise RuntimeError("上游创建聊天成功，但没有返回 chat_id")
        queue_board.log_info("会话", f"已创建上游会话 chat_id={chat_id}")
        return chat_id

    def build_query_and_signature(self, prompt: str, chat_id: str) -> tuple[str, str]:
        self.ensure_auth()
        timestamp_ms = str(int(time.time() * 1000))
        request_id = str(uuid.uuid4())
        core_params = {
            "timestamp": timestamp_ms,
            "requestId": request_id,
            "user_id": self.user_id or "",
        }
        sorted_payload = ",".join(f"{key},{value}" for key, value in sorted(core_params.items()))
        prompt_b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
        time_bucket = str(int(timestamp_ms) // (5 * 60 * 1000))
        derived_key = hmac.new(
            Config.hmac_secret.encode("utf-8"),
            time_bucket.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        message = f"{sorted_payload}|{prompt_b64}|{timestamp_ms}"
        signature = hmac.new(derived_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

        now = datetime.now(timezone.utc)
        extra_params = {
            "version": Config.client_version,
            "platform": "web",
            "token": self.token or "",
            "user_agent": Config.user_agent,
            "language": "zh-CN",
            "languages": "zh-CN",
            "timezone": "Asia/Shanghai",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "screen_resolution": "1920x1080",
            "viewport_height": "919",
            "viewport_width": "944",
            "viewport_size": "944x919",
            "color_depth": "24",
            "pixel_ratio": "1.25",
            "current_url": f"{Config.base_url}/c/{chat_id}",
            "pathname": f"/c/{chat_id}",
            "search": "",
            "hash": "",
            "host": Config.api_host,
            "hostname": Config.api_host,
            "protocol": "https:",
            "referrer": "",
            "title": "Z.ai - Free AI Chatbot & Agent powered by GLM-5 & GLM-4.7",
            "is_mobile": "false",
            "is_touch": "false",
            "max_touch_points": "10",
            "browser_name": "Chrome",
            "os_name": "Windows",
            "timezone_offset": "-480",
            "local_time": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "utc_time": now.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "signature_timestamp": timestamp_ms,
        }
        return urlencode({**core_params, **extra_params}), signature

    def build_chat_body(
        self,
        chat_id: str,
        messages: list[dict],
        prompt: str,
        model: str,
        tools: list[dict] | None,
    ) -> dict:
        now_local = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        body = {
            "stream": True,
            "model": model,
            "messages": messages,
            "signature_prompt": prompt,
            "params": {},
            "extra": {},
            "features": {
                "image_generation": False,
                "web_search": False,
                "auto_web_search": False,
                "preview_mode": True,
                "flags": [],
                "enable_thinking": True,
            },
            "variables": {
                "{{USER_NAME}}": "Guest",
                "{{USER_LOCATION}}": "Unknown",
                "{{CURRENT_DATETIME}}": now_local.strftime("%Y-%m-%d %H:%M:%S"),
                "{{CURRENT_DATE}}": now_local.strftime("%Y-%m-%d"),
                "{{CURRENT_TIME}}": now_local.strftime("%H:%M:%S"),
                "{{CURRENT_WEEKDAY}}": now_local.strftime("%A"),
                "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
                "{{USER_LANGUAGE}}": "zh-CN",
            },
            "chat_id": chat_id,
            "id": str(uuid.uuid4()),
            "current_user_message_id": str(uuid.uuid4()),
            "current_user_message_parent_id": None,
            "background_tasks": {
                "title_generation": True,
                "tags_generation": True,
            },
        }
        if tools:
            body["tools"] = tools
        return body

    def open_chat_stream(
        self,
        chat_id: str,
        messages: list[dict],
        prompt: str,
        model: str,
        tools: list[dict] | None,
        *,
        can_retry: bool = True,
    ):
        query_string, signature = self.build_query_and_signature(prompt, chat_id)
        url = f"{Config.base_url}/api/v2/chat/completions?{query_string}"
        headers = dict(self.http.headers)
        headers["Accept"] = "*/*"
        headers["X-Signature"] = signature
        headers["Referer"] = f"{Config.base_url}/c/{chat_id}"
        body = self.build_chat_body(chat_id, messages, prompt, model, tools)

        queue_board.log_info("上游请求", f"开始请求上游接口 /api/v2/chat/completions，chat_id={chat_id}，模型={model}")
        queue_board.log_debug("上游请求", f"查询参数已生成，signature_timestamp 存在={('signature_timestamp=' in query_string)}")
        queue_board.log_debug("上游请求", f"请求体摘要：{json.dumps(body, ensure_ascii=False)[:800]}")

        try:
            response = self.http.post(url, json=body, headers=headers, stream=True, timeout=300)
            if response.status_code >= 400:
                queue_board.log_error("上游响应", f"状态码={response.status_code}，响应片段={response.text[:500]}")
            response.raise_for_status()
            return response
        except Exception as error:
            if not can_retry:
                raise
            queue_board.log_warning("上游请求", f"本次上游请求失败，准备强制重登后重试一次，原因：{error}")
            self.force_refresh_auth()
            return self.open_chat_stream(chat_id, messages, prompt, model, tools, can_retry=False)

    def parse_sse(self, response) -> Generator[dict, None, None]:
        for raw_line in response.iter_lines():
            if not raw_line or not raw_line.startswith(b"data: "):
                continue
            try:
                event = json.loads(raw_line[6:].decode("utf-8", "ignore"))
                queue_board.log_debug("SSE", f"收到事件键：{list(event.keys()) if isinstance(event, dict) else type(event).__name__}")
                yield event
            except json.JSONDecodeError:
                continue

    def clean_reasoning(self, text: str) -> str:
        if not text:
            return ""
        text = text.replace("</thinking>", "").replace("<Full>", "").replace("</Full>", "")
        text = text.replace("<reasoning>", "").replace("</reasoning>", "")
        return text

    def make_openai_event(self, raw_event: dict) -> dict | None:
        data = (raw_event or {}).get("data") if isinstance(raw_event, dict) and "data" in raw_event else raw_event or {}
        queue_board.log_debug(
            "事件归一化",
            f"包装层={'有' if isinstance(raw_event, dict) and 'data' in raw_event else '无'}，phase={data.get('phase', '')}，有内容={bool(data.get('delta_content') or data.get('edit_content'))}，有工具={bool(data.get('tool_calls'))}，done={bool(data.get('done'))}",
        )
        if data.get("done"):
            return {"done": True, "usage": data.get("usage") or {}}
        if data.get("tool_calls"):
            return {"tool_calls": data.get("tool_calls") or [], "usage": data.get("usage") or {}}

        phase = data.get("phase", "")
        content = data.get("delta_content") or data.get("edit_content") or ""
        if not content and not data.get("usage"):
            return None
        if phase == "thinking":
            return {"reasoning_content": self.clean_reasoning(content), "usage": data.get("usage") or {}}
        if phase == "answer":
            return {"content": content, "usage": data.get("usage") or {}}
        return {"usage": data.get("usage") or {}} if data.get("usage") else None

    def merge_usage(self, usage: dict, new_usage: dict | None) -> dict:
        new_usage = new_usage or {}
        return {
            "prompt_tokens": int(new_usage.get("prompt_tokens", usage.get("prompt_tokens", 0)) or 0),
            "completion_tokens": int(new_usage.get("completion_tokens", usage.get("completion_tokens", 0)) or 0),
            "total_tokens": int(new_usage.get("total_tokens", usage.get("total_tokens", 0)) or 0),
        }

    def create_completion(self, messages: list[dict], model: str, tools: list[dict] | None, *, can_retry: bool = True):
        prompt = self.get_prompt(messages)
        prompt_text = self.build_prompt_text(messages)
        flat_messages = self.build_flat_messages(messages)
        queue_board.log_debug("请求整理", f"原始消息数={len(messages)}，最后用户文本长度={len(prompt)}，压平文本长度={len(prompt_text)}")
        try:
            chat_id = self.create_chat(prompt_text or prompt, model)
            response = self.open_chat_stream(chat_id, flat_messages, prompt_text or prompt, model, tools, can_retry=can_retry)
            return chat_id, response
        except Exception as error:
            if not can_retry:
                raise
            queue_board.log_warning("请求整理", f"创建 completion 首次失败，准备强制重登后重试一次，原因：{error}")
            self.force_refresh_auth()
            return self.create_completion(messages, model, tools, can_retry=False)


zai = ZaiServer()


def make_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:29]}"


def make_openai_chunk(completion_id: str, model: str, *, delta: dict | None = None, finish_reason: str | None = None) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}],
    }


@app.route("/v1/models", methods=["GET", "OPTIONS"])
def list_models():
    if request.method == "OPTIONS":
        return zai.add_cors(make_response())

    try:
        return zai.add_cors(jsonify(zai.get_models()))
    except Exception as error:
        queue_board.log_error("模型接口", f"获取模型失败：{error}")
        return zai.add_cors(jsonify(make_error_response(str(error)))), 500


@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def chat_completions():
    if request.method == "OPTIONS":
        return zai.add_cors(make_response())

    body = request.get_json(force=True, silent=True) or {}
    messages = body.get("messages") or []
    model = body.get("model") or Config.default_model
    stream = bool(body.get("stream", False))
    tools = body.get("tools")
    completion_id = make_completion_id()

    if not messages:
        return zai.add_cors(jsonify(make_error_response("messages 不能为空", "invalid_request_error"))), 400

    request_state = queue_board.enter_request(model=model, stream=stream)

    try:
        _chat_id, upstream = zai.create_completion(messages, model, tools)

        if stream:

            def collect_stream_events(current_upstream):
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                chunks: list[str] = []
                tool_index = 0
                event_count = 0
                done_event_count = 0
                content_event_count = 0
                reasoning_event_count = 0
                tool_event_count = 0

                chunks.append(f"data: {json.dumps(make_openai_chunk(completion_id, model, delta={'role': 'assistant'}), ensure_ascii=False)}\n\n")

                for raw_event in zai.parse_sse(current_upstream):
                    event = zai.make_openai_event(raw_event)
                    if not event:
                        continue

                    event_count += 1
                    usage = zai.merge_usage(usage, event.get("usage"))

                    if event.get("tool_calls"):
                        tool_event_count += 1
                        for tool_call in event["tool_calls"]:
                            chunk = make_openai_chunk(
                                completion_id,
                                model,
                                delta={
                                    "tool_calls": [
                                        {
                                            "index": tool_index,
                                            "id": tool_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                                            "type": "function",
                                            "function": {
                                                "name": tool_call.get("function", {}).get("name", ""),
                                                "arguments": tool_call.get("function", {}).get("arguments", ""),
                                            },
                                        }
                                    ]
                                },
                            )
                            chunks.append(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n")
                            tool_index += 1
                        continue

                    if event.get("reasoning_content"):
                        reasoning_event_count += 1
                        chunk = make_openai_chunk(completion_id, model, delta={"reasoning_content": event["reasoning_content"]})
                        chunks.append(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n")
                        continue

                    if event.get("content"):
                        content_event_count += 1
                        chunk = make_openai_chunk(completion_id, model, delta={"content": event["content"]})
                        chunks.append(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n")
                        continue

                    if event.get("done"):
                        done_event_count += 1
                        break

                queue_board.log_info(
                    "流式统计",
                    f"事件数={event_count}，done 数={done_event_count}，内容事件={content_event_count}，思考事件={reasoning_event_count}，工具事件={tool_event_count}，已输出块={len(chunks)}",
                )
                has_real_output = bool(content_event_count or reasoning_event_count or tool_event_count)
                return chunks, usage, tool_index, has_real_output

            current_upstream = upstream
            chunks, usage, tool_index, has_real_output = collect_stream_events(current_upstream)

            for attempt in range(1, Config.chat_retry_count + 1):
                if has_real_output:
                    break
                queue_board.log_warning("流式重试", f"检测到空流，第 {attempt}/{Config.chat_retry_count} 次重试，准备重登后重新请求")
                zai.force_refresh_auth()
                _chat_id, current_upstream = zai.create_completion(messages, model, tools, can_retry=False)
                chunks, usage, tool_index, has_real_output = collect_stream_events(current_upstream)

            if not has_real_output:
                queue_board.finish_request(request_state, "连续空流，返回 502 错误")
                return zai.add_cors(jsonify(make_error_response(f"上游连续返回空流，已重试 {Config.chat_retry_count} 次", "server_error"))), 502

            def generate_stream():
                finish_reason = "tool_calls" if tool_index > 0 else "stop"
                for chunk in chunks:
                    yield chunk
                yield f"data: {json.dumps(make_openai_chunk(completion_id, model, finish_reason=finish_reason), ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [], 'usage': usage}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            queue_board.finish_request(request_state, "流式请求成功")
            return zai.add_cors(Response(generate_stream(), mimetype="text/event-stream"))

        def collect_once(current_upstream):
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: list[dict] = []
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            event_count = 0
            answer_event_count = 0
            reasoning_event_count = 0
            tool_event_count = 0
            done_event_count = 0

            for raw_event in zai.parse_sse(current_upstream):
                event = zai.make_openai_event(raw_event)
                if not event:
                    continue

                event_count += 1
                usage = zai.merge_usage(usage, event.get("usage"))
                if event.get("tool_calls"):
                    tool_event_count += 1
                    for tool_call in event["tool_calls"]:
                        tool_calls.append(
                            {
                                "id": tool_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                                "type": "function",
                                "function": {
                                    "name": tool_call.get("function", {}).get("name", ""),
                                    "arguments": tool_call.get("function", {}).get("arguments", ""),
                                },
                            }
                        )
                if event.get("reasoning_content"):
                    reasoning_event_count += 1
                    reasoning_parts.append(event["reasoning_content"])
                if event.get("content"):
                    answer_event_count += 1
                    content_parts.append(event["content"])
                if event.get("done"):
                    done_event_count += 1
                    break

            queue_board.log_info(
                "非流式统计",
                f"事件数={event_count}，回答事件={answer_event_count}，思考事件={reasoning_event_count}，工具事件={tool_event_count}，done 数={done_event_count}，正文长度={len(''.join(content_parts))}",
            )
            return content_parts, reasoning_parts, tool_calls, usage

        content_parts, reasoning_parts, tool_calls, usage = collect_once(upstream)
        for attempt in range(1, Config.chat_retry_count + 1):
            if content_parts or reasoning_parts or tool_calls:
                break
            queue_board.log_warning("非流式重试", f"检测到空回复，第 {attempt}/{Config.chat_retry_count} 次重试，准备重登后重新请求")
            zai.force_refresh_auth()
            _chat_id, upstream = zai.create_completion(messages, model, tools, can_retry=False)
            content_parts, reasoning_parts, tool_calls, usage = collect_once(upstream)

        if not content_parts and not reasoning_parts and not tool_calls:
            queue_board.finish_request(request_state, "连续空回复，返回 502 错误")
            return zai.add_cors(jsonify(make_error_response(f"上游连续返回空回复，已重试 {Config.chat_retry_count} 次", "server_error"))), 502

        message: dict = {"role": "assistant", "content": "".join(content_parts)}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            message["content"] = None
            message["tool_calls"] = tool_calls

        result = {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": usage,
        }
        queue_board.finish_request(request_state, "非流式请求成功")
        return zai.add_cors(jsonify(result))

    except Exception as error:
        queue_board.finish_request(request_state, f"请求失败：{error}")
        queue_board.log_error("聊天接口", f"处理失败：{error}")
        return zai.add_cors(jsonify(make_error_response(str(error)))), 500


if __name__ == "__main__":
    queue_board.log_success("启动", f"服务启动成功，监听 http://0.0.0.0:{Config.port} ，聊天请求已启用全局串行队列")
    app.run(host="0.0.0.0", port=Config.port, debug=Config.debug, threaded=True)
