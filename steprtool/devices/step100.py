"""Step 100 (StepIR) controller.

Wire protocol (from the legacy C++ source):
  11-byte command frame, no response from device.

  Offset  Value          Meaning
  ------  -------------  ----------------------------------------------
    0     0x40 ('@')     start
    1     0x41 ('A')     unit address
    2     0x00           constant
    3     freq_hi        bits 23..16 of frequency in TENS OF HZ
    4     freq_mid       bits 15..8
    5     freq_lo        bits 7..0
    6     0x00           constant
    7     direction      0x00 normal / 0x40 180 / 0x80 bidirectional
    8     0x52 ('R')     command (retune)
    9     0x00           constant
   10     0x0D (CR)      terminator

Frequency: the UI accepts kHz (integer). On the wire we send tens of Hz
(kHz * 100), encoded as a big-endian 24-bit integer.

The Home and Calibrate command frames are not yet defined — those buttons
return a 'NOT IMPLEMENTED' response and do not lock the device.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..config import Step100Config
from .base import (
    DeviceController,
    LastAction,
    Operator,
    format_bytes_hex,
    now_iso,
)


logger = logging.getLogger(__name__)


# Limits for the on-wire 24-bit field, expressed in kHz.
MAX_WIRE_TENS_OF_HZ = 0xFFFFFF
MAX_FREQ_KHZ = MAX_WIRE_TENS_OF_HZ // 100   # 167_772 kHz (~167.77 MHz)
MIN_FREQ_KHZ = 1                            # anything > 0 is technically encodable


@dataclass
class CommandResult:
    """Returned to the route layer after a command is processed."""
    action: str
    detail: str
    bytes_hex: str
    status: str          # "SENT" | "MOCK" | "NOT IMPLEMENTED"
    wait_seconds: int    # 0 if the action does not lock the device


class Step100Controller(DeviceController):
    """Builds command frames, writes them out, and broadcasts results."""

    def __init__(self, cfg: Step100Config, socketio):
        super().__init__("step100", cfg.serial, cfg.wait_seconds, socketio)
        self.cfg = cfg

    # -------------------------------------------------- frame construction

    @staticmethod
    def _validate_freq_khz(freq_khz: int) -> None:
        if not isinstance(freq_khz, int):
            raise ValueError("frequency must be an integer (kHz)")
        if freq_khz < MIN_FREQ_KHZ:
            raise ValueError(f"frequency must be >= {MIN_FREQ_KHZ} kHz")
        if freq_khz > MAX_FREQ_KHZ:
            raise ValueError(
                f"frequency {freq_khz} kHz exceeds protocol maximum "
                f"({MAX_FREQ_KHZ} kHz)"
            )

    def build_frequency_frame(self, freq_khz: int) -> bytes:
        """Return the 11 bytes for a 'change frequency' command."""
        self._validate_freq_khz(freq_khz)
        tens_of_hz = freq_khz * 100
        f_hi = (tens_of_hz >> 16) & 0xFF
        f_mid = (tens_of_hz >> 8) & 0xFF
        f_lo = tens_of_hz & 0xFF
        return bytes([
            0x40,                       # '@'
            0x41,                       # 'A'
            0x00,
            f_hi,
            f_mid,
            f_lo,
            0x00,
            self.cfg.direction_byte,    # element direction
            0x52,                       # command: retune
            0x00,
            0x0D,                       # CR
        ])

    # -------------------------------------------------------------- actions

    def change_frequency(self, freq_khz: int, operator: Operator) -> CommandResult:
        """Build, send (or mock), broadcast, and start the wait timer."""
        self._try_acquire()
        try:
            frame = self.build_frequency_frame(freq_khz)
            hex_str = format_bytes_hex(frame)
            status = self._write_bytes(frame)
        except Exception:
            # Roll the lock back if we failed before starting the timer.
            self._release_lock()
            raise

        detail = f"{freq_khz} kHz, direction={self.cfg.direction}"
        last = LastAction(
            device=self.name,
            action="Change Frequency",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            operator=operator.label(),
            timestamp=now_iso(),
        )
        logger.info(
            "[%s] step100.change_frequency %s | bytes %s | status=%s",
            operator.label(), detail, hex_str, status,
        )
        self._broadcast_last_action(last)
        self._start_wait_timer()
        return CommandResult(
            action="Change Frequency",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            wait_seconds=self.wait_seconds,
        )

    def home(self, operator: Operator) -> CommandResult:
        """Step 100 Home — wire bytes not yet defined."""
        last = LastAction(
            device=self.name,
            action="Home",
            detail="command bytes pending",
            bytes_hex="",
            status="NOT IMPLEMENTED",
            operator=operator.label(),
            timestamp=now_iso(),
        )
        logger.info("[%s] step100.home (not implemented)", operator.label())
        self._broadcast_last_action(last)
        return CommandResult(
            action="Home",
            detail="command bytes pending",
            bytes_hex="",
            status="NOT IMPLEMENTED",
            wait_seconds=0,
        )

    def calibrate(self, operator: Operator) -> CommandResult:
        """Step 100 Calibrate — wire bytes not yet defined."""
        last = LastAction(
            device=self.name,
            action="Calibrate",
            detail="command bytes pending",
            bytes_hex="",
            status="NOT IMPLEMENTED",
            operator=operator.label(),
            timestamp=now_iso(),
        )
        logger.info("[%s] step100.calibrate (not implemented)", operator.label())
        self._broadcast_last_action(last)
        return CommandResult(
            action="Calibrate",
            detail="command bytes pending",
            bytes_hex="",
            status="NOT IMPLEMENTED",
            wait_seconds=0,
        )
