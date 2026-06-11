"""HTTP API for steprtool.

All command endpoints require an operator (name + callsign) in the JSON body.
All command endpoints also reject the request with HTTP 403 if the antenna
state is currently 'disconnected' (via lightning email or URL override).
"""

from __future__ import annotations

import logging
import re

from flask import Blueprint, current_app, jsonify, request

from ..config import SDA100_DIRECTION_MAP
from ..devices.base import DeviceBusy, Operator


logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")


_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,10}$")


def _extract_operator(payload: dict) -> Operator:
    op = payload.get("operator") or {}
    name = (op.get("name") or "").strip()
    callsign = (op.get("callsign") or "").strip().upper()
    if not name:
        raise ValueError("operator.name is required")
    if not callsign:
        raise ValueError("operator.callsign is required")
    if not _CALLSIGN_RE.match(callsign):
        raise ValueError("operator.callsign must be 3-10 letters/digits")
    return Operator(name=name, callsign=callsign)


def _extract_direction(payload: dict) -> str:
    direction = (payload.get("direction") or "").strip().lower()
    if not direction:
        raise ValueError("direction is required")
    if direction not in SDA100_DIRECTION_MAP:
        raise ValueError(f"direction must be one of {list(SDA100_DIRECTION_MAP)}")
    return direction


def _check_antennas_connected():
    """Return a 403 response if antennas are currently disconnected, else None."""
    state = current_app.config.get("ANTENNA_STATE")
    if state is not None and state.is_disconnected():
        snap = state.snapshot()
        return jsonify({
            "error": "antennas disconnected",
            "antenna_state": snap,
        }), 403
    return None


def _sda100():
    return current_app.config["SDA100"]


def _dcu2():
    return current_app.config["DCU2"]


# -------------------------------------------------------------------- status

@api.get("/status")
def status():
    state = current_app.config.get("ANTENNA_STATE")
    return jsonify({
        "sda100": _sda100().state(),
        "dcu2": _dcu2().state(),
        "last_action": current_app.config.get("LAST_ACTION"),
        "antenna_state": state.snapshot() if state else None,
    })


# ------------------------------------------------------------------ Step 100

@api.post("/sda100/frequency")
def sda100_frequency():
    blocked = _check_antennas_connected()
    if blocked is not None: return blocked
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
        freq_khz_raw = payload.get("frequency_khz")
        if freq_khz_raw is None:
            return jsonify({"error": "frequency_khz is required"}), 400
        try:
            freq_khz = int(freq_khz_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "frequency_khz must be an integer"}), 400
        direction = _extract_direction(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _sda100().change_frequency(freq_khz, direction, operator)
    except DeviceBusy as e:
        return jsonify({"error": "device busy", "seconds_remaining": e.seconds_remaining}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(_command_result_json("sda100", result))


@api.post("/sda100/home")
def sda100_home():
    blocked = _check_antennas_connected()
    if blocked is not None: return blocked
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
        direction = _extract_direction(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _sda100().home(direction, operator)
    except DeviceBusy as e:
        return jsonify({"error": "device busy", "seconds_remaining": e.seconds_remaining}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(_command_result_json("sda100", result))


@api.post("/sda100/calibrate")
def sda100_calibrate():
    blocked = _check_antennas_connected()
    if blocked is not None: return blocked
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
        direction = _extract_direction(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _sda100().calibrate(direction, operator)
    except DeviceBusy as e:
        return jsonify({"error": "device busy", "seconds_remaining": e.seconds_remaining}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    print(f"FINALLY  SDA100 calibrate result: {result}")

    return jsonify(_command_result_json("sda100", result))

@api.post("/api/sda100/query")
def sda100_query():
    blocked = _check_antennas_connected()
    if blocked is not None: return blocked
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _sda100().query_status(operator)
    except DeviceBusy as exc:
        return jsonify({"error": "device busy",
                        "seconds_remaining": exc.seconds_remaining}), 409
    except RuntimeError as exc:
        logger.error("SDA100 query failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify(_command_result_json("sda100", result))

# ---------------------------------------------------------------------- DCU-2

@api.post("/dcu2/azimuth")
def dcu2_azimuth():
    blocked = _check_antennas_connected()
    if blocked is not None: return blocked
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
        az_raw = payload.get("azimuth")
        if az_raw is None:
            return jsonify({"error": "azimuth is required"}), 400
        try:
            azimuth = int(az_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "azimuth must be an integer"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _dcu2().change_direction(azimuth, operator)
    except DeviceBusy as e:
        return jsonify({"error": "device busy", "seconds_remaining": e.seconds_remaining}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(_command_result_json("dcu2", result))


def _command_result_json(device: str, result) -> dict:
    return {
        "device": device,
        "action": result.action,
        "detail": result.detail,
        "bytes_hex": result.bytes_hex,
        "status": result.status,
        "wait_seconds": result.wait_seconds,
    }


# ---------------------------- N1MM remote-relay endpoint ------------------
# The steprtool-relay desktop helper, run on a remote operator's machine,
# captures N1MM RadioInfo UDP locally and POSTs the TXFreq value here over
# HTTPS. Authentication is a single shared secret in the X-Steprtool-Auth
# header (set RELAY_SECRET in .env; leave blank to disable auth and accept
# any caller — useful while testing on a closed tailnet).

@api.post("/n1mm/txfreq")
def n1mm_txfreq():
    expected = current_app.config.get("RELAY_SECRET", "")
    if expected:
        provided = request.headers.get("X-Steprtool-Auth", "")
        if provided != expected:
            return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    tx_raw = payload.get("tx_freq_tens_of_hz")
    if tx_raw is None:
        return jsonify({"error": "tx_freq_tens_of_hz is required"}), 400
    try:
        tx_freq = int(tx_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "tx_freq_tens_of_hz must be an integer"}), 400

    sda100 = current_app.config["SDA100"]
    applied, reason = sda100.maybe_auto_retune_remote(
        tx_freq, operator.name, operator.callsign,
    )
    return jsonify({
        "applied": applied,
        "reason": reason,
        "tx_freq_khz": tx_freq // 100,
    })
