---
name: chat-reply
description: 根据用户ID和对方消息内容，调用 chat 能力生成私信回复内容。当 agent 需要「获取私信回复文案」时路由到此能力。
triggers:
  - 获取回复内容
  - 生成私信回复
  - 回复什么
  - chat
  - 私信回复文案
  - 小红书私信与
---

# Skill: Chat 回复能力路由说明

本文档描述「chat」能力的接口契约，供 agent 做 routing 和参数拼装时使用。

当 agent 判断当前任务需要「根据对方消息生成回复内容」时，即应路由到本能力。

---

## 一、能力概述

| 项目 | 说明 |
|------|------|
| **能力名** | `chat` |
| **接口地址** | `POST http://192.168.110.91:9093/redirect/chat` |
| **用途** | 传入对方用户标识与消息内容，返回应回复的文字内容 |
| **调用时机** | 已读取到对方新消息、需要生成回复文案时 |

---

## 二、请求参数结构

**请求头：**

```
Content-Type: application/json
```

**请求体：**

```json
{
  "session_id": "string",
  "platform": "string",
  "user_input": "string",
  "job_number": "string"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | `string` | 是 | 对方用户唯一标识，填入从对方主页读取的小红书号 |
| `platform` | `string` | 是 | 固定填 `小红书` |
| `user_input` | `string` | 是 | 对方发送的消息内容，多条消息用 `\|` 拼接为一个字符串传入，例如：`"在吗\|想问一下\|怎么买"` |
| `job_number` | `string` | 是 | 业务编号，按实际场景填写 |

**请求示例：**

```bash
curl --location 'http://192.168.110.91:9093/redirect/chat' \
--header 'Content-Type: application/json' \
--data '{
  "session_id": "mock_chasen",
  "platform": "小红书",
  "user_input": "还有吗",
  "job_number": "ads"
}'
```

---

## 三、响应结构

API 原始响应：

```json
{
  "content_list": ["你好呀～", "在的，请说", "可以的，您可以通过以下方式购买……"]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content_list` | `string[]` | 要回复的内容数组，每个元素对应一条独立回复消息 |

**调用方处理方式：**

收到响应后，将 `content_list` 用英文分号 `;` 拼接为字符串，再交给 agent 按 `;` 拆分逐条发送：

```python
message = ";".join(result["content_list"])
# 结果示例："你好呀～;在的，请说;可以的，您可以通过以下方式购买……"
```

---

## 四、Agent Routing 判断逻辑

当满足以下**全部条件**时，路由到 `chat` 能力：

1. 已获取到对方用户的 `session_id`（即小红书号，非空字符串）
2. 已收集到对方至少一条新消息（`user_input` 非空）
3. 当前任务目标为「回复私信」

不满足以上条件时，**不得**调用本能力，应继续执行前置步骤（如读取 session_id、收集消息）。

---

## 五、返回值处理规范

- HTTP 状态码非 200 时，记录错误，**不执行任何回复动作**，直接跳过该用户。
- `content_list` 中每个元素为一条独立回复，必须**逐条**发送，不得合并成一条。
- `content_list` 为空数组时，视为无需回复，直接跳过。
