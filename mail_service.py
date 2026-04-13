import logging
import re
import time
import random
import json
import ssl
import http.cookiejar
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Dict, Optional
from curl_cffi import requests as curl_requests


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@dataclass
class EmailResult:
    success: bool
    email: Optional[str] = None
    error: Optional[str] = None
    provider: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


_logger = get_logger("mail")


class MailServiceError(RuntimeError):
    def __init__(self, action, original_error):
        self.action = action
        self.original_error = original_error
        super().__init__(f"{action}失败: {original_error}")


class MailService:
    def __init__(
        self,
        api_url="https://mail.chatgpt.org.uk",
        verify_ssl=False,
        prefer_urllib=True,
        allow_env_proxy=True,
    ):
        self.api_url = api_url
        self.verify_ssl = verify_ssl
        self.prefer_urllib = prefer_urllib
        self.allow_env_proxy = allow_env_proxy
        self.inbox_token = None
        self.inbox_token_expires_at = 0
        self.current_email = None
        self._cookie_jar = http.cookiejar.CookieJar()
        self._ssl_context = (
            ssl.create_default_context()
            if self.verify_ssl
            else ssl._create_unverified_context()
        )
        self._openers = []
        if self.allow_env_proxy:
            self._openers.append(
                urllib.request.build_opener(
                    urllib.request.ProxyHandler(),
                    urllib.request.HTTPSHandler(context=self._ssl_context),
                    urllib.request.HTTPCookieProcessor(self._cookie_jar),
                )
            )
        self._openers.append(
            urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=self._ssl_context),
                urllib.request.HTTPCookieProcessor(self._cookie_jar),
            )
        )
        self.http = curl_requests.Session(verify=self.verify_ssl)
        self.headers = {
            "content-type": "application/json",
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Referer": "https://mail.chatgpt.org.uk/4c5882fb@ghelper.icu",
        }

    def _is_tls_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return (
            "tls connect error" in msg
            or "openssl_internal" in msg
            or "ssl" in msg
            or "curl: (35)" in msg
        )

    def _extract_auth(self, data):
        if not isinstance(data, dict):
            return
        auth = data.get("auth") or {}
        token = auth.get("token")
        email = auth.get("email")
        expires_at = auth.get("expires_at") or auth.get("expiresAt")
        if token:
            self.inbox_token = token
        if email:
            self.current_email = email
        if expires_at:
            try:
                self.inbox_token_expires_at = int(expires_at)
            except (TypeError, ValueError):
                self.inbox_token_expires_at = 0

    def _parse_browser_auth(self, html):
        if not html:
            return None
        m = re.search(r"window\.__BROWSER_AUTH\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not m:
            return None
        auth_obj = json.loads(m.group(1))
        if not isinstance(auth_obj, dict):
            return None
        return auth_obj

    def _browser_auth_needs_refresh(self, email=None):
        if not self.inbox_token:
            return True
        if (
            self.inbox_token_expires_at
            and self.inbox_token_expires_at - int(time.time()) <= 120
        ):
            return True
        return bool(
            email and self.current_email and email.lower() != self.current_email.lower()
        )

    def _bootstrap_browser_session(self):
        try:
            html = self._urllib_request_html("/")
            auth_obj = self._parse_browser_auth(html)
            if not auth_obj:
                return
            self._extract_auth({"auth": auth_obj})
        except Exception:
            return

    def _ensure_browser_session(self, email=None, force=False):
        if force or self._browser_auth_needs_refresh(email=email):
            self._bootstrap_browser_session()
        if email and self.current_email and email.lower() != self.current_email.lower():
            self._issue_inbox_token(email)

    def _is_browser_session_error(self, err: Exception) -> bool:
        return "browser session required" in str(err).lower()

    def _build_headers(self, email=None):
        headers = dict(self.headers)
        if self.inbox_token:
            headers["x-inbox-token"] = self.inbox_token
        if email:
            headers["Referer"] = f"{self.api_url}/zh/{email}"
        return headers

    def _urllib_request_html(self, path):
        url = f"{self.api_url}{path}"
        req = urllib.request.Request(
            url, headers=self._build_headers(email=self.current_email), method="GET"
        )
        last_err = None
        for opener in self._openers:
            try:
                with opener.open(req, timeout=20) as resp:
                    return resp.read().decode("utf-8", errors="ignore")
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        raise Exception("html request failed")

    def _urllib_request_json(
        self, method, path, params=None, json_body=None, email=None
    ):
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        url = f"{self.api_url}{path}{query}"
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        headers = self._build_headers(email=email)
        req = urllib.request.Request(url, headers=headers, data=body, method=method)

        last_err = None
        for opener in self._openers:
            try:
                with opener.open(req, timeout=20) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    data = json.loads(raw)
                    self._extract_auth(data)
                    return data
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")[:300]
                except Exception:
                    pass
                raise Exception(f"HTTP {e.code} {path} {body}")
            except Exception as e:
                last_err = e

        if last_err is None:
            raise Exception("urllib request failed without detailed error")

        if self.api_url.startswith("https://") and self._is_tls_error(last_err):
            http_url = f"http://{self.api_url[len('https://') :]}{path}{query}"
            req = urllib.request.Request(
                http_url, headers=headers, data=body, method=method
            )
            for opener in self._openers:
                try:
                    with opener.open(req, timeout=20) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                        self._extract_auth(data)
                        return data
                except Exception:
                    continue
        raise last_err

    def _issue_inbox_token(self, email):
        if not email:
            return
        try:
            self._ensure_browser_session(force=not self.inbox_token)
            data = self._urllib_request_json(
                "POST",
                "/api/inbox-token",
                json_body={"email": email},
                email=email,
            )
            self._extract_auth(data)
        except Exception as e:
            self.log(f"Issue inbox token failed: {e}")

    def log(self, msg, level: int = logging.INFO):
        _logger.log(level, msg)

    def _create_temp_email_raw(self):
        """申请临时邮箱"""
        self.log("正在申请邮箱...")
        attempts = 3
        for i in range(1, attempts + 1):
            try:
                data = None
                if self.prefer_urllib:
                    self._ensure_browser_session()
                    # HAR 显示 GET/POST 都可用，依次尝试
                    method_errors = []
                    for method, payload in [("GET", None), ("POST", {})]:
                        try:
                            data = self._urllib_request_json(
                                method,
                                "/api/generate-email",
                                json_body=payload,
                                email=self.current_email,
                            )
                            if data.get("success"):
                                break
                            method_errors.append(f"{method}: {data}")
                        except Exception as e:
                            if self._is_browser_session_error(e):
                                self._ensure_browser_session(force=True)
                                try:
                                    data = self._urllib_request_json(
                                        method,
                                        "/api/generate-email",
                                        json_body=payload,
                                        email=self.current_email,
                                    )
                                    if data.get("success"):
                                        break
                                except Exception as retry_err:
                                    method_errors.append(f"{method} retry: {retry_err}")
                            method_errors.append(f"{method}: {e}")
                            continue
                    if not data:
                        raise Exception(
                            "generate-email GET/POST 均失败 | "
                            + " | ".join(method_errors)
                        )
                else:
                    try:
                        r = self.http.get(
                            f"{self.api_url}/api/generate-email",
                            headers=self._build_headers(email=self.current_email),
                            timeout=20,
                        )
                        data = r.json()
                        self._extract_auth(data)
                    except Exception as e:
                        if not self._is_tls_error(e):
                            raise
                        self.log(f"curl TLS 失败，切换 urllib 兜底: {e}")
                        data = self._urllib_request_json(
                            "GET", "/api/generate-email", email=self.current_email
                        )

                if data.get("success"):
                    email = data["data"]["email"]
                    self.current_email = email
                    self._issue_inbox_token(email)
                    self.log(f"成功申请邮箱: {email}")
                    return email
                raise Exception(f"API 返回失败: {data}")
            except Exception as e:
                self.log(f"邮箱申请异常: {e} (attempt {i}/{attempts})")
                if i < attempts:
                    time.sleep(0.8 + random.random())
        return None

    def request_email(self):
        """
        对外接口：申请临时邮箱并返回统一结构
        """
        try:
            email = self._create_temp_email_raw()
            if not email:
                return EmailResult(
                    success=False,
                    email=None,
                    error="临时邮箱申请失败 (重试后仍无效)",
                    provider="temp",
                    raw={"api_url": self.api_url},
                )
            return EmailResult(
                success=True,
                email=email,
                provider="temp",
                raw={"api_url": self.api_url},
            )
        except Exception as e:
            self.log(f"request_email 异常: {e}")
            return EmailResult(
                success=False,
                email=None,
                error=str(e),
                provider="temp",
                raw={
                    "api_url": self.api_url,
                    "exception": str(e),
                    "exception_type": type(e).__name__,
                },
            )

    def _get_emails(self, email):
        """获取邮件列表"""
        try:
            self._ensure_browser_session(email=email)
            if self.current_email != email or not self.inbox_token:
                self._issue_inbox_token(email)
            if self.prefer_urllib:
                data = self._urllib_request_json(
                    "GET", "/api/emails", params={"email": email}, email=email
                )
            else:
                try:
                    r = self.http.get(
                        f"{self.api_url}/api/emails",
                        params={"email": email},
                        headers=self._build_headers(email=email),
                        timeout=20,
                    )
                    data = r.json()
                    self._extract_auth(data)
                except Exception as e:
                    if not self._is_tls_error(e):
                        raise
                    self.log(f"curl TLS 失败，切换 urllib 兜底: {e}")
                    data = self._urllib_request_json(
                        "GET", "/api/emails", params={"email": email}, email=email
                    )
            return data.get("data", {}).get("emails", [])
        except Exception as e:
            self.log(f"获取邮件列表异常: {e}")
            return []

    def _get_latest_email_content(self, email):
        """获取最新一封邮件的内容（已清洗HTML）"""
        emails = self._get_emails(email)
        if emails:
            content = emails[0].get("content") or emails[0].get("html_content") or ""
            # 清洗HTML
            text_content = re.sub("<[^<]+?>", " ", content)
            return text_content.strip()
        return None

    def _get_latest_email_html(self, email):
        """获取最新一封邮件的原始HTML内容"""
        emails = self._get_emails(email)
        if emails:
            return emails[0].get("html_content") or emails[0].get("content") or ""
        return None

    def _get_content_by_regex(self, email, regex_pattern, timeout=60, sleep_interval=3):
        """通过正则从邮件中提取内容"""
        # self.log(f"等待 {email} 的匹配内容 (pattern: {regex_pattern})...")
        start = time.time()
        seen_contents = set()

        while time.time() - start < timeout:
            text_content = self._get_latest_email_content(email)
            if text_content and text_content not in seen_contents:
                seen_contents.add(text_content)
                match = re.search(
                    regex_pattern, text_content, re.IGNORECASE | re.DOTALL
                )
                if match:
                    # 如果有分组则返回分组内容，否则返回整个匹配
                    result = match.group(1) if match.groups() else match.group(0)
                    self.log(f"正则匹配成功: {result}")
                    return result

            time.sleep(sleep_interval)

        # self.log("等待正则匹配超时")
        return None

    def _poll_code_raw(self, email, timeout=60, sleep_interval=3):
        self.log(f"等待 {email} 的验证码 (最多 {timeout}s)")

        href_pattern = r"href=[\"']([^\"']*https://chat\.qwen\.ai/api/v1/auths/activate\s*[^\"']*)[\"']"

        start = time.time()
        seen_contents = set()

        while time.time() - start < timeout:
            html_content = self._get_latest_email_html(email)
            if html_content and html_content not in seen_contents:
                seen_contents.add(html_content)
                match = re.search(href_pattern, html_content, re.IGNORECASE)
                if match:
                    href = match.group(1).strip()
                    self.log(f"验证码链接匹配成功: {href}")
                    return href
            time.sleep(sleep_interval)

        self.log("等待验证码超时")
        return None

    def poll_code(self, email, timeout=60, sleep_interval=3):
        """
        对外接口：轮询验证码
        """
        try:
            return self._poll_code_raw(
                email, timeout=timeout, sleep_interval=sleep_interval
            )
        except Exception as e:
            self.log(f"poll_code 异常: {e}")
            return None


if __name__ == "__main__":
    setup_logging()
    service = MailService()
    _logger.info("正在测试申请临时邮箱...")
    res = service.request_email()
    if res.success:
        _logger.info("申请成功: %s", res.email)
    else:
        _logger.error("申请失败: %s", res.error)
