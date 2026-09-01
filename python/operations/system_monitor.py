"""
PANDORA System Monitor
=======================
Collects system telemetry and sends it to Node orchestrator via WebSocket.

Outbound events:
  { "event": "SYSTEM_STATUS", "data": { cpu, memory, disk, network, platform } }
  { "event": "SYSTEM_ALERT",  "level": "warning", "message": "CPU at 92%" }

Reference: Jarvis system_status() and system_monitor module in Jarvis_v1_7r_core.py
  - Retains: CPU, memory, network I/O collection via psutil
  - Retains: boot sequence diagnostics display (cpu_usage, mem, net_io)
  - Retains: high CPU/memory alert thresholds (90% CPU, 95% RAM)
  - Retains: cross-platform support (Windows / Linux / macOS)
  - Adds: structured JSON payload for Electron HUD rendering
  - Removes: speak() calls — alerts routed through Node instead
  - Removes: win32gui notification monitoring (moved to Electron layer)
"""

import asyncio
import json
import logging
import platform
import time

import websockets

logger = logging.getLogger("pandora.sysmon")

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not installed — system monitoring unavailable")

DEFAULT_CFG = {
    "node_ws_uri": "ws://localhost:8765",
    "poll_interval": 10,        # seconds between telemetry pushes
    "cpu_alert_threshold": 90,  # % — matches Jarvis threshold
    "mem_alert_threshold": 95,  # %
    "network_sample_seconds": 1,
}


# ---------------------------------------------------------------------------
# Telemetry collection
# ---------------------------------------------------------------------------
def collect_status(network_sample_seconds: int = 1) -> dict:
    """
    Collect a point-in-time system snapshot.
    Mirrors Jarvis boot sequence diagnostics + system_monitor.report_detailed_status().
    """
    if not PSUTIL_AVAILABLE:
        return {"error": "psutil not available"}

    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count(logical=True)

    # Memory
    mem = psutil.virtual_memory()
    mem_used_gb = round(mem.used / (1024 ** 3), 2)
    mem_total_gb = round(mem.total / (1024 ** 3), 2)
    mem_percent = mem.percent

    # Disk
    disk = psutil.disk_usage("/")
    disk_used_gb = round(disk.used / (1024 ** 3), 1)
    disk_total_gb = round(disk.total / (1024 ** 3), 1)
    disk_percent = disk.percent

    # Network I/O (sampled over network_sample_seconds)
    net_before = psutil.net_io_counters()
    time.sleep(network_sample_seconds)
    net_after = psutil.net_io_counters()
    net_sent_mb = round((net_after.bytes_sent - net_before.bytes_sent) / (1024 ** 2), 2)
    net_recv_mb = round((net_after.bytes_recv - net_before.bytes_recv) / (1024 ** 2), 2)

    # Platform
    os_name = platform.system()
    os_version = platform.version()
    machine = platform.machine()

    return {
        "timestamp": time.time(),
        "cpu": {
            "percent": cpu_percent,
            "cores": cpu_count,
        },
        "memory": {
            "used_gb": mem_used_gb,
            "total_gb": mem_total_gb,
            "percent": mem_percent,
        },
        "disk": {
            "used_gb": disk_used_gb,
            "total_gb": disk_total_gb,
            "percent": disk_percent,
        },
        "network": {
            "sent_mb_s": net_sent_mb,
            "recv_mb_s": net_recv_mb,
        },
        "platform": {
            "os": os_name,
            "version": os_version,
            "machine": machine,
        },
    }


def check_alerts(status: dict, cfg: dict) -> list[dict]:
    """
    Return list of alert dicts for any threshold breach.
    Mirrors Jarvis: cpu > 90% or mem > 95% → speak warning.
    """
    alerts = []
    cpu = status.get("cpu", {}).get("percent", 0)
    mem = status.get("memory", {}).get("percent", 0)
    disk = status.get("disk", {}).get("percent", 0)

    if cpu > cfg.get("cpu_alert_threshold", 90):
        alerts.append({"level": "warning", "message": f"CPU load is critically high at {cpu}%."})

    if mem > cfg.get("mem_alert_threshold", 95):
        alerts.append({"level": "warning", "message": f"Memory usage is critically high at {mem}%."})

    if disk > 90:
        alerts.append({"level": "info", "message": f"Disk usage is at {disk}%."})

    return alerts


# ---------------------------------------------------------------------------
# System Monitor Service
# ---------------------------------------------------------------------------
class SystemMonitorService:
    """
    Periodically collects telemetry and pushes it to Node.
    Also responds to on-demand SYSTEM_STATUS_REQUEST events.
    """

    def __init__(self, cfg: dict):
        self.cfg = {**DEFAULT_CFG, **cfg}
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
            status = await asyncio.get_event_loop().run_in_executor(
                None, collect_status, self.cfg["network_sample_seconds"]
            )
            await self._emit({"event": "SYSTEM_STATUS", "data": status})

            for alert in check_alerts(status, self.cfg):
                await self._emit({**alert, "event": "SYSTEM_ALERT"})
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
                    await asyncio.gather(
                        self._poll_loop(),
                        self._handle_messages(ws),
                    )
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    service = SystemMonitorService({})
    service.start()
