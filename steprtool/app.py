"""Flask + Socket.IO application factory."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request
from flask_socketio import SocketIO, emit

from .activity import ActivityFeed
from .antenna_state import AntennaState
from .config import Config
from .connection_tracker import ConnectionTracker
from .devices.dcu2 import Dcu2Controller
from .devices.sda100 import SDA100Controller
from .email_listener import EmailListener
from .routes.api import api as api_blueprint
from .routes.pages import pages as pages_blueprint
from .udp_listener import UdpListener


LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "steprtool.log"
USER_ACTIVITY_LOG_FILE = LOG_DIR / "user_activity.log"


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # User-activity log: own file, simpler format, doesn't propagate.
    activity_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    activity_handler = logging.handlers.RotatingFileHandler(
        USER_ACTIVITY_LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8",
    )
    activity_handler.setFormatter(activity_fmt)

    activity_console = logging.StreamHandler()
    activity_console.setFormatter(activity_fmt)

    activity_logger = logging.getLogger("steprtool.user_activity")
    activity_logger.setLevel(logging.INFO)
    activity_logger.propagate = False
    activity_logger.addHandler(activity_handler)
    activity_logger.addHandler(activity_console)


def create_app(config: Config) -> tuple[Flask, SocketIO]:
    _setup_logging()
    log = logging.getLogger(__name__)

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = "steprtool-not-used-for-auth"

    socketio = SocketIO(
        app, cors_allowed_origins="*", async_mode="threading",
        logger=False, engineio_logger=False,
    )

    # Activity feed (recent events + broadcast + log). Seed from existing
    # user_activity.log so the home page shows history after a restart.
    activity = ActivityFeed(socketio)
    seeded = activity.seed_from_log(USER_ACTIVITY_LOG_FILE)
    if seeded:
        log.info("Activity feed seeded with %d recent event(s) from %s",
                 seeded, USER_ACTIVITY_LOG_FILE)

    # Record the restart itself as the newest event. The service starts at
    # boot, so this closely tracks machine power-on / reboot time.
    activity.record("System restart due to power outage or manual shutdown")

    # Connection tracker (debounced join/left).
    tracker = ConnectionTracker(activity_feed=activity)

    # Antenna state. Default to 'connected' so dev/testing without email
    # is usable; the email listener (if enabled) will overwrite this with
    # walkback truth, or flip to 'disconnected' if walkback finds nothing.
    antenna_state = AntennaState(
        socketio, default_status="connected", activity_feed=activity,
    )

    # Device controllers.
    sda100 = SDA100Controller(
        config.sda100, socketio,
        freq_change_tens_of_hz=config.udp.freq_change_tens_of_hz,
        activity_feed=activity,
    )
    sda100.antenna_state = antenna_state
    dcu2 = Dcu2Controller(config.dcu2, socketio, activity_feed=activity)

    app.config["SDA100"] = sda100
    app.config["DCU2"] = dcu2
    app.config["LAST_ACTION"] = None
    app.config["ANTENNA_STATE"] = antenna_state
    app.config["ACTIVITY_FEED"] = activity
    app.config["CONNECTION_TRACKER"] = tracker
    app.config["IC7300_URL"] = config.ic7300_url
    app.config["CALENDAR_URL"] = config.calendar_url
    app.config["CHAT_URL"] = config.chat_url

    # Keep server-side LAST_ACTION for newcomers.
    _orig_sda100 = sda100._broadcast_last_action
    def _wrapped_sda100(last):
        app.config["LAST_ACTION"] = last.to_dict()
        _orig_sda100(last)
    sda100._broadcast_last_action = _wrapped_sda100  # type: ignore[assignment]

    _orig_dcu2 = dcu2._broadcast_last_action
    def _wrapped_dcu2(last):
        app.config["LAST_ACTION"] = last.to_dict()
        _orig_dcu2(last)
    dcu2._broadcast_last_action = _wrapped_dcu2  # type: ignore[assignment]

    app.register_blueprint(pages_blueprint)
    app.register_blueprint(api_blueprint)

    # ---- Socket.IO handlers ----

    @socketio.on("connect")
    def _on_connect():
        sid = request.sid  # type: ignore[attr-defined]
        emit("state", {
            "sda100": sda100.state(),
            "dcu2": dcu2.state(),
            "last_action": app.config.get("LAST_ACTION"),
            "online_users": tracker.public_users(),
            "antenna_state": antenna_state.snapshot(),
            "activity_events": activity.snapshot(),
        })

    @socketio.on("identify")
    def _on_identify(data):
        if not isinstance(data, dict):
            return
        sid = request.sid  # type: ignore[attr-defined]
        name = (data.get("name") or "").strip()
        callsign = (data.get("callsign") or "").strip().upper()
        if not name or not callsign:
            return
        tracker.on_identify(sid, name, callsign)
        socketio.emit("online_users", tracker.public_users())

    @socketio.on("disconnect")
    def _on_disconnect():
        sid = request.sid  # type: ignore[attr-defined]
        tracker.on_disconnect(sid)
        socketio.emit("online_users", tracker.public_users())

    @socketio.on("activity_visit")
    def _on_activity_visit(data):
        """Client tells server it's about to leave for an external page."""
        if not isinstance(data, dict):
            return
        target = data.get("target")
        labels = {"ic7300": "IC-7300", "calendar": "the calendar", "chat": "the chat"}
        label = labels.get(target)
        if label is None:
            return
        sid = request.sid  # type: ignore[attr-defined]
        info = None
        # Pull the operator from the tracker so we don't trust the client.
        for cs, entry in tracker._callsigns.items():  # type: ignore[attr-defined]
            if sid in entry.get("sids", set()):
                info = {"callsign": cs, "name": entry.get("name", "")}
                break
        if not info:
            return
        activity.record(f"{info['name']} {info['callsign']} visited {label}")

    # ---- UDP listener ----
    udp = UdpListener(
        host=config.udp.bind_host,
        ports=config.udp.ports,
        sda100_controller=sda100,
    )
    udp.start()
    app.config["UDP_LISTENER"] = udp

    # ---- Email listener ----
    if config.email.enabled:
        email = EmailListener(
            host=config.email.imap_host,
            port=config.email.imap_port,
            username=config.email.username,
            password=config.email.password,
            poll_seconds=config.email.poll_seconds,
            walkback_days=config.email.walkback_days,
            antenna_state=antenna_state,
            allowed_senders=config.email.allowed_senders,
            activity_feed=activity,
        )
        email.start()
        app.config["EMAIL_LISTENER"] = email
        log.info(
            "Email listener enabled: %s@%s:%d poll=%ds walkback=%dd "
            "allowed_senders=%s",
            config.email.username, config.email.imap_host, config.email.imap_port,
            config.email.poll_seconds, config.email.walkback_days,
            config.email.allowed_senders or "(any)",
        )
    else:
        log.info("Email listener disabled (EMAIL_ENABLED=false).")

    log.info(
        "Configuration: SDA 100 port=%s wait=%ds direction=%s | "
        "DCU-2 port=%s wait=%ds | UDP %s:%s freq_change=%d | "
        "IC7300=%s | Calendar=%s | Chat=%s",
        config.sda100.serial.port, config.sda100.wait_seconds,
        config.sda100.direction,
        config.dcu2.serial.port, config.dcu2.wait_seconds,
        config.udp.bind_host, config.udp.ports,
        config.udp.freq_change_tens_of_hz,
        config.ic7300_url or "(blank)",
        config.calendar_url or "(blank)",
        config.chat_url or "(blank)",
    )

    return app, socketio
