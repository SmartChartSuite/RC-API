"""Helpers for running blocking work outside async event loops."""

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
