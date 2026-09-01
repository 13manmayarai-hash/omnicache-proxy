"""
SingleFlight: In-flight request deduplication and coalescing bus.
Prevents cache stampedes by ensuring only ONE upstream request executes for concurrent identical queries.
"""

import asyncio
from typing import Dict, Any, Callable, Awaitable, Tuple, Optional

class FlightCall:
    def __init__(self):
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waiters: int = 1

class SingleFlightGroup:
    def __init__(self):
        self._flights: Dict[str, FlightCall] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        key: str,
        fn: Callable[[], Awaitable[Tuple[Dict[str, Any], Optional[list]]]],
        timeout_seconds: float = 30.0
    ) -> Tuple[Dict[str, Any], Optional[list], bool]:
        """
        Executes fn only once for the given key concurrently.
        Returns (response_payload, stream_chunks, is_leader).
        is_leader is True if this invocation was the one that executed the function,
        and False if it coalesced onto an already in-flight execution.
        """
        async with self._lock:
            if key in self._flights:
                flight = self._flights[key]
                flight.waiters += 1
                is_leader = False
            else:
                flight = FlightCall()
                self._flights[key] = flight
                is_leader = True

        if not is_leader:
            # Wait for leader to finish with timeout
            try:
                result = await asyncio.wait_for(asyncio.shield(flight.future), timeout=timeout_seconds)
                return result[0], result[1], False
            except Exception as e:
                # If leader failed or timed out, fall back
                raise e

        # Leader execution
        try:
            res_payload, chunks = await fn()
            if not flight.future.done():
                flight.future.set_result((res_payload, chunks))
            return res_payload, chunks, True
        except Exception as e:
            if not flight.future.done():
                flight.future.set_exception(e)
            raise e
        finally:
            async with self._lock:
                if key in self._flights:
                    del self._flights[key]

flight_bus = SingleFlightGroup()
