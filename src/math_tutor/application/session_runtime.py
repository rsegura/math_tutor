"""Process-local cancellation for speech/model generations.

Durable educational state never lives here.  A mutation cancels outstanding
generation before it crosses the persistence fence, preventing stale speech
from being released while the authoritative state changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock


@dataclass(frozen=True, slots=True)
class Generation:
    session_id: str
    sequence: int
    _cancelled: Event = field(compare=False, repr=False)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def generation_id(self) -> str:
        return f"gen-{self.sequence}"


class SessionRuntime:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[str, Generation] = {}
        self._sequence = 0

    def start_generation(self, session_id: str) -> Generation:
        with self._lock:
            previous = self._active.get(session_id)
            if previous is not None:
                previous._cancelled.set()
            self._sequence += 1
            generation = Generation(session_id, self._sequence, Event())
            self._active[session_id] = generation
            return generation

    def cancel_generation(self, session_id: str) -> None:
        with self._lock:
            generation = self._active.pop(session_id, None)
            if generation is not None:
                generation._cancelled.set()

    def active_generation(self, session_id: str) -> Generation | None:
        with self._lock:
            return self._active.get(session_id)

    def is_active(self, session_id: str, generation_id: str) -> bool:
        with self._lock:
            generation = self._active.get(session_id)
            return generation is not None and not generation.cancelled and generation.generation_id == generation_id

    def consume_generation(self, session_id: str, generation_id: str) -> bool:
        """Atomically claim the still-active generation for one mutation."""

        with self._lock:
            generation = self._active.get(session_id)
            if generation is None or generation.cancelled or generation.generation_id != generation_id:
                return False
            generation._cancelled.set()
            del self._active[session_id]
            return True
