"""End-to-end test: fake fleet over virtual time.

Core invariants: exactly ONE alert per excursion per condition (temp drift
> 3°C, SoC < 20%); no duplicate alerts while the excursion persists; the
alert re-arms after the condition clears; Dog Mode ending resets tracking;
the car is never woken just to check Dog Mode state; this app issues zero
commands.
"""
from datetime import datetime, timezone

from tesla_dogmode_guardian.config import Settings, VehicleConfig
from tesla_dogmode_guardian.dog import DogDecider
from tesla_dogmode_guardian.simulator import (ClimateEvent, FakeVehicle,
                                              estimate_daily_cost,
                                              run_simulation)


def build():
    car = FakeVehicle(
        vin="SIM1", name="Model Y", parked=True,
        keeper_mode="Dog", inside_temp=21.5, soc=65.0,
        script=[
            ClimateEvent(at_minute=25, keeper="Dog", temp=22.8, soc=64.0),
            ClimateEvent(at_minute=40, keeper="Dog", temp=24.8, soc=63.0),   # alert #1 (temp)
            ClimateEvent(at_minute=60, keeper="Dog", temp=25.5, soc=62.0),   # no duplicate
            ClimateEvent(at_minute=80, keeper="Dog", temp=21.8, soc=61.0),   # re-arm
            ClimateEvent(at_minute=100, keeper="Dog", temp=25.0, soc=60.0),  # alert #2 (temp)
            ClimateEvent(at_minute=110, keeper="Dog", temp=25.0, soc=18.0),  # alert #3 (soc)
            ClimateEvent(at_minute=130, keeper=None, temp=24.0, soc=17.0),   # owner returns
        ],
    )
    settings = Settings(
        vehicles=[VehicleConfig(name="Model Y", vin="SIM1")],
        poll_interval_seconds=300,
    )
    return car, settings, DogDecider(settings)


def test_exactly_three_alerts_one_per_excursion():
    car, settings, decider = build()
    start = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    report = run_simulation([car], decider, settings, start, hours=4.0, tick_minutes=5.0)

    alerts = report["alerts"]
    assert [a[2] for a in alerts] == ["temp", "temp", "soc"], alerts
    assert [a[0] for a in alerts] == ["12:40", "13:40", "13:50"], alerts


def test_no_duplicate_while_excursion_persists():
    car, settings, decider = build()
    start = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    report = run_simulation([car], decider, settings, start, hours=4.0, tick_minutes=5.0)
    # Between the first temp alert (12:40) and the recovery (13:20) the
    # timeline must show zero further actions.
    quiet = [t for t in report["timeline"]
             if "12:45" <= t["time"] < "13:20"]
    assert quiet
    assert sum(1 for t in quiet if t["actions"]) == 0


def test_tracking_reset_when_dog_ends():
    car, settings, decider = build()
    start = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    run_simulation([car], decider, settings, start, hours=4.0, tick_minutes=5.0)
    t = decider.tracked["SIM1"]
    assert t.dog_on is False and t.alerted == set() and t.baseline_c is None


def test_no_wake_no_commands():
    car, settings, decider = build()
    start = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    report = run_simulation([car], decider, settings, start, hours=4.0, tick_minutes=5.0)
    assert report["wakes"] == 0, "the car must never be woken just to check Dog Mode"
    assert report["reads"] > 0
    cost = estimate_daily_cost(vehicles=1, poll_interval_seconds=300)
    assert cost["commands_per_day"] == 0.0
    assert cost["monthly_usd"] < 20.0  # 24/7 at 5-min cadence; real use is per-errand
