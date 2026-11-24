# 大肥鱼中转站 · Hub

**让 QQ 机器人和桌面宠物共享同一个"她"——同一套人格、同一份记忆。**

```
┌─────────────┐
│  QQ 机器人   │──┐
│ (AstrBot)   │  │        ┌──────────────────┐
└─────────────┘  ├───────▶│   大肥鱼中转站    │──────▶ DeepSeek
┌─────────────┐  │        │  127.0.0.1:8787  │
│  桌面宠物    │──┘        └────────┬─────────┘
│   (AIRI)    │                     │
└─────────────┘            ┌────────▼─────────┐
                           │  记忆库（外部）   │
                           │  LivingMemory     │
                           └──────────────────┘
```

## 它解决什么问题

| 没有中转站 | 有中转站 |
|---|---|
| QQ 里记得你，桌面上不记得 | 两边共享同一份记忆 |
| 桌宠退出就清空历史 | 你说过的话被提炼成长期记忆 |
| 换模型要改两处配置 | 只改中转站一处 |
| 人格散落在各处 | 人格只有一份，实时同步 |

## 核心能力

- **OpenAI 兼容接口**：任何支持自定义 API 地址的客户端都能接
- **人格注入**：从 AstrBot 数据库实时读取，改人格 30 秒内生效
- **记忆召回**：每次对话前自动检索相关记忆并注入提示词
- **记忆写回**：对话结束后异步提炼要点，写回记忆库
- **原文归档**：所有对话存本地 SQLite，可回溯、可重建
- **参数调整**：直接在应用内改 AstrBot 插件参数（自动检测插件与版本）
- **深色 / 浅色主题**：一键切换
- **本地优先**：只监听 `127.0.0.1`，密钥不出本机

## 快速开始

### 1. 准备

| 依赖 | 说明 |
|---|---|
| Windows 10/11 x64 | — |
| Python 3.10+ | 推荐直接用 AstrBot 内置的那个 |
| AstrBot + LivingMemory 插件 | 提供人格与记忆引擎 |
| DeepSeek API Key | 或任何 OpenAI 兼容服务 |

### 2. 下载

| 文件 | 大小 | 链接 |
|---|---|---|
| `dafeiyu-hub-0.3.2-portable.exe` | 105.2 MB | [下载](https://github.com/dongjieling/dafeiyu-hub/releases/download/v0.3.2/dafeiyu-hub-0.3.2-portable.exe) |

或在 [Releases 页面](https://github.com/dongjieling/dafeiyu-hub/releases) 选择版本下载。

**放到普通目录**（如 `D:\大肥鱼中转站\`），不要放受限目录，双击运行。

> ⚠️ 未签名 EXE，SmartScreen 可能提示「未知发布者」，选「更多信息 → 仍要运行」。
> SHA256：`8837c0aa12e4e81daad754fa16d8d3783f48d8b0e33b9904272bb3eae651d6ef`

> ⚠️ 首次运行会提示配置 Python 路径与记忆库地址。

### 3. 配置记忆库

1. AstrBot 面板 → 插件 → LivingMemory → 配置 → 开启「外部记忆 API」（端口 7801）
2. 生成 API Key：`/lmem apikey create hub write`
3. 把 Key 填进中转站配置页

### 4. 接客户端

**桌面宠物（AIRI）**：设置 → 服务来源 → 聊天 → OpenAI 兼容

```
Base URL: http://127.0.0.1:8787/v1
API Key:  local（任意）
模型:     deepseek-chat
```

**QQ 机器人（AstrBot）**：模型提供商 → OpenAI 兼容，同上。

### 5. 验证

```powershell
curl http://127.0.0.1:8787/health
```

```json
{
  "status": "ok",
  "persona_chars": 1853,
  "lm_connected": true,
  "upstream_key_loaded": true
}
```

## 版本

当前版本 **v0.3.2**：记忆原子补丁启动自检与一键修复。

## 文档

| 文档 | 内容 |
|---|---|
| [快速上手](docs/快速上手.md) | **装机后第一份该读的**：每天开机顺序、三个灯、怎么让她记住 |
| [使用说明](docs/使用说明.md) | 界面、参数页、配置项、日常操作 |
| [安装中转站](docs/安装-中转站.md) | 从零部署本软件 |
| [安装 QQ 机器人](docs/安装-QQ机器人.md) | NapCat + AstrBot |
| [安装桌面宠物](docs/安装-桌宠.md) | AIRI + Live2D 模型 |
| [常见问题](docs/常见问题.md) | 踩坑与排查 |
| [开发记录](docs/开发记录.md) | 版本演进与技术决策 |

## 技术栈

| 层 | 选择 |
|---|---|
| 外壳 | Electron |
| 界面 | 原生 HTML/CSS/JS（无框架，体积小） |
| 后端 | Python 标准库（零第三方依赖），模块化拆分 |
| 存储 | SQLite（归档库） |
| 打包 | electron-builder（portable / nsis） |

## 目录结构

```
src/
├── electron/
│   ├── main.js         主进程：窗口 / 托盘 / 启停 sidecar
│   └── preload.js      IPC 安全桥
├── renderer/           界面（状态 / 配置 / 日志）
├── sidecar/
│   ├── hub.py          HTTP 服务：代理 + 人格 + 记忆
│   └── config.example.json
└── assets/             图标
```

## 开发

```powershell
cd src
npm install
npm start          # 开发运行

npm install -D electron-builder
node node_modules/electron-builder/cli.js --win portable --x64
```

## 隐私与安全

- 服务只监听 `127.0.0.1`，外网无法访问
- API Key 只存在本机配置文件与进程内存
- 记忆与对话全部存本地 SQLite，不上传任何服务器
- 除模型请求外，不发起任何网络连接

## 已知限制

- 不内置 Python，需要系统里有一个
- 记忆召回依赖 LivingMemory 的嵌入模型（需另行配置）
- 桌宠的表情联动取决于桌宠软件本身
- Windows 专用（未测试 macOS / Linux）

## 许可证

本项目代码采用 MIT 许可证。

依赖的第三方组件遵循各自许可：
- [LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) — AGPL-3.0（本项目仅通过 HTTP 调用，未包含其代码）
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — AGPL-3.0
- [AIRI](https://github.com/moeru-ai/airi) — MIT
