from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from .models import TraceEvent


class TraceWriter(Protocol):
    def write(self, event: TraceEvent) -> None: ...

    def close(self) -> None: ...


def make_event(
    session_id: str,
    problem_id: str,
    event_index: int,
    event_type: str,
    payload: Mapping[str, Any],
) -> TraceEvent:
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return TraceEvent(1, session_id, problem_id, event_index, event_type, timestamp, payload)


class JsonlTraceWriter:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8", newline="\n")

    def write(self, event: TraceEvent) -> None:
        if self._file.closed:
            raise ValueError("trace writer is closed")
        line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> JsonlTraceWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
