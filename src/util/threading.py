"""Run blocking infrastructure calls without blocking the async event loop.

This module provides the small `asyncio.to_thread` adapter used for synchronous
database operations. It does not own database access or application concurrency
policy; persistence belongs to `src.services.job_state`, and callers decide which
work to offload.
"""

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

_Params = ParamSpec("_Params")
_Result = TypeVar("_Result")


async def run_db(
    function: Callable[_Params, _Result],
    *args: _Params.args,
    **kwargs: _Params.kwargs,
) -> _Result:
    """Run synchronous database work in a worker thread."""
    return await asyncio.to_thread(function, *args, **kwargs)
