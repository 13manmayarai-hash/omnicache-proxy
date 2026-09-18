"""
Built-in Resilient Self-Keepalive Worker.
Prevents cloud container spin-downs (e.g. Render Free Tier 15-minute idle sleep)
by issuing scheduled lightweight HTTP health probes against the deployment.
"""

import time
import asyncio
import threading
import logging
import httpx
from typing import Dict, Any, Optional
from core.config import config

logger = logging.getLogger("omnicache.keepalive")


class KeepAliveWorker:
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.pings_sent = 0
        self.pings_failed = 0
        self.last_ping_status: Optional[int] = None
        self.last_ping_timestamp: Optional[float] = None
        self.last_error: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    def get_target_url(self) -> str:
        """Determines the target health check URL."""
        if config.KEEP_ALIVE_URL:
            return config.KEEP_ALIVE_URL
        if config.PUBLIC_URL:
            base = config.PUBLIC_URL.rstrip("/")
            if not base.endswith("/healthz"):
                return f"{base}/healthz"
            return base
        return f"http://127.0.0.1:{config.PORT}/healthz"

    def is_enabled(self) -> bool:
        # If explicitly enabled, or if running in Render/Cloud environment with public URL
        return bool(config.KEEP_ALIVE_ENABLED or config.RENDER_EXTERNAL_URL or config.PUBLIC_URL)

    def start(self):
        """Starts the background keepalive worker thread."""
        if self._running:
            return
        if not self.is_enabled():
            logger.info("[KeepAlive] Worker disabled (no cloud environment or KEEP_ALIVE_ENABLED=false).")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="omnicache-keepalive-worker",
            daemon=True
        )
        self._thread.start()
        logger.info(f"⚡ [KeepAlive] Background worker active. Target: {self.get_target_url()} every {config.KEEP_ALIVE_INTERVAL_SECONDS}s")

    def stop(self):
        """Stops the background worker."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker_routine())
        except Exception as exc:
            logger.debug(f"[KeepAlive] Worker loop stopped: {exc}")
        finally:
            self._loop.close()

    async def _worker_routine(self):
        # Initial sleep before first ping to allow server socket initialization
        await asyncio.sleep(15.0)
        
        timeout = httpx.Timeout(15.0, connect=10.0)
        limits = httpx.Limits(max_connections=5, max_keepalive_connections=2)
        self._client = httpx.AsyncClient(timeout=timeout, limits=limits, headers={"User-Agent": "OmniCache-KeepAlive/3.1.0"})

        while self._running:
            target_url = self.get_target_url()
            try:
                resp = await self._client.get(target_url)
                self.pings_sent += 1
                self.last_ping_status = resp.status_code
                self.last_ping_timestamp = time.time()
                self.last_error = None
                logger.debug(f"[KeepAlive] Ping to {target_url} returned HTTP {resp.status_code}")
            except Exception as exc:
                self.pings_failed += 1
                self.last_ping_timestamp = time.time()
                self.last_error = str(exc)
                logger.debug(f"[KeepAlive] Ping to {target_url} failed: {exc}")

            # Sleep in 1-second slices so shutdown is instant
            interval = max(30, config.KEEP_ALIVE_INTERVAL_SECONDS)
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1.0)

        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def ping_now(self) -> Dict[str, Any]:
        """Performs an immediate manual keepalive ping."""
        target_url = self.get_target_url()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                t0 = time.perf_counter()
                resp = await client.get(target_url, headers={"User-Agent": "OmniCache-KeepAlive-Manual/3.1.0"})
                elapsed_ms = (time.perf_counter() - t0) * 1000
                self.pings_sent += 1
                self.last_ping_status = resp.status_code
                self.last_ping_timestamp = time.time()
                self.last_error = None
                return {
                    "status": "success" if resp.status_code == 200 else "error",
                    "status_code": resp.status_code,
                    "latency_ms": round(elapsed_ms, 2),
                    "target_url": target_url,
                    "timestamp": self.last_ping_timestamp
                }
        except Exception as exc:
            self.pings_failed += 1
            self.last_ping_timestamp = time.time()
            self.last_error = str(exc)
            return {
                "status": "error",
                "error": str(exc),
                "target_url": target_url,
                "timestamp": self.last_ping_timestamp
            }

    def get_status(self) -> Dict[str, Any]:
        """Returns telemetry status for /healthz and /stats."""
        return {
            "enabled": self.is_enabled(),
            "target_url": self.get_target_url(),
            "interval_seconds": config.KEEP_ALIVE_INTERVAL_SECONDS,
            "pings_sent": self.pings_sent,
            "pings_failed": self.pings_failed,
            "last_ping_status": self.last_ping_status,
            "last_ping_timestamp": self.last_ping_timestamp,
            "last_error": self.last_error
        }


keepalive_worker = KeepAliveWorker()
