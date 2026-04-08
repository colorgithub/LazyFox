import logging                                                                         # Python 内置日志库用于输出分级日志
import sys                                                                             # sys 用于判断当前输出流是否为终端以及绑定 stdout
from typing import Optional                                                            # 类型提示工具用于增强代码可读性

"""
# 基础用法：控制台彩色日志
log = Log(name="MyApp")
log.debug("这是调试信息")      # 亮青色
log.info("这是普通信息")       # 亮绿色
log.warning("这是警告信息")    # 亮黄色
log.error("这是错误信息")      # 亮红色
log.critical("这是致命错误")   # 亮洋红

# 异常捕获：自动打印堆栈
log = Log(name="MyApp")
try:
    1 / 0
except Exception:
    log.exception("捕获到异常")  # 自动打印堆栈跟踪

# 同时输出到文件
log = Log(name="MyApp")
# 添加文件处理器，只记录 WARNING 及以上级别
log.addFileHandler("app.log", level=logging.WARNING)

log.info("这条只会在控制台显示")
log.error("这条会同时显示在控制台和文件中")

# 动态调整日志级别
log = Log(name="MyApp")
log.setLevel(logging.WARNING)  # 动态提升日志级别
log.debug("这条会被过滤掉")
log.warning("这条会显示")
"""


class Log:
    """通用日志模块"""

    def __init__(self, name: str = "app", level: int = logging.DEBUG, resetHandlers: bool = False):
        self.name = name                                                               # 当前日志器名称，便于多模块隔离日志
        self.level = level                                                             # 当前日志等级，决定哪些日志会被输出
        self.logger = logging.getLogger(name)                                          # 获取或创建同名 logger 实例
        self.logger.setLevel(level)                                                    # 设置 logger 的最低处理级别
        self.logger.propagate = False                                                  # 关闭向父 logger 传播，避免重复打印

        if resetHandlers: self.logger.handlers.clear()                                 # 显式要求重置时清空已有处理器
        if not self.logger.handlers:                                                   # 只有在没有处理器时才初始化默认输出
            consoleHandler = self._buildConsoleHandler(level)                          # 创建控制台处理器
            self.logger.addHandler(consoleHandler)                                     # 把控制台处理器挂到当前 logger 上

    # ==================== 配置层 ====================

    def _buildConsoleHandler(self, level: int):                                        # 创建一个输出到控制台的处理器
        handler = logging.StreamHandler(sys.stdout)                                    # 把日志输出到标准输出而不是标准错误
        handler.setLevel(level)                                                        # 设置该处理器自己的日志等级
        handler.setFormatter(self._buildFormatter(useColor=sys.stdout.isatty()))       # 终端环境下启用颜色，重定向时自动关闭颜色
        return handler                                                                 # 返回处理器供外部挂载

    def _buildFileHandler(self, filename: str, level: Optional[int] = None, encoding: str = "utf-8"):  # 创建一个输出到文件的处理器
        handler = logging.FileHandler(filename, encoding=encoding)                     # 创建文件处理器用于写入日志文件
        handler.setLevel(self.level if level is None else level)                       # 未指定等级时沿用当前 logger 等级
        handler.setFormatter(self._buildFormatter(useColor=False))                     # 文件中通常不需要 ANSI 颜色码
        return handler                                                                 # 返回文件处理器

    def _buildFormatter(self, useColor: bool = True):                                  # 构造一个自定义格式化器
        outer = self                                                                   # 保存外层 self，供内部格式化器访问颜色映射

        class _Formatter(logging.Formatter):                                           # 局部格式化器类，仅在当前方法内使用
            def format(self, record: logging.LogRecord) -> str:                        # 自定义日志记录格式化逻辑
                color = outer._getLevelColor(record.levelno)                           # 根据日志级别选取颜色
                tag = outer._getLevelTag(record.levelno)                               # 根据日志级别选取短标签
                message = record.getMessage()                                          # 取出真正的日志文本内容

                base = f"[{tag}] {message}"                                            # 构造基础日志文本格式
                if useColor and color:                                                 # 需要颜色且当前级别有颜色映射时
                    base = f"{color}{base}{outer.RESET}"                               # 在首尾包上 ANSI 颜色控制码

                if record.exc_info:                                                    # 如果当前日志带有异常堆栈信息
                    excText = self.formatException(record.exc_info)                    # 用 logging 内置方式格式化异常
                    if useColor and color:                                             # 终端彩色模式下
                        base += f"\n{color}{excText}{outer.RESET}"                     # 给异常堆栈也套同色显示
                    else:
                        base += f"\n{excText}"                                         # 非彩色模式直接拼接纯文本异常

                if record.stack_info:                                                  # 如果显式附带了 stack_info 调用栈信息
                    if useColor and color:                                             # 彩色模式下
                        base += f"\n{color}{record.stack_info}{outer.RESET}"           # 给 stack 信息也使用同级别颜色
                    else:
                        base += f"\n{record.stack_info}"                               # 非彩色模式直接拼接调用栈文本

                return base                                                            # 返回最终格式化后的字符串

        return _Formatter()                                                            # 返回一个可供 handler 使用的 formatter 实例

    def _getLevelColor(self, levelno: int) -> str:                                     # 根据日志级别返回对应颜色码
        if levelno >= logging.CRITICAL: return self.CRITICAL                           # CRITICAL 使用亮洋红提高警示感
        if levelno >= logging.ERROR: return self.ERROR                                 # ERROR 使用亮红色突出错误
        if levelno >= logging.WARNING: return self.WARNING                             # WARNING 使用亮黄色表示警告
        if levelno >= logging.INFO: return self.INFO                                   # INFO 使用亮绿色表示正常信息
        return self.DEBUG                                                              # 其余默认按 DEBUG 使用亮青色

    def _getLevelTag(self, levelno: int) -> str:                                       # 根据日志级别返回更短更易识别的标签
        if levelno >= logging.CRITICAL: return "FATAL"                                 # 严重错误显示为 FATAL
        if levelno >= logging.ERROR: return "ERROR"                                    # 错误显示为 ERROR
        if levelno >= logging.WARNING: return "WARN"                                   # 警告显示为 WARN
        if levelno >= logging.INFO: return "INFO"                                      # 普通信息显示为 INFO
        return "DEBUG"                                                                 # 调试信息显示为 DEBUG

    def setLevel(self, level: int):                                                    # 动态修改当前 logger 及全部 handler 的日志等级
        self.level = level                                                             # 保存新的日志等级到对象状态
        self.logger.setLevel(level)                                                    # 更新 logger 的最低处理级别
        for handler in self.logger.handlers:                                           # 遍历当前挂载的所有处理器
            handler.setLevel(level)                                                    # 同步更新每个处理器的输出级别

    def addFileHandler(self, filename: str, level: Optional[int] = None, encoding: str = "utf-8"):  # 追加文件输出能力
        handler = self._buildFileHandler(filename, level=level, encoding=encoding)     # 创建文件处理器
        self.logger.addHandler(handler)                                                # 挂到当前 logger 上
        return handler                                                                 # 返回处理器方便调用方后续精细控制

    def addConsoleHandler(self, level: Optional[int] = None):                          # 追加一个新的控制台处理器
        handler = self._buildConsoleHandler(self.level if level is None else level)    # 创建控制台处理器
        self.logger.addHandler(handler)                                                # 挂到当前 logger 上
        return handler                                                                 # 返回该处理器供调用方使用

    def clearHandlers(self):                                                           # 清空当前 logger 的全部处理器
        self.logger.handlers.clear()                                                   # 移除所有输出通道，避免重复打印

    def resetHandlers(self):                                                           # 重置为仅保留一个默认控制台处理器
        self.clearHandlers()                                                           # 先清空当前全部处理器
        self.logger.addHandler(self._buildConsoleHandler(self.level))                  # 再挂回一个默认控制台处理器

    def getLogger(self):                                                               # 获取底层原生 logging.Logger 对象
        return self.logger                                                             # 方便与第三方库或旧代码集成

    # ==================== 输出层 ====================

    def log(self, level: int, message: str, *args, **kwargs):                          # 通用日志输出入口
        self.logger.log(level, message, *args, **kwargs)                               # 直接调用底层 logger.log

    def debug(self, message: str, *args, **kwargs):                                    # 输出 DEBUG 级别日志
        self.logger.debug(message, *args, **kwargs)                                    # 调试阶段最常用

    def info(self, message: str, *args, **kwargs):                                     # 输出 INFO 级别日志
        self.logger.info(message, *args, **kwargs)                                     # 表示正常业务信息

    def warning(self, message: str, *args, **kwargs):                                  # 输出 WARNING 级别日志
        self.logger.warning(message, *args, **kwargs)                                  # 表示潜在风险但程序仍可继续

    def warn(self, message: str, *args, **kwargs):                                     # 提供 warn 别名以兼容旧习惯
        self.logger.warning(message, *args, **kwargs)                                  # 内部统一转发到 warning

    def error(self, message: str, *args, **kwargs):                                    # 输出 ERROR 级别日志
        self.logger.error(message, *args, **kwargs)                                    # 表示当前操作已经失败

    def critical(self, message: str, *args, **kwargs):                                 # 输出 CRITICAL 级别日志
        self.logger.critical(message, *args, **kwargs)                                 # 表示严重错误或系统级故障

    def fatal(self, message: str, *args, **kwargs):                                    # 提供 fatal 别名增强语义表达
        self.logger.critical(message, *args, **kwargs)                                 # fatal 本质上等价于 critical

    def exception(self, message: str, *args, **kwargs):                                # 输出带异常堆栈的 ERROR 日志
        self.logger.exception(message, *args, **kwargs)                                # 只能在 except 块中使用最有价值

    # ==================== 常量区 ====================

    RESET = "\033[0m"                                                                  # ANSI 重置颜色控制码
    DEBUG = "\033[96m"                                                                 # DEBUG 使用亮青色
    INFO = "\033[92m"                                                                  # INFO 使用亮绿色
    WARNING = "\033[93m"                                                               # WARNING 使用亮黄色
    ERROR = "\033[91m"                                                                 # ERROR 使用亮红色
    CRITICAL = "\033[95m"                                                              # CRITICAL 使用亮洋红色
    
    
    
# ==================== 测试入口 ====================

if __name__ == "__main__":
    # 1. 初始化日志器
    # 默认输出到控制台，DEBUG 级别
    log = Log(name="TestApp", level=logging.DEBUG)
    
    print("--- 开始测试控制台彩色输出 ---")
    
    # 2. 测试各级别日志输出
    log.debug("这是一条 DEBUG 信息 (亮青色)")
    log.info("这是一条 INFO 信息 (亮绿色)")
    log.warning("这是一条 WARNING 信息 (亮黄色)")
    log.error("这是一条 ERROR 信息 (亮红色)")
    log.critical("这是一条 CRITICAL 信息 (亮洋红)")
    log.fatal("这是一条 FATAL 信息 (别名，同 CRITICAL)")

    # 3. 测试异常堆栈打印
    print("\n--- 测试异常堆栈打印 ---")
    try:
        result = 10 / 0
    except ZeroDivisionError:
        log.exception("捕获到除零异常")
    
    # 4. 测试文件输出 (文件中不应有颜色码)
    print("\n--- 测试文件输出 (查看当前目录下的 test.log) ---")
    # 添加一个文件处理器，只记录 WARNING 及以上级别
    log.addFileHandler("test.log", level=logging.WARNING)
    
    log.info("这条信息只会出现在控制台，不会写入文件 (级别不够)")
    log.error("这条错误会同时出现在控制台和文件中")
    
    # 5. 测试动态修改日志级别
    print("\n--- 测试动态修改日志级别 ---")
    print(f"当前日志级别: {logging.getLevelName(log.level)}")
    
    log.setLevel(logging.WARNING)
    print("已将日志级别设置为 WARNING")
    
    log.debug("这条 DEBUG 信息将不会显示 (被过滤)")
    log.warning("这条 WARNING 信息会显示")
    
    print("\n--- 测试结束 ---")
