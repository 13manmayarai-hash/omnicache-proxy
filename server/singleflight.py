"""
SingleFlight: In-flight request deduplication and coalescing bus.
Prevents cache stampedes by ensuring only ONE upstream request executes for concurrent identical queries.
Supports distributed Redis coordination (SET NX EX, Pub/Sub, and polling) with automatic local in-memory fallback.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, Callable, Awaitable, Tuple, Optional

logger = logging.getLogger("omnicache.singleflight")

class FlightCall:
    def __init__(self):
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waiters: int = 1

class SingleFlightGroup:
    def __init__(self, redis_client=None, prefix: str = "omnicache:flight"):
        self._flights: Dict[str, FlightCall] = {}
        self._lock = asyncio.Lock()
        self._redis = redis_client
        self._prefix = prefix

    def set_redis_client(self, redis_client, prefix: str = "omnicache:flight"):
        """Attach or update the Redis client used for distributed deduplication."""
        self._redis = redis_client
        self._prefix = prefix

    async def _redis_call(self, method_name: str, *args, **kwargs) -> Any:
        """Helper to invoke synchronous or asynchronous Redis client methods safely."""
        client = self._redis
        if client is None:
            return None
        try:
            func = getattr(client, method_name, None)
            if func is None:
                return None
            res = func(*args, **kwargs)
            if asyncio.iscoroutine(res):
                res = await res
            return res
        except Exception as e:
            logger.debug("SingleFlight Redis call '%s' failed: %s", method_name, e)
            return None

    async def execute(
        self,
        key: str,
        fn: Callable[[], Awaitable[Tuple[Dict[str, Any], Optional[list]]]],
        timeout_seconds: float = 30.0
    ) -> Tuple[Dict[str, Any], Optional[list], bool]:
        """
        Executes fn only once for the given key concurrently.
        Returns (response_payload, stream_chunks, is_leader).
        is_leader is True if this invocation executed the function,
        and False if it coalesced onto an already in-flight execution.
        """
        # 1. Process-local coalescing gate
        async with self._lock:
            if key in self._flights:
                flight = self._flights[key]
                flight.waiters += 1
                is_local_leader = False
            else:
                flight = FlightCall()
                self._flights[key] = flight
                is_local_leader = True

        if not is_local_leader:
            # Process-local follower: wait for local leader execution
            try:
                result = await asyncio.wait_for(asyncio.shield(flight.future), timeout=timeout_seconds)
                return result[0], result[1], False
            except Exception as e:
                raise e

        # 2. Check if distributed Redis backend is configured
        redis_client = self._redis
        if redis_client is None:
            # Dynamic lookup in vector_cache storage
            try:
                from core.vector_cache import cache_instance
                if hasattr(cache_instance.storage, "client") and cache_instance.storage.client is not None:
                    redis_client = cache_instance.storage.client
                    self._redis = redis_client
            except Exception:
                pass

        # 3. Local-only fallback if Redis is not configured
        if redis_client is None:
            try:
                res_payload, chunks = await fn()
                if not flight.future.done():
                    flight.future.set_result((res_payload, chunks))
                return res_payload, chunks, True
            except Exception as e:
                if not flight.future.done():
                    flight.future.set_exception(e)
                    flight.future.add_done_callback(lambda f: f.exception())
                raise e
            finally:
                async with self._lock:
                    if key in self._flights and self._flights[key] is flight:
                        del self._flights[key]

        # 4. Distributed Redis Leader / Follower Execution
        lock_key = f"{self._prefix}:lock:{key}"
        data_key = f"{self._prefix}:data:{key}"
        chan_key = f"{self._prefix}:chan:{key}"
        lock_ttl = max(int(timeout_seconds) + 10, 15)

        try:
            acquired = await self._redis_call("set", lock_key, "locked", nx=True, ex=lock_ttl)
        except Exception as lock_err:
            logger.warning("SingleFlight: Redis lock attempt failed: %s. Falling back to local leader.", lock_err)
            acquired = True

        if acquired:
            # Distributed Leader
            try:
                res_payload, chunks = await fn()
                try:
                    data_blob = json.dumps({"payload": res_payload, "chunks": chunks})
                    await self._redis_call("set", data_key, data_blob, ex=min(int(timeout_seconds) + 15, 60))
                    await self._redis_call("publish", chan_key, "DONE")
                except Exception as save_err:
                    logger.debug("SingleFlight: Redis save coalesced response failed: %s", save_err)
                finally:
                    await self._redis_call("delete", lock_key)

                if not flight.future.done():
                    flight.future.set_result((res_payload, chunks))
                return res_payload, chunks, True
            except Exception as fn_err:
                try:
                    err_blob = json.dumps({"error": str(fn_err)})
                    await self._redis_call("set", data_key, err_blob, ex=10)
                    await self._redis_call("publish", chan_key, "ERROR")
                except Exception:
                    pass
                finally:
                    await self._redis_call("delete", lock_key)

                if not flight.future.done():
                    flight.future.set_exception(fn_err)
                    flight.future.add_done_callback(lambda f: f.exception())
                raise fn_err
            finally:
                async with self._lock:
                    if key in self._flights and self._flights[key] is flight:
                        del self._flights[key]

        # Distributed Follower: Poll Redis data_key with backoff
        try:
            poll_start = time.perf_counter()
            poll_interval = 0.05
            received_data = None

            while (time.perf_counter() - poll_start) < timeout_seconds:
                raw = await self._redis_call("get", data_key)
                if raw:
                    received_data = raw
                    break

                lock_active = await self._redis_call("exists", lock_key)
                if not lock_active:
                    # Leader released lock; allow brief 50ms buffer for data write before promotion
                    await asyncio.sleep(0.05)
                    raw = await self._redis_call("get", data_key)
                    if raw:
                        received_data = raw
                        break
                    # Lock cleared without data: attempt to promote self to leader
                    retry_lock = await self._redis_call("set", lock_key, "locked", nx=True, ex=lock_ttl)
                    if retry_lock:
                        try:
                            res_payload, chunks = await fn()
                            data_blob = json.dumps({"payload": res_payload, "chunks": chunks})
                            await self._redis_call("set", data_key, data_blob, ex=min(int(timeout_seconds) + 15, 60))
                            await self._redis_call("publish", chan_key, "DONE")
                            if not flight.future.done():
                                flight.future.set_result((res_payload, chunks))
                            return res_payload, chunks, True
                        finally:
                            await self._redis_call("delete", lock_key)
                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, 0.25)

            if received_data:
                parsed = json.loads(received_data)
                if "error" in parsed:
                    err = RuntimeError(f"Distributed SingleFlight leader failed: {parsed['error']}")
                    if not flight.future.done():
                        flight.future.set_exception(err)
                        flight.future.add_done_callback(lambda f: f.exception())
                    raise err

                res_payload = parsed.get("payload")
                chunks = parsed.get("chunks")
                if not flight.future.done():
                    flight.future.set_result((res_payload, chunks))
                return res_payload, chunks, False

            # Timeout waiting for distributed leader: fallback to local execution
            logger.warning("SingleFlight: Timed out waiting for distributed leader for key %s. Fallback execution.", key)
            res_payload, chunks = await fn()
            if not flight.future.done():
                flight.future.set_result((res_payload, chunks))
            return res_payload, chunks, True
        finally:
            async with self._lock:
                if key in self._flights and self._flights[key] is flight:
                    del self._flights[key]

flight_bus = SingleFlightGroup()

