"""IMAP polling listener for lightning-detector emails.

Watches a single mailbox (typically `steprtool-alerts@w5gad.org`) for messages
whose subject or body contains the phrase "Antennas Disconnected." or
"Antennas Connected." and pushes the resulting state into AntennaState.

Connection model: poll-and-disconnect. Every POLL_SECONDS we open an IMAPS
connection, login, SELECT INBOX, SEARCH for messages since the most recent
timestamp we've already processed (or, on first poll, since N days ago),
process each new UID, then close. This avoids stale-connection issues and
keeps the listener forgiving of network blips.

Timestamp extraction supports the lightning-detector body format, e.g.
"05/22/2026, 02:14:19 PM Central Daylight Time". If that isn't found, we
fall back to the email's Date: header. If neither is present, we use
"now" — a last resort.

This module uses only the Python stdlib (imaplib, email, ssl).
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional


logger = logging.getLogger(__name__)


# Phrase matching is case-insensitive substring; the period in the user's
# spec is included but treated as optional in case the device occasionally
# omits it.
_PHRASE_DISCONNECT = re.compile(r"antennas\s+disconnected\.?", re.IGNORECASE)
_PHRASE_CONNECT    = re.compile(r"antennas\s+connected\.?",    re.IGNORECASE)
# Power-cycle notice from the Lightning Rig Saver. Means the antennas are
# NOW disconnected and will reconnect after a lightning-free period. The
# wording includes a configurable number of minutes ("...for 10 min after
# bootup"), so we key only on the stable tail phrase.
_PHRASE_ANT_CLOSURE = re.compile(r"please\s+wait\s+for\s+ant\s+closure", re.IGNORECASE)

# Custom activity-feed / log message for the power-cycle disconnect.
POWER_CYCLE_DISCONNECT_MESSAGE = (
    "Antennas Disconnected due to power outage. Back when no lightning for a period"
)

# Long timezone name -> short abbreviation -> UTC offset.
# US zones (where the antenna and the lightning detector live) covered;
# extend here if needed.
_TZ_ABBREV = {
    "central daylight time":  "-0500",
    "central standard time":  "-0600",
    "eastern daylight time":  "-0400",
    "eastern standard time":  "-0500",
    "mountain daylight time": "-0600",
    "mountain standard time": "-0700",
    "pacific daylight time":  "-0700",
    "pacific standard time":  "-0800",
    "atlantic daylight time": "-0300",
    "atlantic standard time": "-0400",
    "alaska daylight time":   "-0800",
    "alaska standard time":   "-0900",
    "hawaii standard time":   "-1000",
    "utc":                    "+0000",
    "gmt":                    "+0000",
}

# Matches "05/22/2026, 02:14:19 PM Central Daylight Time" and similar.
_TIMESTAMP_RE = re.compile(
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})"
    r"[,\s]+"
    r"(?P<time>\d{1,2}:\d{2}:\d{2}\s*[AaPp][Mm])"
    r"\s+"
    r"(?P<tz>(?:[A-Za-z]+\s+)+Time|UTC|GMT)"
)


def parse_phrase_event(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (status, activity_message_override).

    status is 'connected', 'disconnected', or None (no match).
    activity_message_override is a custom message string when the matched
    phrase warrants special wording (currently only the power-cycle notice);
    otherwise None and the caller uses the default message.

    Disconnect variants are checked before connect, so disconnect wins if
    more than one phrase somehow appears in a single message.
    """
    if _PHRASE_ANT_CLOSURE.search(text):
        return "disconnected", POWER_CYCLE_DISCONNECT_MESSAGE
    if _PHRASE_DISCONNECT.search(text):
        return "disconnected", None
    if _PHRASE_CONNECT.search(text):
        return "connected", None
    return None, None


def parse_phrase_status(text: str) -> Optional[str]:
    """Return 'connected', 'disconnected', or None for non-matching text."""
    status, _ = parse_phrase_event(text)
    return status


def parse_body_timestamp(text: str) -> Optional[datetime]:
    """Pull the lightning-detector timestamp from message text, or None."""
    m = _TIMESTAMP_RE.search(text)
    if not m:
        return None
    date_str = m.group("date")
    time_str = m.group("time").upper().replace(" ", "")
    tz_str   = m.group("tz").strip().lower()

    offset = _TZ_ABBREV.get(tz_str)
    if offset is None:
        logger.debug("unknown timezone name in body: %r", tz_str)
        return None

    full = f"{date_str} {time_str} {offset}"
    try:
        dt = datetime.strptime(full, "%m/%d/%Y %I:%M:%S%p %z")
    except ValueError as e:
        logger.debug("body timestamp parse failed for %r: %s", full, e)
        return None
    return dt.astimezone(timezone.utc)


def _sender_addresses(msg) -> list[str]:
    """Return all email addresses (lowercase) from From: and Reply-To:."""
    from email.utils import getaddresses
    headers = []
    for hdr in ("From", "Reply-To"):
        val = msg.get(hdr)
        if val:
            headers.append(val)
    addresses = []
    for name, addr in getaddresses(headers):
        a = (addr or "").strip().lower()
        if a:
            addresses.append(a)
    return addresses


def _get_message_text(msg) -> str:
    """Extract a concatenation of subject + plain-text body for matching."""
    parts: list[str] = []
    subject = msg.get("Subject", "")
    if subject:
        parts.append(subject)

    if msg.is_multipart():
        for sub in msg.walk():
            ct = sub.get_content_type()
            if ct == "text/plain":
                payload = sub.get_payload(decode=True)
                if payload:
                    charset = sub.get_content_charset() or "utf-8"
                    try:
                        parts.append(payload.decode(charset, errors="replace"))
                    except LookupError:
                        parts.append(payload.decode("utf-8", errors="replace"))
        # If no text/plain, fall back to text/html (strip tags crudely)
        if len(parts) <= 1:
            for sub in msg.walk():
                if sub.get_content_type() == "text/html":
                    payload = sub.get_payload(decode=True)
                    if payload:
                        charset = sub.get_content_charset() or "utf-8"
                        try:
                            html_text = payload.decode(charset, errors="replace")
                        except LookupError:
                            html_text = payload.decode("utf-8", errors="replace")
                        # Crude tag strip; good enough for phrase matching.
                        parts.append(re.sub(r"<[^>]+>", " ", html_text))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                parts.append(payload.decode(charset, errors="replace"))
            except LookupError:
                parts.append(payload.decode("utf-8", errors="replace"))

    return "\n".join(parts)


def _message_timestamp(msg, body_text: str) -> datetime:
    """Prefer the body's lightning-detector timestamp; fall back to Date:."""
    body_ts = parse_body_timestamp(body_text)
    if body_ts is not None:
        return body_ts
    date_header = msg.get("Date")
    if date_header:
        try:
            hdr = parsedate_to_datetime(date_header)
            if hdr.tzinfo is None:
                hdr = hdr.replace(tzinfo=timezone.utc)
            return hdr.astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


class EmailListener:
    """Polls an IMAP mailbox for lightning-detector status messages."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        poll_seconds: int,
        walkback_days: int,
        antenna_state,
        allowed_senders: Optional[list[str]] = None,
        activity_feed=None,
    ):
        self.host = host
        self.port = port
        self.username = username
        # Gmail's App Password UI sometimes displays the 16-char password
        # broken with spaces every 4 chars; users frequently paste it that
        # way. Strip whitespace to be forgiving.
        self.password = "".join((password or "").split())
        self.poll_seconds = poll_seconds
        self.walkback_days = walkback_days
        self.antenna_state = antenna_state
        self.activity_feed = activity_feed
        # How many recent matching emails to backfill into the activity feed
        # on startup.
        self.backfill_count = 5
        # Lowercase set for O(1) match; empty = allow all.
        self.allowed_senders = {s.strip().lower() for s in (allowed_senders or []) if s.strip()}

        self._processed_uids: set[bytes] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Walk back synchronously to seed initial state, then start polling.

        If the walkback fails (e.g. service starts at boot before the network
        and DNS are ready), set a safety baseline and let the poll loop retry
        the walkback every poll interval until it succeeds. Only after a
        successful walkback do we switch to incremental new-mail polling.
        """
        self._walkback_done = False
        try:
            self._initial_walkback()
            self._walkback_done = True
        except Exception as e:
            logger.exception("IMAP initial walkback failed: %s", e)
            # Show 'disconnected' for now, but stamp the baseline at epoch so
            # the retried walkback's events apply correctly (update() rejects
            # events older than the current state's timestamp; a 'now'
            # baseline would silently swallow every real email).
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            self.antenna_state.replace_default(
                "disconnected", source="default", timestamp=epoch,
            )
            logger.info(
                "walkback failed; baseline set to disconnected@epoch. "
                "Poll loop will retry walkback every %ds until it succeeds.",
                self.poll_seconds,
            )

        self._thread = threading.Thread(
            target=self._poll_loop, name="email-listener", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ----------------------------------------------------- IMAP operations

    def _connect(self) -> imaplib.IMAP4_SSL:
        ctx = ssl.create_default_context()
        # Connection-level timeout via socket. imaplib doesn't expose one
        # directly; we set it on the underlying socket after construction.
        M = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx)
        try:
            M.sock.settimeout(20.0)
        except Exception:
            pass
        M.login(self.username, self.password)
        return M

    def _friendly_message(self, status: str, message_override: Optional[str]) -> str:
        """Display text for an email event (matches antenna_state wording)."""
        if message_override:
            return message_override
        return "Antennas Disconnected" if status == "disconnected" else "Antennas Reconnected"

    def _parse_uid(self, M: imaplib.IMAP4_SSL, uid: bytes) -> Optional[dict]:
        """Fetch and parse one UID. Returns a dict or None.

        On a matching message returns
            {"uid", "status", "message_override", "ts"}
        and marks the UID processed. Non-matching / disallowed-sender
        messages are also marked processed (so we don't re-evaluate them
        every poll) and return None. Does NOT touch antenna_state.
        """
        if uid in self._processed_uids:
            return None
        try:
            typ, msg_data = M.uid("fetch", uid, "(RFC822)")
        except Exception as e:
            logger.warning("IMAP UID FETCH %r failed: %s", uid, e)
            return None
        if typ != "OK" or not msg_data or msg_data[0] is None:
            return None

        try:
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
            msg = email.message_from_bytes(raw)

            if self.allowed_senders:
                addrs = _sender_addresses(msg)
                if not any(a in self.allowed_senders for a in addrs):
                    logger.debug("UID %r skipped: senders %s not in allowlist", uid, addrs)
                    self._processed_uids.add(uid)
                    return None

            body_text = _get_message_text(msg)
            status, message_override = parse_phrase_event(body_text)
            if status is None:
                self._processed_uids.add(uid)
                return None
            ts = _message_timestamp(msg, body_text)
            self._processed_uids.add(uid)
            return {
                "uid": uid, "status": status,
                "message_override": message_override, "ts": ts,
            }
        except Exception as e:
            logger.warning("IMAP message %r processing failed: %s", uid, e)
            return None

    def _search_since(self, M: imaplib.IMAP4_SSL, days_ago: int) -> list[bytes]:
        """Return UIDs of messages received in the last `days_ago` days."""
        since = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%d-%b-%Y")
        typ, data = M.uid("search", None, f"SINCE {since}")
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    # ----------------------------------------------------- walkback + loop

    def _initial_walkback(self) -> None:
        logger.info("IMAP walkback: looking back %d days on %s:%d as %s",
                    self.walkback_days, self.host, self.port, self.username)

        # Reset baseline so historical emails can override it. AntennaState's
        # update() rejects events older than its current timestamp; since
        # __init__ sets that to "now", every walkback email would look stale.
        # We reset to epoch + disconnected: the most-recent matching email
        # wins the chronological race, and we land on a safe default if
        # walkback finds nothing.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.antenna_state.replace_default(
            "disconnected", source="default", timestamp=epoch,
        )

        M = self._connect()
        try:
            typ, _ = M.select("INBOX", readonly=True)
            if typ != "OK":
                raise RuntimeError("IMAP SELECT INBOX failed")
            uids = self._search_since(M, self.walkback_days)
            logger.info("walkback: %d candidate message(s) in window", len(uids))

            # Parse every candidate (no state changes yet), collecting the
            # matching events.
            events = []
            for uid in sorted(uids, key=lambda b: int(b)):
                ev = self._parse_uid(M, uid)
                if ev is not None:
                    events.append(ev)
            events.sort(key=lambda e: e["ts"])

            if events:
                # Set current state from the most recent matching email —
                # silently, so we don't replay the whole history into the feed.
                latest = events[-1]
                self.antenna_state.update(
                    status=latest["status"], timestamp=latest["ts"],
                    source="email", operator=None,
                    activity_message=latest["message_override"],
                    record_activity=False,
                )
                # Backfill the most recent few into the feed with their real
                # email timestamps (no log persist, no broadcast — clients
                # aren't connected yet and the mailbox is the record).
                backfilled = 0
                if self.activity_feed is not None:
                    for ev in events[-self.backfill_count:]:
                        msg = self._friendly_message(ev["status"], ev["message_override"])
                        self.activity_feed.record(
                            msg, timestamp=ev["ts"], persist=False, broadcast=False,
                        )
                        backfilled += 1
                logger.info(
                    "walkback complete: antenna %s (from email at %s); "
                    "%d matching email(s), backfilled %d into activity feed",
                    latest["status"], latest["ts"].isoformat(timespec="seconds"),
                    len(events), backfilled,
                )
            else:
                logger.info(
                    "walkback: no matching emails found in %d-day window; "
                    "staying at safe default (disconnected)",
                    self.walkback_days,
                )
        finally:
            try: M.close()
            except Exception: pass
            try: M.logout()
            except Exception: pass

    def _poll_once(self) -> None:
        M = self._connect()
        try:
            typ, _ = M.select("INBOX", readonly=True)
            if typ != "OK":
                raise RuntimeError("IMAP SELECT INBOX failed")
            uids = self._search_since(M, days_ago=1)
            new_uids = [u for u in uids if u not in self._processed_uids]
            for uid in sorted(new_uids, key=lambda b: int(b)):
                ev = self._parse_uid(M, uid)
                if ev is None:
                    continue
                # Live event: apply state, which records to the feed (with the
                # email's own timestamp) and broadcasts. persist=False is set
                # inside antenna_state for email-sourced events.
                self.antenna_state.update(
                    status=ev["status"], timestamp=ev["ts"], source="email",
                    operator=None, activity_message=ev["message_override"],
                )
                logger.info(
                    "email UID %s -> antenna %s at %s%s",
                    ev["uid"].decode("ascii", "replace"), ev["status"],
                    ev["ts"].isoformat(timespec="seconds"),
                    " (power-cycle notice)" if ev["message_override"] else "",
                )
        finally:
            try: M.close()
            except Exception: pass
            try: M.logout()
            except Exception: pass

    def _poll_loop(self) -> None:
        # First sleep before the first poll to avoid hammering the server
        # right after the walkback.
        while not self._stop.is_set():
            self._stop.wait(self.poll_seconds)
            if self._stop.is_set():
                break
            try:
                if not self._walkback_done:
                    # Initial walkback didn't succeed (typically because the
                    # service started before the network was up). Retry it
                    # now; this gives the same clean "set state silently +
                    # backfill 5" semantics as a normal startup, instead of
                    # cascading every missed transition through the live path.
                    try:
                        self._initial_walkback()
                        self._walkback_done = True
                        logger.info("walkback recovered on poll-loop retry")
                    except Exception as e:
                        logger.warning(
                            "walkback retry failed (will retry in %ds): %s",
                            self.poll_seconds, e,
                        )
                        continue
                self._poll_once()
            except Exception as e:
                logger.warning("IMAP poll error: %s", e)
