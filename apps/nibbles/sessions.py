"""Per-browser memory: what this shopper looked at and what was said.

Held in process, which suits a single worker. Move to Redis before scaling
out, otherwise a shopper's second message can land on a worker that has never
heard of them.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Set

MAX_SESSIONS = 5_000
IDLE_SECONDS = 60 * 60 * 6


class SessionStore:
    def __init__(self, history_turns: int = 8) -> None:
        self.history_turns = history_turns
        self._data: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def _blank(self) -> Dict:
        return {"families": set(), "styles": set(), "sizes": [],
                "last_items": [], "history": [], "touched": time.time()}

    def get(self, sid: str) -> Dict:
        with self._lock:
            entry = self._data.get(sid)
            if entry is None:
                self._evict()
                entry = self._data[sid] = self._blank()
            entry["touched"] = time.time()
            return entry

    def _evict(self) -> None:
        """Drop idle sessions, then the oldest, so memory stays bounded."""
        now = time.time()
        stale = [k for k, v in self._data.items() if now - v["touched"] > IDLE_SECONDS]
        for key in stale:
            self._data.pop(key, None)
        while len(self._data) >= MAX_SESSIONS:
            oldest = min(self._data, key=lambda k: self._data[k]["touched"])
            self._data.pop(oldest, None)

    def remember_interest(self, sid: str, families: Set[str], styles: Set[str],
                          grams: Optional[int], items: List[Dict]) -> None:
        entry = self.get(sid)
        entry["families"].update(families)
        entry["styles"].update(styles)
        if grams:
            entry["sizes"] = (entry["sizes"] + [grams])[-5:]
        if items:
            entry["last_items"] = items[:5]

    def add_turn(self, sid: str, role: str, content: str) -> None:
        history = self.get(sid)["history"]
        history.append({"role": role, "content": content})
        limit = self.history_turns * 2
        del history[: max(0, len(history) - limit)]

    def history(self, sid: str) -> List[Dict]:
        return list(self.get(sid)["history"])

    def clear(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)

    @property
    def active(self) -> int:
        return len(self._data)
