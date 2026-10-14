"""Configuration for Tesla Dog Mode Guardian. All settings come from the
environment (see .env.example). Copy .env.example to .env — .env is
git-ignored so secrets never leave your machine."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("tcc-dog")


class ConfigError(Exception):
    pass


@dataclass
class VehicleConfig:
    name: str
    vin: str


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    region: str = "na"
    redirect_uri: str = "http://localhost:8080/callback"
    token_file: str = "~/.tcc/tokens.json"
    vehicles: list[VehicleConfig] = field(default_factory=list)
    dog_temp_drift_c: float = 3.0
    dog_soc_min: float = 20.0
    dog_setpoint_c: float | None = None
    ntfy_topic: str = ""
    state_file: str = "~/.tcc/dogmode_guardian_state.json"
    poll_interval_seconds: int = 300
    dry_run: bool = False


def _parse_vehicles(raw: str) -> list[VehicleConfig]:
    """Format: name=VIN,name2=VIN2 (or bare VINs)."""
    vehicles = []
    for idx, item in enumerate(raw.split(",")):
        item = item.strip()
        if not item:
            continue
        name = None
        if "=" in item:
            name, item = item.split("=", 1)
            name = name.strip()
        vin = item.strip()
        if not vin:
            raise ConfigError("Empty VIN in TCC_VEHICLES")
        vehicles.append(VehicleConfig(name=name or f"car-{idx + 1}", vin=vin))
    if not vehicles:
        raise ConfigError("TCC_VEHICLES is empty — add at least one name=VIN entry")
    return vehicles


def _int(env: dict, key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _float(env: dict, key: str, default: float | None) -> float | None:
    raw = env.get(key, None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        return float(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{key} must be a number") from exc


def _bool(env: dict, key: str, default: bool) -> bool:
    return env.get(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: dict | None = None, require_auth: bool = True) -> Settings:
    env = env if env is not None else os.environ

    client_id = env.get("TESLA_CLIENT_ID", "").strip()
    client_secret = env.get("TESLA_CLIENT_SECRET", "").strip()
    if require_auth and (not client_id or not client_secret):
        raise ConfigError("TESLA_CLIENT_ID and TESLA_CLIENT_SECRET are required (see README).")

    region = env.get("TESLA_REGION", "na").strip().lower()
    if region not in ("na", "eu", "cn"):
        raise ConfigError(f"TESLA_REGION must be na/eu/cn, got {region!r}")

    vehicles_raw = env.get("TCC_VEHICLES", "").strip()
    vehicles = _parse_vehicles(vehicles_raw) if (require_auth or vehicles_raw) else []

    setpoint = _float(env, "TCC_DOG_SETPOINT_C", None)
    if setpoint is not None and not (-10 <= setpoint <= 35):
        log.warning("TCC_DOG_SETPOINT_C=%s looks implausible — drift is measured against it", setpoint)

    drift = _float(env, "TCC_DOG_TEMP_DRIFT_C", 3.0)
    if drift is None or drift <= 0:
        raise ConfigError("TCC_DOG_TEMP_DRIFT_C must be a positive number")

    soc_min = _float(env, "TCC_DOG_SOC_MIN", 20.0)
    if soc_min is None or not (0 < soc_min < 100):
        raise ConfigError("TCC_DOG_SOC_MIN must be between 0 and 100")

    ntfy_topic = env.get("TCC_NTFY_TOPIC", "").strip()
    if not ntfy_topic:
        log.warning("TCC_NTFY_TOPIC not set — alerts will be logged only, "
                    "not pushed to a second contact")

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        region=region,
        redirect_uri=env.get("TESLA_REDIRECT_URI", "http://localhost:8080/callback").strip(),
        token_file=env.get("TESLA_TOKEN_FILE", "~/.tcc/tokens.json").strip(),
        vehicles=vehicles,
        dog_temp_drift_c=drift,
        dog_soc_min=soc_min,
        dog_setpoint_c=setpoint,
        ntfy_topic=ntfy_topic,
        state_file=env.get("TCC_STATE_FILE", "~/.tcc/dogmode_guardian_state.json").strip(),
        poll_interval_seconds=_int(env, "TCC_POLL_INTERVAL_SECONDS", 300),
        dry_run=_bool(env, "TCC_DRY_RUN", False),
    )
