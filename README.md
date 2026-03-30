# AstrBot Tasker Connect

通过 ntfy.sh 把 LLM 生成的闹钟指令推送到 Android Tasker 客户端。

## 功能说明

- 向 LLM 暴露工具：`tasker_set_alarm`
- 向 LLM 暴露工具：`tasker_get_battery`
- 向 LLM 暴露工具：`tasker_get_location`
- 提供手动指令：`/电量查询`（直接触发电量查询）
- 提供手动指令：`/开盒`（直接触发定位查询）
- 提供手动指令：`/查找定位`（定位查询别名）
- LLM 调用工具后，插件直接向 ntfy topic 发送系统级推送
- 无需插件内 HTTP Server、无长轮询阻塞
- 推送 payload 固定为 Tasker 可直接解析的格式
- 定位结果可选接入高德：坐标转换 + 逆地理编码

## LLM Tool

工具名：`tasker_set_alarm`

参数：

- `hour`：0-23
- `minute`：0-59

返回：推送成功/失败信息。

工具名：`tasker_get_battery`

参数：

- 无参数

返回：设备电量信息（通过回传 topic 等待结果）。

工具名：`tasker_get_location`

参数：

- 无参数

返回：设备定位信息（通过回传 topic 等待结果）。

## 手动指令

- `/电量查询`：触发一次电量查询，并将结果直接回复到当前会话。
- `/开盒`：触发一次定位查询，并将结果直接回复到当前会话。
- `/查找定位`：触发一次定位查询，并将结果直接回复到当前会话。

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

电量查询请求（发送到 `ntfy_topic`）示例：

```json
{
	"action": "get_battery",
	"data": {
		"request_id": "a1b2c3d4e5f6",
		"reply_topic": "your_reply_topic"
	}
}
```

电量查询回传（Tasker 推送到 `battery_reply_topic`）示例：

```json
{
	"action": "battery_status",
	"data": {
		"request_id": "a1b2c3d4e5f6",
		"level": 82,
		"is_charging": false
	}
}
```

定位查询请求（发送到 `ntfy_topic`）示例：

```json
{
	"action": "get_location",
	"data": {
		"request_id": "a1b2c3d4e5f6",
		"reply_topic": "your_reply_topic"
	}
}
```

定位查询回传（Tasker 推送到 `battery_reply_topic`）示例：

```json
{
	"action": "location",
	"data": {
		"request_id": "a1b2c3d4e5f6",
		"latitude": 31.2304,
		"longitude": 121.4737,
		"address": "上海市黄浦区..."
	}
}
```

## 配置项

可在 AstrBot 插件面板直接修改（由 `_conf_schema.json` 提供）：

- `ntfy_server`：ntfy 服务地址（默认 `https://ntfy.sh`）
- `ntfy_topic`：推送 topic（建议随机高强度字符串）
- `battery_reply_topic`：电量查询回传 topic（建议与主 topic 分离）
- `generate_random_topic_once`：单次随机生成 topic 开关（保存后重载生效，自动回写并复位）
- `random_topic_length`：随机 topic 长度（最小 16，默认 32）
- `ntfy_token`：可选 Bearer Token
- `amap_api_key`：高德地图 API Key（可选，启用定位地址增强）
- `amap_coordsys`：设备回传原坐标系（gps/mapbar/baidu/autonavi）
- `http_timeout_sec`：推送请求超时时间
- `battery_wait_timeout_sec`：电量查询等待回传超时时间

## 调试建议

- 先在手机上手动订阅同一个 topic，确认可收到消息
- 若使用自建 ntfy，建议配置 TLS 与访问控制
- topic 建议使用难猜测字符串，避免被外部污染推送
- 建议 `ntfy_topic` 用于下发命令，`battery_reply_topic` 专门用于状态回传
- 配置 `amap_api_key` 后，定位将自动调用高德坐标转换与逆地理编码，优先返回格式化地址

### 一键随机 topic 使用方式

1. 在插件设置中将 `generate_random_topic_once` 改为 `true` 并保存。
2. 在插件面板点击重载插件。
3. 插件会自动生成随机 `ntfy_topic`，并把开关自动重置为 `false`。
