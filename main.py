from __future__ import annotations

import json
import secrets
import string
from urllib.parse import quote

import httpx

from astrbot.api import AstrBotConfig, llm_tool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_tasker_connect",
    "Nekyuu",
    "基于 ntfy.sh 的系统级闹钟推送插件",
    "2.0.0",
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
        self._ntfy_token = str(config.get("ntfy_token", "")).strip()
        self._http_timeout_sec = max(3, int(config.get("http_timeout_sec", 10)))
        self._topic_length = max(16, int(config.get("random_topic_length", 32)))

    async def initialize(self) -> None:
        self._refresh_config_runtime()
        self._generate_topic_if_requested()

        if not self._ntfy_topic:
            logger.warning(
                "ntfy_topic is empty. tasker_set_alarm will fail until configured."
            )
        logger.info(
            f"Tasker ntfy plugin initialized. server={self._ntfy_server}, "
            f"topic={'<empty>' if not self._ntfy_topic else self._ntfy_topic}"
        )

    async def terminate(self) -> None:
        logger.info("Tasker ntfy plugin terminated")

    def _refresh_config_runtime(self) -> None:
        """Reload frequently used settings from AstrBotConfig into runtime fields."""
        self._ntfy_server = str(
            self.config.get("ntfy_server", "https://ntfy.sh")
        ).rstrip("/")
        self._ntfy_topic = str(self.config.get("ntfy_topic", "")).strip()
        self._ntfy_token = str(self.config.get("ntfy_token", "")).strip()
        self._http_timeout_sec = max(3, int(self.config.get("http_timeout_sec", 10)))
        self._topic_length = max(16, int(self.config.get("random_topic_length", 32)))

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

        topic = quote(self._ntfy_topic, safe="")
        url = f"{self._ntfy_server}/{topic}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Title": "AstrBot Alarm",
            "Tags": "alarm,astrbot,tasker",
        }
        if self._ntfy_token:
            headers["Authorization"] = f"Bearer {self._ntfy_token}"

        logger.debug(f"sending ntfy push to {url}")
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
                return f"推送失败：HTTP {resp.status_code}"
        except Exception as e:
            logger.error(f"ntfy push exception: {e}")
            return f"推送失败：{str(e)}"

        logger.info(
            f"ntfy push success: time={hour_int:02d}:{minute_int:02d}, "
            f"topic={self._ntfy_topic}"
        )
        return f"推送成功：闹钟指令 {hour_int:02d}:{minute_int:02d} 已下发"
