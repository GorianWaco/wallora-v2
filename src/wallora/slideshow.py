"""Slideshow engine for Wallora (uses GLib timeout)."""
from __future__ import annotations

import random
from typing import Callable, List, Optional

from gi.repository import GLib

from wallora.config import Config


class Slideshow:
    def __init__(self, config: Config):
        self.config = config
        self._timeout_id: Optional[int] = None
        self._callback: Optional[Callable[[], None]] = None
        self._wallpapers: List[str] = []
        self._index: int = 0

    @property
    def is_running(self) -> bool:
        return self._timeout_id is not None

    def start(
        self,
        wallpapers: List[str],
        callback: Callable[[], None],
        interval_seconds: Optional[int] = None,
    ):
        """Start or restart the slideshow."""
        self.stop()
        if not wallpapers:
            return

        self._wallpapers = wallpapers
        self._callback = callback
        self._index = 0

        interval = interval_seconds or self.config.get("slideshow.interval_seconds", 300)
        interval = max(5, min(interval, 86400))  # 5s .. 24h

        self._timeout_id = GLib.timeout_add_seconds(interval, self._tick)
        # Immediately advance once
        self._advance()

    def stop(self):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None
        self._callback = None

    def _tick(self) -> bool:
        """GLib callback. Return True to keep repeating."""
        self._advance()
        return True

    def _advance(self):
        if not self._wallpapers or not self._callback:
            return

        if self.config.get("slideshow.random", True):
            path = random.choice(self._wallpapers)
        else:
            self._index = (self._index + 1) % len(self._wallpapers)
            path = self._wallpapers[self._index]

        # Update last used in a non-blocking way
        try:
            self._callback()
        except Exception as e:
            print("Slideshow callback error:", e)

        # The actual setting happens in the callback (passed from window)
        # We just trigger it.

    def get_current_playlist(self) -> List[str]:
        return list(self._wallpapers)

    def update_interval(self, seconds: int):
        if self.is_running:
            # Restart with new interval
            was_running = self.is_running
            cb = self._callback
            walls = self._wallpapers
            self.stop()
            if was_running and cb:
                self.start(walls, cb, seconds)

    def advance_next(self):
        """Manually go to the next wallpaper (used by UI Next button)."""
        if not self._wallpapers or not self._callback:
            return
        self._advance_manual(forward=True)

    def advance_prev(self):
        """Manually go to the previous wallpaper."""
        if not self._wallpapers or not self._callback:
            return
        self._advance_manual(forward=False)

    def _advance_manual(self, forward: bool):
        if self.config.get("slideshow.random", True):
            # In random mode we still pick random on manual too
            path = random.choice(self._wallpapers)
        else:
            if forward:
                self._index = (self._index + 1) % len(self._wallpapers)
            else:
                self._index = (self._index - 1) % len(self._wallpapers)
            path = self._wallpapers[self._index]

        try:
            self._callback()
        except Exception as e:
            print("Slideshow manual advance error:", e)

    def get_current_index(self) -> int:
        return self._index

    def get_playlist_length(self) -> int:
        return len(self._wallpapers)
