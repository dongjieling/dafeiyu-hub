# NapCat 替代方案调研（v1.2）

> 结论先行：**不急换。** 先用 LLOneBot 做旁路验证，NapCat 继续当主力；等 NapCat 真正不可用再切。

## 背景

现在 QQ 接入链路：

```
AstrBot（aiocqhttp 适配器） ←反向 WS← NapCat ←→ QQ
```

NapCat 是社区维护的 OneBot 11 实现。社区反馈其维护节奏放缓，
存在"某天 QQ 协议变动导致不可用"的单点风险。

## 候选对比

| 方案 | 协议 | 平台 | 维护状态 | 迁移成本 | 备注 |
|---|---|---|---|---|---|
| **LLOneBot** | OneBot 11 | Windows / LiteLoaderQQNT | 活跃 | 低 | 插件形态，换端口和 token 即可；和 NapCat 同为 OneBot 11 |
| **Lagrange.OneBot** | OneBot 11 | 跨平台（.NET） | 活跃 | 中 | 独立进程，签名协议自管；登录方式与 NapCat 不同 |
| **go-cqhttp** | OneBot 11 | 跨平台 | 已归档 | 低 | 不再维护，不建议新用 |
| **NapCat（现状）** | OneBot 11 | Windows / Docker | 放缓 | — | 当前在用 |

## 建议路线

1. **不主动切换**：现在 NapCat 还能用，切换只会引入新变量
2. **准备旁路**：在另一台机器/另一个 QQ 号上跑 LLOneBot，验证：
   - 反向 WS 能连上 AstrBot
   - 私聊、群聊消息格式一致
   - 图片、引用、撤回等事件是否齐全
3. **抽象接入层**（可选，v2.0）：AstrBot 的 aiocqhttp 适配器本身屏蔽了差异，
   只要新实现兼容 OneBot 11，中转站**完全不用改**
4. **切换条件**：NapCat 连续两周无法登录，或关键事件缺失

## 对中转站的影响

**零改动。** 中转站只和 AstrBot 的对话库打交道，不直接连 NapCat。
需要动的只有：

- AstrBot 的 `aiocqhttp` 配置（host / port / token）
- NapCat 侧的反向 WS 地址

## 待验证清单

- [ ] LLOneBot 在 Windows 上的登录稳定性
- [ ] 群消息的 `group_id` / `sender_id` 格式是否与 NapCat 一致
- [ ] 图片/语音消息在 AstrBot 里的落库格式
- [ ] 与 AstrBot 4.29.4 的兼容性

> 调研基于社区公开信息，未做长时间实测；切换前必须旁路验证。
