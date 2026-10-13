import threading

from src.util.threading import run_db


async def test_run_db_executes_synchronous_call_off_event_loop():
    event_loop_thread = threading.get_ident()
    observed_threads: list[int] = []

    def _blocking_operation() -> str:
        observed_threads.append(threading.get_ident())
        return "done"

    result = await run_db(_blocking_operation)

    assert result == "done"
    assert observed_threads
    assert observed_threads[0] != event_loop_thread
