"""Entry point for steprtool.

Run directly:  python run.py
Run as service: configured via NSSM to launch this same script.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure 'steprtool' and 'scripts' packages are importable regardless of CWD.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

from steprtool.app import create_app  # noqa: E402
from steprtool.config import load_config, ConfigError  # noqa: E402

# Re-import without dashes by using importlib (the file is `generate-cert.py`).
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "generate_cert", HERE / "scripts" / "generate-cert.py"
)
generate_cert_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(generate_cert_mod)  # type: ignore[union-attr]


def main() -> int:
    try:
        cfg = load_config(HERE / ".env")
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2

    # Ensure HTTPS cert exists, generating a self-signed one if not.
    generate_cert_mod.ensure_cert(cfg.web.cert_file, cfg.web.key_file)

    app, socketio = create_app(cfg)

    print(
        f"steprtool listening on https://{cfg.web.host}:{cfg.web.port}/  "
        f"(Step 100 port={cfg.step100.serial.port}, "
        f"DCU-2 port={cfg.dcu2.serial.port})"
    )

    socketio.run(
        app,
        host=cfg.web.host,
        port=cfg.web.port,
        # Threading mode runs on Werkzeug, which takes a single ssl_context
        # (path-tuple or ssl.SSLContext) rather than separate certfile/keyfile.
        ssl_context=(str(cfg.web.cert_file), str(cfg.web.key_file)),
        debug=False,
        use_reloader=False,
        # Werkzeug refuses to start outside debug mode unless this is set. We
        # have ~3 users behind Tailscale, not a public production server.
        allow_unsafe_werkzeug=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
