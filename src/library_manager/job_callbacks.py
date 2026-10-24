from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class JobCallbacks:
    on_log: Callable[[str], None]
    on_progress: Callable[[int, int], None]
    should_cancel: Callable[[], bool]


# Back-compat alias
SortCallbacks = JobCallbacks
