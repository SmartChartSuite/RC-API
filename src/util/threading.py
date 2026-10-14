"""Run blocking infrastructure calls without blocking the async event loop.

This module provides the small `asyncio.to_thread` adapter used for synchronous
database operations. It does not own database access or application concurrency
policy; persistence belongs to the `src.services.job_state` package, and callers decide which
work to offload.
"""

import asyncio
from collections.abc import Callable


async def run_db[**Params, Result](
    function: Callable[Params, Result],
    *args: Params.args,
    **kwargs: Params.kwargs,
) -> Result:
    """Run synchronous database work in a worker thread."""
    return await asyncio.to_thread(function, *args, **kwargs)
