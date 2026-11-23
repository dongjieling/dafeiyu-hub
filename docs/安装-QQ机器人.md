# 安装 QQ 对话软件（NapCat + AstrBot）

> 目标：让一个真实 QQ 号能收发消息，并接上大模型。
> 这是中转站的"上游"——人格与记忆都在这里。

---

## 一、整体结构

```
你的 QQ ←→ NapCat（协议端） ←→ AstrBot（框架） ←→ 模型 / 中转站
```

| 组件 | 作用 |
|---|---|
| **NapCat** | 把 QQ 变成可编程接口（OneBot v11 协议） |
| **AstrBot** | 处理消息、加载人格与插件、调用模型 |

---

## 二、安装 NapCat

### 2.1 下载

官方仓库曾清空过发布产物，实际可用来源是社区存档：

```
https://github.com/XingYunStar/NapCatQQ/releases
```

选 `NapCat.Shell.Windows.OneKey.zip`（约 1 MB，是安装器）。

### 2.2 准备 QQ 安装包

安装器会自动下载 QQ 9.9.33，但官方链接可能失效。若失败，手动准备：

- 文件名：`QQ_9.9.33_260813_x64_01.exe`
- 放在安装器同目录

### 2.3 补 NapCat 本体

安装器写死了一个已失效的下载地址，需要手动补：

1. 下载 `NapCat.Shell.zip`（约 36 MB，同 Releases 页面）
2. 放进安装目录（与安装器同级）
3. 重新运行安装器 —— 它会跳过下载，直接解压

### 2.4 安装

双击 `NapCat.Shell.Windows.OneKey.zip` 里的安装器，按提示走。

成功标志：

```
[成功] NapCat解压成功
[成功] 文件复制完成
```

### 2.5 启动与登录

双击安装目录里的 `napcat.bat`，会弹出：

- 一个黑窗口（运行日志）
- 一张二维码图片

用手机 QQ 扫码登录。

**常见报错**：

```
[error] 当前账号(xxx)已登录,无法重复登录
```

→ 同一个号不能同时登录两份桌面端 QQ。先退出其它客户端。

### 2.6 获取 WebUI

登录成功后日志会出现：

```
[NapCat] [WebUi] WebUi Token: xxxxxxxx
[NapCat] [WebUi] User Panel Url: http://127.0.0.1:6099/webui?token=...
```

打开这个地址就是 NapCat 的管理面板。

---

## 三、安装 AstrBot

### 3.1 下载

```
https://github.com/AstrBotDevs/AstrBot-desktop/releases
```

选 `AstrBot_x.x.x_windows_amd64_setup.exe`。

挑选规则：文件名同时含 `windows` 和 `amd64` 和 `setup.exe`。

### 3.2 安装

默认装到 `C:\Users\<用户名>\AppData\Local\AstrBot`，内置 Python，无需另装环境。

### 3.3 首次登录

启动后在日志里找初始密码，打开面板：

```
http://localhost:6185
```

登录后建议改密码。

---

## 四、连接两者（关键一步）

### 4.1 方向别搞反

AstrBot 支持的是 **OneBot v11 反向 WebSocket**：

```
✅ AstrBot 当服务端（监听 6199），NapCat 主动连过来
❌ AstrBot 去连 NapCat 的端口（这是正向，不支持）
```

### 4.2 AstrBot 侧

面板 → 消息平台 → 新增 → **aiocqhttp（OneBot v11）**：

| 字段 | 值 |
|---|---|
| 机器人名称 | 一号（记下来，后面要用） |
| 反向 WS 主机 | `0.0.0.0` |
| 端口 | `6199` |
| Token | 自定义一串，例如 `my_token_2026` |

### 4.3 NapCat 侧

打开 NapCat WebUI → 网络配置 → 新建 **WebSocket 客户端**：

| 字段 | 值 |
|---|---|
| 名称 | astrbot |
| URL | `ws://127.0.0.1:6199/ws` |
| Token | 与 AstrBot 里填的完全一致 |
| 消息格式 | array |

⚠️ 三个必须：

1. URL 结尾必须有 `/ws`
2. 两边 Token 完全一致
3. 不要同时开 WebSocket 服务端（会占 3001 端口冲突）

### 4.4 验证

NapCat 日志：

```
WebSocket反向服务: ws://127.0.0.1:6199/ws, : 已启动
```

AstrBot 日志：

```
aiocqhttp(OneBot v11) 适配器已连接。
```

### 4.5 测试

私聊机器人发「你好」，能回就通了。

---

## 五、配置模型

面板 → 模型提供商 → 新增：

| 字段 | 值 |
|---|---|
| 类型 | OpenAI 兼容 |
| Base URL | `https://api.deepseek.com/v1` |
| 模型 | `deepseek-chat` |
| API Key | 你的 DeepSeek Key |

**如果要接中转站**，把 Base URL 换成：

```
http://127.0.0.1:8787/v1
```

API Key 填 `local`，模型填 `deepseek-chat`。

---

## 六、配置人格

人格不在配置文件里，在数据库。

### 方式 A：面板

面板 → 人格 → 新建 → 填名字和提示词 → 设为默认。

### 方式 B：直接改数据库

```python
import sqlite3
from pathlib import Path

db = Path.home() / '.astrbot' / 'data' / 'data_v4.db'
con = sqlite3.connect(db)
con.execute(
    "INSERT OR REPLACE INTO personas (persona_id, system_prompt, begin_dialogs, created_at, updated_at) "
    "VALUES ('dafeiyu', ?, '', 0, 0)",
    ('你的人格文本……',))
con.commit()
con.close()
```

### 启用

`cmd_config.json`：

```json
"persona": { "persona_id": "dafeiyu", "safety_mode": false }
```

⚠️ 改配置文件前**先退出 AstrBot**，否则会被界面保存覆盖。

---

## 七、配置知识库（可选）

1. 面板 → 知识库 → 新建
2. 上传 txt / md 文档
3. 等待解析（会调用嵌入模型）
4. 在人格或平台设置里勾选启用

**嵌入模型**需要在提供商里单独配置：

| 字段 | 值 |
|---|---|
| 类型 | OpenAI Embedding |
| Base URL | `https://api.siliconflow.cn/v1` |
| 模型 | `BAAI/bge-m3` |
| 维度 | `1024` |

⚠️ 模型名不能有多余空格，否则报 `Model does not exist`。

---

## 八、推荐插件

| 插件 | 作用 |
|---|---|
| **LivingMemory** | 长期记忆（中转站依赖它） |
| affection | 情绪状态 |
| proactive_chat | 主动发消息 |

安装：面板 → 插件 → 市场 → 搜索 → 安装。

---

## 九、检查清单

| # | 检查 | 通过标准 |
|---|---|---|
| 1 | NapCat 启动 | 黑窗口有日志、二维码出现 |
| 2 | QQ 登录 | 日志出现账号信息 |
| 3 | AstrBot 面板 | 6185 能打开 |
| 4 | WS 连接 | AstrBot 日志「适配器已连接」 |
| 5 | 收发消息 | 私聊能对话 |
| 6 | 人格 | 问「你是谁」答对 |
| 7 | LivingMemory | 插件列表里在运行 |

---

## 十、常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `EADDRINUSE 3001` | 残留进程占端口 | 任务管理器结束所有 `QQ.exe`，只留一个 |
| 适配器一直连不上 | Token 不一致 / URL 少 `/ws` | 逐字核对 |
| 扫码提示已登录 | 同号另一处登录 | 退出其它客户端 |
| 人格改了没反应 | 界面覆盖 / 会话历史 | 先退出 AstrBot 再改；清空会话历史 |
| 知识库搜不到 | 文件没建库 | 文件要上传到知识库才算建库 |
