"""Flask + Socket.IO application factory."""

from __future__ import annotations

import logging
import logging.handlers
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request
from flask_socketio import SocketIO, emit

from .antenna_state import AntennaState
from .config import Config
from .devices.dcu2 import Dcu2Controller
from .devices.step100 import Step100Controller
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

    # ---- Main log: software-oriented events (HTTP, IMAP, UDP, errors, etc.)
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

    # ---- User-activity log: who did what, in its own file.
    # propagate=False so these lines don't ALSO end up in steprtool.log.
    # Simpler format (no logger name) since the file is single-purpose.
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
    user_log = logging.getLogger("steprtool.user_activity")

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = "steprtool-not-used-for-auth"

    socketio = SocketIO(
        app, cors_allowed_origins="*", async_mode="threading",
        logger=False, engineio_logger=False,
    )

    # Antenna state. Default to 'connected' so dev/testing without email
    # is usable; the email listener (if enabled) will overwrite this with
    # walkback truth, or flip to 'disconnected' if walkback finds nothing.
    antenna_state = AntennaState(socketio, default_status="connected")
    app.config["ANTENNA_STATE"] = antenna_state

    # Device controllers.
    step100 = Step100Controller(
        config.step100, socketio,
        freq_change_tens_of_hz=config.udp.freq_change_tens_of_hz,
    )
    step100.antenna_state = antenna_state          # for auto-retune gating
    dcu2 = Dcu2Controller(config.dcu2, socketio)
    app.config["STEP100"] = step100
    app.config["DCU2"] = dcu2
    app.config["LAST_ACTION"] = None
    app.config["IC7300_URL"] = config.ic7300_url

    # Online users.
    online_users: dict[str, dict] = {}
    online_users_lock = threading.Lock()
    app.config["ONLINE_USERS"] = online_users

    def online_users_public() -> list[dict]:
        with online_users_lock:
            unique: dict[str, str] = {}
            for info in online_users.values():
                cs = info.get("callsign") or ""
                if cs:
                    unique[cs] = info.get("name", "")
        return [{"callsign": c, "name": n} for c, n in sorted(unique.items())]

    def broadcast_online_users() -> None:
        socketio.emit("online_users", online_users_public())

    # Wrap controllers' broadcast so we keep a server-side LAST_ACTION copy.
    _orig_step100 = step100._broadcast_last_action
    def _wrapped_step100(last):
        app.config["LAST_ACTION"] = last.to_dict()
        _orig_step100(last)
    step100._broadcast_last_action = _wrapped_step100  # type: ignore[assignment]

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
        ip = request.remote_addr or "unknown"
        with online_users_lock:
            online_users[sid] = {
                "name": "", "callsign": "", "ip": ip,
                "connected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        emit("state", {
            "step100": step100.state(),
            "dcu2": dcu2.state(),
            "last_action": app.config.get("LAST_ACTION"),
            "online_users": online_users_public(),
            "antenna_state": antenna_state.snapshot(),
        })

    @socketio.on("identify")
    def _on_identify(data):
        if not isinstance(data, dict):
            return
        sid = request.sid  # type: ignore[attr-defined]
        name = (data.get("name") or "").strip()
        callsign = (data.get("callsign") or "").strip().upper()
        with online_users_lock:
            info = online_users.get(sid)
            if info is not None:
                info["name"] = name
                info["callsign"] = callsign
                user_log.info("identify sid=%s ip=%s as %s %s",
                              sid, info.get("ip"), callsign, name)
        broadcast_online_users()

    @socketio.on("disconnect")
    def _on_disconnect():
        sid = request.sid  # type: ignore[attr-defined]
        with online_users_lock:
            online_users.pop(sid, None)
        broadcast_online_users()

    # ---- UDP listener ----
    udp = UdpListener(
        host=config.udp.bind_host,
        ports=config.udp.ports,
        step100_controller=step100,
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
        )
        email.start()
        app.config["EMAIL_LISTENER"] = email
        log.info(
            "Email listener enabled: %s@%s:%d poll=%ds walkback=%dd",
            config.email.username, config.email.imap_host, config.email.imap_port,
            config.email.poll_seconds, config.email.walkback_days,
        )
    else:
        log.info("Email listener disabled (EMAIL_ENABLED=false). "
                 "Antenna state defaults to 'connected'.")

    log.info(
        "Configuration: Step 100 port=%s wait=%ds direction=%s | "
        "DCU-2 port=%s wait=%ds | UDP %s:%s freq_change=%d",
        config.step100.serial.port, config.step100.wait_seconds,
        config.step100.direction,
        config.dcu2.serial.port, config.dcu2.wait_seconds,
        config.udp.bind_host, config.udp.ports,
        config.udp.freq_change_tens_of_hz,
    )

    return app, socketio
