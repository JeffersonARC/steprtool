"""Flask + Socket.IO application factory."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from flask import Flask
from flask_socketio import SocketIO

from .config import Config
from .devices.dcu2 import Dcu2Controller
from .devices.step100 import Step100Controller
from .routes.api import api as api_blueprint
from .routes.pages import pages as pages_blueprint


LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "steprtool.log"


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Avoid double handlers if this is called twice (e.g. during reload).
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


def create_app(config: Config) -> tuple[Flask, SocketIO]:
    _setup_logging()
    log = logging.getLogger(__name__)

    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )
    # Flask wants a secret key even though we don't use sessions in v1.
    app.config["SECRET_KEY"] = "steprtool-not-used-for-auth"

    # Socket.IO. CORS is wide-open because we serve everything from the
    # same origin and this lives on a Tailscale-only network. The 'threading'
    # async mode uses plain Python threads (no eventlet/gevent). WebSocket
    # support is provided by the simple-websocket package; if absent we
    # automatically fall back to HTTP long-polling.
    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False,
    )

    # Build the device controllers and stash them in app.config so the
    # routes can find them via current_app.
    step100 = Step100Controller(config.step100, socketio)
    dcu2 = Dcu2Controller(config.dcu2, socketio)
    app.config["STEP100"] = step100
    app.config["DCU2"] = dcu2
    app.config["LAST_ACTION"] = None

    # Keep a server-side copy of the most recent last-action so new clients
    # can catch up on connect. We hook the SocketIO server's outgoing 'last_action'
    # event by wrapping the controllers' broadcast method.
    _orig_broadcast = step100._broadcast_last_action
    def _wrapped_step100(last):
        app.config["LAST_ACTION"] = last.to_dict()
        _orig_broadcast(last)
    step100._broadcast_last_action = _wrapped_step100  # type: ignore[assignment]

    _orig_broadcast2 = dcu2._broadcast_last_action
    def _wrapped_dcu2(last):
        app.config["LAST_ACTION"] = last.to_dict()
        _orig_broadcast2(last)
    dcu2._broadcast_last_action = _wrapped_dcu2  # type: ignore[assignment]

    app.register_blueprint(pages_blueprint)
    app.register_blueprint(api_blueprint)

    @socketio.on("connect")
    def _on_connect():
        # Push the current state to the new client.
        from flask_socketio import emit
        emit("state", {
            "step100": step100.state(),
            "dcu2": dcu2.state(),
            "last_action": app.config.get("LAST_ACTION"),
        })

    log.info(
        "Configuration: Step 100 port=%s wait=%ds direction=%s | DCU-2 port=%s wait=%ds",
        config.step100.serial.port, config.step100.wait_seconds,
        config.step100.direction,
        config.dcu2.serial.port, config.dcu2.wait_seconds,
    )

    return app, socketio
