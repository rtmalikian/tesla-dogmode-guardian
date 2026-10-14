"""Run loop: read Dog Mode state (no-wake), decide, alert the second contact.

Cost discipline: the scheduler NEVER wakes the car just to check cabin
temperature or battery level (wakes are the priciest Fleet API call). If
the car is asleep, the pass is skipped and retried on the next poll.

This app issues NO commands at all — it only sends ntfy alerts. It is a
backup notifier, not life support.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .config import Settings
from .dog import DogAlert, DogDecider, VehicleDogState, keeper_is_dog
from .notifier import NotifierError, send_ntfy
from .tesla_client import TeslaApiError, TeslaClient

log = logging.getLogger("tcc-dog")

DISCLAIMER = (
    "tcc-dog is a BACKUP notifier only — not life support. It does not "
    "replace checking on your pet. The Fleet API can be down, the car can "
    "be unreachable, and your phone can be off. NEVER leave a pet "
    "unattended longer than you would without this script."
)


def build_states(client, settings: Settings) -> list[VehicleDogState]:
    states = []
    for vc in settings.vehicles:
        data = client.read_if_awake(vc.vin)
        if data is None:
            log.info("%s: asleep or unreachable — skipping (no wake)", vc.name)
            continue
        climate = data.get("climate_state", {}) or {}
        charge = data.get("charge_state", {}) or {}
        drive = data.get("drive_state", {}) or {}
        shift = drive.get("shift_state")
        parked = shift in ("P", None)
        states.append(VehicleDogState(
            vin=vc.vin,
            name=vc.name,
            parked=parked,
            dog_on=keeper_is_dog(climate.get("climate_keeper_mode")),
            inside_temp_c=climate.get("inside_temp"),
            soc=charge.get("battery_level"),
        ))
    return states


def load_decider(settings: Settings) -> DogDecider:
    decider = DogDecider(settings)
    path = Path(settings.state_file).expanduser()
    if path.exists():
        try:
            decider.from_dict(json.loads(path.read_text()))
        except Exception as exc:
            log.warning("Could not load state file: %s", exc)
    return decider


def save_decider(decider: DogDecider, settings: Settings) -> None:
    path = Path(settings.state_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decider.to_dict(), indent=2))


def execute(client, decider: DogDecider, settings: Settings, decision) -> None:
    for action in decision.actions:
        if isinstance(action, DogAlert):
            log.warning("DOG ALERT  %s: %s", action.name, action.detail)
            if settings.dry_run:
                log.info("dry-run: alert not sent")
                continue
            if not settings.ntfy_topic:
                log.warning("TCC_NTFY_TOPIC not set — alert logged only: %s", action.detail)
                continue
            try:
                send_ntfy(
                    settings.ntfy_topic,
                    f"🐕 Dog Mode alert: {action.name}",
                    f"{action.detail}. Backup notifier only — not life support. "
                    f"Check on your pet now.",
                )
            except NotifierError as exc:
                # The alert failed to push: re-arm the excursion so the
                # next pass retries the alert instead of swallowing it.
                t = decider._tracked(action.vin)
                t.alerted.discard(action.kind)
                log.warning("ntfy send failed for %s: %s — will retry next pass",
                            action.name, exc)
                continue
            log.info("ntfy alert pushed for %s", action.name)
    if decision.note:
        log.info("note: %s", decision.note)


def run_once(client, decider: DogDecider, settings: Settings,
             now: datetime | None = None):
    """Single sense-decide-act pass."""
    now = now or datetime.now().astimezone()
    states = build_states(client, settings)
    for s in states:
        log.info("%-12s dog=%s temp=%s soc=%s",
                 s.name, "ON" if s.dog_on else "off",
                 f"{s.inside_temp_c:.1f}°C" if s.inside_temp_c is not None else "?",
                 f"{s.soc:.0f}%" if s.soc is not None else "?")
    decision = decider.decide(states, now)
    execute(client, decider, settings, decision)
    save_decider(decider, settings)
    return decision


def run_loop(settings: Settings) -> None:
    print(f"\n⚠️  {DISCLAIMER}\n")
    client = TeslaClient(
        client_id=settings.client_id, client_secret=settings.client_secret,
        region=settings.region, redirect_uri=settings.redirect_uri,
        token_file=settings.token_file,
    )
    decider = load_decider(settings)
    mode = "DRY-RUN" if settings.dry_run else "LIVE"
    log.info("Tesla Dog Mode Guardian starting (%s), poll every %ds, drift>%.1f°C, soc<%g%%",
             mode, settings.poll_interval_seconds,
             settings.dog_temp_drift_c, settings.dog_soc_min)
    while True:
        try:
            run_once(client, decider, settings)
        except TeslaApiError as exc:
            log.error("Fleet API error: %s", exc)
        except Exception:
            log.exception("Unexpected error in run loop")
        time.sleep(settings.poll_interval_seconds)
