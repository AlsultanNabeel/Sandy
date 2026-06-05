import threading
from typing import Optional

_local = threading.local()


def set_trace_id(trace_id: Optional[str]) -> None:
    setattr(_local, "trace_id", trace_id)


def get_trace_id() -> Optional[str]:
    return getattr(_local, "trace_id", None)


def clear_trace_id() -> None:
    if hasattr(_local, "trace_id"):
        delattr(_local, "trace_id")
