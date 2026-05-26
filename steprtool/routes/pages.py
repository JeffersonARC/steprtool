"""Page routes:
  GET /            -> home.html      (with three choice tiles + activity feed)
  GET /steprtool   -> steprtool.html (the device controls)

Either route can also accept ?antennas=connected|disconnected&callsign=XXX
to apply a manual override; after applying we redirect back to the same
clean URL.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, render_template, request, redirect, url_for


logger = logging.getLogger(__name__)

pages = Blueprint("pages", __name__)


_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,10}$")
_VALID_ANTENNA = ("connected", "disconnected")


def _maybe_apply_override(endpoint_name: str):
    """If ?antennas=... is present, apply it (with callsign attribution)
    and return a redirect response. Otherwise return None."""
    antennas = (request.args.get("antennas") or "").strip().lower()
    if not antennas:
        return None

    callsign = (request.args.get("callsign") or "").strip().upper()
    if antennas not in _VALID_ANTENNA:
        return _override_error(f"'antennas' must be one of {_VALID_ANTENNA}", 400)
    if not callsign:
        return _override_error("'callsign' query parameter is required for override", 400)
    if not _CALLSIGN_RE.match(callsign):
        return _override_error("'callsign' must be 3-10 letters/digits", 400)

    state = current_app.config.get("ANTENNA_STATE")
    if state is None:
        return _override_error("antenna state not initialized", 500)

    applied = state.update(
        status=antennas,
        timestamp=datetime.now(timezone.utc),
        source="override",
        operator=callsign,
    )
    if not applied:
        logger.info(
            "URL override: antennas=%s by %s rejected (stale vs current state)",
            antennas, callsign,
        )
    # Redirect to the clean URL of whichever page they came in on.
    return redirect(url_for(endpoint_name), code=303)


@pages.get("/")
def home():
    override = _maybe_apply_override("pages.home")
    if override is not None:
        return override

    activity = current_app.config.get("ACTIVITY_FEED")
    activity_snapshot = activity.snapshot() if activity is not None else []

    return render_template(
        "home.html",
        ic7300_url=current_app.config.get("IC7300_URL", ""),
        calendar_url=current_app.config.get("CALENDAR_URL", ""),
        chat_url=current_app.config.get("CHAT_URL", ""),
        activity_events=activity_snapshot,
    )


@pages.get("/steprtool")
def steprtool():
    override = _maybe_apply_override("pages.steprtool")
    if override is not None:
        return override
    return render_template("steprtool.html")


def _override_error(message: str, code: int):
    body = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>steprtool — override error</title>"
        "<style>body{background:#0e1113;color:#d7dde2;font-family:sans-serif;"
        "padding:32px;max-width:640px;margin:auto}"
        "code{background:#1c2125;padding:2px 6px;border-radius:3px;color:#f0a73a}"
        "a{color:#f0a73a}</style>"
        f"<h1>Override rejected</h1><p>{message}</p>"
        "<p>Example: <code>?antennas=disconnected&amp;callsign=KJ5BYZ</code></p>"
        f"<p><a href='{url_for('pages.home')}'>Back to home</a></p>"
    )
    return body, code
