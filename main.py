from __future__ import annotations

import asyncio
import json
import re
import secrets
import string
import time
from urllib.parse import quote

import httpx

from astrbot.api import AstrBotConfig, llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_tasker_connect",
    "Nekyuu",
    "基于 ntfy.sh 的系统级闹钟推送插件",
    "2.0.1",
)
class TaskerConnectPlugin(Star):
    """Tasker ntfy 推送插件。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        self._ntfy_server = str(config.get("ntfy_server", "https://ntfy.sh")).rstrip(
            "/"
        )
        self._ntfy_topic = str(config.get("ntfy_topic", "")).strip()
        self._battery_reply_topic = str(config.get("battery_reply_topic", "")).strip()
        self._ntfy_token = str(config.get("ntfy_token", "")).strip()
        self._http_timeout_sec = max(3, int(config.get("http_timeout_sec", 10)))
        self._battery_wait_timeout_sec = max(
            5, int(config.get("battery_wait_timeout_sec", 25))
        )
        self._amap_api_key = str(config.get("amap_api_key", "")).strip()
        self._amap_coordsys = str(config.get("amap_coordsys", "gps")).strip() or "gps"
        self._topic_length = max(16, int(config.get("random_topic_length", 32)))

    async def initialize(self) -> None:
        self._refresh_config_runtime()
        self._generate_topic_if_requested()

        if not self._ntfy_topic:
            logger.warning(
                "ntfy_topic is empty. tasker_set_alarm will fail until configured."
            )
        if not self._battery_reply_topic:
            logger.warning(
                "battery_reply_topic is empty. tasker_get_battery will fail until configured."
            )
        logger.info(
            f"Tasker ntfy plugin initialized. server={self._ntfy_server}, "
            f"topic={'<empty>' if not self._ntfy_topic else self._ntfy_topic}, "
            f"battery_reply_topic={'<empty>' if not self._battery_reply_topic else self._battery_reply_topic}"
        )

    async def terminate(self) -> None:
        logger.info("Tasker ntfy plugin terminated")

    def _refresh_config_runtime(self) -> None:
        """Reload frequently used settings from AstrBotConfig into runtime fields."""
        self._ntfy_server = str(
            self.config.get("ntfy_server", "https://ntfy.sh")
        ).rstrip("/")
        self._ntfy_topic = str(self.config.get("ntfy_topic", "")).strip()
        self._battery_reply_topic = str(
            self.config.get("battery_reply_topic", "")
        ).strip()
        self._ntfy_token = str(self.config.get("ntfy_token", "")).strip()
        self._http_timeout_sec = max(3, int(self.config.get("http_timeout_sec", 10)))
        self._battery_wait_timeout_sec = max(
            5, int(self.config.get("battery_wait_timeout_sec", 25))
        )
        self._amap_api_key = str(self.config.get("amap_api_key", "")).strip()
        self._amap_coordsys = (
            str(self.config.get("amap_coordsys", "gps")).strip() or "gps"
        )
        self._topic_length = max(16, int(self.config.get("random_topic_length", 32)))

    async def _amap_convert_coord(
        self, longitude: float, latitude: float
    ) -> tuple[float, float] | None:
        """Convert source coordinates to AMap standard coordinates."""
        if not self._amap_api_key:
            return None

        amap_url = "https://restapi.amap.com/v3/assistant/coordinate/convert"
        params = {
            "key": self._amap_api_key,
            "locations": f"{longitude:.6f},{latitude:.6f}",
            "coordsys": self._amap_coordsys,
            "output": "JSON",
        }

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
                resp = await client.get(amap_url, params=params)
            if resp.status_code >= 400:
                logger.warning(f"amap coord convert http error: {resp.status_code}")
                return None

            data = resp.json()
            if str(data.get("status")) != "1":
                logger.warning(
                    f"amap coord convert failed: info={data.get('info', 'unknown')}"
                )
                return None

            locations = str(data.get("locations", "")).strip()
            if not locations:
                return None

            # AMap may return multiple converted points joined by ';'.
            first_point = locations.split(";", maxsplit=1)[0].strip()
            if "," not in first_point:
                logger.warning(
                    f"amap coord convert invalid locations format: {locations}"
                )
                return None

            lon_s, lat_s = first_point.split(",", maxsplit=1)
            return float(lon_s), float(lat_s)
        except Exception as e:
            logger.warning(f"amap coord convert exception: {e}")
            return None

    async def _amap_reverse_geocode(
        self, longitude: float, latitude: float
    ) -> str | None:
        """Resolve coordinates to a human-readable address via AMap regeo API."""
        if not self._amap_api_key:
            return None

        amap_url = "https://restapi.amap.com/v3/geocode/regeo"
        params = {
            "key": self._amap_api_key,
            "location": f"{longitude:.6f},{latitude:.6f}",
            "extensions": "base",
            "output": "JSON",
        }

        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
                resp = await client.get(amap_url, params=params)
            if resp.status_code >= 400:
                logger.warning(f"amap regeo http error: {resp.status_code}")
                return None

            data = resp.json()
            if str(data.get("status")) != "1":
                logger.warning(f"amap regeo failed: info={data.get('info', 'unknown')}")
                return None

            regeo = (
                data.get("regeocode") if isinstance(data.get("regeocode"), dict) else {}
            )
            address = regeo.get("formatted_address") if isinstance(regeo, dict) else ""
            if not address:
                return None
            return str(address)
        except Exception as e:
            logger.warning(f"amap regeo exception: {e}")
            return None

    def _generate_topic_if_requested(self) -> None:
        """One-shot topic generator triggered by settings toggle."""
        if not bool(self.config.get("generate_random_topic_once", False)):
            return

        alphabet = string.ascii_letters + string.digits
        new_topic = "".join(secrets.choice(alphabet) for _ in range(self._topic_length))
        self.config["ntfy_topic"] = new_topic
        self.config["generate_random_topic_once"] = False
        self.config.save_config()

        self._ntfy_topic = new_topic
        logger.info(
            f"Generated random ntfy_topic via settings toggle, len={self._topic_length}"
        )

    def _build_ntfy_headers(self, title: str, tags: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Title": title,
            "Tags": tags,
        }
        if self._ntfy_token:
            headers["Authorization"] = f"Bearer {self._ntfy_token}"
        return headers

    async def _post_ntfy_payload(
        self,
        topic: str,
        payload: dict,
        title: str,
        tags: str,
    ) -> tuple[bool, str]:
        topic_escaped = quote(topic, safe="")
        url = f"{self._ntfy_server}/{topic_escaped}"
        headers = self._build_ntfy_headers(title=title, tags=tags)

        logger.debug(f"sending ntfy payload to {url}")
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout_sec) as client:
                resp = await client.post(
                    url,
                    content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                )
            if resp.status_code >= 400:
                logger.error(
                    f"ntfy push failed: status={resp.status_code}, body={resp.text}"
                )
                return False, f"HTTP {resp.status_code}"
        except Exception as e:
            logger.error(f"ntfy push exception: {e}")
            return False, str(e)

        return True, ""

    async def _wait_for_battery_reply(self, request_id: str) -> dict | None:
        """Poll battery reply topic and return parsed JSON payload for the request id."""
        if not self._battery_reply_topic:
            logger.error("battery_reply_topic not configured")
            return None

        topic_escaped = quote(self._battery_reply_topic, safe="")
        url = f"{self._ntfy_server}/{topic_escaped}/json"
        headers = self._build_ntfy_headers(title="", tags="")
        headers.pop("Content-Type", None)
        headers.pop("Title", None)
        headers.pop("Tags", None)

        deadline = time.monotonic() + self._battery_wait_timeout_sec
        logger.info(
            f"waiting battery reply: request_id={request_id}, "
            f"topic={self._battery_reply_topic}, url={url}, timeout={self._battery_wait_timeout_sec}s"
        )

        poll_id = None
        seen_ids = set()
        rate_limit_hit = False
        async with httpx.AsyncClient(
            timeout=max(5, self._battery_wait_timeout_sec + 2)
        ) as client:
            while True:
                remain = int(deadline - time.monotonic())
                if remain <= 0:
                    logger.warning(
                        f"battery reply timeout: request_id={request_id}, "
                        f"seen {len(seen_ids)} messages but none matched"
                    )
                    return None

                poll_timeout = min(10, remain)
                params = {
                    "poll": "1",
                    "timeout": f"{poll_timeout}s",
                    "limit": "100",  # Get up to 100 messages per poll
                }
                if poll_id:
                    params["poll"] = poll_id

                try:
                    resp = await client.get(url, params=params, headers=headers)
                    logger.debug(f"polling response: status={resp.status_code}")
                except Exception as e:
                    logger.error(f"battery reply poll exception: {e}")
                    return None

                if resp.status_code == 429:
                    if not rate_limit_hit:
                        logger.warning(
                            "ntfy.sh rate limit hit (429). Retrying with longer intervals..."
                        )
                        rate_limit_hit = True
                    await asyncio.sleep(2)  # Back off
                    continue

                if resp.status_code >= 400:
                    logger.error(
                        f"battery reply poll failed: status={resp.status_code}, body={resp.text}"
                    )
                    return None

                # Extract poll ID from response headers for next iteration
                new_poll_id = resp.headers.get("X-Poll-ID")
                if new_poll_id:
                    poll_id = new_poll_id
                    logger.debug(f"updated poll_id: {poll_id}")

                try:
                    data = resp.json()
                    logger.debug(f"polling response data: {str(data)[:300]}")
                except Exception as e:
                    body_text = resp.text.strip()
                    ndjson_events = []
                    if body_text:
                        for line in body_text.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                ndjson_events = []
                                break
                            if isinstance(obj, dict):
                                ndjson_events.append(obj)

                    if ndjson_events:
                        data = ndjson_events
                        logger.debug(
                            f"polling response parsed as ndjson with {len(ndjson_events)} events"
                        )
                    else:
                        logger.warning(
                            f"battery reply parse failed: non-json body={resp.text[:200]}, error={e}"
                        )
                        continue

                # Handle both single message and multi-message responses
                messages = data if isinstance(data, list) else [data]
                logger.debug(f"processing {len(messages)} messages from polling")

                for idx, evt in enumerate(messages):
                    if not isinstance(evt, dict):
                        logger.debug(f"message #{idx} is not dict: {type(evt)}")
                        continue

                    evt_id = evt.get("id", "?")
                    evt_type = evt.get("event", "unknown")
                    logger.debug(f"message #{idx} id={evt_id}, event={evt_type}")

                    if evt.get("event") != "message":
                        logger.debug(
                            f"battery reply ignored event: {evt_type} (expected 'message')"
                        )
                        continue

                    message = evt.get("message", "")
                    if not message:
                        logger.debug(f"message #{idx} has empty message field")
                        continue

                    logger.debug(f"parsing message: {message[:200]}")
                    payload = self._parse_ntfy_message_payload(message)
                    if payload is None:
                        logger.warning(
                            f"battery reply ignored malformed json message: {message[:120]}"
                        )
                        continue

                    if not isinstance(payload, dict):
                        logger.debug(f"parsed payload is not dict: {type(payload)}")
                        continue

                    data_dict = (
                        payload.get("data")
                        if isinstance(payload.get("data"), dict)
                        else {}
                    )
                    msg_request_id = data_dict.get("request_id") or payload.get(
                        "request_id"
                    )

                    # Convert request_id to string for comparison
                    msg_request_id_str = str(msg_request_id) if msg_request_id else None
                    seen_ids.add(msg_request_id_str)

                    logger.debug(
                        f"message payload: action={payload.get('action')}, "
                        f"request_id={msg_request_id_str}, want_request_id={request_id}"
                    )

                    if msg_request_id_str != request_id:
                        logger.debug(
                            f"battery reply ignored by request_id mismatch: "
                            f"want={request_id}, got={msg_request_id_str}"
                        )
                        continue

                    logger.info(
                        f"battery reply matched: request_id={request_id}, "
                        f"action={payload.get('action')}"
                    )
                    return payload

    def _parse_ntfy_message_payload(self, message: str) -> dict | None:
        """Parse ntfy message payload with compatibility for escaped JSON text."""
        candidate = str(message).strip()
        if not candidate:
            return None

        # Case 1: normal JSON object string.
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, str):
                # Case 2: double-encoded JSON string.
                obj2 = json.loads(obj)
                if isinstance(obj2, dict):
                    return obj2
        except Exception:
            pass

        # Case 3: escaped object text like {\"action\":...} from some Tasker setups.
        normalized = candidate.replace(r"\"", '"')
        if normalized != candidate:
            try:
                obj = json.loads(normalized)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        # Case 4: recover common unquoted request_id token and retry once.
        repaired = re.sub(
            r'("request_id"\s*:\s*)([A-Za-z0-9_-]+)',
            r'\1"\2"',
            normalized,
        )
        if repaired != normalized:
            try:
                obj = json.loads(repaired)
                if isinstance(obj, dict):
                    logger.warning(
                        "battery reply message repaired by auto-quoting request_id"
                    )
                    return obj
            except Exception:
                pass

        return None

    @llm_tool(name="tasker_set_alarm")
    async def tasker_set_alarm(
        self, event: AstrMessageEvent, hour: int, minute: int
    ) -> str:
        """通过 ntfy.sh 推送远程闹钟指令。

        Args:
            hour(int): 闹钟小时，24 小时制，范围 0-23
            minute(int): 闹钟分钟，范围 0-59
        """
        logger.info(f"tasker_set_alarm called: hour={hour}, minute={minute}")

        hour_int = int(hour)
        minute_int = int(minute)
        if not (0 <= hour_int <= 23):
            return "设置失败：hour 必须在 0 到 23 之间。"
        if not (0 <= minute_int <= 59):
            return "设置失败：minute 必须在 0 到 59 之间。"
        if not self._ntfy_topic:
            return "设置失败：请先在插件配置中设置 ntfy_topic。"

        payload = {
            "action": "set_alarm",
            "data": {
                "hour": f"{hour_int:02d}",
                "minute": f"{minute_int:02d}",
            },
        }

        ok, err = await self._post_ntfy_payload(
            topic=self._ntfy_topic,
            payload=payload,
            title="AstrBot Alarm",
            tags="alarm,astrbot,tasker",
        )
        if not ok:
            return f"推送失败：{err}"

        logger.info(
            f"ntfy push success: time={hour_int:02d}:{minute_int:02d}, "
            f"topic={self._ntfy_topic}"
        )
        return f"推送成功：闹钟指令 {hour_int:02d}:{minute_int:02d} 已下发"

    @llm_tool(name="tasker_get_battery")
    async def tasker_get_battery(self, event: AstrMessageEvent) -> str:
        """查询远端契约者 Android 设备电量（通过回传 topic 等待结果）。"""
        self._refresh_config_runtime()

        if not self._ntfy_topic:
            return "查询失败：请先配置 ntfy_topic。"
        if not self._battery_reply_topic:
            return "查询失败：请先配置 battery_reply_topic。"

        request_id = secrets.token_hex(8)
        req_payload = {
            "action": "get_battery",
            "data": {
                "request_id": request_id,
                "reply_topic": self._battery_reply_topic,
            },
        }

        ok, err = await self._post_ntfy_payload(
            topic=self._ntfy_topic,
            payload=req_payload,
            title="AstrBot Battery Query",
            tags="battery,astrbot,tasker",
        )
        if not ok:
            return f"查询失败：电量请求下发失败（{err}）"

        reply = await self._wait_for_battery_reply(request_id)
        if reply is None:
            return (
                "查询超时：未收到设备电量回传。"
                f"请确认 Tasker 正在监听 {self._ntfy_topic} 并回传到 {self._battery_reply_topic}。"
            )

        data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
        level = data.get("level", data.get("battery", "未知"))
        status = data.get("status", data.get("battery_status", None))

        # 电池状态映射表
        status_map = {
            1: "未知",
            2: "充电中",
            3: "放电",
            4: "未充电",
            5: "满电",
        }

        status_text = "未知"
        if isinstance(status, int) and status in status_map:
            status_text = status_map[status]
        elif isinstance(status, str):
            status_clean = status.strip()
            if status_clean.isdigit():
                status_text = status_map.get(int(status_clean), "未知")
            else:
                status_text = status_clean or "未知"

        return f"设备当前电量：{level}%（{status_text}）"

    @llm_tool(name="tasker_get_location")
    async def tasker_get_location(self, event: AstrMessageEvent) -> str:
        """查询远端 Android 设备定位（通过回传 topic 等待结果）。用于获取契约者当前位置"""
        self._refresh_config_runtime()

        if not self._ntfy_topic:
            return "查询失败：请先配置 ntfy_topic。"
        if not self._battery_reply_topic:
            return "查询失败：请先配置 battery_reply_topic。"

        request_id = secrets.token_hex(8)
        req_payload = {
            "action": "get_location",
            "data": {
                "request_id": request_id,
                "reply_topic": self._battery_reply_topic,
            },
        }

        ok, err = await self._post_ntfy_payload(
            topic=self._ntfy_topic,
            payload=req_payload,
            title="AstrBot Location Query",
            tags="location,astrbot,tasker",
        )
        if not ok:
            return f"查询失败：定位请求下发失败（{err}）"

        reply = await self._wait_for_battery_reply(request_id)
        if reply is None:
            return (
                "查询超时：未收到设备定位回传。"
                f"请确认 Tasker 正在监听 {self._ntfy_topic} 并回传到 {self._battery_reply_topic}。"
            )

        data = reply.get("data") if isinstance(reply.get("data"), dict) else {}
        latitude_raw = data.get("latitude", data.get("lat", None))
        longitude_raw = data.get("longitude", data.get("lng", None))

        try:
            latitude = float(latitude_raw)
            longitude = float(longitude_raw)
        except (TypeError, ValueError):
            return "定位查询成功，但回传中未包含有效经纬度。"

        converted = await self._amap_convert_coord(
            longitude=longitude, latitude=latitude
        )
        if converted is not None:
            longitude, latitude = converted
            logger.info(
                f"location converted by amap: lon={longitude:.6f}, lat={latitude:.6f}, coordsys={self._amap_coordsys}"
            )

        amap_address = await self._amap_reverse_geocode(
            longitude=longitude, latitude=latitude
        )
        if amap_address:
            return f"设备当前位置：{amap_address}（{latitude:.6f}, {longitude:.6f}）"
        return f"设备当前位置坐标：纬度 {latitude:.6f}，经度 {longitude:.6f}"

    @filter.command("电量查询")
    async def tasker_battery_command(self, event: AstrMessageEvent):
        """手动触发远端设备电量查询。"""
        result = await self.tasker_get_battery(event)
        yield event.plain_result(result)

    @filter.command("开盒")
    async def tasker_location_command(self, event: AstrMessageEvent):
        """手动触发远端设备定位查询。"""
        result = await self.tasker_get_location(event)
        yield event.plain_result(result)

    @filter.command("查找定位")
    async def tasker_location_command_alias(self, event: AstrMessageEvent):
        """手动触发远端设备定位查询（别名指令）。"""
        result = await self.tasker_get_location(event)
        yield event.plain_result(result)
