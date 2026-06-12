"""DCU-2 (hy-gain) rotator controller.

Wire protocol: 4800,N,8,1 ASCII (per device manual).
  AP1xxx;  set target bearing (xxx = 000..359, 3 digits)
  AM1;     start rotation
  ;        reset / clear previous command

We send "AP1xxx;AM1;" combined. The controller does not report bearing back,
so we cannot tell when motion finishes; the wait timer covers the worst-case
rotation time.
"""

from __future__ import annotations

import logging
from time import sleep


from ..config import Dcu2Config
from .base import (
    DeviceController,
    LastAction,
    Operator,
    format_bytes_hex,
    now_iso,
)
from .sda100 import CommandResult


logger = logging.getLogger(__name__)


class Dcu2Controller(DeviceController):
    def __init__(self, cfg: Dcu2Config, socketio, activity_feed=None):
        super().__init__("dcu2", cfg.serial, cfg.wait_seconds, socketio)
        self.cfg = cfg
        self.activity = activity_feed

    @staticmethod
    def _validate_azimuth(azimuth: int) -> None:
        if not isinstance(azimuth, int):
            raise ValueError("azimuth must be an integer")
        if azimuth < 0 or azimuth > 359:
            raise ValueError("azimuth must be between 0 and 359")

    def build_azimuth_command(self, azimuth: int) -> bytes:
        self._validate_azimuth(azimuth)
        return f"AP1{azimuth:03d};".encode("ascii")

    def change_direction(self, azimuth: int, operator: Operator) -> CommandResult:
        self._try_acquire()
        try:
            frame = self.build_azimuth_command(azimuth)
            hex_str = format_bytes_hex(frame)
            status = self._write_bytes(frame)
            sleep(0.5)
            frame2 = "AM1;".encode("ascii")
            hex_str2 = format_bytes_hex(frame2)
            status2 = self._write_bytes(frame2)
        except Exception:
            self._release_lock()
            raise

        detail = f"azimuth {azimuth}\u00b0   ASCII: {frame.decode('ascii')}"
        last = LastAction(
            device=self.name,
            action="Change Direction",
            detail=detail,
            bytes_hex=hex_str,
            status=status & status2,
            operator=operator.label(),
            timestamp=now_iso(),
            inputs={"dcu2_az": azimuth},
        )
        if self.activity is not None:
            self.activity.record(
                f"{operator.name} {operator.callsign} set azimuth to {azimuth}\u00b0"
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
