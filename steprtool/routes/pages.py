"""Page routes: just the index template, plus the antennas-override
query-parameter handler.

The override is applied on the GET to "/" when the URL has
?antennas=connected (or disconnected) &callsign=KJ5BYZ. After applying,
we redirect to "/" (clean URL) so a browser refresh doesn't keep
re-applying the same override.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, render_template, request, redirect, url_for


logger = logging.getLogger(__name__)
user_logger = logging.getLogger("steprtool.user_activity")

pages = Blueprint("pages", __name__)


_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,10}$")
_VALID_ANTENNA = ("connected", "disconnected")


@pages.get("/")
def index():
    antennas = (request.args.get("antennas") or "").strip().lower()
    if antennas:
        # Override path.
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
        if applied:
            user_logger.info(
                "URL override: antennas=%s by %s from %s",
                antennas, callsign, request.remote_addr,
            )
        else:
            logger.info(
                "URL override: antennas=%s by %s rejected (stale vs current state)",
                antennas, callsign,
            )
        # Redirect to clean URL to prevent re-application on refresh.
        return redirect(url_for("pages.index"), code=303)

    return render_template(
        "index.html",
        ic7300_url=current_app.config.get("IC7300_URL", ""),
    )


def _override_error(message: str, code: int):
    """A tiny HTML page explaining what was wrong with the override URL."""
    body = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>steprtool — override error</title>"
        "<style>body{background:#0e1113;color:#d7dde2;font-family:sans-serif;"
        "padding:32px;max-width:640px;margin:auto}"
        "code{background:#1c2125;padding:2px 6px;border-radius:3px;color:#f0a73a}"
        "a{color:#f0a73a}</style>"
        f"<h1>Override rejected</h1><p>{message}</p>"
        "<p>Example: <code>?antennas=disconnected&amp;callsign=KJ5BYZ</code></p>"
        f"<p><a href='{url_for('pages.index')}'>Back to steprtool</a></p>"
    )
    return body, code
