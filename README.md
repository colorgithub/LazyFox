<div align="center">

# 🦊 LazyFox

### 懒惰不是罪，自动化才是美。

**一个专为浏览器自动化与逆向场景打造的 Python 极速开发脚手架**

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](LICENSE)


</div>

---

## 📖 简介

> "Hey! LazyFox! 是时候让代码替你打工了。"

这是一个为了解决“每次逆向都要从头搭环境”的痛点而生的模板仓库。它集成了浏览器自动化、网络请求伪装、临时邮箱接收等逆向 PoC 开发中最高频使用的工具，让你专注于核心逻辑，而不是环境配置。


## 🛠️ 内置能力

- **🎨 彩色日志系统** (`tools/log.py`)
  - 告别黑白控制台，支持高亮、分级输出，调试体验 UP UP。
- **📬 临时邮箱模块** (`TempMail/`)
  - `emailnator.py`: 临时 Gmail 邮箱服务。
  - `etempmail.py`:  免费 Edu / .COM 临时邮箱服务。
  - `gptmail.py`: 具备多个域名的临时邮箱服务。
- **🚀 核心依赖** (`requirements.txt`)
  - 预置 **Camoufox**、**Playwright**（浏览器自动化）
  - 预置 **httpx**、**curl_cffi**（高隐匿 HTTP 请求）

## 🎯 适用场景

如果你正在做以下事情，LazyFox 可能是你的最佳起手式：

- 🕵️‍♂️ **新站点逆向分析**：快速搭建 PoC，验证接口逻辑。
- 🤖 **自动化注册机**：配合邮箱模块，打通验证码/注册链接流程。
- 🧩 **接口调试**：作为基础工程，集成各类调试脚本。
- 🧪 **Web 自动化测试**：基于 Camoufox 的快速测试用例编写。

## 📦 环境要求

- **Python 3.12+** (推荐使用最新版以获得最佳性能)

## 🚀 快速安装

### ⚡ 方式 1：使用 `uv`（极速推荐）

这是目前最快的 Python 包管理器，谁用谁知道。

```bash
# 1. 安装 uv (如果还没装)
pip install uv

# 2. 同步依赖
uv sync
```

### 方式 2：使用 `pip`

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 快速开始

运行项目入口：

```bash
python main.py
```

## 模板定位

这个仓库适合作为以下工作的起点：
- 新站点自动化流程 PoC
- 逆向接口调试时的基础工程
- 邮件验证码/邮件链接抓取的流程打通
- 注册机
