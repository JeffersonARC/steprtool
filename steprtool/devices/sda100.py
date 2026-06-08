"""Step 100 (StepIR) controller.

Wire protocol (per SteppIR "Transceiver Interface" doc, 06/23/11):

  11-byte command frame, no response from device.

  Offset (1-indexed in doc, shown 0-indexed here)
    0   0x40  '@'
    1   0x41  'A'
    2   0x00
    3   freq_hi   bits 23..16  ┐  24-bit big-endian value: frequency
    4   freq_mid  bits 15..8   │  divided by 10 (i.e. tens of Hz)
    5   freq_lo  bits 7..0    ┘
    6   0x00              'ac' — ignored, place filler
    7   dir               direction byte
    8   cmd               ASCII command (R/S/V/etc.)
    9   0x00              ignored
   10   0x0D              CR terminator

Direction values (we use two; "bidirectional" and "3/4 wave" is for verticals, not used):
   0x00 normal
   0x40 180 (reverse)

Commands implemented:
   'R' 0x52  set frequency + direction; also re-enables serial freq update
             after a previous Home command
   'S' 0x53  Home antenna (retracts elements; turns OFF serial freq update,
             so the next Change Frequency restores it via 'R')
   'V' 0x56  Calibrate antenna

The UI accepts frequency in kHz; on the wire we send tens of Hz (kHz * 100)
as a big-endian 24-bit integer in bytes 3..5.
"""

from __future__ import annotations

import logging
import threading
from time import sleep
from dataclasses import dataclass
from typing import Optional

from ..config import SDA100Config, SDA100_DIRECTION_MAP
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
MAX_FREQ_KHZ = MAX_WIRE_TENS_OF_HZ // 100      # 167_772 kHz (~167.77 MHz)
MIN_FREQ_KHZ = 1

# Command bytes
CMD_CHANGE_FREQ = 0x52  # 'R'
CMD_HOME        = 0x53  # 'S'
CMD_CALIBRATE   = 0x56  # 'V'
CMD_SET_AUTOTRACK = 0x00 # '?'


@dataclass
class CommandResult:
    """Returned to the route layer after a command is processed."""
    action: str
    detail: str
    bytes_hex: str
    status: str          # "SENT" | "MOCK" | "NOT IMPLEMENTED"
    wait_seconds: int    # 0 if the action does not lock the device


def _validate_direction(direction: str) -> None:
    if direction not in SDA100_DIRECTION_MAP:
        raise ValueError(
            f"direction must be one of {list(SDA100_DIRECTION_MAP)} "
            f"(got {direction!r})"
        )


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


class SDA100Controller(DeviceController):
    """Builds command frames, writes them out, and broadcasts results."""

    def __init__(self, cfg: SDA100Config, socketio, freq_change_tens_of_hz: int,
                 activity_feed=None):
        super().__init__("sda100", cfg.serial, cfg.wait_seconds, socketio)
        self.cfg = cfg

        # Mutable state. Direction starts from .env; operators can change it
        # via the UI on any command. last_freq_tens_of_hz tracks what we most
        # recently *sent* to the antenna, NOT what we received from N1MM; we
        # use it both for the UDP-trigger delta check and as the frequency
        # field for Home/Calibrate frames.
        self._state_lock_mut = threading.Lock()    # protects fields below
        self.current_direction: str = cfg.direction
        self.last_freq_tens_of_hz: int = 0         # 0 = never set
        self._auto_seeded: bool = False            # first UDP message just seeds

        self.freq_change_tens_of_hz = freq_change_tens_of_hz

        # Set by app.py after construction. When non-None and reporting
        # "disconnected", auto-retune is suppressed. (Manual commands are
        # blocked at the route layer; this guard is for the UDP path.)
        self.antenna_state = None

        # Activity feed records friendly user-facing events. Optional so
        # legacy tests/scripts can instantiate without one.
        self.activity = activity_feed

    # ----------------------------------------------------- frame construction

    def build_frame(self, freq_tens_of_hz: int, direction: str, cmd_byte: int) -> bytes:
        """Build any of the 11-byte Step 100 command frames."""
        f_hi = (freq_tens_of_hz >> 16) & 0xFF
        f_mid = (freq_tens_of_hz >> 8) & 0xFF
        f_lo = freq_tens_of_hz & 0xFF
        return bytes([
            0x40,                                    # '@'
            0x41,                                    # 'A'
            0x00,
            f_hi,
            f_mid,
            f_lo,
            0x00,                                    # 'ac' place filler
            SDA100_DIRECTION_MAP[direction],
            cmd_byte,
            0x00,
            0x0D,                                    # CR
        ])

    # ---------------------------------------------------------------- state

    def state(self) -> dict:
        base = super().state()
        with self._state_lock_mut:
            base["direction"] = self.current_direction
            base["last_freq_khz"] = self.last_freq_tens_of_hz // 100 \
                if self.last_freq_tens_of_hz else 0
        return base

    # ---------------------------------------------------- common send helper

    def _send(self, frame: bytes) -> tuple[str, str]:
        hex_str = format_bytes_hex(frame)
        status = self._write_bytes(frame)
        return hex_str, status

    def _update_state(self, *, direction: str | None = None,
                      last_freq_tens_of_hz: int | None = None) -> None:
        with self._state_lock_mut:
            if direction is not None:
                self.current_direction = direction
            if last_freq_tens_of_hz is not None:
                self.last_freq_tens_of_hz = last_freq_tens_of_hz

    def _inputs_dict(self, freq_khz: Optional[int], direction: str) -> dict:
        d: dict = {"sda100_direction": direction}
        if freq_khz is not None:
            d["sda100_freq"] = freq_khz
        return d

    # ---------------------------------------------------------- public actions

    def change_frequency(self, freq_khz: int, direction: str,
                         operator: Operator) -> CommandResult:
        _validate_freq_khz(freq_khz)
        _validate_direction(direction)

        self._try_acquire()
        try:
            tens_of_hz = freq_khz * 100
            frame = self.build_frame(tens_of_hz, direction, CMD_CHANGE_FREQ)
            hex_str, status = self._send(frame)
            sleep(1.0)
            hex_str, status = self._send(frame)

        except Exception:
            self._release_lock()
            raise

        self._update_state(direction=direction, last_freq_tens_of_hz=tens_of_hz)

        detail = f"{freq_khz} kHz, direction={direction}"
        last = LastAction(
            device=self.name,
            action="Change Frequency",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            operator=operator.label(),
            timestamp=now_iso(),
            inputs=self._inputs_dict(freq_khz, direction),
        )
        if operator.callsign == "N1MMAUTO":
            user_message = f"N1MM auto-tuned to {freq_khz} kHz"
        elif operator.callsign == "N1MMREMOTE":
            # operator.name is pre-formatted as "N1MM ({name} {callsign})"
            # by maybe_auto_retune_remote().
            user_message = f"{operator.name} auto-tuned to {freq_khz} kHz"
        else:
            user_message = f"{operator.name} {operator.callsign} set frequency to {freq_khz} kHz"
        if self.activity is not None:
            self.activity.record(user_message)
        self._broadcast_last_action(last)
        self._start_wait_timer()
        return CommandResult(
            action="Change Frequency",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            wait_seconds=self.wait_seconds,
        )

    def home(self, direction: str, operator: Operator) -> CommandResult:
        """Send Home command (S/0x53). Uses last-known frequency in the frame."""
        _validate_direction(direction)

        self._try_acquire()
        try:
            with self._state_lock_mut:
                freq_tens = self.last_freq_tens_of_hz
            frame = self.build_frame(freq_tens, direction, CMD_HOME)
            hex_str, status = self._send(frame)
            sleep(1.0)
            hex_str, status = self._send(frame)
        except Exception:
            self._release_lock()
            raise

        # Home command turns OFF serial frequency update on the controller.
        # The next change_frequency (cmd 'R') re-enables it. We update our
        # tracked direction, but NOT last_freq (we didn't change frequency).
        self._update_state(direction=direction)

        freq_str = f"{freq_tens // 100} kHz (last set)" if freq_tens else "0 (never set)"
        detail = f"home; freq field={freq_str}; direction={direction}"
        last = LastAction(
            device=self.name,
            action="Home",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            operator=operator.label(),
            timestamp=now_iso(),
            inputs=self._inputs_dict(None, direction),
        )
        if self.activity is not None:
            self.activity.record(
                f"{operator.name} {operator.callsign} homed the antenna"
            )
        self._broadcast_last_action(last)
        self._start_wait_timer()
        return CommandResult(
            action="Home",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            wait_seconds=self.wait_seconds,
        )

    def calibrate(self, direction: str, operator: Operator) -> CommandResult:
        """Send Calibrate command (V/0x56)."""
        _validate_direction(direction)

        self._try_acquire()
        try:
            with self._state_lock_mut:
                freq_tens = self.last_freq_tens_of_hz
            frame = self.build_frame(freq_tens, direction, CMD_CALIBRATE)
            hex_str, status = self._send(frame)
            sleep(1.0)
            hex_str, status = self._send(frame)

        except Exception:
            self._release_lock()
            raise

        self._update_state(direction=direction)

        freq_str = f"{freq_tens // 100} kHz (last set)" if freq_tens else "0 (never set)"
        detail = f"calibrate; freq field={freq_str}; direction={direction}"
        last = LastAction(
            device=self.name,
            action="Calibrate",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            operator=operator.label(),
            timestamp=now_iso(),
            inputs=self._inputs_dict(None, direction),
        )
        if self.activity is not None:
            self.activity.record(
                f"{operator.name} {operator.callsign} calibrated the antenna"
            )
        self._broadcast_last_action(last)
        self._start_wait_timer()
        return CommandResult(
            action="Calibrate",
            detail=detail,
            bytes_hex=hex_str,
            status=status,
            wait_seconds=self.wait_seconds,
        )

    # ---------------------------------------------------------- status query

    _QUERY_CMD    = bytes([0x3F, 0x41, 0x0D])   # ASCII "? A CR"
    _RESP_DIR_MASK = 0x07  # low 3 bits encode direction in status response
    #   0x00 = normal, 0x02 = 180°, 0x01 = bi-directional, 0x04 = 3/4-wave

    def query_status(
        self,
        operator: "Operator",
        max_attempts: int = 5,
    ) -> tuple[int, str]:
        """Read the SDA100's current frequency and direction over serial.

        Protocol — send 3 bytes, read 11-byte response:
          byte:  0    1    2    3    4    5    6    7    8    9    10
          value: @    A    0x00 Fh   Fm   Fl   ac   dir  vh   vl   CR

        Frequency: (Fh<<16 | Fm<<8 | Fl) in tens-of-Hz.
        Direction byte uses DIFFERENT bit encoding from the set command:
          bit 1 (0x02) = 180°, bit 0 (0x01) = bi-dir, bit 2 (0x04) = 3/4-wave.
          The doc says "other bits will be set so the value must be filtered" —
          we mask to bits 0-2 before comparing.

        In MOCK mode: returns current internally-tracked state without touching
        hardware, and still broadcasts so all UIs refresh.

        Raises DeviceBusy if the antenna wait window is still active.
        Raises RuntimeError after all max_attempts fail.
        """
        self._try_acquire()
        try:
            # ------ MOCK path ------
            if self._serial is None:
                with self._state_lock_mut:
                    freq_khz  = self.last_freq_tens_of_hz // 100
                    direction = self.current_direction
                logger.info("query_status (MOCK): %d kHz, %s", freq_khz, direction)
                self._emit_query_result(freq_khz, direction, b"", operator)
                return freq_khz, direction

            # ------ Real hardware path ------
            # _try_acquire() sets _busy=True, preventing concurrent auto-retune
            # commands from racing onto the serial port during our read.
            # _release_lock() in the finally block clears it WITHOUT calling
            # _start_wait_timer(), because reading causes no antenna motion.
            last_error = "no attempts made"
            for attempt in range(1, max_attempts + 1):
                try:
                    saved_timeout = self._serial.timeout
                    self._serial.timeout = 1.5   # per-attempt read deadline
                    try:
                        self._serial.reset_input_buffer()
                        self._serial.write(self._QUERY_CMD)
                        self._serial.flush()
                        raw = self._serial.read(11)
                    finally:
                        self._serial.timeout = saved_timeout
                except Exception as exc:
                    last_error = f"serial error: {exc}"
                    logger.warning("SDA100 query %d/%d: %s",
                                attempt, max_attempts, last_error)
                    sleep(0.2)
                    continue

                # --- validate frame ---
                if len(raw) != 11:
                    last_error = f"short read: expected 11, got {len(raw)}"
                    logger.warning("SDA100 query %d/%d: %s",
                                attempt, max_attempts, last_error)
                    continue
                if raw[0] != 0x40 or raw[1] != 0x41:
                    last_error = f"bad header: {raw[0]:02X} {raw[1]:02X}"
                    logger.warning("SDA100 query %d/%d: %s",
                                attempt, max_attempts, last_error)
                    continue
                if raw[10] != 0x0D:
                    last_error = f"bad terminator: {raw[10]:02X}"
                    logger.warning("SDA100 query %d/%d: %s",
                                attempt, max_attempts, last_error)
                    continue

                # --- parse ---
                freq_tens_hz = (raw[3] << 16) | (raw[4] << 8) | raw[5]
                freq_khz     = freq_tens_hz // 100

                dir_bits = raw[7] & self._RESP_DIR_MASK
                if dir_bits == 0x00:
                    direction = "normal"
                elif dir_bits == 0x02:
                    direction = "180"
                else:
                    # bi-directional or 3/4-wave — not representable in this UI
                    with self._state_lock_mut:
                        direction = self.current_direction
                    logger.warning(
                        "SDA100 query: unexpected direction bits 0x%02X "
                        "(bi-dir or 3/4-wave); keeping current direction=%s",
                        dir_bits, direction,
                    )

                logger.info(
                    "SDA100 query %d/%d OK: %d kHz, %s  raw=%s",
                    attempt, max_attempts, freq_khz, direction, raw.hex(),
                )
                self._update_state(
                    direction=direction,
                    last_freq_tens_of_hz=freq_tens_hz,
                )
                self._emit_query_result(freq_khz, direction, raw, operator)
                return freq_khz, direction

        finally:
            self._release_lock()   # clear busy flag; no wait timer

        raise RuntimeError(
            f"SDA100 query failed after {max_attempts} attempts: {last_error}"
        )

    def _emit_query_result(
        self,
        freq_khz:  int,
        direction: str,
        raw_resp:  bytes,
        operator:  "Operator",
    ) -> None:
        """Broadcast query result to all connected UIs and record activity."""
        last = LastAction(
            device=self.name,
            action="Query SDA100",
            detail=f"{freq_khz} kHz, direction={direction}",
            bytes_hex=raw_resp.hex() if raw_resp else "MOCK",
            status="ok",
            operator=operator.label(),
            timestamp=now_iso(),
            inputs=self._inputs_dict(freq_khz, direction),
        )
        self._broadcast_last_action(last)
        if self.activity is not None:
            self.activity.record(
                f"{operator.name} {operator.callsign} "
                f"queried SDA100: {freq_khz} kHz, {direction} direction"
            )

    # -------------------------------------------------- UDP auto-retune entry

    def maybe_auto_retune(self, new_freq_tens_of_hz: int) -> bool:
        """Called by the UDP listener with a TXFreq from N1MM.

        Sends a Change Frequency if the new value differs from our last-sent
        value by at least FREQ_CHANGE tens-of-Hz AND the device isn't busy.
        Returns True if a command was actually issued.
        """
        if new_freq_tens_of_hz <= 0:
            return False
        if new_freq_tens_of_hz > MAX_WIRE_TENS_OF_HZ:
            logger.warning(
                "auto-retune: N1MM TXFreq %d exceeds Step 100 protocol max; skipping",
                new_freq_tens_of_hz,
            )
            return False
        if self.antenna_state is not None and self.antenna_state.is_disconnected():
            logger.info(
                "auto-retune: antennas disconnected; skipping (new_freq=%d)",
                new_freq_tens_of_hz,
            )
            return False

        with self._state_lock_mut:
            last = self.last_freq_tens_of_hz
            seeded = self._auto_seeded
            direction = self.current_direction
            # First UDP message we ever see: just seed last_freq, don't retune.
            # That avoids slamming the antenna with a retune the moment
            # steprtool starts up.
            if not seeded:
                self.last_freq_tens_of_hz = new_freq_tens_of_hz
                self._auto_seeded = True
                logger.info(
                    "auto-retune: seeded last_freq from N1MM at %d tens-of-Hz; "
                    "no command sent",
                    new_freq_tens_of_hz,
                )
                return False
            delta = abs(new_freq_tens_of_hz - last)

        if delta < self.freq_change_tens_of_hz:
            return False

        if self._busy:
            logger.info(
                "auto-retune: would retune to %d tens-of-Hz (delta %d) but "
                "device busy; skipping",
                new_freq_tens_of_hz, delta,
            )
            return False

        freq_khz = new_freq_tens_of_hz // 100
        operator = Operator(name="N1MM auto-retune", callsign="N1MMAUTO")
        try:
            self.change_frequency(freq_khz, direction, operator)
        except Exception as e:
            logger.warning("auto-retune failed: %s", e)
            return False
        return True

    # ------------------------------------------ remote-relay (HTTPS) retune

    def maybe_auto_retune_remote(
        self, new_freq_tens_of_hz: int, remote_name: str, remote_callsign: str,
    ) -> tuple[bool, str]:
        """Same delta/busy/disconnect logic as maybe_auto_retune(), but
        invoked by a remote N1MM forwarded over the JSON API. Differs in
        three ways:
          1. The first remote packet is NOT silently seeded — the remote
             operator's relay only starts on an explicit user action, so
             we honor that intent and retune on packet #1 too (subject to
             the usual delta check against any prior last_freq).
          2. The operator label includes the real human name + callsign
             from the API payload, so the activity feed reads
             "N1MM (Jane Doe W5XYZ) auto-tuned to 14300 kHz" rather than
             the anonymous local "N1MM auto-tuned to 14300 kHz".
          3. Returns (applied, reason) so the API can report back what
             actually happened.
        """
        if new_freq_tens_of_hz <= 0:
            return False, "invalid frequency"
        if new_freq_tens_of_hz > MAX_WIRE_TENS_OF_HZ:
            logger.warning(
                "remote-retune: TXFreq %d exceeds Step 100 protocol max; skipping",
                new_freq_tens_of_hz,
            )
            return False, "exceeds protocol max"
        if self.antenna_state is not None and self.antenna_state.is_disconnected():
            logger.info(
                "remote-retune: antennas disconnected; skipping (new_freq=%d)",
                new_freq_tens_of_hz,
            )
            return False, "antennas disconnected"

        with self._state_lock_mut:
            last = self.last_freq_tens_of_hz
            direction = self.current_direction
        # First packet ever: any non-zero delta from 0 trivially passes.
        delta = abs(new_freq_tens_of_hz - last) if last > 0 else new_freq_tens_of_hz

        if last > 0 and delta < self.freq_change_tens_of_hz:
            return False, "below delta threshold"
        if self._busy:
            logger.info(
                "remote-retune: device busy; skipping (new_freq=%d delta=%d)",
                new_freq_tens_of_hz, delta,
            )
            return False, "device busy"

        freq_khz = new_freq_tens_of_hz // 100
        operator = Operator(
            name=f"N1MM ({remote_name} {remote_callsign})",
            callsign="N1MMREMOTE",
        )
        try:
            self.change_frequency(freq_khz, direction, operator)
        except Exception as e:
            logger.warning("remote-retune failed: %s", e)
            return False, f"error: {e}"
        return True, "applied"
