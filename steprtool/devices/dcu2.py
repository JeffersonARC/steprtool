"""DCU-2 (hy-gain) rotator controller.

Wire protocol (from the hy-gain DCU-1/DCU-2 manual; both share the same
command set):

  Serial: 4800,N,8,1 ASCII.
  Command "AP1xxx;"  -- set target bearing, xxx is 3 ASCII digits 000..359
  Command "AM1;"     -- start rotation to the target bearing
  Command ";"        -- reset / clear previous command

We send "AP1xxx;AM1;" as one combined write. The controller is fire-and-forget
(no bearing reported back), so success status is reported as soon as the
write completes; the wait timer covers the worst-case rotation time.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..config import Dcu2Config
from .base import (
    DeviceController,
    LastAction,
    Operator,
    format_bytes_hex,
    now_iso,
)
from .step100 import CommandResult


logger = logging.getLogger(__name__)


class Dcu2Controller(DeviceController):
    """DCU-2 rotator controller."""

    def __init__(self, cfg: Dcu2Config, socketio):
        super().__init__("dcu2", cfg.serial, cfg.wait_seconds, socketio)
        self.cfg = cfg

    # -------------------------------------------------- frame construction

    @staticmethod
    def _validate_azimuth(azimuth: int) -> None:
        if not isinstance(azimuth, int):
            raise ValueError("azimuth must be an integer")
        if azimuth < 0 or azimuth > 359:
            raise ValueError("azimuth must be between 0 and 359")

    def build_azimuth_command(self, azimuth: int) -> bytes:
        """Return the ASCII bytes for 'set bearing and rotate'."""
        self._validate_azimuth(azimuth)
        return f"AP1{azimuth:03d};AM1;".encode("ascii")

    # -------------------------------------------------------------- actions

    def change_direction(self, azimuth: int, operator: Operator) -> CommandResult:
        self._try_acquire()
        try:
            frame = self.build_azimuth_command(azimuth)
            hex_str = format_bytes_hex(frame)
            status = self._write_bytes(frame)
        except Exception:
            self._release_lock()
            raise

        detail = f"azimuth {azimuth}\u00b0   ASCII: {frame.decode('ascii')}"
        last = LastAction(
            device=self.name,
            action="Change Direction",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            operator=operator.label(),
            timestamp=now_iso(),
        )
        logger.info(
            "[%s] dcu2.change_direction %s | bytes %s | status=%s",
            operator.label(), detail, hex_str, status,
        )
        self._broadcast_last_action(last)
        self._start_wait_timer()
        return CommandResult(
            action="Change Direction",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            wait_seconds=self.wait_seconds,
        )
