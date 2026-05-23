"""HTTP API for steprtool.

All command endpoints require an operator (name + callsign) in the JSON body.
Successful command responses include the bytes sent and the wait_seconds.
"""

from __future__ import annotations

import logging
import re

from flask import Blueprint, current_app, jsonify, request

from ..config import STEP100_DIRECTION_MAP
from ..devices.base import DeviceBusy, Operator


logger = logging.getLogger(__name__)

api = Blueprint("api", __name__, url_prefix="/api")


# Callsign: letters + digits, 3..10 chars. Forgiving — covers US plus most DX.
_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,10}$")


def _extract_operator(payload: dict) -> Operator:
    """Pull and validate the operator block from the request body."""
    op = payload.get("operator") or {}
    name = (op.get("name") or "").strip()
    callsign = (op.get("callsign") or "").strip().upper()
    if not name:
        raise ValueError("operator.name is required")
    if not callsign:
        raise ValueError("operator.callsign is required")
    if not _CALLSIGN_RE.match(callsign):
        raise ValueError(
            "operator.callsign must be 3-10 letters/digits (no punctuation)"
        )
    return Operator(name=name, callsign=callsign)


def _extract_direction(payload: dict) -> str:
    """Pull and validate the Step 100 direction from the request body."""
    direction = (payload.get("direction") or "").strip().lower()
    if not direction:
        raise ValueError("direction is required")
    if direction not in STEP100_DIRECTION_MAP:
        raise ValueError(
            f"direction must be one of {list(STEP100_DIRECTION_MAP)}"
        )
    return direction


def _step100():
    return current_app.config["STEP100"]


def _dcu2():
    return current_app.config["DCU2"]


# -------------------------------------------------------------------- status

@api.get("/status")
def status():
    """Snapshot for newly-loaded pages."""
    return jsonify({
        "step100": _step100().state(),
        "dcu2": _dcu2().state(),
        "last_action": current_app.config.get("LAST_ACTION"),
    })


# ------------------------------------------------------------------ Step 100

@api.post("/step100/frequency")
def step100_frequency():
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
        result = _step100().change_frequency(freq_khz, direction, operator)
    except DeviceBusy as e:
        return jsonify({
            "error": "device busy",
            "seconds_remaining": e.seconds_remaining,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "device": "step100",
        "action": result.action,
        "detail": result.detail,
        "bytes_hex": result.bytes_hex,
        "status": result.status,
        "wait_seconds": result.wait_seconds,
    })


@api.post("/step100/home")
def step100_home():
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
        direction = _extract_direction(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _step100().home(direction, operator)
    except DeviceBusy as e:
        return jsonify({
            "error": "device busy",
            "seconds_remaining": e.seconds_remaining,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "device": "step100",
        "action": result.action,
        "detail": result.detail,
        "bytes_hex": result.bytes_hex,
        "status": result.status,
        "wait_seconds": result.wait_seconds,
    })


@api.post("/step100/calibrate")
def step100_calibrate():
    payload = request.get_json(silent=True) or {}
    try:
        operator = _extract_operator(payload)
        direction = _extract_direction(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        result = _step100().calibrate(direction, operator)
    except DeviceBusy as e:
        return jsonify({
            "error": "device busy",
            "seconds_remaining": e.seconds_remaining,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "device": "step100",
        "action": result.action,
        "detail": result.detail,
        "bytes_hex": result.bytes_hex,
        "status": result.status,
        "wait_seconds": result.wait_seconds,
    })


# ---------------------------------------------------------------------- DCU-2

@api.post("/dcu2/azimuth")
def dcu2_azimuth():
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
        return jsonify({
            "error": "device busy",
            "seconds_remaining": e.seconds_remaining,
        }), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "device": "dcu2",
        "action": result.action,
        "detail": result.detail,
        "bytes_hex": result.bytes_hex,
        "status": result.status,
        "wait_seconds": result.wait_seconds,
    })
