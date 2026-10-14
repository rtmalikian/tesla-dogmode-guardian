"""Fake fleet for testing and demos. No API calls, no real cars.

The virtual timeline parks a car with Dog Mode on and a 21.5°C baseline,
then runs it through: a mild warmup (no alert), a temp excursion (alert
#1), a sustained drift (no duplicate), a recovery (re-arm), a second
excursion (alert #2), a battery drop (alert #3), and finally the owner
returning with Dog Mode off (tracking reset).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class ClimateEvent:
    at_minute: float  # minutes after sim start
    keeper: str | None = None      # "Dog" / None (owner turned keeper off)
    temp: float | None = None      # inside_temp, °C
    soc: float | None = None       # battery_level, percent
    asleep: bool = False


@dataclass
class FakeVehicle:
    vin: str
    name: str
    parked: bool = True
    keeper_mode: str | None = None
    inside_temp: float | None = None
    soc: float | None = None
    asleep: bool = False
    script: list[ClimateEvent] = field(default_factory=list)
    _applied_upto: float = field(default=-1.0, repr=False)  # each event fires once

    def apply_script(self, elapsed_minutes: float) -> None:
        for ev in sorted(self.script, key=lambda e: e.at_minute):
            if ev.at_minute <= elapsed_minutes and ev.at_minute > self._applied_upto:
                self.keeper_mode = ev.keeper
                self.inside_temp = ev.temp
                self.soc = ev.soc
                self.asleep = ev.asleep
                self._applied_upto = ev.at_minute


class FakeTeslaClient:
    """Stand-in for TeslaClient with the methods the scheduler uses."""

    def __init__(self, vehicles: list[FakeVehicle]):
        self.vehicles = {v.vin: v for v in vehicles}
        self.command_log: list[tuple[str, str, object]] = []
        self.reads = 0
        self.wakes = 0

    def read_if_awake(self, vin: str) -> dict | None:
        v = self.vehicles[vin]
        if v.asleep:
            return None
        self.reads += 1
        return {
            "state": "online",
            "vehicle_state": {},
            "drive_state": {"shift_state": "P" if v.parked else "D"},
            "climate_state": {
                "climate_keeper_mode": v.keeper_mode,
                "inside_temp": v.inside_temp,
            },
            "charge_state": {"battery_level": v.soc},
        }

    def tick(self, minutes: float, elapsed: float) -> None:
        for v in self.vehicles.values():
            v.apply_script(elapsed + minutes)


def estimate_daily_cost(vehicles: int, poll_interval_seconds: int) -> dict:
    """Community-reported Fleet API rates (~$1/500 data calls, ~$1/1,000
    commands, ~$1/50 wakes; re-verify against developer.tesla.com).

    This app issues NO commands and NO wakes — the whole bill is data
    reads. At the default 5-minute schedule that is 288 reads/day/vehicle.
    Run the daemon only while a pet is actually in the car: a one-hour
    errand costs about $0.02.
    """
    passes_per_day = 24 * 3600 / poll_interval_seconds
    daily_reads = passes_per_day * vehicles
    daily = daily_reads / 500.0  # commands: 0, wakes: 0
    return {
        "reads_per_day": daily_reads,
        "commands_per_day": 0.0,
        "daily_usd": daily,
        "monthly_usd": daily * 30,
    }


def run_simulation(vehicles: list[FakeVehicle], decider, settings,
                   start: datetime, hours: float = 4.0,
                   tick_minutes: float = 5.0) -> dict:
    """Drive the real scheduler against the fake fleet over virtual time."""
    from .scheduler import run_once

    client = FakeTeslaClient(vehicles)
    now = start
    end = start + timedelta(hours=hours)
    elapsed = 0.0
    timeline: list[dict] = []
    while now < end:
        decision = run_once(client, decider, settings, now=now)
        timeline.append({
            "time": now.strftime("%H:%M"),
            "dog": {vv.name: ("ON" if vv.keeper_mode == "Dog" else "off") for vv in vehicles},
            "temp": {vv.name: vv.inside_temp for vv in vehicles},
            "soc": {vv.name: vv.soc for vv in vehicles},
            "note": decision.note,
            "actions": [(type(a).__name__, a.name, a.kind) for a in decision.actions],
        })
        client.tick(tick_minutes, elapsed)
        elapsed += tick_minutes
        now += timedelta(minutes=tick_minutes)
    return {
        "timeline": timeline,
        "alerts": [
            (t["time"], a[1], a[2]) for t in timeline for a in t["actions"]
        ],
        "reads": client.reads,
        "wakes": client.wakes,
        "cost": estimate_daily_cost(len(vehicles), settings.poll_interval_seconds),
    }
