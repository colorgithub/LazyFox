"""
这个文件提供一个更智能、更适合业务直接调用的 Browser 类。  # 文件作用：这是统一浏览器入口，外部只需要 import Browser 就能直接用

设计目标：  # 这里明确这个类存在的原因，方便后面维护时不跑偏
1. 内部组合 Camoufox 或 Playwright Chromium，而不是继承它们。  # 因为底层都更适合作为被托管资源，而不是业务直接继承
2. 自动创建并管理 page。  # 调用方不需要自己 new_page
3. 保留直觉式方法名。  # 比如 click、fill、goto，不发明一堆新 API
4. 当传入增强参数时自动进入智能模式。  # 比如 click(..., showSelector="...") 会自动重试和校验
5. 支持 selector 是字符串，也支持 selector 是候选列表。  # 这很适合真实项目里不稳定的页面结构
6. 支持 engine="camoufox" 或 engine="playwright"。  # 方便在不同站点风控条件下切换底层浏览器引擎
"""

from dataclasses import dataclass                                                     # dataclass 用来保存配置，结构简单清楚
from typing import Any, Optional                                                      # 这里只保留常见类型提示，避免复杂度过高
import time                                                                           # 用于短暂 sleep 和重试间隔
from pathlib import Path                                                              # 用于处理上传文件路径

from camoufox.sync_api import Camoufox                                                # Camoufox 同步 API，作为一个可选后端
from playwright.sync_api import sync_playwright                                       # Playwright 同步入口，用于 Chromium 后端


@dataclass
class BrowserConfig:
    clickRetryCount: int = 3                                                          # click 默认重试次数。点击类动作最容易遇到页面没反应
    actionRetryCount: int = 2                                                         # 非点击动作默认重试次数。克制一点，避免重复执行太多次
    retryInterval: float = 1.0                                                        # 两次动作之间默认等待秒数。太短页面可能还没反应过来
    actionTimeout: int = 8000                                                         # 普通动作超时，单位毫秒。给元素出现和动作执行一点时间
    resultTimeout: int = 2500                                                         # 动作后等结果的默认时间，单位毫秒。偏短，方便进入下一轮重试
    gotoTimeout: int = 30000                                                          # 打开页面通常更慢，所以给更长超时
    waitTimeout: int = 10000                                                          # wait 系列方法的默认超时
    typeDelay: int = 80                                                               # 逐字输入时每个字符的间隔，单位毫秒
    isDebug: bool = True                                                              # 是否打印调试日志
    engine: str = "camoufox"                                                          # 浏览器后端类型，可选 camoufox 或 playwright
    headless: bool = False                                                            # 默认是否无头运行，仅对 playwright 明确生效，也透传给 camoufox
    viewportWidth: int = 1440                                                         # Playwright 默认视口宽度
    viewportHeight: int = 900                                                         # Playwright 默认视口高度
    userAgent: str = ""                                                               # 可选自定义 UA，不传则使用浏览器默认 UA


class Browser:
    def __init__(
        self,
        page=None,
        config: Optional[BrowserConfig] = None,
        autoStart: bool = True,
        **browserArgs,
    ):
        print("正在初始化 Browser。")                                                  # 初始化开始时打印，方便定位启动阶段问题

        self.config = config or BrowserConfig()                                       # 没传配置就使用默认配置，保持最少心智负担
        self.browserArgs = browserArgs                                                # 保存底层浏览器启动参数，后面 start 时会直接使用

        self.engine = str(self.browserArgs.pop("engine", self.config.engine)).lower() # 优先使用显式传入 engine，否则走配置默认值
        self.headless = self.browserArgs.pop("headless", self.config.headless)        # headless 支持从外部参数覆盖配置默认值

        self.camoufoxContext = None                                                   # 保存 Camoufox(...) 返回的上下文对象，后面需要手动 __enter__ / __exit__
        self.rawBrowser = None                                                        # 保存底层浏览器对象或上下文对象
        self.page = page                                                              # 如果外部已经传了 page，就直接接管，不重复创建
        self.isStarted = page is not None                                             # 只要外部传了 page，就视为已启动

        self.playwrightDriver = None                                                  # Playwright 启动器对象，即 sync_playwright().start() 的返回值
        self.playwrightBrowser = None                                                 # Playwright 浏览器实例，比如 chromium.launch(...) 的结果
        self.playwrightContext = None                                                 # Playwright BrowserContext，上下文级别资源
        self.ownsExternalPage = page is not None                                      # 外部传入 page 时，当前 Browser 不负责关闭底层浏览器

        if autoStart and self.page is None:                                           # 默认自动启动，这样 import 后可以直接用，最符合 UOP
            self.start()

        print("Browser 初始化完成。")                                                  # 初始化完成后打印，说明 Browser 已经准备好了

    # ==================== 生命周期管理 ====================

    def __enter__(self):
        if not self.isStarted or self.page is None:                                   # with 进入时如果还没启动，就自动启动
            self.start()
        return self                                                                   # 返回自己，外部自然使用 browser.click(...) 这样的写法

    def __exit__(self, excType, excValue, traceback):
        self.close()                                                                  # with 结束时自动关闭，保证资源释放

    def start(self):
        if self.isStarted and self.page is not None:                                  # 已经启动且 page 可用时，不重复启动
            self.log("Browser 已经启动，跳过重复启动。")
            return self

        if self.engine == "camoufox":                                                 # 走 Camoufox 后端启动流程
            self._startCamoufox()
            return self

        if self.engine in ("playwright", "chromium"):                                 # 走 Playwright Chromium 后端启动流程
            self._startPlaywright()
            return self

        raise RuntimeError(f"不支持的浏览器引擎: {self.engine}")                      # 未知引擎直接报错，避免静默退回错误路径

    def _startCamoufox(self):
        self.log("正在启动 Camoufox。")                                               # 启动 Camoufox 前先打印日志，让流程清楚

        camoufoxArgs = dict(self.browserArgs)                                         # 复制参数，避免污染外部传入对象
        if "headless" not in camoufoxArgs:                                            # 如果外部没显式传 headless
            camoufoxArgs["headless"] = self.headless                                  # 就使用统一配置中的 headless 值

        self.camoufoxContext = Camoufox(**camoufoxArgs)                               # 创建 Camoufox 上下文对象，这一步还没真正进入 with
        self.rawBrowser = self.camoufoxContext.__enter__()                            # 手动进入上下文，这样非 with 场景也能统一复用
        self.page = self.rawBrowser.new_page()                                        # 启动后立刻创建 page，后面所有业务方法都围绕这个 page 工作
        self.isStarted = True                                                         # 标记为已启动，避免后续重复创建

        self.log("Camoufox 启动完成，page 已创建。")                                   # 打印启动完成日志

    def _startPlaywright(self):
        self.log("正在启动 Playwright Chromium。")                                     # 启动 Chromium 前先打印日志

        playwrightArgs = dict(self.browserArgs)                                       # 复制参数，避免污染外部传入对象
        launchArgs = playwrightArgs.pop("launchArgs", {})                             # 允许调用方额外传 launchArgs 定制 browser.launch 参数
        contextArgs = playwrightArgs.pop("contextArgs", {})                           # 允许调用方额外传 contextArgs 定制 new_context 参数

        self.playwrightDriver = sync_playwright().start()                             # 启动 Playwright 驱动进程
        self.playwrightBrowser = self.playwrightDriver.chromium.launch(               # 启动 Chromium 浏览器实例
            headless=self.headless,                                                   # 是否无头运行
            **launchArgs,                                                             # 额外启动参数透传给 launch
        )

        if "viewport" not in contextArgs:                                             # 如果调用方没显式指定 viewport
            contextArgs["viewport"] = {                                               # 就使用配置里的默认视口
                "width": self.config.viewportWidth,
                "height": self.config.viewportHeight,
            }

        if self.config.userAgent and "user_agent" not in contextArgs:                 # 如果配置了 UA 且调用方没覆盖
            contextArgs["user_agent"] = self.config.userAgent                         # 把自定义 UA 注入到上下文中

        self.playwrightContext = self.playwrightBrowser.new_context(                  # 创建一个独立浏览器上下文
            **contextArgs,                                                            # 透传上下文创建参数
        )
        self.rawBrowser = self.playwrightBrowser                                      # rawBrowser 统一指向底层浏览器实例，方便调试时查看
        self.page = self.playwrightContext.new_page()                                 # 创建业务 page，后续智能方法仍然统一操作 self.page
        self.isStarted = True                                                         # 标记为已启动

        self.log("Playwright Chromium 启动完成，page 已创建。")                        # 打印启动完成日志

    def close(self) -> bool:
        self.log("正在关闭 Browser。")                                                 # 关闭前打印日志，明确生命周期结束点

        try:
            if self.page and hasattr(self.page, "close"):                             # 先关 page，这样更稳
                try:
                    self.page.close()
                except Exception:
                    pass                                                              # page 关闭失败时继续往下收尾，尽量释放整体资源

            if self.engine == "camoufox":                                             # Camoufox 的资源释放逻辑
                if self.camoufoxContext is not None:                                  # Camoufox 是上下文管理器，所以这里对称调用 __exit__
                    try:
                        self.camoufoxContext.__exit__(None, None, None)
                    except Exception:
                        pass                                                          # 底层关闭失败时也尽量吞掉，避免收尾阶段炸掉主流程

            elif self.engine in ("playwright", "chromium"):                           # Playwright Chromium 的资源释放逻辑
                if self.playwrightContext is not None:                                # 先关 context
                    try:
                        self.playwrightContext.close()
                    except Exception:
                        pass

                if self.playwrightBrowser is not None:                                # 再关 browser
                    try:
                        self.playwrightBrowser.close()
                    except Exception:
                        pass

                if self.playwrightDriver is not None:                                 # 最后停掉 Playwright 驱动
                    try:
                        self.playwrightDriver.stop()
                    except Exception:
                        pass

            self.page = None                                                          # 清空引用，避免后面误用已关闭对象
            self.rawBrowser = None                                                    # 清空真实浏览器对象引用
            self.camoufoxContext = None                                               # 清空 Camoufox 上下文对象引用
            self.playwrightDriver = None                                              # 清空 Playwright 驱动引用
            self.playwrightBrowser = None                                             # 清空 Playwright browser 引用
            self.playwrightContext = None                                             # 清空 Playwright context 引用
            self.isStarted = False                                                    # 状态归零，表示当前实例已关闭

            self.log("Browser 已关闭。")
            return True
        except Exception as error:
            self.log(f"close 失败：{error}")
            return False

    def ensurePage(self):
        if self.page is not None:                                                     # 已经有 page 时直接返回，这是最常见路径
            return self.page

        if not self.isStarted:                                                        # 没启动就先启动，让调用方不需要关心启动时机
            self.start()

        if self.page is None:                                                         # 启动后仍然没有 page，说明底层启动流程真的出了问题
            raise RuntimeError("Browser.page 不可用，请检查浏览器启动流程。")

        return self.page

    # ==================== 基础工具 ====================

    def log(self, message: str):
        if not self.config.isDebug:                                                   # 关闭调试日志时直接不打印
            return

        print(message)                                                                # 调试模式下打印日志，便于看清整个动作链

    def sleep(self, seconds: float):
        self.log(f"等待 {seconds} 秒。")                                               # 主动说明程序为什么在等，避免“看起来像卡住”
        time.sleep(seconds)                                                           # 真正执行等待

    def getPage(self):
        return self.ensurePage()                                                      # 对外暴露 page，但仍然保证 page 一定已准备好

    def getTimeout(self, timeout: Optional[int], defaultValue: int) -> int:
        return timeout if timeout is not None else defaultValue                       # 调用方有传就优先用传入值，没传才走默认值

    def getRetryCount(self, retryCount: Optional[int], actionName: str) -> int:
        if retryCount is not None:                                                    # 调用方明确指定了重试次数时，优先尊重调用方
            return retryCount

        if actionName == "click":                                                     # click 最容易遇到“点了没反应”，默认多给点机会
            return self.config.clickRetryCount

        return self.config.actionRetryCount                                           # 其他动作默认更保守一点

    def getRetryInterval(self, retryInterval: Optional[float]) -> float:
        return retryInterval if retryInterval is not None else self.config.retryInterval  # 没传就走统一默认间隔

    def hasSmartRule(
        self,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        valueIs: Optional[str] = None,
        countIs: Optional[int] = None,
        countAtLeast: Optional[int] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        return any(
            [
                showSelector,                                                         # 点击或输入后等某个元素出现
                hideSelector,                                                         # 点击或输入后等某个元素消失
                urlContains,                                                          # 地址包含某段文本
                textContains,                                                         # 页面文本包含某段内容
                valueIs is not None,                                                  # 输入框值等于目标值
                countIs is not None,                                                  # 元素数量正好等于目标数量
                countAtLeast is not None,                                             # 元素数量至少达到目标数量
                titleContains,                                                        # 页面标题包含目标文本
                retryCount is not None,                                               # 明确传了重试次数，也说明调用者希望走增强流程
                retryInterval is not None,                                            # 明确传了重试间隔，也说明调用者希望走增强流程
            ]
        )

    # ==================== selector 统一处理 ====================

    def getBestSelector(self, selector: Any, timeout: Optional[int] = None) -> Optional[str]:
        if isinstance(selector, str):                                                 # 传单个字符串时，直接返回，不做额外处理
            return selector

        if not isinstance(selector, list):                                            # 不是字符串也不是列表时，说明 selector 结构不对
            return None

        timeout = self.getTimeout(timeout, 600)                                       # 试探候选选择器时用很短超时，避免拖慢整体流程

        for oneSelector in selector:                                                  # 依次尝试每个候选选择器，找到第一个真实存在的
            if not oneSelector:                                                       # 空值直接跳过
                continue

            try:
                if self.has(oneSelector, state="attached", timeout=timeout):          # 找到挂在 DOM 上的选择器就足够了
                    return oneSelector
            except Exception:
                continue                                                               # 某个选择器写法异常时直接跳过，不让整个流程中断

        return selector[0] if selector else None                                      # 都没判断出来时，退回第一个，保留原始意图

    def normalizeSelector(self, selector: Any, timeout: Optional[int] = None) -> str:
        bestSelector = self.getBestSelector(selector, timeout=timeout)                 # 把字符串或候选列表都收束成一个真实要用的 selector

        if not bestSelector:                                                          # 找不到可用 selector 时直接报清楚错误，避免后面全链路都是奇怪异常
            raise RuntimeError(f"无效 selector: {selector}")

        return bestSelector

    def getLocator(self, selector: Any, timeout: Optional[int] = None):
        page = self.ensurePage()                                                      # 每次拿 locator 前先保证 page 已可用
        selector = self.normalizeSelector(selector, timeout=timeout)                   # 统一把 selector 处理成最终可用字符串
        return page.locator(selector).first                                           # 统一使用 first，避免匹配多个元素时行为不稳定

    # ==================== 页面状态与等待 ====================

    def isPageReady(self) -> bool:
        page = self.ensurePage()                                                      # 读取页面状态前先保证 page 可用

        try:
            state = page.evaluate("document.readyState")                              # 读取浏览器原生 readyState，判断页面是否基本可操作
            return state in ["interactive", "complete"]                               # interactive 表示 DOM 可用了，complete 表示页面基本加载完
        except Exception:
            return False                                                              # 读取失败时直接返回 False，让外层逻辑决定是否继续等

    def waitPageReady(self, timeout: Optional[int] = None) -> bool:
        timeout = self.getTimeout(timeout, self.config.waitTimeout)                    # 没传 timeout 就用统一 wait 默认值
        endTime = time.time() + timeout / 1000                                        # 换算成结束时间，后面循环判断更直观

        self.log("正在等待页面进入可操作状态。")                                        # 告诉调用方当前为什么在等

        while time.time() < endTime:                                                  # 用短轮询判断 readyState 是否变好
            if self.isPageReady():
                self.log("页面已经进入可操作状态。")
                return True

            time.sleep(0.2)                                                           # 每次稍等一点，给页面推进状态的时间

        self.log("等待页面可操作状态超时。")
        return False

    def has(
        self,
        selector: Any,
        state: str = "visible",
        timeout: Optional[int] = None,
    ) -> bool:
        if not selector:                                                              # 没有 selector 时没有检查意义
            self.log("has 检查失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # 判断类方法默认用较短超时，避免拖沓

        try:
            self.getLocator(selector, timeout=timeout).wait_for(state=state, timeout=timeout)  # 直接等到底层状态成立
            return True
        except Exception:
            return False                                                              # 判断类方法返回布尔值更好用，不抛异常

    def show(self, selector: Any, timeout: Optional[int] = None) -> bool:
        return self.has(selector, state="visible", timeout=timeout)                    # show 本质上就是“元素可见”

    def wait(
        self,
        selector: Any,
        state: str = "visible",
        timeout: Optional[int] = None,
        countIs: Optional[int] = None,
        countAtLeast: Optional[int] = None,
        textContains: Optional[str] = None,
    ) -> bool:
        if not selector:                                                              # 没 selector 就不知道要等谁
            self.log("wait 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.waitTimeout)                    # wait 默认比普通结果等待更长
        endTime = time.time() + timeout / 1000                                        # 统一换算结束时间

        self.log(f"正在等待元素状态：{selector}")

        while time.time() < endTime:
            if countIs is not None:                                                   # 需要等待精确数量时，优先走数量判断
                currentCount = self.count(selector)
                if currentCount == countIs:
                    self.log(f"等待成功：元素数量已经等于 {countIs}。")
                    return True

            elif countAtLeast is not None:                                            # 需要等待至少多少个时，走最小数量判断
                currentCount = self.count(selector)
                if currentCount >= countAtLeast:
                    self.log(f"等待成功：元素数量已经达到 {currentCount}。")
                    return True

            elif textContains is not None:                                            # 需要等待文本内容时，轮询当前文本
                text = self.getText(selector, defaultValue="")
                if textContains in text:
                    self.log("等待成功：元素文本已经包含目标内容。")
                    return True

            else:                                                                     # 普通情况就等元素状态成立
                if self.has(selector, state=state, timeout=300):
                    self.log(f"等待成功：元素状态已经满足 -> {state}")
                    return True

            time.sleep(0.2)                                                           # 每轮稍等一点，避免 CPU 空转，也让页面有时间变化

        self.log("wait 超时。")
        return False

    def count(self, selector: Any) -> int:
        if not selector:                                                              # 没 selector 没法统计
            self.log("count 失败：selector 为空。")
            return 0

        page = self.ensurePage()                                                      # 统计前先保证 page 可用
        selector = self.normalizeSelector(selector, timeout=300)                       # 把候选 selector 收束成最终 selector

        try:
            return page.locator(selector).count()                                     # 直接返回命中数量
        except Exception:
            return 0                                                                  # 统计失败时返回 0，更适合自动化流程里的条件判断

    def getText(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        defaultValue: str = "",
        isStrip: bool = True,
    ) -> str:
        if not selector:                                                              # 没目标就没法取文本
            self.log("getText 失败：selector 为空。")
            return defaultValue

        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # 取文本前先短等一下元素出现

        if not self.has(selector, timeout=timeout):
            return defaultValue                                                       # 元素没出来时直接返回默认值，不抛异常

        try:
            text = self.getLocator(selector, timeout=timeout).inner_text(timeout=timeout)  # inner_text 更接近用户实际看到的文本
            return text.strip() if isStrip else text                                  # 默认去掉首尾空白，让数据更干净
        except Exception:
            return defaultValue

    def getValue(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        defaultValue: str = "",
    ) -> str:
        if not selector:                                                              # 没目标就没法取输入值
            self.log("getValue 失败：selector 为空。")
            return defaultValue

        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # 读取输入值前给元素一点出现时间

        if not self.has(selector, timeout=timeout):
            return defaultValue

        try:
            return self.getLocator(selector, timeout=timeout).input_value(timeout=timeout)  # input_value 专门用于取输入框值
        except Exception:
            return defaultValue

    def getHtml(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        defaultValue: str = "",
        isOuter: bool = False,
    ) -> str:
        if not selector:                                                              # 没目标就没法取 HTML
            self.log("getHtml 失败：selector 为空。")
            return defaultValue

        timeout = self.getTimeout(timeout, self.config.resultTimeout)

        if not self.has(selector, state="attached", timeout=timeout):                  # 取 HTML 不要求可见，只要求在 DOM 上
            return defaultValue

        try:
            if isOuter:
                return self.getLocator(selector, timeout=timeout).evaluate("node => node.outerHTML")  # outerHTML 包含元素自己本身
            return self.getLocator(selector, timeout=timeout).evaluate("node => node.innerHTML")      # innerHTML 只包含内部内容
        except Exception:
            return defaultValue

    def isChecked(self, selector: Any, timeout: Optional[int] = None) -> bool:
        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # 勾选状态判断前先短等元素出现

        if not self.has(selector, timeout=timeout):
            return False

        try:
            return self.getLocator(selector, timeout=timeout).is_checked(timeout=timeout)  # 交给底层来判断是否已勾选
        except Exception:
            return False

    def isDisabled(self, selector: Any, timeout: Optional[int] = None) -> bool:
        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # 禁用状态判断前先短等元素出现

        if not self.has(selector, timeout=timeout):
            return False

        try:
            return self.getLocator(selector, timeout=timeout).is_disabled(timeout=timeout)  # 交给底层判断是否禁用
        except Exception:
            return False

    def find(self, selector: Any, timeout: Optional[int] = None):
        if not selector:                                                              # 没 selector 就没法找元素
            self.log("find 失败：selector 为空。")
            return None

        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # find 前短等一下元素存在

        if not self.has(selector, state="attached", timeout=timeout):
            self.log(f"find 失败：元素未出现 -> {selector}")
            return None

        try:
            return self.getLocator(selector, timeout=timeout)                          # 返回 locator，供高级用法直接使用
        except Exception as error:
            self.log(f"find 失败：{error}")
            return None

    # ==================== 统一结果判定引擎 ====================

    def isSmartSuccess(
        self,
        selector: Optional[Any] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        valueIs: Optional[str] = None,
        countIs: Optional[int] = None,
        countAtLeast: Optional[int] = None,
        titleContains: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> bool:
        page = self.ensurePage()                                                      # 结果判断时也必须保证 page 存在
        timeout = self.getTimeout(timeout, self.config.resultTimeout)                  # 没传就走统一结果等待超时

        self.log("正在检查动作结果。")                                                 # 这是智能模式里的关键步骤，必须留日志

        if not any([showSelector, hideSelector, urlContains, textContains, valueIs is not None, countIs is not None, countAtLeast is not None, titleContains]):
            self.log("没有提供智能结果条件，默认认为动作成功。")                         # 没有结果条件时，不人为制造失败
            return True

        if showSelector and self.has(showSelector, timeout=timeout):
            self.log(f"结果成功：目标元素已出现 -> {showSelector}")
            return True

        if hideSelector and self.has(hideSelector, state="hidden", timeout=timeout):
            self.log(f"结果成功：目标元素已隐藏 -> {hideSelector}")
            return True

        if urlContains and urlContains in page.url:
            self.log(f"结果成功：当前地址已包含 -> {urlContains}")
            return True

        if textContains:
            try:
                pageText = page.locator("body").inner_text(timeout=timeout)           # 用整页文本判断提示文案、toast、全局成功信息
            except Exception:
                pageText = ""

            if textContains in pageText:
                self.log(f"结果成功：页面文本已包含 -> {textContains}")
                return True

        if valueIs is not None and selector:
            currentValue = self.getValue(selector, timeout=timeout, defaultValue="")   # valueIs 默认针对当前 selector 本身判断
            if currentValue == valueIs:
                self.log("结果成功：输入值已经等于目标值。")
                return True

        if countIs is not None and selector:
            currentCount = self.count(selector)
            if currentCount == countIs:
                self.log(f"结果成功：元素数量已经等于 {countIs}。")
                return True

        if countAtLeast is not None and selector:
            currentCount = self.count(selector)
            if currentCount >= countAtLeast:
                self.log(f"结果成功：元素数量已经达到 {currentCount}。")
                return True

        if titleContains:
            try:
                title = page.title()                                                  # 页面标题有时也是一种很自然的页面成功标记
                if titleContains in title:
                    self.log(f"结果成功：页面标题已包含 -> {titleContains}")
                    return True
            except Exception:
                pass

        self.log("结果检查未通过。")
        return False

    def makeSure(
        self,
        selector: Optional[Any] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        valueIs: Optional[str] = None,
        countIs: Optional[int] = None,
        countAtLeast: Optional[int] = None,
        titleContains: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> bool:
        return self.isSmartSuccess(
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            valueIs=valueIs,
            countIs=countIs,
            countAtLeast=countAtLeast,
            titleContains=titleContains,
            timeout=timeout,
        )

    def runAction(
        self,
        actionName: str,
        actionFunc,
        selector: Optional[Any] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        valueIs: Optional[str] = None,
        countIs: Optional[int] = None,
        countAtLeast: Optional[int] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
        resultTimeout: Optional[int] = None,
    ) -> bool:
        actualRetryCount = self.getRetryCount(retryCount, actionName)                  # 根据动作类型拿默认重试次数
        actualRetryInterval = self.getRetryInterval(retryInterval)                     # 统一拿重试间隔
        resultTimeout = self.getTimeout(resultTimeout, self.config.resultTimeout)      # 统一拿结果等待超时

        for index in range(actualRetryCount + 1):                                      # 比如重试 3 次，意味着总共最多尝试 4 次
            attempt = index + 1                                                        # 把 0 开始的索引转成人类更直觉的次数

            self.log(f"{actionName} 第 {attempt} 次尝试开始。")                         # 每次尝试都打出来，方便回溯问题

            try:
                actionFunc()                                                           # 真正执行动作，比如 click、fill、press
            except Exception as error:
                self.log(f"{actionName} 第 {attempt} 次动作报错：{error}")              # 动作报错不立刻死，让智能流程继续接管
            else:
                isSuccess = self.isSmartSuccess(
                    selector=selector,
                    showSelector=showSelector,
                    hideSelector=hideSelector,
                    urlContains=urlContains,
                    textContains=textContains,
                    valueIs=valueIs,
                    countIs=countIs,
                    countAtLeast=countAtLeast,
                    titleContains=titleContains,
                    timeout=resultTimeout,
                )

                if isSuccess:
                    self.log(f"{actionName} 第 {attempt} 次尝试成功。")
                    return True

            if attempt <= actualRetryCount:                                            # 只要还有机会，就等一下再重试
                self.log(f"{actionName} 第 {attempt} 次未达到预期，等待 {actualRetryInterval} 秒后重试。")
                time.sleep(actualRetryInterval)

        self.log(f"{actionName} 已达到最大尝试次数，但仍未成功。")
        return False

    # ==================== 页面导航类 ====================

    def goto(
        self,
        url: str,
        timeout: Optional[int] = None,
        waitUntil: str = "load",
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
        **kwargs,
    ) -> bool:
        if not url:
            self.log("goto 失败：url 为空。")
            return False

        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在打开页面：{url}")

        def action():
            page.goto(url, timeout=timeout, wait_until=waitUntil, **kwargs)           # 这里两种引擎都兼容 page.goto 语义
            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

        if not isSmart:
            try:
                action()
                self.log("页面打开完成。")
                return True
            except Exception as error:
                self.log(f"goto 失败：{error}")
                return False

        return self.runAction(
            actionName="goto",
            actionFunc=action,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains or url,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
            resultTimeout=timeout,
        )

    def reload(
        self,
        timeout: Optional[int] = None,
        waitUntil: str = "domcontentloaded",
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log("正在刷新页面。")

        def action():
            try:
                page.reload(timeout=timeout, wait_until=waitUntil)
            except Exception as error:
                self.log(f"reload 底层调用报错，但继续检查页面状态：{error}")
            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

        if not isSmart:
            try:
                action()
                self.log("页面刷新完成。")
                return True
            except Exception as error:
                self.log(f"reload 失败：{error}")
                return False

        return self.runAction(
            actionName="reload",
            actionFunc=action,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
            resultTimeout=timeout,
        )

    def back(
        self,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        retryCount = self.getRetryCount(retryCount, "back")
        retryInterval = self.getRetryInterval(retryInterval)

        self.log("正在执行后退。")

        for index in range(retryCount + 1):
            attempt = index + 1
            beforeUrl = page.url

            self.log(f"back 第 {attempt} 次尝试开始，当前地址：{beforeUrl}")

            try:
                page.go_back(timeout=min(timeout, 5000), wait_until="domcontentloaded")
            except Exception as error:
                self.log(f"原生 go_back 报错，准备继续检查或兜底：{error}")

            isChanged = self.waitUrlChange(beforeUrl, timeout=4000)

            if not isChanged:
                self.log("原生 go_back 后地址未变化，尝试使用 history.back() 兜底。")

                try:
                    page.evaluate("history.back()")
                except Exception as error:
                    self.log(f"history.back() 执行失败：{error}")

                isChanged = self.waitUrlChange(beforeUrl, timeout=4000)

            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

            currentUrl = page.url
            self.log(f"back 第 {attempt} 次尝试后当前地址：{currentUrl}")

            if not isChanged:
                if attempt <= retryCount:
                    self.log(f"back 第 {attempt} 次没有产生地址变化，等待 {retryInterval} 秒后重试。")
                    time.sleep(retryInterval)
                    continue

                self.log("back 已达到最大尝试次数，但页面地址始终没有变化。")
                return False

            isTargetOk = self.isSmartSuccess(
                showSelector=showSelector,
                urlContains=urlContains,
                timeout=max(self.config.resultTimeout, 4000),
            )

            if not showSelector and not urlContains:
                self.log(f"back 第 {attempt} 次尝试成功：页面地址已经变化。")
                return True

            if isTargetOk:
                self.log(f"back 第 {attempt} 次尝试成功：页面已经回到目标状态。")
                return True

            if attempt <= retryCount:
                self.log(f"back 第 {attempt} 次地址已变化，但目标状态未达成，等待 {retryInterval} 秒后重试。")
                time.sleep(retryInterval)

        self.log("back 已达到最大尝试次数，但仍未到达目标页面。")
        return False

    def forward(
        self,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.gotoTimeout)
        retryCount = self.getRetryCount(retryCount, "forward")
        retryInterval = self.getRetryInterval(retryInterval)

        self.log("正在执行前进。")

        for index in range(retryCount + 1):
            attempt = index + 1
            beforeUrl = page.url

            self.log(f"forward 第 {attempt} 次尝试开始，当前地址：{beforeUrl}")

            try:
                page.go_forward(timeout=min(timeout, 5000), wait_until="domcontentloaded")
            except Exception as error:
                self.log(f"原生 go_forward 报错，准备继续检查或兜底：{error}")

            isChanged = self.waitUrlChange(beforeUrl, timeout=4000)

            if not isChanged:
                self.log("原生 go_forward 后地址未变化，尝试使用 history.forward() 兜底。")

                try:
                    page.evaluate("history.forward()")
                except Exception as error:
                    self.log(f"history.forward() 执行失败：{error}")

                isChanged = self.waitUrlChange(beforeUrl, timeout=4000)

            self.waitPageReady(timeout=min(timeout, self.config.waitTimeout))

            currentUrl = page.url
            self.log(f"forward 第 {attempt} 次尝试后当前地址：{currentUrl}")

            if not isChanged:
                if attempt <= retryCount:
                    self.log(f"forward 第 {attempt} 次没有产生地址变化，等待 {retryInterval} 秒后重试。")
                    time.sleep(retryInterval)
                    continue

                self.log("forward 已达到最大尝试次数，但页面地址始终没有变化。")
                return False

            isTargetOk = self.isSmartSuccess(
                showSelector=showSelector,
                urlContains=urlContains,
                timeout=max(self.config.resultTimeout, 4000),
            )

            if not showSelector and not urlContains:
                self.log(f"forward 第 {attempt} 次尝试成功：页面地址已经变化。")
                return True

            if isTargetOk:
                self.log(f"forward 第 {attempt} 次尝试成功：页面已经到达目标状态。")
                return True

            if attempt <= retryCount:
                self.log(f"forward 第 {attempt} 次地址已变化，但目标状态未达成，等待 {retryInterval} 秒后重试。")
                time.sleep(retryInterval)

        self.log("forward 已达到最大尝试次数，但仍未到达目标页面。")
        return False

    def openPage(self, url: Optional[str] = None, showSelector: Optional[Any] = None) -> bool:
        if not url:
            hasPage = self.ensurePage() is not None
            self.log(f"当前 page 是否可用：{hasPage}")
            return hasPage

        return self.goto(url, showSelector=showSelector)

    # ==================== 元素动作类 ====================

    def tryClick(self, selector: Any, timeout: int, isForce: bool = False):
        locator = self.getLocator(selector, timeout=timeout)
        locator.scroll_into_view_if_needed(timeout=timeout)

        if isForce:
            locator.click(timeout=timeout, force=True)
            return

        try:
            locator.click(timeout=timeout)
            return
        except Exception:
            pass

        try:
            locator.click(timeout=timeout, force=True)
            return
        except Exception:
            pass

        locator.evaluate("node => node.click()")

    def click(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        button: str = "left",
        clickCount: int = 1,
        delay: Optional[int] = None,
        modifiers: Optional[list] = None,
        position: Optional[dict] = None,
        isForce: bool = False,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        titleContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("click 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在点击元素：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"click 失败：元素未出现 -> {selector}")
            return False

        def action():
            if not isSmart and not isForce:
                self.getLocator(selector, timeout=timeout).click(
                    timeout=timeout,
                    button=button,
                    click_count=clickCount,
                    delay=delay,
                    modifiers=modifiers,
                    position=position,
                )
                return

            self.tryClick(selector, timeout=timeout, isForce=isForce)

        if not isSmart:
            try:
                action()
                self.log("点击完成。")
                return True
            except Exception as error:
                self.log(f"click 失败：{error}")
                return False

        return self.runAction(
            actionName="click",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            titleContains=titleContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def fill(
        self,
        selector: Any,
        value: str,
        timeout: Optional[int] = None,
        isClear: bool = True,
        valueIs: Optional[str] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("fill 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        expectedValue = value if valueIs is None else valueIs
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            valueIs=expectedValue if (valueIs is not None or showSelector or hideSelector or retryCount is not None or retryInterval is not None) else None,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在填写输入框：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"fill 失败：输入框未出现 -> {selector}")
            return False

        def action():
            locator = self.getLocator(selector, timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            if isClear:
                locator.fill("", timeout=timeout)
            locator.fill(value, timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("填写完成。")
                return True
            except Exception as error:
                self.log(f"fill 失败：{error}")
                return False

        return self.runAction(
            actionName="fill",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            valueIs=expectedValue,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def press(
        self,
        selector: Any,
        key: str,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        hideSelector: Optional[Any] = None,
        urlContains: Optional[str] = None,
        textContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("press 失败：selector 为空。")
            return False

        if not key:
            self.log("press 失败：key 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在按键：{key} -> {selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"press 失败：元素未出现 -> {selector}")
            return False

        def action():
            locator = self.getLocator(selector, timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            locator.focus()
            locator.press(key, timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("按键完成。")
                return True
            except Exception as error:
                self.log(f"press 失败：{error}")
                return False

        return self.runAction(
            actionName="press",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            hideSelector=hideSelector,
            urlContains=urlContains,
            textContains=textContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def screenshot(
        self,
        path: Optional[str] = None,
        fullPage: bool = True,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        **kwargs,
    ) -> str:
        page = self.ensurePage()
        timeout = self.getTimeout(timeout, self.config.actionTimeout)

        if showSelector:
            self.wait(showSelector, timeout=timeout)

        if not path:
            path = f"browser-shot-{int(time.time())}.png"

        self.log(f"正在截图：{path}")

        try:
            page.screenshot(path=path, full_page=fullPage, timeout=timeout, **kwargs)
            self.log("截图完成。")
            return path
        except Exception as error:
            self.log(f"screenshot 失败：{error}")
            return ""

    def evaluate(self, script: str, arg: Any = None, defaultValue: Any = None) -> Any:
        if not script:
            self.log("evaluate 失败：script 为空。")
            return defaultValue

        page = self.ensurePage()
        self.log("正在执行页面脚本。")

        try:
            if arg is None:
                return page.evaluate(script)
            return page.evaluate(script, arg)
        except Exception as error:
            self.log(f"evaluate 失败：{error}")
            return defaultValue

    def waitUrlChange(self, oldUrl: str, timeout: Optional[int] = None) -> bool:
        timeout = self.getTimeout(timeout, self.config.waitTimeout)
        endTime = time.time() + timeout / 1000

        self.log(f"正在等待页面地址变化，旧地址是：{oldUrl}")

        while time.time() < endTime:
            try:
                currentUrl = self.ensurePage().url
                if currentUrl != oldUrl:
                    self.log(f"页面地址已变化：{oldUrl} -> {currentUrl}")
                    return True
            except Exception:
                pass

            time.sleep(0.2)

        self.log("等待页面地址变化超时。")
        return False

    def hasUrlChanged(self, oldUrl: str) -> bool:
        try:
            return self.ensurePage().url != oldUrl
        except Exception:
            return False
    def dblclick(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("dblclick 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在双击元素：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"dblclick 失败：元素未出现 -> {selector}")
            return False

        def action():
            locator = self.getLocator(selector, timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            locator.dblclick(timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("双击完成。")
                return True
            except Exception as error:
                self.log(f"dblclick 失败：{error}")
                return False

        return self.runAction(
            actionName="dblclick",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def hover(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("hover 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在悬停元素：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"hover 失败：元素未出现 -> {selector}")
            return False

        def action():
            locator = self.getLocator(selector, timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            locator.hover(timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("悬停完成。")
                return True
            except Exception as error:
                self.log(f"hover 失败：{error}")
                return False

        return self.runAction(
            actionName="hover",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def type(
        self,
        selector: Any,
        value: str,
        timeout: Optional[int] = None,
        delay: Optional[int] = None,
        isClear: bool = True,
        valueIs: Optional[str] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("type 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        delay = delay if delay is not None else self.config.typeDelay
        selector = self.normalizeSelector(selector, timeout=timeout)
        expectedValue = value if valueIs is None else valueIs
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            valueIs=expectedValue if (valueIs is not None or showSelector or retryCount is not None or retryInterval is not None) else None,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        self.log(f"正在逐字输入：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"type 失败：输入框未出现 -> {selector}")
            return False

        def action():
            locator = self.getLocator(selector, timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            locator.click(timeout=timeout)
            if isClear:
                locator.fill("", timeout=timeout)
            locator.type(value, delay=delay, timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("逐字输入完成。")
                return True
            except Exception as error:
                self.log(f"type 失败：{error}")
                return False

        return self.runAction(
            actionName="type",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            valueIs=expectedValue,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def check(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("check 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在勾选复选框：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"check 失败：元素未出现 -> {selector}")
            return False

        if self.isChecked(selector, timeout=timeout):
            self.log("复选框已经是勾选状态。")
            return True

        def action():
            self.getLocator(selector, timeout=timeout).check(timeout=timeout)
            if not self.isChecked(selector, timeout=timeout):
                raise RuntimeError("复选框勾选后状态仍未变为选中。")

        if not isSmart:
            try:
                action()
                self.log("勾选完成。")
                return True
            except Exception as error:
                self.log(f"check 失败：{error}")
                return False

        return self.runAction(
            actionName="check",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def uncheck(
        self,
        selector: Any,
        timeout: Optional[int] = None,
        hideSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("uncheck 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(hideSelector=hideSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在取消勾选复选框：{selector}")

        if not self.has(selector, timeout=timeout):
            self.log(f"uncheck 失败：元素未出现 -> {selector}")
            return False

        if not self.isChecked(selector, timeout=timeout):
            self.log("复选框已经是未勾选状态。")
            return True

        def action():
            self.getLocator(selector, timeout=timeout).uncheck(timeout=timeout)
            if self.isChecked(selector, timeout=timeout):
                raise RuntimeError("复选框取消勾选后仍然是选中状态。")

        if not isSmart:
            try:
                action()
                self.log("取消勾选完成。")
                return True
            except Exception as error:
                self.log(f"uncheck 失败：{error}")
                return False

        return self.runAction(
            actionName="uncheck",
            actionFunc=action,
            selector=selector,
            hideSelector=hideSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def select(
        self,
        selector: Any,
        value: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("select 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(showSelector=showSelector, retryCount=retryCount, retryInterval=retryInterval)

        self.log(f"正在选择下拉项：{selector} -> {value}")

        if not self.has(selector, timeout=timeout):
            self.log(f"select 失败：元素未出现 -> {selector}")
            return False

        def action():
            locator = self.getLocator(selector, timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            locator.select_option(value=value, timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("下拉选择完成。")
                return True
            except Exception as error:
                self.log(f"select 失败：{error}")
                return False

        return self.runAction(
            actionName="select",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def setInputFiles(
        self,
        selector: Any,
        filePath: Any,
        timeout: Optional[int] = None,
        showSelector: Optional[Any] = None,
        textContains: Optional[str] = None,
        retryCount: Optional[int] = None,
        retryInterval: Optional[float] = None,
    ) -> bool:
        if not selector:
            self.log("setInputFiles 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)
        isSmart = self.hasSmartRule(
            showSelector=showSelector,
            textContains=textContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

        fileList = filePath if isinstance(filePath, list) else [filePath]

        for onePath in fileList:
            if not Path(onePath).exists():
                self.log(f"setInputFiles 失败：文件不存在 -> {onePath}")
                return False

        self.log(f"正在上传文件到元素：{selector}")

        if not self.has(selector, state="attached", timeout=timeout):
            self.log(f"setInputFiles 失败：上传控件未出现 -> {selector}")
            return False

        def action():
            self.getLocator(selector, timeout=timeout).set_input_files(filePath, timeout=timeout)

        if not isSmart:
            try:
                action()
                self.log("文件上传动作已执行。")
                return True
            except Exception as error:
                self.log(f"setInputFiles 失败：{error}")
                return False

        return self.runAction(
            actionName="setInputFiles",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
            textContains=textContains,
            retryCount=retryCount,
            retryInterval=retryInterval,
        )

    def focus(self, selector: Any, timeout: Optional[int] = None) -> bool:
        if not selector:
            self.log("focus 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)

        if not self.has(selector, timeout=timeout):
            self.log(f"focus 失败：元素未出现 -> {selector}")
            return False

        try:
            self.getLocator(selector, timeout=timeout).focus()
            self.log(f"已聚焦元素：{selector}")
            return True
        except Exception as error:
            self.log(f"focus 失败：{error}")
            return False

    def blur(self, selector: Any, timeout: Optional[int] = None, showSelector: Optional[Any] = None) -> bool:
        if not selector:
            self.log("blur 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)

        if not self.has(selector, timeout=timeout):
            self.log(f"blur 失败：元素未出现 -> {selector}")
            return False

        def action():
            self.getLocator(selector, timeout=timeout).evaluate("node => node.blur()")

        if not showSelector:
            try:
                action()
                self.log(f"已让元素失焦：{selector}")
                return True
            except Exception as error:
                self.log(f"blur 失败：{error}")
                return False

        return self.runAction(
            actionName="blur",
            actionFunc=action,
            selector=selector,
            showSelector=showSelector,
        )

    def scroll(self, selector: Optional[Any] = None, position: Optional[str] = None) -> bool:
        page = self.ensurePage()
        self.log("正在执行滚动。")

        try:
            if selector:
                selector = self.normalizeSelector(selector, timeout=600)
                self.getLocator(selector, timeout=600).scroll_into_view_if_needed(timeout=self.config.actionTimeout)
                self.log(f"已滚动到元素位置：{selector}")
                return True

            if position == "top":
                page.evaluate("window.scrollTo(0, 0)")
                self.log("已滚动到页面顶部。")
                return True

            if position == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                self.log("已滚动到页面底部。")
                return True

            page.mouse.wheel(0, 800)
            self.log("已执行一次普通向下滚动。")
            return True
        except Exception as error:
            self.log(f"滚动失败：{error}")
            return False

    def remove(self, selector: Any, timeout: Optional[int] = None) -> bool:
        if not selector:
            self.log("remove 失败：selector 为空。")
            return False

        timeout = self.getTimeout(timeout, self.config.actionTimeout)
        selector = self.normalizeSelector(selector, timeout=timeout)

        if not self.has(selector, state="attached", timeout=timeout):
            self.log(f"remove 跳过：元素本来就不存在 -> {selector}")
            return True

        self.log(f"正在从页面中移除元素：{selector}")

        try:
            self.ensurePage().locator(selector).evaluate_all("nodes => nodes.forEach(node => node.remove())")
            self.log("元素移除完成。")
            return True
        except Exception as error:
            self.log(f"remove 失败：{error}")
            return False



















if __name__ == "__main__":
    import os  # 用来判断截图文件和上传测试文件是否存在
    import tempfile  # 用来临时创建一个上传文件，便于测试 setInputFiles
    from pathlib import Path  # 处理测试文件路径更清楚

    def printTitle(title: str):
        print("\n" + "=" * 80)  # 大分隔线，方便控制台阅读
        print(title)  # 打印阶段标题
        print("=" * 80)

    def printStep(name: str):
        print(f"\n--- {name} ---")  # 每个测试项单独一段，更容易定位失败位置

    def assertTrue(value: bool, message: str):
        if not value:  # 条件不成立时直接抛异常，让当前测试明确失败
            raise AssertionError(message)

    def assertEqual(left, right, message: str):
        if left != right:  # 值不相等时抛异常，并带上左右值，方便排查
            raise AssertionError(f"{message} | left={left!r}, right={right!r}")

    def runTest(testName: str, testFunc, testResults: list):
        printStep(testName)  # 先打印当前测试名字，控制台更友好

        try:
            result = testFunc()  # 执行测试函数

            if result is False:  # 显式返回 False 时，也视为失败
                print(f"[FAIL] {testName}")
                testResults.append((testName, False))
                return

            print(f"[PASS] {testName}")  # 没异常且不是 False，就视为成功
            testResults.append((testName, True))
        except Exception as error:
            print(f"[FAIL] {testName} -> {error}")  # 捕获异常但不中断整个测试流程
            testResults.append((testName, False))

    printTitle("Browser 全面测试开始")  # 整个测试开始时打印大标题

    # ===== 你只需要改这里 =====
    baseUrl = "http://127.0.0.1:5500/tests"  # 改成你自己部署测试页面的地址，不要带最后的 html 文件名
    page1Url = f"{baseUrl}/browser_test_page1.html"  # 测试页 1 地址
    page2Url = f"{baseUrl}/browser_test_page2.html"  # 测试页 2 地址

    print(f"测试页面 1：{page1Url}")  # 打印测试地址，方便肉眼确认
    print(f"测试页面 2：{page2Url}")

    testResults = []  # 统一收集所有测试结果

    with tempfile.TemporaryDirectory() as tempDirString:  # 临时目录只用于上传测试文件和截图
        tempDir = Path(tempDirString)
        uploadFile = tempDir / "upload.txt"  # 准备一个上传测试文件
        uploadFile.write_text("hello upload", encoding="utf-8")  # 写一点内容，确保文件真实存在

        with Browser(
            engine="playwright",
            headless=False,  # 这里你也可以改成 True。调试阶段建议 False，更容易看见页面过程
            os="windows",
            geoip=True,
            humanize=False,
        ) as browser:
            # ===== 生命周期与基础能力 =====

            runTest("getPage 可调用", lambda: assertTrue(browser.getPage() is not None, "getPage 返回为空"), testResults)

            runTest("openPage 不带 url", lambda: assertTrue(browser.openPage(), "openPage 不带 url 失败"), testResults)

            runTest("log 可调用", lambda: browser.log("这是 Browser.log 测试。"), testResults)

            runTest("sleep 可调用", lambda: browser.sleep(0.1), testResults)

            # ===== 页面导航类 =====

            runTest("goto 普通调用", lambda: assertTrue(browser.goto(page1Url), "goto 普通调用失败"), testResults)

            runTest(
                "goto 智能调用 showSelector",
                lambda: assertTrue(browser.goto(page1Url, showSelector="#app"), "goto 智能调用失败"),
                testResults,
            )

            runTest(
                "openPage 带 url",
                lambda: assertTrue(browser.openPage(page1Url, showSelector="#app"), "openPage 带 url 失败"),
                testResults,
            )

            runTest(
                "click 跳转到 page2",
                lambda: assertTrue(browser.click("#toPage2", showSelector="#page2Title", urlContains="browser_test_page2.html"), "跳转到 page2 失败"),
                testResults,
            )

            runTest(
                "恢复到 page1",
                lambda: assertTrue(browser.goto(page1Url, showSelector="#app"), "恢复到 page1 失败"),
                testResults,
            )

            # ===== 读取与判断类 =====

            runTest("has 可见元素", lambda: assertTrue(browser.has("#app"), "has 失败"), testResults)

            runTest("show 可见元素", lambda: assertTrue(browser.show("#app"), "show 失败"), testResults)

            runTest("find 元素", lambda: assertTrue(browser.find("#app") is not None, "find 失败"), testResults)

            runTest("getText", lambda: assertEqual(browser.getText("#app"), "App Ready", "getText 失败"), testResults)

            runTest("getHtml inner", lambda: assertTrue("App Ready" in browser.getHtml("#app"), "getHtml inner 失败"), testResults)

            runTest("getHtml outer", lambda: assertTrue("<h1" in browser.getHtml("#app", isOuter=True), "getHtml outer 失败"), testResults)

            runTest("count 初始数量", lambda: assertEqual(browser.count(".item"), 2, "count 初始数量错误"), testResults)

            runTest("wait 元素可见", lambda: assertTrue(browser.wait("#app"), "wait 失败"), testResults)

            runTest("isDisabled", lambda: assertTrue(browser.isDisabled("#disabledButton"), "isDisabled 失败"), testResults)

            runTest("isChecked 初始未勾选", lambda: assertTrue(browser.isChecked("#agree") is False, "isChecked 初始状态错误"), testResults)

            runTest(
                "makeSure titleContains",
                lambda: assertTrue(browser.makeSure(titleContains="Browser Test Page One"), "makeSure titleContains 失败"),
                testResults,
            )

            runTest(
                "动作测试前恢复到 page1",
                lambda: assertTrue(browser.goto(page1Url, showSelector="#app"), "动作测试前恢复到 page1 失败"),
                testResults,
            )

            # ===== 动作类 =====

            runTest(
                "click 智能重试直到 successPanel 出现",
                lambda: assertTrue(browser.click("#submit", showSelector="#successPanel", retryCount=2, retryInterval=0.2), "click 智能重试失败"),
                testResults,
            )

            runTest(
                "dblclick",
                lambda: assertTrue(browser.dblclick("#doubleButton", showSelector="#doubleResult"), "dblclick 失败"),
                testResults,
            )

            runTest(
                "hover",
                lambda: assertTrue(browser.hover("#menuArea", showSelector="#submenu"), "hover 失败"),
                testResults,
            )

            runTest(
                "fill",
                lambda: assertTrue(browser.fill("#email", "test@example.com", valueIs="test@example.com"), "fill 失败"),
                testResults,
            )

            runTest(
                "getValue",
                lambda: assertEqual(browser.getValue("#email"), "test@example.com", "getValue 失败"),
                testResults,
            )

            runTest(
                "type",
                lambda: assertTrue(browser.type("#typeInput", "hello", valueIs="hello"), "type 失败"),
                testResults,
            )

            runTest(
                "press Enter",
                lambda: assertTrue(browser.press("#searchInput", "Enter", showSelector="#resultPanel"), "press 失败"),
                testResults,
            )

            runTest(
                "focus",
                lambda: assertTrue(browser.focus("#blurInput"), "focus 失败"),
                testResults,
            )

            runTest(
                "blur",
                lambda: assertTrue(browser.blur("#blurInput", showSelector="#blurTip"), "blur 失败"),
                testResults,
            )

            runTest(
                "check",
                lambda: assertTrue(browser.check("#agree"), "check 失败"),
                testResults,
            )

            runTest(
                "isChecked 勾选后为 True",
                lambda: assertTrue(browser.isChecked("#agree"), "isChecked 勾选后错误"),
                testResults,
            )

            runTest(
                "uncheck",
                lambda: assertTrue(browser.uncheck("#agree"), "uncheck 失败"),
                testResults,
            )

            runTest(
                "select",
                lambda: assertTrue(browser.select("#country", "US", showSelector="#selectResult"), "select 失败"),
                testResults,
            )

            runTest(
                "setInputFiles",
                lambda: assertTrue(browser.setInputFiles("#fileInput", str(uploadFile), showSelector="#uploadResult"), "setInputFiles 失败"),
                testResults,
            )

            runTest(
                "上传后文件名",
                lambda: assertEqual(browser.getText("#uploadName"), "upload.txt", "上传文件名错误"),
                testResults,
            )

            runTest(
                "click + hideSelector",
                lambda: assertTrue(browser.click("#hideLoading", hideSelector="#loading"), "hideSelector 失败"),
                testResults,
            )

            runTest(
                "scroll 到元素",
                lambda: assertTrue(browser.scroll("#bottomArea"), "scroll 到元素失败"),
                testResults,
            )

            runTest(
                "scroll 到顶部",
                lambda: assertTrue(browser.scroll(position="top"), "scroll top 失败"),
                testResults,
            )

            runTest(
                "scroll 到底部",
                lambda: assertTrue(browser.scroll(position="bottom"), "scroll bottom 失败"),
                testResults,
            )

            runTest(
                "click 添加 item",
                lambda: assertTrue(browser.click("#addItems"), "addItems 点击失败"),
                testResults,
            )

            runTest(
                "wait countAtLeast",
                lambda: assertTrue(browser.wait(".item", countAtLeast=4, timeout=3000), "wait countAtLeast 失败"),
                testResults,
            )

            runTest(
                "count 增加后数量",
                lambda: assertEqual(browser.count(".item"), 4, "count 增加后错误"),
                testResults,
            )

            runTest(
                "makeSure countAtLeast",
                lambda: assertTrue(browser.makeSure(selector=".item", countAtLeast=4), "makeSure countAtLeast 失败"),
                testResults,
            )

            runTest(
                "showHiddenButton 后 hiddenTarget 出现",
                lambda: assertTrue(browser.click("#showHiddenButton", showSelector="#hiddenTarget"), "hiddenTarget 未出现"),
                testResults,
            )

            runTest(
                "高级测试前恢复到 page1",
                lambda: assertTrue(browser.goto(page1Url, showSelector="#app"), "高级测试前恢复到 page1 失败"),
                testResults,
            )

            # ===== 高级与调试类 =====

            runTest(
                "evaluate",
                lambda: assertEqual(browser.evaluate("() => document.title"), "Browser Test Page One", "evaluate 失败"),
                testResults,
            )

            runTest(
                "remove",
                lambda: assertTrue(browser.remove("#removeMe"), "remove 失败"),
                testResults,
            )

            runTest(
                "remove 后元素不存在",
                lambda: assertTrue(browser.has("#removeMe", state="attached", timeout=300) is False, "remove 后元素仍存在"),
                testResults,
            )

            runTest(
                "screenshot",
                lambda: assertTrue(os.path.exists(browser.screenshot(path=str(tempDir / "quick-test-shot.png"), showSelector="#app")), "screenshot 文件不存在"),
                testResults,
            )

        # Browser.close() 不单独作为测试项了。  # 因为 with 结束时已经自动调用 close，更符合真实使用场景

    printTitle("测试结果汇总")  # 所有测试结束后统一汇总

    passCount = sum(1 for _, passed in testResults if passed)  # 统计通过数量
    failCount = sum(1 for _, passed in testResults if not passed)  # 统计失败数量

    for name, passed in testResults:
        mark = "PASS" if passed else "FAIL"  # 统一格式展示每个测试项状态
        print(f"[{mark}] {name}")

    print("\n" + "-" * 80)
    print(f"总数：{len(testResults)}")
    print(f"通过：{passCount}")
    print(f"失败：{failCount}")
    print("-" * 80)

    if failCount == 0:
        print("全部测试通过。")
    else:
        print("有测试失败，请根据上面的 FAIL 项检查 Browser 实现或测试页面内容。")
