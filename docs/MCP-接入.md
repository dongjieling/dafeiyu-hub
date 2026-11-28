# MCP 接入（v1.2）

把记忆和人格暴露给支持 MCP 的 AI 客户端（Claude Desktop、Cherry Studio 等）。

## 启动方式

在 MCP 客户端里添加一个 stdio 服务：

```json
{
  "mcpServers": {
    "dafeiyu-memory": {
      "command": "C:\\Users\\<你>\\.astrbot\\data\\python\\python.exe",
      "args": ["D:\\dafeiyu-hub\\sidecar\\mcp_server.py"],
      "env": {
        "HUB_DATA_DIR": "C:\\Users\\<你>\\AppData\\Roaming\\dafeiyu-hub"
      }
    }
  }
}
```

如果用的是安装包，把 `sidecar` 换成解压后的 `resources\\sidecar` 路径。

## 提供的工具

| 工具 | 作用 | 参数 |
|---|---|---|
| `memory_recall` | 按语义召回长期记忆 | `query`，可选 `limit` |
| `memory_list` | 按关键词浏览记忆原文 | `query`、`limit` |
| `persona_get` | 读取当前人格 | 无 |
| `memory_health` | 查看记忆规模与写入队列 | 无 |

全部只读；不提供写入、修改、删除。

## 依赖

- 与中转站共用同一份 `config.json` 和归档密钥
- 需要本机 LivingMemory 插件在运行（召回走它的外部 API）
- 不需要中转站本体在运行

## 验证

```powershell
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp_server.py
```

应返回四个工具名。

## 一键注册到 AstrBot

中转站「配置」页底部有 **MCP → 注册到 AstrBot** 按钮：

- 自动把 `dafeiyu-memory` 写入 `%USERPROFILE%\.astrbot\data\mcp_server.json`
- 保留已有的 MCP 服务器
- 写入前备份为 `mcp_server.json.bak_<时间戳>`
- 注册后需要重启 AstrBot 才会加载

注册后的工具会出现在 AstrBot 的 MCP 工具列表里，QQ 侧可以主动调用：

- `memory_recall`：支持 `date_from` / `date_to`，问「昨晚」时可直接查区间
- `memory_list`：支持 `kind` / 日期 / 重要度过滤
- `persona_get` / `memory_health`

## 手动配置

```json
{
  "mcpServers": {
    "dafeiyu-memory": {
      "command": "<python.exe 路径>",
      "args": ["<sidecar>\\mcp_server.py"],
      "env": { "HUB_DATA_DIR": "<数据目录>" },
      "active": true
    }
  }
}
```
