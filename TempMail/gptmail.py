import re                                                                              # 正则表达式库用于从文本中提取验证码和链接
import time                                                                            # 时间库用于轮询等待和超时控制
import json                                                                            # JSON 库用于解析接口返回的数据
import ssl                                                                             # SSL 库用于控制 HTTPS 校验行为
import hashlib                                                                         # 哈希库用于给邮件生成稳定指纹避免重复处理
import urllib.parse                                                                    # URL 编码工具用于拼接查询参数
import urllib.request                                                                  # urllib 请求库用于模拟浏览器请求
import urllib.error                                                                    # urllib 异常库用于捕获 HTTP 错误
import http.cookiejar                                                                  # CookieJar 用于保存浏览器会话 cookie

"""
# 拿验证码
with TempMail() as mail:
    print(mail.generateEmail())                                                        # 生成临时邮箱地址
    mail.getInbox()                                                                    # 建立基线避免把历史邮件当成新邮件
    # ... 去注册页面填这个邮箱，点发送验证码 ...
    print(mail.getCode())                                                              # 自动等待并提取验证码

# 拿激活链接
with TempMail() as mail:
    mail.generateEmail()                                                               # 先生成邮箱
    mail.getInbox()                                                                    # 建立基线
    print(mail.getLink(keyword="activate"))                                            # 自动等待并提取包含 activate 的链接

# 手动翻邮件
with TempMail() as mail:
    mail.generateEmail()                                                               # 先生成邮箱
    time.sleep(10)                                                                     # 等一会让邮件送达
    for item in mail.getInbox():                                                       # 获取新邮件列表
        body = mail.readMessage(item.get("mailID"))                                    # 通过 mailID 读取正文并标记已读
        print(mail.findCode(item.get("subject", "") + "\\n" + body))                   # 从主题+正文中找验证码
        print(mail.findLink(body))                                                     # 从正文中找链接
"""


class TempMail:
    """
    TempMail 临时邮箱工具类

    功能说明：
        提供临时邮箱生成、收件箱管理和邮件内容提取功能，主要用于自动化测试中的
        验证码/激活链接获取场景。

    主要方法：
        - generateEmail(): 生成新的临时邮箱地址
        - getInbox(): 获取收件箱邮件列表
        - getCode(): 自动等待并提取邮件中的验证码
        - getLink(keyword): 自动等待并提取包含指定关键词的链接
        - readMessage(mailID): 读取指定邮件的完整内容
    """


    def __init__(self, apiUrl="https://mail.chatgpt.org.uk", verifySsl=False):
        self.apiUrl = apiUrl                                                           # 邮箱服务的根地址
        self.verifySsl = verifySsl                                                     # 是否校验 HTTPS 证书
        self.token = ""                                                                # 当前邮箱对应的 x-inbox-token
        self.tokenExpiresAt = 0                                                        # token 过期时间戳
        self.email = ""                                                                # 当前生成的临时邮箱地址
        self.currentEmail = ""                                                         # 这里直接补上旧版 mail_service.py 的 current_email 语义，后续所有鉴权流程都围绕它走
        self.defaultRefererEmail = "4c5882fb@ghelper.icu"                            # 旧版 mail_service.py 用这个默认邮箱页做 Referer，服务端更容易放行会话初始化
        self.browserAuth = {}                                                          # 从首页 HTML 中提取出的浏览器认证对象
        self.seenIds = set()                                                           # 已经读过的邮件编号集合
        self.baselineIds = set()                                                       # 生成邮箱后首次查询时建立的历史邮件基线集合
        self.mailMap = {}                                                              # mailID 到邮件对象的映射，便于 readMessage 通过 ID 读取
        self.cookieJar = http.cookiejar.CookieJar()                                    # 保存会话 cookie 的容器
        self.sslContext = (                                                            # 创建 SSL 上下文对象
            ssl.create_default_context() if self.verifySsl else ssl._create_unverified_context()
        )
        self.openers = [                                                               # 同时准备“使用系统代理”和“禁用代理”两套 opener
            urllib.request.build_opener(
                urllib.request.ProxyHandler(),
                urllib.request.HTTPSHandler(context=self.sslContext),
                urllib.request.HTTPCookieProcessor(self.cookieJar),
            ),
            urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=self.sslContext),
                urllib.request.HTTPCookieProcessor(self.cookieJar),
            ),
        ]
        self.headers = {
            "content-type": "application/json",                                      # 这里直接照搬旧版 mail_service.py 的 headers 结构，避免 header 细节差异继续影响鉴权
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Referer": f"{self.apiUrl}/{self.defaultRefererEmail}",
        }

    # ==================== 算子层 ====================

    def buildHeaders(self, email=None):                                                # 构造一组尽量接近浏览器的请求头
        headers = dict(self.headers)                                                   # 直接从旧版 mail_service.py 风格的 headers 模板复制，避免字段大小写或默认值漂移
        if self.token: headers["x-inbox-token"] = self.token                          # 如果已有 inbox token 就带上给服务端校验
        if email: headers["Referer"] = f"{self.apiUrl}/zh/{email}"                    # 只有真正访问某个邮箱接口时，才切换到该邮箱页 Referer
        return headers                                                                 # 返回完整请求头字典

    def extractAuth(self, data):                                                       # 从接口返回或浏览器对象中提取 auth 信息
        if not isinstance(data, dict): return                                          # 不是字典时直接忽略
        auth = data.get("auth", data)                                                  # 有些场景直接传 auth 对象，有些场景外层包一层 auth
        if not isinstance(auth, dict): return                                          # 不是字典时直接忽略

        token = auth.get("token", "")                                                  # 取 token
        email = auth.get("email", "")                                                  # 取邮箱
        expiresAt = auth.get("expires_at", auth.get("expiresAt", 0))                   # 同时兼容 expires_at 和 expiresAt

        if token: self.token = str(token)                                              # 保存 token
        if email:
            self.email = str(email)                                                    # 保存邮箱
            self.currentEmail = str(email)                                             # 同步保存 currentEmail，让行为和旧版 mail_service.py 对齐
        try: self.tokenExpiresAt = int(expiresAt or 0)                                 # 保存过期时间戳
        except Exception: self.tokenExpiresAt = 0                                      # 解析失败则归零

    def parseBrowserAuth(self, html):                                                  # 从首页 HTML 里解析 window.__BROWSER_AUTH
        if not html: return None                                                       # 空 HTML 直接返回空
        match = re.search(r"window\.__BROWSER_AUTH\s*=\s*(\{.*?\});", html, re.DOTALL) # 匹配浏览器认证对象
        if not match: return None                                                      # 没匹配到则返回空
        try: authObj = json.loads(match.group(1))                                      # 把对象文本解析成字典
        except Exception: return None                                                  # 解析失败时返回空
        if not isinstance(authObj, dict): return None                                  # 不是字典时返回空
        return authObj                                                                 # 返回认证对象

    def requestHtml(self, path="/", email=None):                                       # 请求 HTML 页面并返回文本
        url = self.apiUrl + path                                                       # 拼接完整 URL
        request = urllib.request.Request(                                              # 构造 GET 请求对象
            url=url,
            headers=self.buildHeaders(email=(email or self.currentEmail)),             # 这里严格对齐旧版 mail_service.py，访问首页时走 currentEmail 上下文
            method="GET",
        )

        lastError = None                                                               # 记录最后一次请求异常，便于最终抛出
        for opener in self.openers:                                                    # 依次尝试系统代理和禁用代理两条路径
            try:
                with opener.open(request, timeout=20) as response:
                    return response.read().decode("utf-8", errors="ignore")
            except Exception as error:
                lastError = error
                continue

        if lastError:
            raise lastError

        raise RuntimeError("requestHtml failed")                                      # 理论上不会走到这里，保底抛错

    def bootstrapBrowserSession(self):                                                 # 访问首页并提取浏览器会话认证信息
        html = self.requestHtml("/")                                                   # 请求首页 HTML
        authObj = self.parseBrowserAuth(html)                                          # 解析 window.__BROWSER_AUTH
        if not authObj: return None                                                    # 没拿到认证对象时返回空
        self.browserAuth = authObj                                                     # 保存浏览器认证对象
        self.extractAuth(authObj)                                                      # 从对象中提取 token / email / 过期时间
        return authObj                                                                 # 返回原始对象

    def browserAuthNeedsRefresh(self, email=None):                                     # 判断浏览器认证是否需要刷新
        if not self.token: return True                                                 # 没有 token 时一定需要刷新
        if self.tokenExpiresAt and self.tokenExpiresAt - int(time.time()) <= 120:      # 剩余有效期不足 120 秒时提前刷新
            return True
        if email and self.currentEmail and email.lower() != self.currentEmail.lower(): # 这里改成和旧版 mail_service.py 一样比较 currentEmail，而不是比较 self.email
            return True
        return False                                                                   # 其他情况可继续复用当前认证

    def ensureBrowserSession(self, email=None, force=False):                           # 确保浏览器会话和 inbox token 可用
        if force or self.browserAuthNeedsRefresh(email=email):                         # 强制刷新或当前认证过期时
            self.bootstrapBrowserSession()                                             # 重新访问首页获取浏览器认证
        if email and self.currentEmail and email.lower() != self.currentEmail.lower(): # 这里也改成和旧版 mail_service.py 完全一致的 currentEmail 判断
            self.issueInboxToken(email)                                                # 为目标邮箱重新签发 inbox token

    def sendRequest(self, method, path, params=None, body=None, email=None):           # 发送 JSON 请求到服务端
        query = ""                                                                     # 初始化查询串
        if params: query = "?" + urllib.parse.urlencode(params)                        # 存在查询参数时拼接到 URL 后面
        url = self.apiUrl + path + query                                               # 得到完整请求地址

        rawBody = None                                                                 # 默认没有请求体
        if body is not None: rawBody = json.dumps(body).encode("utf-8")                # 请求体不为空时按 JSON 编码

        request = urllib.request.Request(                                              # 构造请求对象
            url=url,
            headers=self.buildHeaders(email=email),                                    # 带上浏览器头和 inbox token
            data=rawBody,                                                              # 请求体字节流
            method=method,                                                             # GET / POST
        )

        lastError = None                                                               # 保存最后一次失败原因，避免静默返回空
        for opener in self.openers:                                                    # 先走系统代理，再走禁用代理兜底
            try:
                with opener.open(request, timeout=20) as response:
                    text = response.read().decode("utf-8", errors="ignore")
                    data = json.loads(text)
                    self.extractAuth(data)
                    return data
            except urllib.error.HTTPError as error:
                text = ""
                try: text = error.read().decode("utf-8", errors="ignore")
                except Exception: pass
                raise RuntimeError(f"HTTP {error.code} {path} {text[:300]}")
            except Exception as error:
                lastError = error
                continue

        if lastError:
            raise lastError

        raise RuntimeError(f"sendRequest failed: {path}")                             # 保底抛错，避免上层误判成“没有新邮件”

    def issueInboxToken(self, email):                                                  # 为某个邮箱签发或刷新 x-inbox-token
        if not email: return None                                                      # 没有邮箱时无法签发 token
        self.ensureBrowserSession(force=not bool(self.token))                          # 先像旧版 mail_service.py 一样初始化浏览器会话，不能提前污染 currentEmail
        data = self.sendRequest(                                                       # 调用 inbox-token 接口
            "POST",
            "/api/inbox-token",
            body={"email": email},                                                     # 官方示例明确要求传 email
            email=email,
        )
        self.extractAuth(data)                                                         # 提取并保存新的 token
        self.email = str(email)                                                        # 请求成功后再把当前邮箱锁定为目标邮箱
        self.currentEmail = str(email)                                                 # 这里补上旧版 current_email 的同步赋值，这一步之前缺失会导致后续会话判断一直跑偏
        return data                                                                    # 返回原始响应

    def fetchEmailAddress(self):                                                       # 请求服务端生成一个新的临时邮箱地址
        self.ensureBrowserSession(force=True)                                          # 先确保浏览器会话存在，否则 generate-email 可能 401

        errors = []                                                                    # 用于记录 GET / POST 两种尝试的错误
        attempts = [("GET", None), ("POST", {})]                                       # 官方示例说明 GET/POST 都可能可用，按顺序尝试

        for method, body in attempts:                                                  # 依次尝试两种方式
            try:
                data = self.sendRequest(method, "/api/generate-email", body=body, email=self.email)  # 调用生成邮箱接口
                if data.get("success"):                                                # success=true 说明生成成功
                    email = str(data.get("data", {}).get("email", ""))                 # 取出新邮箱地址
                    if email: self.email = email                                       # 保存当前邮箱
                    self.issueInboxToken(self.email)                                   # 为新邮箱立即签发 inbox token
                    return data                                                        # 成功后直接返回
                errors.append(f"{method}: {data}")                                     # 接口返回失败时记录下来
            except Exception as error:
                errors.append(f"{method}: {error}")                                    # 请求抛异常时记录错误
                continue                                                               # 继续尝试下一种方法

        raise RuntimeError("generate-email failed | " + " | ".join(errors))            # 两种方式都失败时抛出综合异常

    def fetchEmailList(self, email):                                                   # 拉取指定邮箱的邮件列表
        if not email: return []                                                        # 没有邮箱时直接返回空列表
        self.ensureBrowserSession(email=email)                                         # 确保浏览器会话和该邮箱的 token 可用
        if self.currentEmail.lower() != email.lower() or not self.token:               # 这里必须像旧版 mail_service.py 一样比较 currentEmail，而不是比较 self.email
            self.issueInboxToken(email)                                                # 为目标邮箱重新签发 token

        data = self.sendRequest(                                                       # 请求邮件列表接口
            "GET",
            "/api/emails",
            params={"email": email},                                                   # 按官方示例通过查询参数传 email
            email=email,
        )
        messageList = data.get("data", {}).get("emails", [])                           # 从 data.emails 中取出邮件数组
        return messageList                                                             # 返回原始邮件列表

    def buildMailFingerprint(self, item):                                              # 为一封邮件生成稳定唯一的本地 mailID
        if not isinstance(item, dict): return ""                                       # 非字典数据直接返回空字符串

        rawId = str(item.get("id", item.get("_id", "")))                               # 优先使用服务端可能提供的 id
        subject = str(item.get("subject", ""))                                         # 主题
        sender = str(item.get("from") or item.get("from_address") or item.get("sender", ""))  # 发件人要兼容 from_address，旧邮件接口经常走这个字段
        date = str(item.get("date") or item.get("created_at") or item.get("createdAt", ""))    # 时间
        body = str(item.get("body") or item.get("content") or item.get("html_content", ""))    # 这里必须用 or 链，避免 content="" 时吞掉 html_content

        raw = "\n".join([rawId, subject, sender, date, body])                          # 把关键字段拼成原始文本
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()          # 计算 SHA1 作为 mailID

    def normalizeMessage(self, item):                                                  # 统一服务端邮件结构为 TempMail 内部结构
        if not isinstance(item, dict): return None                                     # 格式不对时返回空

        subject = str(item.get("subject", ""))                                         # 标准化主题字段
        sender = str(item.get("from") or item.get("from_address") or item.get("sender", ""))  # 标准化发件人字段，同时兼容 from_address
        date = str(item.get("date") or item.get("created_at") or item.get("createdAt", ""))    # 标准化时间字段
        body = item.get("content") or item.get("html_content") or item.get("body", "")           # 这里必须使用 or 链，才能在 content 为空串时继续落到 html_content
        if body is None: body = ""                                                     # 避免 None 干扰后续处理
        body = str(body)                                                               # 保证正文最终为字符串

        normalized = {                                                                 # 构造统一邮件对象
            "subject": subject,
            "from": sender,
            "date": date,
            "body": body,
        }
        normalized["mailID"] = self.buildMailFingerprint(normalized)                   # 生成本地 mailID 指纹
        return normalized                                                              # 返回标准结构邮件对象

    def findCode(self, text):                                                          # 从文本中找出 6 位数字验证码
        match = re.search(r"\b\d{6}\b", text)                                         # 匹配独立的 6 位连续数字
        if not match: return None                                                      # 没找到就返回空
        return match.group(0)                                                          # 找到了就返回这 6 位数字

    def findLink(self, text, keyword=""):                                              # 从文本中找出链接地址
        allLinks = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)                  # 提取所有 http/https 开头的链接
        if keyword: allLinks = [x for x in allLinks if keyword in x]                   # 有关键词时只保留包含它的链接
        if not allLinks: return None                                                   # 没找到就返回空
        return allLinks[0]                                                             # 返回第一个匹配的链接

    # ==================== 编排层 ====================

    def generateEmail(self):                                                           # 生成一个新的临时邮箱地址
        self.fetchEmailAddress()                                                       # 调用底层接口生成邮箱并更新对象状态
        self.seenIds = set()                                                           # 清空已读记录避免新邮箱被旧状态干扰
        self.baselineIds = set()                                                       # 清空基线记录让新邮箱从零开始
        self.mailMap = {}                                                              # 清空邮件映射缓存
        return self.email                                                              # 返回邮箱地址

    def listAll(self):                                                                 # 列出当前邮箱的全部邮件包括已读和基线旧邮件
        if not self.email: return []                                                   # 没有邮箱时返回空列表
        try: rawList = self.fetchEmailList(self.email)                                 # 请求完整邮件列表
        except Exception: return []                                                    # 请求失败时返回空列表

        normalizedList = []                                                            # 收集标准结构邮件列表
        for item in rawList:                                                           # 遍历服务端原始邮件
            mail = self.normalizeMessage(item)                                         # 规范化邮件结构
            if not mail: continue                                                      # 格式异常时跳过
            self.mailMap[mail["mailID"]] = mail                                        # 写入 mailID 到邮件对象映射
            normalizedList.append(mail)                                                # 收集到结果列表中
        return normalizedList                                                          # 返回完整标准邮件列表

    def getLatestMail(self):                                                           # 获取最新一封邮件，不区分新旧，适合做调试或读取已存在邮件
        allMailList = self.listAll()                                                   # 直接读取完整邮件列表，避免被基线机制过滤掉旧邮件
        if not allMailList: return None                                                # 没有邮件时返回空
        return allMailList[0]                                                          # 服务端返回通常已按时间倒序排列，第一封就是最新邮件

    def getInbox(self):                                                                # 获取当前未读的新邮件列表
        if not self.email: return []                                                   # 没有邮箱时直接返回空列表

        try: messageList = self.fetchEmailList(self.email)                             # 拉取当前邮箱完整邮件列表
        except Exception as error:
            raise RuntimeError(f"getInbox failed: {error}")                           # 不再静默吃掉异常，避免把请求失败误判成“没有新邮件”

        normalizedList = []                                                            # 用于收集标准化后的邮件列表
        for item in messageList:                                                       # 遍历原始邮件
            mail = self.normalizeMessage(item)                                         # 统一成内部结构
            if not mail: continue                                                      # 格式异常时跳过
            self.mailMap[mail["mailID"]] = mail                                        # 写入映射以便 readMessage 读取
            normalizedList.append(mail)                                                # 收集标准邮件

        if not self.baselineIds:                                                       # 基线为空说明这是第一次调用
            for item in normalizedList:                                                # 遍历当前所有已有邮件
                mailId = item.get("mailID", "")                                        # 取出 mailID
                if mailId: self.baselineIds.add(mailId)                                # 记入基线集合用于排除历史邮件
            return []                                                                  # 第一次调用只建基线不返回邮件

        skipIds = self.seenIds | self.baselineIds                                      # 合并已读和基线得到需要跳过的编号集合
        freshList = []                                                                 # 收集真正的新邮件
        for item in normalizedList:                                                    # 遍历标准邮件列表
            mailId = item.get("mailID", "")                                            # 取出 mailID
            if not mailId: continue                                                    # 没有编号就跳过
            if mailId in skipIds: continue                                             # 已读或历史邮件都跳过
            freshList.append(item)                                                     # 剩下的就是新邮件

        return freshList                                                               # 返回新邮件列表

    def readMessage(self, messageId):                                                  # 读取一封邮件正文并标记已读
        if not messageId: return ""                                                    # 没有 messageId 时返回空文本
        item = self.mailMap.get(str(messageId))                                        # 从映射中取出对应邮件对象
        if not item: return ""                                                         # 找不到邮件时返回空文本
        self.seenIds.add(str(messageId))                                               # 标记为已读避免重复处理
        return str(item.get("body", ""))                                               # 返回正文内容

    def waitNewMail(self, timeoutSeconds=60, pollSeconds=2):                           # 等待一封新邮件到达
        startTime = time.time()                                                        # 记录开始时间
        seenBodySet = set()                                                            # 记录已经看过的正文，兼容有些服务端不稳定返回 mail id

        while time.time() - startTime < timeoutSeconds:                                # 在超时时间内循环
            freshList = self.getInbox()                                                # 优先走基于基线的新邮件检测
            if freshList: return freshList[0]                                          # 有新邮件就返回第一封

            allMailList = self.listAll()                                               # 如果新邮件检测没命中，再回退到全量轮询
            for item in allMailList:
                mailId = str(item.get("mailID", ""))
                bodyText = str(item.get("body", ""))

                if mailId and mailId in self.baselineIds:
                    continue

                if bodyText and bodyText in seenBodySet:
                    continue

                if bodyText:
                    seenBodySet.add(bodyText)

                if mailId:
                    self.mailMap[mailId] = item
                    return item

            time.sleep(pollSeconds)                                                    # 没有就等待后继续轮询
        return None                                                                    # 超时了返回空

    def getCode(self, timeoutSeconds=60, pollSeconds=2):                               # 等新邮件并自动提取验证码
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                           # 等待一封新邮件
        if not mail: return None                                                       # 没等到就返回空

        body = self.readMessage(mail.get("mailID", ""))                                # 读取正文并标记已读
        subject = str(mail.get("subject", ""))                                         # 取出主题辅助匹配验证码

        code = self.findCode(subject)                                                  # 先从主题中找验证码
        if code: return code                                                           # 找到就直接返回

        code = self.findCode(body)                                                     # 主题中没有再从正文中找
        if code: return code                                                           # 找到就返回

        return self.findCode(subject + "\n" + body)                                    # 最后把主题和正文拼起来再找一次

    def getLink(self, keyword="", timeoutSeconds=60, pollSeconds=2):                   # 等新邮件并自动提取链接
        mail = self.waitNewMail(timeoutSeconds, pollSeconds)                           # 等待一封新邮件
        if not mail: return None                                                       # 没等到就返回空

        body = self.readMessage(mail.get("mailID", ""))                                # 读取正文并标记已读
        link = self.findLink(body, keyword)                                            # 从正文中找链接
        if link: return link                                                           # 找到就直接返回

        subject = str(mail.get("subject", ""))                                         # 备用地取主题
        return self.findLink(subject, keyword)                                         # 极少数场景下主题里也可能有链接

    def clearMarks(self):                                                              # 清空所有已读和基线标记
        self.seenIds = set()                                                           # 重置已读集合
        self.baselineIds = set()                                                       # 重置基线集合

    def close(self):                                                                   # 关闭资源
        pass                                                                           # urllib opener 无需显式关闭，保留该方法用于兼容原接口

    def __enter__(self): return self                                                   # 进入 with 语句时返回自身
    def __exit__(self, *args): self.close()                                            # 退出 with 语句时自动清理
    
    
if __name__ == "__main__":
    testEmail = "ecoleman474@dcheduc.shop"

    print(f"开始测试邮箱: {testEmail}")

    with TempMail() as mail:
        mail.email = testEmail

        print("\n[1] 拉取完整邮件列表")
        try:
            allMailList = mail.listAll()
            print(f"listAll 返回数量: {len(allMailList)}")
            for index, item in enumerate(allMailList[:5], start=1):
                print(f"第 {index} 封主题: {item.get('subject', '')}")
                print(f"第 {index} 封发件人: {item.get('from', '')}")
                print(f"第 {index} 封时间: {item.get('date', '')}")
                print(f"第 {index} 封 mailID: {item.get('mailID', '')}")
                print("-" * 60)
        except Exception as error:
            print(f"listAll 测试失败: {error}")

        print("\n[2] 建立基线并测试收件箱新邮件逻辑")
        try:
            firstInboxList = mail.getInbox()
            print(f"第一次 getInbox 返回数量: {len(firstInboxList)}")

            secondInboxList = mail.getInbox()
            print(f"第二次 getInbox 返回数量: {len(secondInboxList)}")
            for index, item in enumerate(secondInboxList[:5], start=1):
                print(f"新邮件 {index} 主题: {item.get('subject', '')}")
        except Exception as error:
            print(f"getInbox 测试失败: {error}")

        print("\n[3] 读取最新邮件正文并尝试提取验证码")
        try:
            latestMail = mail.getLatestMail()
            if not latestMail:
                print("当前邮箱没有检测到任何邮件")
            else:
                latestMailId = str(latestMail.get("mailID", ""))
                latestBody = mail.readMessage(latestMailId)
                mergedText = str(latestMail.get("subject", "")) + "\n" + latestBody

                print(f"最新邮件主题: {latestMail.get('subject', '')}")
                print(f"最新邮件正文前 1200 字符:\n{latestBody[:1200]}")
                print(f"findCode 结果: {mail.findCode(mergedText)}")
        except Exception as error:
            print(f"读取正文测试失败: {error}")

        print("\n[4] 直接读取旧邮件测试")
        try:
            latestMail = mail.getLatestMail()
            if latestMail:
                print(f"getLatestMail 检测到邮件主题: {latestMail.get('subject', '')}")
            else:
                print("getLatestMail 没有检测到邮件")
        except Exception as error:
            print(f"getLatestMail 测试失败: {error}")