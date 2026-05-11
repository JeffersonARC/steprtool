"""Configuration loader for steprtool.

Reads values from .env (via python-dotenv) and exposes them as a typed
Config object. Fails fast on invalid values.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Direction names -> byte 7 value in the Step 100 command frame.
STEP100_DIRECTION_MAP = {
    "normal": 0x00,
    "180": 0x40,
    "bidirectional": 0x80,
}

# pyserial constants come from the package; we mirror the accepted strings
# here so we can validate without importing pyserial at config-load time.
ALLOWED_PARITY = {"N", "E", "O", "M", "S"}
ALLOWED_BYTESIZE = {5, 6, 7, 8}
ALLOWED_STOPBITS = {"1", "1.5", "2"}


@dataclass
class SerialConfig:
    """Serial-port settings for one device."""
    port: str            # COM port name, or "MOCK" / "" for mock mode
    baud: int
    bytesize: int
    parity: str
    stopbits: str        # kept as string ("1", "1.5", "2") for pyserial mapping
    dtr: bool
    rts: bool

    @property
    def is_mock(self) -> bool:
        return self.port == "" or self.port.upper() == "MOCK"


@dataclass
class Step100Config:
    serial: SerialConfig
    wait_seconds: int
    direction: str       # "normal" | "180" | "bidirectional"

    @property
    def direction_byte(self) -> int:
        return STEP100_DIRECTION_MAP[self.direction]


@dataclass
class Dcu2Config:
    serial: SerialConfig
    wait_seconds: int


@dataclass
class WebConfig:
    host: str
    port: int
    cert_file: Path
    key_file: Path


@dataclass
class Config:
    web: WebConfig
    step100: Step100Config
    dcu2: Dcu2Config


class ConfigError(Exception):
    """Raised when a configuration value is missing or invalid."""


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise ConfigError(f"{name} is required in .env")
    return val if val is not None else ""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from e


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    v = raw.strip().lower()
    if v in ("true", "yes", "y", "1", "on"):
        return True
    if v in ("false", "no", "n", "0", "off"):
        return False
    raise ConfigError(f"{name} must be true/false (got {raw!r})")


def _load_serial(prefix: str, default_port: str = "MOCK") -> SerialConfig:
    port = _env(f"{prefix}_PORT", default_port).strip()

    baud = _env_int(f"{prefix}_BAUD", 4800)
    bytesize = _env_int(f"{prefix}_BYTESIZE", 8)
    if bytesize not in ALLOWED_BYTESIZE:
        raise ConfigError(f"{prefix}_BYTESIZE must be one of {sorted(ALLOWED_BYTESIZE)}")

    parity = _env(f"{prefix}_PARITY", "N").strip().upper()
    if parity not in ALLOWED_PARITY:
        raise ConfigError(f"{prefix}_PARITY must be one of {sorted(ALLOWED_PARITY)}")

    stopbits = _env(f"{prefix}_STOPBITS", "1").strip()
    if stopbits not in ALLOWED_STOPBITS:
        raise ConfigError(f"{prefix}_STOPBITS must be one of {sorted(ALLOWED_STOPBITS)}")

    dtr = _env_bool(f"{prefix}_DTR", False)
    rts = _env_bool(f"{prefix}_RTS", False)

    return SerialConfig(
        port=port,
        baud=baud,
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        dtr=dtr,
        rts=rts,
    )


def load_config(env_path: Path | None = None) -> Config:
    """Load configuration from .env. Pass an explicit path to override."""
    if env_path is None:
        env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)
    # If .env is missing we still proceed using process environment / defaults.

    web = WebConfig(
        host=_env("WEB_HOST", "0.0.0.0"),
        port=_env_int("WEB_PORT", 8443),
        cert_file=Path(_env("CERT_FILE", "certs/cert.pem")),
        key_file=Path(_env("KEY_FILE", "certs/key.pem")),
    )

    step100_direction = _env("STEP100_DIRECTION", "normal").strip().lower()
    if step100_direction not in STEP100_DIRECTION_MAP:
        raise ConfigError(
            f"STEP100_DIRECTION must be one of {list(STEP100_DIRECTION_MAP)} "
            f"(got {step100_direction!r})"
        )

    step100 = Step100Config(
        serial=_load_serial("STEP100"),
        wait_seconds=_env_int("STEP100_WAIT_SECONDS", 10),
        direction=step100_direction,
    )

    dcu2 = Dcu2Config(
        serial=_load_serial("DCU2"),
        wait_seconds=_env_int("DCU2_WAIT_SECONDS", 10),
    )

    if step100.wait_seconds < 0:
        raise ConfigError("STEP100_WAIT_SECONDS must be >= 0")
    if dcu2.wait_seconds < 0:
        raise ConfigError("DCU2_WAIT_SECONDS must be >= 0")

    return Config(web=web, step100=step100, dcu2=dcu2)
