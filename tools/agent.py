import re                                                                              # 正则表达式库用于从控制台文本中提取最终结果和失败信号
import time                                                                            # 时间库用于轮询等待和超时控制
from browser import Browser                                                            # 调用同级 browser.py 中的 Browser 类，通过真实浏览器操作页面


class Agent:
    """Browserbase UI 驱动 Agent 模块"""

    def __init__(self, apiUrl="https://gemini.browserbase.com", browserInstance=None, autoStart=True, **browserArgs):
        self.apiUrl = apiUrl                                                           # 目标站点根地址
        self.browser = browserInstance                                                 # 允许外部传入已有 Browser 实例，便于大系统统一管理浏览器
        self.browserOwned = browserInstance is None                                    # 标记 Browser 是否由当前 Agent 创建，close 时决定是否关闭
        self.browserArgs = browserArgs                                                 # 保存 Browser 初始化参数，只有内部创建 Browser 时才会用到

        self.consoleInstalled = False                                                  # 标记是否已经安装控制台日志捕获器
        self.consoleCursor = 0                                                         # 记录已经读取到第几条控制台日志，避免重复消费
        self.consoleCache = []                                                         # 本地缓存已经读取到的控制台日志文本
        self.lastFinalText = ""                                                        # 保存最近一次识别到的 Final step 文本
        self.lastFinalRaw = ""                                                         # 保存最近一次识别到的 Final step 原始日志
        self.lastErrorText = ""                                                        # 保存最近一次识别到的失败日志文本

        self.defaultModelSelector = [                                                  # Gemini 3 Flash 按钮的候选选择器列表
            'button:has-text("3 Flash")',                                              # 最直观的文本按钮选择器
            'text="3 Flash"',                                                          # Playwright 风格文本选择器
            'button >> text="3 Flash"',                                                # 限定为按钮内部文本
        ]
        self.defaultPromptSelector = [                                                 # 输入框的候选选择器列表
            'input[placeholder*="What"]',                                              # 根据占位文本匹配
            'input[placeholder*="price"]',                                             # 兼容价格类占位提示
            'textarea[placeholder*="What"]',                                           # 某些版本可能改成 textarea
            'textarea',                                                                # 再退一步兼容任意 textarea
            'input[type="text"]',                                                      # 最后兼容普通文本输入框
        ]
        self.defaultRunSelector = [                                                    # Run 按钮的候选选择器列表
            'button:has-text("Run")',                                                  # 文本按钮选择器
            'text="Run"',                                                              # Playwright 风格文本选择器
            'button >> text="Run"',                                                    # 限定按钮内部文本
        ]

        if autoStart:                                                                  # 默认自动启动 Browser，符合导入即用的习惯
            self.start()

    # ==================== 生命周期管理 ====================

    def start(self):                                                                   # 启动并准备 Browser 实例
        if self.browser is not None:                                                   # 已经存在 Browser 实例时直接复用
            return self                                                                # 返回自身便于链式调用

        self.browser = Browser(autoStart=True, **self.browserArgs)                     # 内部创建 Browser 并自动启动真实浏览器
        self.browserOwned = True                                                       # 标记该 Browser 由当前 Agent 持有
        return self                                                                    # 返回自身

    def ensureBrowser(self):                                                           # 确保 Browser 实例可用
        if self.browser is None: self.start()                                          # 没有 Browser 时自动启动
        if self.browser is None: raise RuntimeError("Browser 不可用")                  # 启动后仍为空则抛错
        return self.browser                                                            # 返回可用 Browser 对象

    def close(self):                                                                   # 关闭 Agent 占用的资源
        if self.browserOwned and self.browser is not None:                             # 仅当 Browser 由当前 Agent 创建时才负责关闭
            try: self.browser.close()
            except Exception: pass                                                     # 收尾阶段不让关闭异常中断主流程
            self.browser = None                                                        # 清空 Browser 引用

        self.consoleInstalled = False                                                  # 重置控制台捕获状态
        self.consoleCursor = 0                                                         # 重置控制台读取游标
        self.consoleCache = []                                                         # 清空本地控制台缓存
        self.lastFinalText = ""                                                        # 清空最终文本缓存
        self.lastFinalRaw = ""                                                         # 清空最终原始日志缓存
        self.lastErrorText = ""                                                        # 清空失败文本缓存

    def __enter__(self):                                                               # 进入 with 语句时自动准备资源
        self.start()                                                                   # 确保 Browser 已启动
        return self                                                                    # 返回自身供外部使用

    def __exit__(self, *args):                                                         # 退出 with 语句时自动关闭资源
        self.close()                                                                   # 执行统一关闭逻辑

    # ==================== 页面基础层 ====================

    def openHome(self, timeout=30000):                                                 # 打开 Gemini Browser 首页
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用
        ok = browser.goto(                                                             # 导航到首页
            self.apiUrl + "/",                                                         # 首页地址
            timeout=timeout,                                                           # 页面加载超时时间
            waitUntil="load",                                                          # 等待到 load 提高前端初始化完成概率
            urlContains=self.apiUrl,                                                   # 命中站点地址作为成功条件
            retryCount=1,                                                              # 首页导航轻量重试一次
            retryInterval=1,                                                           # 重试间隔 1 秒
        )
        if not ok: return False                                                        # 打开失败时直接返回 False
        browser.waitPageReady(timeout=10000)                                           # 再等待页面进入可操作状态
        browser.sleep(1.0)                                                             # 给前端 hydration 和初始化一点额外时间
        return True                                                                    # 返回成功

    def ensureHome(self):                                                              # 确保首页已打开
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用
        page = browser.getPage()                                                       # 取底层页面对象
        currentUrl = ""
        try: currentUrl = page.url                                                     # 读取当前页面地址
        except Exception: currentUrl = ""                                              # 读取失败时按空字符串处理

        if currentUrl.startswith(self.apiUrl):                                         # 当前已经在目标站点内时直接复用
            return True                                                                # 返回成功

        return self.openHome()                                                         # 否则重新打开首页

    def installConsoleCapture(self):                                                   # 在页面上下文里安装控制台日志捕获器
        if self.consoleInstalled: return True                                          # 已安装过时直接返回，避免重复包裹 console 方法

        self.ensureHome()                                                              # 安装前先确保目标页面已打开
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        script = """() => {
            if (window.__AGENT_CONSOLE_CAPTURE_INSTALLED__) {                          // 已安装过时不重复安装
                return true;
            }

            window.__AGENT_CONSOLE_CAPTURE_INSTALLED__ = true;                         // 设置安装标记
            window.__AGENT_CONSOLE_LOGS__ = window.__AGENT_CONSOLE_LOGS__ || [];      // 初始化全局日志数组

            const safeSerialize = (value) => {                                         // 把任意 JS 值尽量稳定序列化成字符串
                try {
                    if (typeof value === "string") return value;                       // 字符串直接返回原值
                    if (value instanceof Error) return value.stack || value.message || String(value); // Error 对象优先输出堆栈或消息
                    return JSON.stringify(value);                                      // 普通对象优先转成 JSON
                } catch (e) {
                    try { return String(value); } catch (e2) { return "[Unserializable]"; } // JSON 失败时退回普通字符串
                }
            };

            const wrapMethod = (methodName) => {                                       // 包装某个 console 方法
                const original = console[methodName];                                  // 保存原始 console 方法
                console[methodName] = function (...args) {                             // 替换成包装后的方法
                    try {
                        const text = args.map(safeSerialize).join(" ");                // 把所有参数拼成一行日志文本
                        window.__AGENT_CONSOLE_LOGS__.push({                           // 保存结构化日志到全局数组
                            type: methodName,                                          // 日志类型
                            text,                                                      // 拼接后的文本
                            ts: Date.now()                                             // 时间戳
                        });
                    } catch (e) {}

                    return original.apply(this, args);                                 // 继续调用原始 console 方法
                };
            };

            ["log", "info", "warn", "error", "debug"].forEach(wrapMethod);             // 统一包裹常见控制台输出方法
            return true;                                                               // 返回安装成功标记
        }"""
        ok = browser.evaluate(script, defaultValue=False)                              # 执行控制台捕获脚本
        self.consoleInstalled = bool(ok)                                               # 根据返回值记录安装状态
        return self.consoleInstalled                                                   # 返回安装是否成功

    def clearConsoleLogs(self):                                                        # 清空页面中的控制台捕获缓冲区
        self.ensureHome()                                                              # 清空前先确保页面可用
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        script = """() => {                                                            // 在页面里重置日志数组
            window.__AGENT_CONSOLE_LOGS__ = [];                                        // 清空全局日志缓存
            return true;                                                               // 返回成功标记
        }"""
        browser.evaluate(script, defaultValue=False)                                   # 执行清空脚本
        self.consoleCursor = 0                                                         # 重置本地读取游标
        self.consoleCache = []                                                         # 清空本地缓存
        self.lastFinalText = ""                                                        # 清空最终文本缓存
        self.lastFinalRaw = ""                                                         # 清空最终原始日志缓存
        self.lastErrorText = ""                                                        # 清空失败文本缓存

    def getConsoleLogs(self):                                                          # 获取页面里已捕获的全部控制台日志
        self.installConsoleCapture()                                                   # 读取前确保日志捕获器已经安装
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        script = """() => window.__AGENT_CONSOLE_LOGS__ || []"""                       # 从页面中读取日志数组
        logs = browser.evaluate(script, defaultValue=[])                               # 执行脚本获取日志
        return logs if isinstance(logs, list) else []                                  # 保证最终返回列表

    def pullNewConsoleTexts(self):                                                     # 拉取从上次读取之后新增的控制台文本日志
        logs = self.getConsoleLogs()                                                   # 获取当前全部结构化控制台日志
        if self.consoleCursor >= len(logs): return []                                  # 没有新增日志时返回空列表

        newLogs = logs[self.consoleCursor:]                                            # 截取尚未读取的新日志片段
        self.consoleCursor = len(logs)                                                 # 更新游标到最新位置

        newTexts = []                                                                  # 收集本次新增的纯文本日志
        for item in newLogs:                                                           # 遍历新增结构化日志
            if not isinstance(item, dict): continue                                    # 结构异常时直接跳过
            text = str(item.get("text", ""))                                           # 取文本字段
            if not text: continue                                                      # 空文本跳过
            self.consoleCache.append(text)                                             # 追加到本地缓存
            newTexts.append(text)                                                      # 收集到本次返回列表中

        return newTexts                                                                # 返回本次新增日志文本数组

    # ==================== 预热与验证等待层 ====================

    def pageLooksStable(self):                                                         # 判断页面是否已经进入“基本稳定可交互”的状态
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        state = browser.evaluate(                                                      # 读取若干页面状态指标
            """() => {
                const text = document.body ? document.body.innerText : "";             // 读取整页可见文本
                const hasRun = text.includes("Run");                                   // 页面是否已经出现 Run 文案
                const hasFlash = text.includes("3 Flash");                             // 页面是否已经出现 3 Flash 文案
                const readyState = document.readyState;                                // 浏览器原生加载状态
                return { readyState, hasRun, hasFlash, textLength: text.length };      // 返回结构化状态
            }""",
            defaultValue={},
        )
        if not isinstance(state, dict): return False                                   # 状态结构异常时视为不稳定

        readyState = str(state.get("readyState", ""))                                  # 取 readyState
        hasRun = bool(state.get("hasRun"))                                             # 取 Run 按钮存在标记
        hasFlash = bool(state.get("hasFlash"))                                         # 取 3 Flash 文案存在标记

        if readyState not in ("interactive", "complete"): return False                 # 页面还没进入可操作状态时直接返回 False
        if not hasRun: return False                                                    # Run 都没出现说明页面主体还没准备好
        if not hasFlash: return False                                                  # 3 Flash 都没出现说明模型区还没准备好
        return True                                                                    # 满足这些条件时视为页面基本稳定

    def waitPageStable(self, timeoutSeconds=20, pollSeconds=0.5):                      # 等待页面进入稳定状态
        startTime = time.time()                                                        # 记录开始时间
        while time.time() - startTime < timeoutSeconds:                                # 在总超时时间内轮询
            if self.pageLooksStable():                                                 # 一旦页面看起来稳定
                return True                                                            # 立即返回成功
            time.sleep(pollSeconds)                                                    # 否则稍等后继续检查
        return False                                                                   # 超时后仍不稳定则返回 False

    def waitHumanCheckSettle(self, timeoutSeconds=20, pollSeconds=1):                  # 等待无感人机验证和前端初始化尽量稳定
        self.installConsoleCapture()                                                   # 先确保控制台捕获已安装，便于分析初始化期日志
        startTime = time.time()                                                        # 记录开始时间
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        while time.time() - startTime < timeoutSeconds:                                # 在超时时间内轮询
            self.pullNewConsoleTexts()                                                 # 先拉一次日志，尽可能跟进页面状态

            pageInfo = browser.evaluate(                                               # 从页面读取更细粒度的状态
                """() => {
                    const text = document.body ? document.body.innerText : "";         // 当前页面全文本
                    const readyState = document.readyState;                            // 原生加载状态
                    const hidden = document.hidden;                                    // 页面是否在后台
                    const hasRun = text.includes("Run");                               // 是否出现 Run 按钮文本
                    const hasFlash = text.includes("3 Flash");                         // 是否出现 3 Flash 文本
                    return { readyState, hidden, hasRun, hasFlash, textLength: text.length };
                }""",
                defaultValue={},
            )
            if not isinstance(pageInfo, dict):                                         # 页面状态异常则继续等
                time.sleep(pollSeconds)
                continue

            readyState = str(pageInfo.get("readyState", ""))                           # 取 readyState
            hidden = bool(pageInfo.get("hidden"))                                      # 取页面是否隐藏
            hasRun = bool(pageInfo.get("hasRun"))                                      # 取 Run 文本标记
            hasFlash = bool(pageInfo.get("hasFlash"))                                  # 取 3 Flash 文本标记

            if readyState in ("interactive", "complete") and not hidden and hasRun and hasFlash:# 页面具备基本可交互条件
                browser.sleep(1.2)                                                     # 再额外等待一下，让风控脚本和会话写入更充分
                return True                                                            # 认为已尽量稳定

            time.sleep(pollSeconds)                                                    # 否则继续等

        return False                                                                   # 超时还不稳定就返回 False

    # ==================== UI 操作层 ====================

    def chooseGemini3Flash(self, timeout=10000):                                       # 点击“3 Flash”按钮切换到 gemini-3-flash 模型
        self.ensureHome()                                                              # 确保首页已打开
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        ok = browser.click(                                                            # 点击 3 Flash 按钮
            self.defaultModelSelector,                                                 # 使用候选选择器列表适配页面小改版
            timeout=timeout,                                                           # 点击动作超时
            showSelector=self.defaultPromptSelector,                                   # 成功条件之一：输入框仍可见且页面完成切换
            retryCount=2,                                                              # 轻量重试两次提高点击成功率
            retryInterval=1,                                                           # 每次重试间隔 1 秒
        )
        if not ok: return False                                                        # 点击失败直接返回 False

        browser.sleep(1.0)                                                             # 给模型切换动画或状态更新一点时间
        pageText = browser.evaluate("() => document.body.innerText", defaultValue="")  # 读取页面文本确认模型信息
        if "google/gemini-3-flash-preview" in str(pageText) or "3 Flash" in str(pageText):# 页面文本中出现模型名或按钮文本
            return True                                                                # 视为切换成功
        return True                                                                    # 就算没精确读到文本，只要按钮点击成功也先按成功处理

    def fillGoal(self, goal, timeout=10000):                                           # 向提示输入框填写目标任务
        if not goal: raise RuntimeError("goal 为空")                                   # 目标任务为空时直接报错，避免无意义运行

        self.ensureHome()                                                              # 填写前确保首页已打开
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        ok = browser.fill(                                                             # 往输入框中填写任务文本
            self.defaultPromptSelector,                                                # 输入框候选选择器
            value=goal,                                                                # 要填写的目标文本
            timeout=timeout,                                                           # 填写动作超时
            valueIs=goal,                                                              # 结果校验：输入框当前值应等于目标文本
            retryCount=2,                                                              # 轻量重试两次
            retryInterval=1,                                                           # 重试间隔 1 秒
        )
        return ok                                                                      # 返回填写是否成功

    def clickRun(self, timeout=10000):                                                 # 点击 Run 按钮启动 Agent 任务
        self.ensureHome()                                                              # 执行前确保首页已打开
        browser = self.ensureBrowser()                                                 # 确保 Browser 可用

        ok = browser.click(                                                            # 点击 Run 按钮
            self.defaultRunSelector,                                                   # Run 按钮候选选择器
            timeout=timeout,                                                           # 点击动作超时
            retryCount=2,                                                              # 轻量重试两次
            retryInterval=1,                                                           # 重试间隔 1 秒
        )
        if not ok: return False                                                        # 点击失败直接返回 False

        browser.sleep(0.8)                                                             # 给任务启动和首批前端日志一点时间
        return True                                                                    # 返回成功

    def submitGoal(self, goal, timeout=15000):                                         # 一次性完成预热、模型切换、填写目标、点击 Run
        self.ensureHome()                                                              # 确保首页已打开
        self.installConsoleCapture()                                                   # 提前安装日志捕获器，避免漏掉任务开始后的控制台输出
        self.clearConsoleLogs()                                                        # 提交前清空历史日志，确保后续只看本次任务日志

        if not self.waitPageStable(timeoutSeconds=20, pollSeconds=0.5):                # 先等页面主界面稳定
            raise RuntimeError("页面主界面未稳定，无法开始任务")                         # 主界面都未稳定时直接报错

        if not self.waitHumanCheckSettle(timeoutSeconds=20, pollSeconds=1):            # 再等无感人机验证和初始化尽量完成
            raise RuntimeError("人机验证或前端初始化未稳定，无法开始任务")               # 仍不稳定时直接报错

        if not self.chooseGemini3Flash(timeout=timeout):                               # 先切换到 3 Flash 模型
            raise RuntimeError("切换到 3 Flash 失败")                                  # 切换失败则直接中断

        if not self.fillGoal(goal, timeout=timeout):                                   # 再填写目标任务
            raise RuntimeError("填写 goal 失败")                                       # 填写失败则中断

        if not self.clickRun(timeout=timeout):                                         # 最后点击 Run 启动任务
            raise RuntimeError("点击 Run 失败")                                        # Run 失败则中断

        return True                                                                    # 三步都成功则返回 True

    # ==================== 日志识别层 ====================

    def isFinalStepLog(self, text):                                                    # 判断一条控制台日志是否是最终结果日志
        if not text: return False                                                      # 空文本不可能是最终结果
        return "[useAgentStream] Final step created:" in text                          # 命中特征前缀即视为最终日志

    def isFailureLog(self, text):                                                      # 判断一条控制台日志是否是明确失败日志
        if not text: return False                                                      # 空文本不可能是失败信号

        failureKeywords = [                                                            # 目前已知且最关键的失败特征
            "Agent stream error: Access denied",                                       # 页面日志里明确的流访问拒绝
            "sessionId: null",                                                         # 前端 hook 明确拿不到 sessionId
            "Access denied",                                                           # 通用访问拒绝文案
            "create session failed",                                                   # 兼容可能出现的创建 session 失败文案
            "failed to create session",                                                # 英文失败文案兼容
        ]
        for keyword in failureKeywords:                                                # 遍历所有失败关键词
            if keyword in text: return True                                            # 命中任一关键词即视为失败日志
        return False                                                                   # 全部没命中则不是明确失败日志

    def extractFinalText(self, text):                                                  # 从最终结果日志中提取 text 字段内容
        if not text: return ""                                                         # 空日志直接返回空

        match = re.search(r"text:\s*'([^']+)'", text, re.DOTALL)                       # 优先匹配单引号包裹的 text 字段
        if match: return match.group(1).strip()                                        # 命中后直接返回清洗后的文本

        match = re.search(r'text:\s*"([^"]+)"', text, re.DOTALL)                       # 再兼容双引号包裹的 text 字段
        if match: return match.group(1).strip()                                        # 命中后直接返回

        match = re.search(r'"text"\s*:\s*"([^"]+)"', text, re.DOTALL)                  # 再兼容 JSON 风格的 text 字段
        if match: return match.group(1).strip()                                        # 命中后返回

        return ""                                                                      # 都没匹配到时返回空字符串

    def findOutcomeFromLogs(self, logs):                                               # 从一批日志里识别成功或失败结果
        latestFinalRaw = ""                                                            # 保存最近一条最终结果原始日志
        latestFinalText = ""                                                           # 保存最近一条最终结果提取文本
        latestErrorText = ""                                                           # 保存最近一条失败日志文本

        for line in logs:                                                              # 遍历日志文本
            if not isinstance(line, str): continue                                     # 只处理字符串日志

            if self.isFailureLog(line):                                                # 先判断失败日志
                latestErrorText = line                                                 # 记录最新失败文本

            if self.isFinalStepLog(line):                                              # 再判断最终结果日志
                latestFinalRaw = line                                                  # 记录最新的原始日志
                latestFinalText = self.extractFinalText(line)                          # 提取对应的 text 文本

        return {                                                                       # 返回统一结构化结果
            "finalRaw": latestFinalRaw,
            "finalText": latestFinalText,
            "errorText": latestErrorText,
        }

    # ==================== 等待与编排层 ====================

    def waitFinal(self, timeoutSeconds=180, pollSeconds=1):                            # 等待控制台出现最终结果日志或明确失败日志
        startTime = time.time()                                                        # 记录开始时间

        while time.time() - startTime < timeoutSeconds:                                # 在总超时时间内持续轮询
            newTexts = self.pullNewConsoleTexts()                                      # 只读取新增的控制台日志
            if newTexts:                                                               # 只有有新增日志时才去分析
                result = self.findOutcomeFromLogs(newTexts)                            # 从新增日志中同时查找成功和失败信号

                if result.get("errorText"):                                            # 一旦识别到明确失败日志
                    self.lastErrorText = result.get("errorText", "")                   # 保存失败日志文本
                    raise RuntimeError(f"Agent 执行失败：{self.lastErrorText}")         # 立即抛错，不再傻等超时

                if result.get("finalRaw"):                                             # 找到了最终结果日志
                    self.lastFinalRaw = result.get("finalRaw", "")                     # 保存最终原始日志
                    self.lastFinalText = result.get("finalText", "")                   # 保存提取出的最终文本
                    return {                                                           # 返回最终结构化结果
                        "raw": self.lastFinalRaw,
                        "text": self.lastFinalText,
                    }

            time.sleep(pollSeconds)                                                    # 没有结果就等待一会继续检查

        raise RuntimeError("等待 Final step 超时，且未检测到明确成功或失败结果")         # 超时后抛出统一异常而不是返回 None

    def getResultText(self, timeoutSeconds=180, pollSeconds=1):                        # 提交后等待最终结果并只返回 text 字段
        result = self.waitFinal(timeoutSeconds=timeoutSeconds, pollSeconds=pollSeconds)# 等待最终结果出现或失败抛错
        return result.get("text") or result.get("raw")                                 # 优先返回提取文本，提取不到时退回原始日志

    def runGoal(self, goal, timeoutSeconds=180, pollSeconds=1):                        # 一步完成：提交任务并等待最终结果
        self.submitGoal(goal)                                                          # 执行 UI 提交流程
        return self.getResultText(timeoutSeconds=timeoutSeconds, pollSeconds=pollSeconds)# 等待并返回最终结果文本

    # ==================== 调试与辅助层 ====================

    def listConsoleLogs(self):                                                         # 返回当前已经读取并缓存的所有控制台日志
        self.pullNewConsoleTexts()                                                     # 先把页面中的新增日志拉到本地缓存
        return list(self.consoleCache)                                                 # 返回缓存副本避免外部误修改内部状态

    def getLastFinalRaw(self):                                                         # 返回最近一次识别到的最终结果原始日志
        return self.lastFinalRaw                                                       # 直接返回原始日志字符串

    def getLastFinalText(self):                                                        # 返回最近一次识别到的最终结果文本
        return self.lastFinalText                                                      # 直接返回提取文本字符串

    def getLastErrorText(self):                                                        # 返回最近一次识别到的失败日志文本
        return self.lastErrorText                                                      # 直接返回失败日志字符串


if __name__ == "__main__":
    print("=" * 80)                                                                    # 打印测试开始分隔线
    print("Agent UI 模式测试开始")                                                      # 提示当前进入 UI 驱动测试
    print("=" * 80)                                                                    # 打印测试开始分隔线

    testGoal = "Open baidu.com and search for Python, then finish the task."           # 构造一个相对简单明确的测试目标
    print("测试目标:", testGoal)                                                       # 打印测试目标方便核对

    try:
        with Agent(headless=False) as agent:                                           # 启动真实浏览器进行 UI 测试
            print("[1] 打开首页...")                                                   # 测试步骤 1：打开首页
            ok = agent.openHome()                                                      # 打开 Gemini Browser 首页
            print("openHome:", ok)                                                     # 输出首页打开结果
            if not ok: raise RuntimeError("openHome 失败")                             # 首页打不开就没必要继续后面流程

            print("[2] 安装控制台捕获器...")                                           # 测试步骤 2：安装控制台日志拦截
            ok = agent.installConsoleCapture()                                         # 安装控制台捕获器
            print("installConsoleCapture:", ok)                                        # 输出安装结果
            if not ok: raise RuntimeError("installConsoleCapture 失败")                # 安装失败就无法拿到最终结果

            print("[3] 等页面稳定...")                                                 # 测试步骤 3：等页面主界面和人机验证尽量稳定
            ok1 = agent.waitPageStable(timeoutSeconds=20, pollSeconds=0.5)             # 等页面主界面稳定
            ok2 = agent.waitHumanCheckSettle(timeoutSeconds=20, pollSeconds=1)         # 等人机验证和初始化尽量稳定
            print("waitPageStable:", ok1)                                              # 输出页面稳定结果
            print("waitHumanCheckSettle:", ok2)                                        # 输出验证稳定结果
            if not ok1 or not ok2: raise RuntimeError("页面或人机验证未稳定")           # 任一失败则中止测试

            print("[4] 提交目标任务...")                                               # 测试步骤 4：切模型、填输入框、点击 Run
            ok = agent.submitGoal(testGoal)                                            # 一次性完成任务提交流程
            print("submitGoal:", ok)                                                   # 输出提交流程结果
            if not ok: raise RuntimeError("submitGoal 失败")                           # 提交失败则终止测试

            print("[5] 等待最终结果或失败日志...")                                      # 测试步骤 5：等待最终结果或即时失败
            result = agent.waitFinal(timeoutSeconds=180, pollSeconds=1)                # 最多等待 180 秒
            print("waitFinal result:", result)                                         # 输出最终结果结构

            print("[6] 输出提取结果...")                                               # 测试步骤 6：展示解析后的最终文本
            print("Final Text:", agent.getLastFinalText())                             # 输出最近一次提取的最终文本
            print("Final Raw :", agent.getLastFinalRaw())                              # 输出最近一次匹配到的原始最终日志
            print("Last Error:", agent.getLastErrorText())                             # 输出最近一次识别到的失败日志

            print("[7] 输出最近若干条控制台日志...")                                    # 测试步骤 7：查看最近日志辅助排查
            logs = agent.listConsoleLogs()                                             # 获取当前所有已读取的控制台日志
            for line in logs[-20:]:                                                    # 打印最后 20 条日志，便于观察失败前后过程
                print("LOG:", line)                                                    # 打印日志内容

    except Exception as error:
        print("测试失败：", error)                                                      # 打印测试异常信息

    print("=" * 80)                                                                    # 打印测试结束分隔线
    print("Agent UI 模式测试结束")                                                      # 提示测试结束
    print("=" * 80)                                                                    # 打印测试结束分隔线