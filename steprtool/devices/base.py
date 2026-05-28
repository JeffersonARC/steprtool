"""Shared device-controller machinery.

Each device (SDA 100, DCU-2) has:
  * a single-in-flight lock with a configured wait time
  * a serial port (or "mock" mode that skips the write)
  * broadcasts to all connected web clients via Socket.IO

Flask-SocketIO runs in threading mode: countdowns and lock releases run in
real OS threads spawned via socketio.start_background_task, while
socketio.sleep() behaves like time.sleep() under threading mode.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import serial

from ..config import SerialConfig


logger = logging.getLogger(__name__)


_PARITY_MAP = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
    "M": serial.PARITY_MARK,
    "S": serial.PARITY_SPACE,
}
_STOPBITS_MAP = {
    "1": serial.STOPBITS_ONE,
    "1.5": serial.STOPBITS_ONE_POINT_FIVE,
    "2": serial.STOPBITS_TWO,
}
_BYTESIZE_MAP = {
    5: serial.FIVEBITS,
    6: serial.SIXBITS,
    7: serial.SEVENBITS,
    8: serial.EIGHTBITS,
}


def format_bytes_hex(data: bytes) -> str:
    """Render bytes for the UI / log: uppercase, space-separated, no 0x."""
    return " ".join(f"{b:02X}" for b in data)


@dataclass
class Operator:
    """Identifies who issued a command. Validated at the route layer."""
    name: str
    callsign: str

    def label(self) -> str:
        return f"{self.callsign} {self.name}".strip()


@dataclass
class LastAction:
    """Snapshot of the most recent command for any device, shared with clients."""
    device: str            # "sda100" or "dcu2"
    action: str
    detail: str
    bytes_hex: str
    status: str            # "SENT" | "MOCK" | "NOT IMPLEMENTED"
    operator: str
    timestamp: str
    inputs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "action": self.action,
            "detail": self.detail,
            "bytes_hex": self.bytes_hex,
            "status": self.status,
            "operator": self.operator,
            "timestamp": self.timestamp,
            "inputs": self.inputs,
        }


class DeviceBusy(Exception):
    """Raised when a command is rejected because the device is locked."""
    def __init__(self, seconds_remaining: int):
        self.seconds_remaining = seconds_remaining
        super().__init__(f"device busy, {seconds_remaining} seconds remaining")


class DeviceController:
    """Base class providing serial I/O and the in-flight lock."""

    def __init__(self, name: str, serial_cfg: SerialConfig, wait_seconds: int, socketio):
        self.name = name
        self.serial_cfg = serial_cfg
        self.wait_seconds = wait_seconds
        self.socketio = socketio

        self._state_lock = threading.Lock()
        self._busy = False
        self._busy_seconds_total = 0
        self._busy_seconds_remaining = 0

    def state(self) -> dict:
        with self._state_lock:
            return {
                "device": self.name,
                "busy": self._busy,
                "seconds_remaining": self._busy_seconds_remaining,
                "seconds_total": self._busy_seconds_total,
                "mock": self.serial_cfg.is_mock,
                "port": self.serial_cfg.port if not self.serial_cfg.is_mock else "MOCK",
            }

    def _try_acquire(self) -> None:
        with self._state_lock:
            if self._busy:
                raise DeviceBusy(self._busy_seconds_remaining)
            self._busy = True
            self._busy_seconds_total = self.wait_seconds
            self._busy_seconds_remaining = self.wait_seconds

    def _release_lock(self) -> None:
        with self._state_lock:
            self._busy = False
            self._busy_seconds_total = 0
            self._busy_seconds_remaining = 0

    def _start_wait_timer(self) -> None:
        def _run():
            total = self.wait_seconds
            self.socketio.emit(
                "device_locked",
                {"device": self.name, "seconds_remaining": total, "seconds_total": total},
            )
            remaining = total
            while remaining > 0:
                self.socketio.sleep(1)
                remaining -= 1
                with self._state_lock:
                    self._busy_seconds_remaining = remaining
                self.socketio.emit(
                    "device_countdown",
                    {"device": self.name, "seconds_remaining": remaining,
                     "seconds_total": total},
                )
            with self._state_lock:
                self._busy = False
                self._busy_seconds_total = 0
                self._busy_seconds_remaining = 0
            self.socketio.emit("device_unlocked", {"device": self.name})

        self.socketio.start_background_task(_run)

    def _write_bytes(self, data: bytes) -> str:
        if self.serial_cfg.is_mock:
            logger.info("%s MOCK write: %s", self.name, format_bytes_hex(data))
            return "MOCK"

        ser = serial.Serial(
            port=self.serial_cfg.port,
            baudrate=self.serial_cfg.baud,
            bytesize=_BYTESIZE_MAP[self.serial_cfg.bytesize],
            parity=_PARITY_MAP[self.serial_cfg.parity],
            stopbits=_STOPBITS_MAP[self.serial_cfg.stopbits],
            dsrdtr=False,
            rtscts=False,
            timeout=2.0,
            write_timeout=2.0,
        )
        try:
            ser.dtr = self.serial_cfg.dtr
            ser.rts = self.serial_cfg.rts
            ser.write(data)
            ser.flush()
        finally:
            ser.close()
        logger.info("%s SENT %s: %s", self.name, self.serial_cfg.port, format_bytes_hex(data))
        return "SENT"

    def _broadcast_last_action(self, last: LastAction) -> None:
        self.socketio.emit("last_action", last.to_dict())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
