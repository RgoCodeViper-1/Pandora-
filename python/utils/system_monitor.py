"""
utils/system_monitor.py
========================
Collects system telemetry and pushes it to the Node orchestrator over
WebSocket. Also answers on-demand SYSTEM_STATUS_REQUEST events.

Outbound events:
  { "event": "SYSTEM_STATUS", "payload": { cpu, memory, disk, network, platform } }
  { "event": "SYSTEM_ALERT",  "level": "warning", "message": "CPU at 92%" }

Fixes vs. the legacy version:
  - Config now loads through utils.file_utils.load_config() (shared/config)
    instead of a locally duplicated DEFAULT_CFG merge, keeping one source
    of truth for thresholds project-wide.
  - `asyncio.get_event_loop()` (deprecated) replaced with
    `asyncio.get_running_loop()`.
"""

import asyncio
import json
import logging
import platform
import time

import websockets

from utils.file_utils import load_config

logger = logging.getLogger("pandora.sysmon")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not installed — system monitoring unavailable")

DEFAULT_CFG = {
    "node_ws_uri": "ws://localhost:8765",
    "poll_interval": 10,
    "cpu_alert_threshold": 90,
    "mem_alert_threshold": 95,
    "disk_alert_threshold": 90,
    "network_sample_seconds": 1,
}


def _resolved_cfg(overrides: dict) -> dict:
    shared_settings = load_config().get("settings", {}).get("system_monitor", {})
    return {**DEFAULT_CFG, **shared_settings, **overrides}


def collect_status(network_sample_seconds: int = 1) -> dict:
    """Collect a point-in-time system snapshot."""
    if not PSUTIL_AVAILABLE:
        return {"error": "psutil not available"}

    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count(logical=True)

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    net_before = psutil.net_io_counters()
    time.sleep(network_sample_seconds)
    net_after = psutil.net_io_counters()

    return {
        "timestamp": time.time(),
        "cpu": {"percent": cpu_percent, "cores": cpu_count},
        "memory": {
            "used_gb": round(mem.used / (1024 ** 3), 2),
            "total_gb": round(mem.total / (1024 ** 3), 2),
            "percent": mem.percent,
        },
        "disk": {
            "used_gb": round(disk.used / (1024 ** 3), 1),
            "total_gb": round(disk.total / (1024 ** 3), 1),
            "percent": disk.percent,
        },
        "network": {
            "sent_mb_s": round((net_after.bytes_sent - net_before.bytes_sent) / (1024 ** 2), 2),
            "recv_mb_s": round((net_after.bytes_recv - net_before.bytes_recv) / (1024 ** 2), 2),
        },
        "platform": {
            "os": platform.system(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
    }


def check_alerts(status: dict, cfg: dict) -> list[dict]:
    alerts = []
    cpu = status.get("cpu", {}).get("percent", 0)
    mem = status.get("memory", {}).get("percent", 0)
    disk = status.get("disk", {}).get("percent", 0)

    if cpu > cfg["cpu_alert_threshold"]:
        alerts.append({"level": "warning", "message": f"CPU load is critically high at {cpu}%."})
    if mem > cfg["mem_alert_threshold"]:
        alerts.append({"level": "warning", "message": f"Memory usage is critically high at {mem}%."})
    if disk > cfg["disk_alert_threshold"]:
        alerts.append({"level": "info", "message": f"Disk usage is at {disk}%."})

    return alerts


class SystemMonitorService:
    """Periodically collects telemetry and pushes it to Node."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = _resolved_cfg(cfg or {})
        self._ws = None
        self._running = False

    async def _emit(self, payload: dict) -> None:
        if self._ws:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.warning("WS send failed: %s", exc)

    async def _push_status(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            status = await loop.run_in_executor(
                None, collect_status, self.cfg["network_sample_seconds"]
            )
            await self._emit({"event": "SYSTEM_STATUS", "payload": status})

            for alert in check_alerts(status, self.cfg):
                await self._emit({"event": "SYSTEM_ALERT", "payload": alert})
        except Exception as exc:
            logger.error("Status collection failed: %s", exc)

    async def _poll_loop(self) -> None:
        logger.info("System monitor polling every %ds", self.cfg["poll_interval"])
        while self._running:
            await self._push_status()
            await asyncio.sleep(self.cfg["poll_interval"])

    async def _handle_messages(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("event") == "SYSTEM_STATUS_REQUEST":
                await self._push_status()

    async def _connect_and_run(self) -> None:
        uri = self.cfg["node_ws_uri"]
        reconnect_delay = 2
        while self._running:
            try:
                async with websockets.connect(uri) as ws:
                    self._ws = ws
                    logger.info("System monitor connected to Node at %s", uri)
                    reconnect_delay = 2
                    await asyncio.gather(self._poll_loop(), self._handle_messages(ws))
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                logger.warning("WS lost: %s — retrying in %ds", exc, reconnect_delay)
                self._ws = None
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def start(self) -> None:
        self._running = True
        asyncio.run(self._connect_and_run())

    def stop(self) -> None:
        self._running = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    SystemMonitorService().start()
