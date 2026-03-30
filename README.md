# AstrBot Tasker Connect

通过 ntfy.sh 把 LLM 生成的闹钟指令推送到 Android Tasker 客户端。

## 功能说明

- 向 LLM 暴露工具：`tasker_set_alarm`
- LLM 调用工具后，插件直接向 ntfy topic 发送系统级推送
- 无需插件内 HTTP Server、无长轮询阻塞
- 推送 payload 固定为 Tasker 可直接解析的格式

## LLM Tool

工具名：`tasker_set_alarm`

参数：

- `hour`：0-23
- `minute`：0-59

返回：推送成功/失败信息。

## 推送 Payload

发送到 ntfy 的消息体为：

```json
{
	"action": "set_alarm",
	"data": {
		"hour": "07",
		"minute": "30"
	}
}
```

## 配置项

可在 AstrBot 插件面板直接修改（由 `_conf_schema.json` 提供）：

- `ntfy_server`：ntfy 服务地址（默认 `https://ntfy.sh`）
- `ntfy_topic`：推送 topic（建议随机高强度字符串）
- `generate_random_topic_once`：单次随机生成 topic 开关（保存后重载生效，自动回写并复位）
- `random_topic_length`：随机 topic 长度（最小 16，默认 32）
- `ntfy_token`：可选 Bearer Token
- `http_timeout_sec`：推送请求超时时间

## 调试建议

- 先在手机上手动订阅同一个 topic，确认可收到消息
- 若使用自建 ntfy，建议配置 TLS 与访问控制
- topic 建议使用难猜测字符串，避免被外部污染推送

### 一键随机 topic 使用方式

1. 在插件设置中将 `generate_random_topic_once` 改为 `true` 并保存。
2. 在插件面板点击重载插件。
3. 插件会自动生成随机 `ntfy_topic`，并把开关自动重置为 `false`。
