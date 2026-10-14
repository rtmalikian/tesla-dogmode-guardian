"""Decision logic: backup Dog Mode safety alerts.

Pure logic — no network, `now` injected — so it is fully unit-testable.

While a vehicle's climate keeper is in Dog Mode the decider watches two
things, both read with NO wake (the scheduler skips sleeping cars):

1. Cabin temperature drift: if |inside_temp - baseline| > threshold, the
   climate may have failed (or a door/window was left open) and the
   second contact gets an alert.
2. Battery level: if SoC drops below the minimum while Dog Mode is on,
   the second contact gets an alert (Dog Mode dies with the battery).

The baseline ("what the cabin should be") is the owner-configured
TCC_DOG_SETPOINT_C when set, otherwise the inside temperature observed
when Dog Mode first became active.

Each condition alerts EXACTLY ONCE per excursion: the alert re-arms only
after the condition clears. Staying in a bad state does not re-spam.

DISCLAIMER: this is a backup notifier only. It is not life support and
does not replace checking on the animal. The Fleet API can be down, the
car can be unreachable, the phone can be off — never leave a pet
unattended longer than you would without this script.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VehicleDogState:
    vin: str
    name: str
    parked: bool
    dog_on: bool          # climate_keeper_mode == "Dog"
    inside_temp_c: float | None
    soc: float | None     # battery_level, percent


@dataclass
class DogAlert:
    vin: str
    name: str
    kind: str             # "temp" | "soc"
    detail: str
    temp_c: float | None
    setpoint_c: float | None
    soc: float | None


@dataclass
class Decision:
    actions: list = field(default_factory=list)
    note: str = ""


@dataclass
class TrackedVehicle:
    """Persistent per-vehicle Dog Mode tracking."""
    dog_on: bool = False
    baseline_c: float | None = None  # setpoint reference for drift
    alerted: set = field(default_factory=set)  # kinds already alerted this excursion

    def to_dict(self) -> dict:
        return {
            "dog_on": self.dog_on,
            "baseline_c": self.baseline_c,
            "alerted": sorted(self.alerted),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackedVehicle":
        return cls(
            dog_on=bool(data.get("dog_on", False)),
            baseline_c=data.get("baseline_c"),
            alerted=set(data.get("alerted", [])),
        )


def keeper_is_dog(raw) -> bool:
    """climate_keeper_mode comes back as a string ("Dog") in vehicle_data;
    some endpoints report integers (0=off, 1=keep, 2=dog, 3=camp)."""
    if raw is None:
        return False
    if isinstance(raw, str):
        return raw.strip().lower() == "dog"
    if isinstance(raw, (int, float)):
        return int(raw) == 2
    return False


class DogDecider:
    """Decides Dog Mode guardian alerts for the fleet at a given moment."""

    def __init__(self, settings):
        self.s = settings
        self.tracked: dict[str, TrackedVehicle] = {}

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"tracked": {vin: t.to_dict() for vin, t in self.tracked.items()}}

    def from_dict(self, data: dict) -> None:
        self.tracked = {
            vin: TrackedVehicle.from_dict(t)
            for vin, t in data.get("tracked", {}).items()
        }

    def _tracked(self, vin: str) -> TrackedVehicle:
        if vin not in self.tracked:
            self.tracked[vin] = TrackedVehicle()
        return self.tracked[vin]

    # -- main --------------------------------------------------------------
    def decide(self, states: list[VehicleDogState], now: datetime) -> Decision:
        actions: list = []
        notes: list[str] = []

        for st in states:
            t = self._tracked(st.vin)

            if not st.dog_on:
                if t.dog_on:
                    # Dog Mode ended (owner returned, or keeper switched off).
                    # A fresh session starts fresh: new baseline, re-armed alerts.
                    notes.append(f"{st.name}: Dog Mode ended — tracking reset")
                    self.tracked[st.vin] = TrackedVehicle()
                continue

            # Dog Mode is on — establish the baseline if we don't have one.
            if not t.dog_on:
                t.dog_on = True
                if self.s.dog_setpoint_c is not None:
                    t.baseline_c = self.s.dog_setpoint_c
                elif st.inside_temp_c is not None:
                    t.baseline_c = st.inside_temp_c
                if t.baseline_c is not None:
                    notes.append(f"{st.name}: Dog Mode active, baseline "
                                 f"{t.baseline_c:.1f}°C")
            elif t.baseline_c is None and st.inside_temp_c is not None:
                # First readable temp of this session (earlier reads had none).
                t.baseline_c = st.inside_temp_c
                notes.append(f"{st.name}: baseline established {t.baseline_c:.1f}°C")

            # -- temperature drift -----------------------------------------
            if st.inside_temp_c is None or t.baseline_c is None:
                notes.append(f"{st.name}: no inside-temp reading — temp check skipped")
            else:
                drift = abs(st.inside_temp_c - t.baseline_c)
                if drift > self.s.dog_temp_drift_c:
                    if "temp" not in t.alerted:
                        t.alerted.add("temp")
                        actions.append(DogAlert(
                            st.vin, st.name, "temp",
                            f"cabin {st.inside_temp_c:.1f}°C drifted "
                            f"{drift:.1f}°C from baseline {t.baseline_c:.1f}°C",
                            st.inside_temp_c, t.baseline_c, st.soc,
                        ))
                        notes.append(f"{st.name}: TEMP DRIFT {drift:.1f}°C — alerted")
                    # else: still drifting — one alert per excursion, no re-spam
                else:
                    # Back in range: re-arm the temp alert for the next excursion.
                    t.alerted.discard("temp")

            # -- battery level ---------------------------------------------
            if st.soc is None:
                notes.append(f"{st.name}: no SoC reading — battery check skipped")
            elif st.soc < self.s.dog_soc_min:
                if "soc" not in t.alerted:
                    t.alerted.add("soc")
                    actions.append(DogAlert(
                        st.vin, st.name, "soc",
                        f"battery at {st.soc:.0f}% while Dog Mode is on "
                        f"(minimum {self.s.dog_soc_min:.0f}%) — Dog Mode dies with the battery",
                        st.inside_temp_c, t.baseline_c, st.soc,
                    ))
                    notes.append(f"{st.name}: LOW BATTERY {st.soc:.0f}% — alerted")
            else:
                t.alerted.discard("soc")

        return Decision(actions=actions, note="; ".join(notes))
