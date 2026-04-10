import time                                                                            # 时间库用于超时控制和轮询调度
import json                                                                            # JSON 库用于编码和解析 WebSocket/CDP 消息
import urllib.parse                                                                    # URL 编解码工具用于提取和解码 wss 参数
import httpx                                                                           # HTTP 客户端库用于创建会话和发送 Agent 请求
import websocket                                                                       # websocket-client 库用于连接 CDP WebSocket

""" 
## 用法 1：只创建会话
with Agent() as agent:
    info = agent.createSession()                                                      # 创建 Browserbase 会话
    print(info)                                                                       # 查看 sessionId / sessionUrl / wsUrl

## 用法 2：创建会话后发送目标
with Agent() as agent:
    agent.createSession()                                                             # 创建会话
    agent.sendAgentGoal("打开 Google 并搜索 OpenAI")                                   # 发送 Agent 目标任务

## 用法 3：直接等待目标页面出现
with Agent() as agent:
    info = agent.createSession()                                                      # 创建会话
    agent.sendAgentGoal("打开 example.com 并进入 pricing 页面")                        # 发送任务
    url = agent.waitTarget(                                                           # 监听 URL 变化直到命中目标
        wsUrl=info["wsUrl"],
        targetKeyword="pricing",
        targetLabel="pricing 页面",
        timeoutSeconds=120,
        pollIntervalSeconds=2,
    )
    print(url)                                                                        # 输出命中的目标 URL

## 用法 4：一步完成发送任务并等待结果
with Agent() as agent:
    url = agent.runGoalAndWaitTarget(                                                 # 一步完成“创建会话+发任务+等结果”
        goal="打开 GitHub 并进入 pricing 页面",
        targetKeyword="pricing",
        targetLabel="GitHub Pricing 页面",
        timeoutSeconds=120,
        pollIntervalSeconds=2,
    )
    print(url)                                                                        # 输出最终命中的 URL
"""

class Agent:
    """Browserbase 通用 Agent 模块"""

    def __init__(self, apiUrl="https://gemini.browserbase.com"):
        self.apiUrl = apiUrl                                                           # Browserbase 服务根地址
        self.sessionId = ""                                                            # 当前会话编号
        self.sessionUrl = ""                                                           # 当前会话页面地址
        self.wsUrl = ""                                                                # 当前会话对应的 CDP WebSocket 地址
        self.agentStream = None                                                        # 当前 Agent EventStream 响应对象
        self.wsConnection = None                                                       # 当前 CDP WebSocket 连接对象
        self.messageId = 1                                                             # CDP 消息自增编号
        self.pendingCommands = {}                                                      # 等待响应的 CDP 命令映射表，键为消息 id
        self.targetUrls = {}                                                           # 已观测到的 target URL 映射表，键为 targetId
        self.client = httpx.Client(                                                    # 创建持久化 HTTP 客户端
            headers={
                "User-Agent": "Mozilla/5.0 Chrome/145.0.0.0",                          # 伪装成常见浏览器
                "Content-Type": "application/json",                                    # 默认按 JSON 提交请求
                "Accept": "*/*",                                                       # 接受任意内容类型提高兼容性
            },
            timeout=30,                                                                # 普通 HTTP 请求默认超时时间
            follow_redirects=True,                                                     # 自动跟随重定向
        )

    # ==================== 算子层 ====================

    def sendRequest(self, method, path, params=None, body=None, headers=None, stream=False):# 统一发送 HTTP 请求
        requestHeaders = headers or {}                                                 # 允许调用方附加额外请求头
        response = self.client.request(                                                # 通过持久化客户端发起请求
            method=method,                                                            # 请求方法如 GET / POST
            url=self.apiUrl + path,                                                   # 拼接完整请求地址
            params=params,                                                            # URL 查询参数
            json=body,                                                                # JSON 请求体
            headers=requestHeaders,                                                   # 附加请求头
        )
        response.raise_for_status()                                                   # 非 2xx 状态直接抛异常
        return response                                                               # 返回完整响应对象

    def normalizeWsUrl(self, wsUrl):                                                  # 规范化 Browserbase inspector 暴露的 WebSocket 地址
        if not wsUrl: return ""                                                       # 空值直接返回空字符串
        decodedUrl = urllib.parse.unquote(wsUrl)                                      # 先做 URL 解码还原真实地址
        if decodedUrl.startswith("wss://") or decodedUrl.startswith("ws://"):         # 已经是完整 WebSocket 地址则直接返回
            return decodedUrl
        return "wss://" + decodedUrl                                                  # 否则默认补上 wss:// 前缀

    def extractWsUrlFromSessionUrl(self, sessionUrl):                                 # 从 sessionUrl 中提取 wss 参数对应的 CDP 地址
        if not sessionUrl: return ""                                                  # 空 sessionUrl 直接返回空
        parsed = urllib.parse.urlparse(sessionUrl)                                    # 解析 URL 结构
        query = urllib.parse.parse_qs(parsed.query)                                   # 解析查询参数字典
        rawWs = query.get("wss", [""])[0]                                             # 读取 wss 参数的第一个值
        return self.normalizeWsUrl(rawWs)                                             # 解码并规范化后返回

    def createSession(self, timezone="HKT"):                                          # 创建新的 Browserbase 会话
        response = self.sendRequest(                                                  # 调用 session 创建接口
            "POST",
            "/api/session",
            body={"timezone": timezone},                                              # 按 JS 示例传入时区参数
        )
        data = response.json()                                                        # 把响应解析为字典

        if not data.get("success"):                                                   # success=false 说明服务端创建失败
            raise RuntimeError("创建会话失败: success=false")                          # 抛异常给调用方处理

        self.sessionId = str(data.get("sessionId", ""))                               # 保存会话编号
        self.sessionUrl = str(data.get("sessionUrl", ""))                             # 保存会话页面地址
        self.wsUrl = self.extractWsUrlFromSessionUrl(self.sessionUrl)                 # 从 sessionUrl 中解析出 CDP WebSocket 地址
        self.messageId = 1                                                            # 每次新建会话后重置 CDP 消息编号
        self.pendingCommands = {}                                                     # 清空未完成命令映射
        self.targetUrls = {}                                                          # 清空已观测 URL 状态

        return {                                                                      # 返回结构化结果给调用方
            "sessionId": self.sessionId,
            "sessionUrl": self.sessionUrl,
            "wsUrl": self.wsUrl,
        }

    def sendAgentGoal(self, goal, model="google/gemini-3-flash-preview"):             # 发送 Agent 任务目标并短暂接收 EventStream
        if not self.sessionId:                                                        # 没有 sessionId 说明会话尚未创建
            raise RuntimeError("会话未创建，请先调用 createSession()")                 # 阻止无效调用

        params = {                                                                    # 组装查询参数
            "sessionId": self.sessionId,                                              # 指定当前会话编号
            "goal": goal,                                                             # 指定任务目标文本
            "model": model,                                                           # 指定模型名称
        }

        with self.client.stream(                                                      # 以流式方式建立 EventStream 请求
            "GET",
            self.apiUrl + "/api/agent/stream",
            params=params,
        ) as response:
            response.raise_for_status()                                               # 检查 HTTP 状态码
            self.agentStream = response                                               # 暂存当前流响应对象

            startTime = time.time()                                                   # 记录开始时间
            firstChunk = b""                                                          # 保存读取到的首段数据
            for chunk in response.iter_bytes():                                       # 逐段读取流式数据
                if chunk:                                                             # 只处理非空数据块
                    firstChunk = chunk                                                # 记录首个有效块
                    break                                                             # 收到首段数据后立即停止继续读取
                if time.time() - startTime > 2:                                       # 最多等待 2 秒作为兜底超时
                    break                                                             # 超时则结束读取循环

        self.agentStream = None                                                       # 离开 with 后流已关闭，清空引用
        return firstChunk                                                             # 返回首个数据块给调用方，需要时可自行解码

    def connectWebSocket(self, wsUrl, timeout=10):                                    # 建立底层 CDP WebSocket 连接
        fullWsUrl = self.normalizeWsUrl(wsUrl)                                        # 规范化 WebSocket 地址
        if not fullWsUrl: raise RuntimeError("无效的 WebSocket 地址")                  # 地址为空时直接抛错

        if self.wsConnection:                                                         # 如果已有旧连接则先关闭避免状态污染
            try: self.wsConnection.close()
            except Exception: pass

        self.wsConnection = websocket.create_connection(                              # 建立同步 WebSocket 连接
            fullWsUrl,
            timeout=timeout,                                                          # 连接和读写默认超时时间
            enable_multithread=False,                                                 # 单线程模式即可满足当前设计
        )
        self.wsConnection.settimeout(1.0)                                             # 读消息使用较短超时便于轮询控制
        self.messageId = 1                                                            # 新连接建立后重置消息编号
        self.pendingCommands = {}                                                     # 清空挂起命令表
        return self.wsConnection                                                      # 返回连接对象

    def sendWsMessage(self, message):                                                 # 发送原始 WebSocket 文本消息
        if not self.wsConnection: raise RuntimeError("WebSocket 未连接")               # 连接不存在时直接报错
        self.wsConnection.send(message)                                               # 发送字符串消息到服务端

    def sendCDPCommand(self, method, params=None, timeoutSeconds=5):                  # 发送 CDP 命令并等待响应
        if not self.wsConnection: raise RuntimeError("WebSocket 未连接")               # 没有连接时无法发送 CDP 命令
        if params is None: params = {}                                                # 默认使用空参数字典

        commandId = self.messageId                                                    # 取当前消息编号
        self.messageId += 1                                                           # 自增编号供下次使用

        payload = {                                                                   # 构造标准 CDP 消息结构
            "id": commandId,
            "method": method,
            "params": params,
        }
        self.pendingCommands[commandId] = {                                           # 先在挂起表里注册命令
            "done": False,
            "result": None,
            "error": None,
        }

        try:
            self.sendWsMessage(json.dumps(payload))                                   # 发送 JSON 格式的 CDP 命令
        except Exception:
            self.pendingCommands.pop(commandId, None)                                 # 发送失败时立即移除挂起记录
            raise                                                                     # 把原始异常继续抛出

        deadline = time.time() + timeoutSeconds                                       # 计算绝对超时时刻
        while time.time() < deadline:                                                 # 在命令超时前持续读取消息
            self.processOneWsMessage()                                                # 读取并分发一条 WebSocket 消息

            pending = self.pendingCommands.get(commandId)                             # 取回当前命令的挂起状态
            if not pending:                                                           # 如果记录已不存在，视为异常结束
                raise RuntimeError("CDP 命令状态丢失")                                 # 明确报错而不是静默失败

            if pending["done"]:                                                       # 服务端已经返回了命令结果
                self.pendingCommands.pop(commandId, None)                             # 清理挂起记录
                if pending["error"] is not None:                                      # 如果服务端返回 error 字段
                    raise RuntimeError(str(pending["error"]))                         # 以异常形式抛出错误
                return pending["result"]                                              # 返回命令结果对象

        self.pendingCommands.pop(commandId, None)                                     # 超时后清理挂起命令
        raise RuntimeError("CDP 命令超时")                                             # 抛出统一超时异常

    def clearPendingCommands(self, reason="CDP 连接已关闭"):                           # 清理所有未完成的 CDP 命令
        for commandId in list(self.pendingCommands.keys()):                           # 遍历所有挂起命令编号
            self.pendingCommands[commandId]["done"] = True                            # 标记该命令处理结束
            self.pendingCommands[commandId]["result"] = None                          # 清空结果
            self.pendingCommands[commandId]["error"] = reason                         # 写入关闭原因作为错误信息

    def processOneWsMessage(self):                                                    # 读取并处理一条 WebSocket 消息
        if not self.wsConnection: return None                                         # 连接不存在时直接返回空

        try:
            raw = self.wsConnection.recv()                                            # 从 WebSocket 接收一条消息
        except websocket.WebSocketTimeoutException:                                   # 短超时属于正常轮询行为
            return None                                                               # 没有消息就返回空继续外层循环
        except websocket.WebSocketConnectionClosedException:                          # 连接已关闭时清理挂起命令
            self.clearPendingCommands("CDP 连接已关闭")                                # 通知所有挂起命令失败
            raise RuntimeError("CDP 连接已关闭")                                      # 向上抛出关闭异常

        if raw is None: return None                                                   # 没有数据时返回空
        if isinstance(raw, bytes): raw = raw.decode("utf-8", errors="ignore")         # 字节消息先解码成字符串

        try:
            message = json.loads(raw)                                                 # 把消息文本解析为字典
        except Exception:
            return None                                                               # 非 JSON 消息直接忽略

        if "id" in message and message["id"] in self.pendingCommands:                 # 如果是某个 CDP 命令的响应
            pending = self.pendingCommands[message["id"]]                             # 取出挂起命令状态
            pending["done"] = True                                                    # 标记命令已完成
            if "error" in message and message["error"] is not None:                   # 服务端返回命令错误
                pending["error"] = message["error"].get("message", "CDP 命令失败")    # 保存错误信息
            else:
                pending["result"] = message.get("result")                             # 保存正常结果
            return message                                                            # 返回原始消息给调用方

        if message.get("method") in ("Target.targetCreated", "Target.targetInfoChanged"):# 如果是 target 相关事件
            info = (message.get("params") or {}).get("targetInfo") or {}              # 取 targetInfo 结构
            if info.get("type") == "page":                                            # 只关注页面类型 target
                targetKey = str(info.get("targetId") or info.get("url") or "page")    # 优先用 targetId 作为键
                currentUrl = str(info.get("url") or "")                               # 取当前页面 URL
                if currentUrl: self.targetUrls[targetKey] = currentUrl                # 更新目标 URL 映射

        return message                                                                # 返回解析后的消息对象

    def getTargets(self):                                                             # 获取当前浏览器上下文中的全部 targets
        result = self.sendCDPCommand("Target.getTargets")                             # 调用 CDP 的 Target.getTargets 方法
        targetInfos = result.get("targetInfos", []) if isinstance(result, dict) else []# 取出 targetInfos 数组
        return targetInfos if isinstance(targetInfos, list) else []                   # 保证最终返回列表

    def isTargetUrl(self, currentUrl, targetKeyword=None, targetMatcher=None):        # 判断某个 URL 是否满足目标条件
        if not currentUrl: return False                                               # 空 URL 不可能命中目标
        if callable(targetMatcher):                                                   # 如果调用方传了自定义匹配函数
            return bool(targetMatcher(currentUrl))                                    # 交给调用方自定义判断逻辑
        if targetKeyword:                                                             # 否则回退到简单字符串包含判断
            return targetKeyword in currentUrl                                        # 命中关键词则视为目标页面
        return False                                                                  # 没有 matcher 也没有关键词时默认不命中

    def observeTargetUrl(self, targetKey, currentUrl, onUrlChange=None):             # 记录并处理某个 target 的 URL 变化
        if not currentUrl or currentUrl == "about:blank": return False                # 空白页不视为有效 URL 变化
        previousUrl = self.targetUrls.get(targetKey, "")                              # 取该 target 上一次记录的 URL
        if previousUrl == currentUrl: return False                                    # URL 没变就不重复处理
        self.targetUrls[targetKey] = currentUrl                                       # 更新最新 URL 状态
        if callable(onUrlChange): onUrlChange(currentUrl)                             # 如果传了回调就通知调用方
        return True                                                                   # 返回 True 表示确实发生了 URL 变化

    def enableTargetDiscovery(self):                                                  # 启用 Target 发现，减少依赖高频页面事件
        return self.sendCDPCommand(                                                   # 发送 CDP 开关命令
            "Target.setDiscoverTargets",
            {"discover": True},
        )

    # ==================== 编排层 ====================

    def waitTarget(self, wsUrl=None, targetKeyword=None, targetMatcher=None, targetLabel=None, onUrlChange=None, onTargetReached=None, timeoutSeconds=1800, pollIntervalSeconds=3, reconnectDelaySeconds=0.5, staleReconnectSeconds=12):# 连接 CDP 并等待目标页面出现
        fullWsUrl = self.normalizeWsUrl(wsUrl or self.wsUrl)                          # 优先使用传入 wsUrl，否则使用当前会话 wsUrl
        if not fullWsUrl: raise RuntimeError("未提供可用的 WebSocket 地址")            # 没有 WebSocket 地址时无法继续

        deadline = time.time() + timeoutSeconds                                       # 计算总超时时刻
        lastUrlChangeAt = time.time()                                                 # 记录最近一次观测到 URL 变化的时间
        lastReconnectAt = 0                                                           # 记录最近一次重连时间
        lastPollAt = 0                                                                # 记录最近一次主动轮询时间
        targetDescription = targetLabel or targetKeyword or "目标页面"                 # 构造用于结果说明的目标描述

        while time.time() < deadline:                                                 # 在总超时时间内反复尝试连接和轮询
            try:
                self.connectWebSocket(fullWsUrl)                                      # 建立 CDP WebSocket 连接
                self.enableTargetDiscovery()                                          # 开启 target 发现能力
                lastReconnectAt = time.time()                                         # 更新最近重连时间
                lastPollAt = 0                                                        # 新连接建立后立即允许首次轮询

                while time.time() < deadline:                                         # 在当前连接存活期间持续监控
                    self.processOneWsMessage()                                        # 先处理一条被动推送的 WebSocket 消息

                    now = time.time()                                                 # 记录当前时间用于后续判断
                    if now - lastPollAt >= pollIntervalSeconds:                       # 到达主动轮询间隔时
                        lastPollAt = now                                              # 更新最近轮询时间
                        targets = self.getTargets()                                   # 主动获取所有 targets 列表
                        sawNonBlankUrl = False                                        # 标记本轮是否观测到非空白页面

                        for target in targets:                                        # 遍历所有 target
                            if target.get("type") and target.get("type") != "page":   # 只关心 page 类型 target
                                continue                                              # 非页面 target 直接跳过

                            currentUrl = str(target.get("url") or "")                 # 读取当前 target 的 URL
                            targetKey = str(target.get("targetId") or currentUrl or "page")# 优先取 targetId 作为唯一键
                            changed = self.observeTargetUrl(                          # 记录 URL 变化并触发回调
                                targetKey,
                                currentUrl,
                                onUrlChange=onUrlChange,
                            )
                            if changed: lastUrlChangeAt = now                         # 只有 URL 真变化时才更新最近变化时间

                            if currentUrl and currentUrl != "about:blank":            # 观测到有效页面 URL
                                sawNonBlankUrl = True                                 # 标记本轮见过有效 URL

                            if self.isTargetUrl(currentUrl, targetKeyword, targetMatcher):# 如果当前 URL 已命中目标
                                if callable(onTargetReached):                         # 如果调用方提供了目标到达回调
                                    result = onTargetReached(currentUrl)              # 执行回调并允许其返回自定义结果
                                    return result if result is not None else currentUrl# 回调返回空时仍回退返回当前 URL
                                return currentUrl                                     # 默认返回命中的目标 URL

                        if (not sawNonBlankUrl                                        # 长时间看不到有效 URL
                                and now - lastUrlChangeAt >= staleReconnectSeconds    # 且距离上次 URL 变化已超过阈值
                                and now - lastReconnectAt >= staleReconnectSeconds):  # 且距离上次重连也已超过阈值
                            break                                                     # 跳出当前连接循环，进入外层重连流程

            except Exception:
                pass                                                                  # 当前连接出错时静默进入下一轮重连

            self.disconnect()                                                         # 每轮失败后先彻底断开旧连接
            if time.time() + reconnectDelaySeconds >= deadline:                       # 如果剩余时间已经不足以再重连一次
                break                                                                 # 直接结束等待流程
            time.sleep(reconnectDelaySeconds)                                         # 短暂等待后再重连，避免空转过快

        raise RuntimeError(f"等待{targetDescription}超时")                             # 超时后抛出统一异常

    def getCurrentTargets(self):                                                      # 便捷方法：返回当前所有 target 列表
        if not self.wsConnection:                                                     # 如果还没连接 WebSocket
            if not self.wsUrl: raise RuntimeError("尚未创建会话或未提供 wsUrl")        # 连 wsUrl 都没有时直接报错
            self.connectWebSocket(self.wsUrl)                                         # 自动建立 WebSocket 连接
            self.enableTargetDiscovery()                                              # 开启 target 发现能力
        return self.getTargets()                                                      # 返回 CDP 查询到的全部 targets

    def runGoalAndWaitTarget(self, goal, targetKeyword=None, targetMatcher=None, targetLabel=None, onUrlChange=None, onTargetReached=None, timeoutSeconds=1800, pollIntervalSeconds=3):# 一步完成：发任务并等待目标页面
        if not self.sessionId: self.createSession()                                   # 没有会话时先自动创建会话
        self.sendAgentGoal(goal)                                                      # 向 Agent 发送任务目标
        return self.waitTarget(                                                       # 然后等待目标页面出现
            wsUrl=self.wsUrl,
            targetKeyword=targetKeyword,
            targetMatcher=targetMatcher,
            targetLabel=targetLabel,
            onUrlChange=onUrlChange,
            onTargetReached=onTargetReached,
            timeoutSeconds=timeoutSeconds,
            pollIntervalSeconds=pollIntervalSeconds,
        )

    def disconnect(self):                                                             # 关闭 Agent 流和 CDP WebSocket 连接
        if self.agentStream is not None:                                              # 如果保留了流对象引用
            try: self.agentStream.close()
            except Exception: pass
            self.agentStream = None                                                   # 清空流对象引用

        if self.wsConnection is not None:                                             # 如果当前存在 WebSocket 连接
            try: self.wsConnection.close()
            except Exception: pass
            self.wsConnection = None                                                  # 清空连接引用

        self.clearPendingCommands("CDP 连接已关闭")                                    # 把所有挂起命令标记为失败

    def close(self):                                                                  # 关闭底层资源释放网络连接
        self.disconnect()                                                             # 先断开流和 WebSocket
        self.client.close()                                                           # 再关闭 HTTP 客户端连接池

    def __enter__(self): return self                                                  # 进入 with 语句时返回自身
    def __exit__(self, *args): self.close()                                           # 退出 with 语句时自动关闭资源