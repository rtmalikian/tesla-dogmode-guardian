"""Decision-logic unit tests for DogDecider."""
from datetime import datetime, timezone

from tesla_dogmode_guardian.config import Settings
from tesla_dogmode_guardian.dog import (DogAlert, DogDecider, VehicleDogState,
                                        keeper_is_dog)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def make_decider(**overrides) -> DogDecider:
    return DogDecider(Settings(**overrides))


def state(vin="VIN1", name="Model Y", parked=True, dog_on=True,
          temp=21.5, soc=65.0) -> VehicleDogState:
    return VehicleDogState(vin=vin, name=name, parked=parked, dog_on=dog_on,
                           inside_temp_c=temp, soc=soc)


# -- keeper parsing ---------------------------------------------------------

def test_keeper_is_dog_strings():
    assert keeper_is_dog("Dog")
    assert keeper_is_dog("dog")
    assert keeper_is_dog(" DOG ")
    assert not keeper_is_dog("Keep")
    assert not keeper_is_dog("Camp")
    assert not keeper_is_dog("Off")


def test_keeper_is_dog_ints():
    assert keeper_is_dog(2)       # community code for Dog
    assert not keeper_is_dog(1)
    assert not keeper_is_dog(0)
    assert not keeper_is_dog(None)


# -- baseline -----------------------------------------------------------------

def test_baseline_from_first_observation():
    d = make_decider()
    decision = d.decide([state(temp=21.5)], NOW)
    assert decision.actions == []
    assert d.tracked["VIN1"].baseline_c == 21.5
    assert "21.5" in decision.note


def test_baseline_from_configured_setpoint():
    d = make_decider(dog_setpoint_c=21.0)
    d.decide([state(temp=24.5)], NOW)  # drift 3.5 > 3 -> temp alert, baseline is the setpoint
    t = d.tracked["VIN1"]
    assert t.baseline_c == 21.0
    assert len(d.tracked["VIN1"].alerted & {"temp"}) == 1


def test_no_alert_when_dog_off():
    d = make_decider()
    decision = d.decide([state(dog_on=False, temp=40.0, soc=5.0)], NOW)
    assert decision.actions == []


# -- temperature drift ---------------------------------------------------------

def test_alert_on_temp_drift():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    decision = d.decide([state(temp=25.0)], NOW)  # drift 3.5
    assert len(decision.actions) == 1
    act = decision.actions[0]
    assert isinstance(act, DogAlert)
    assert act.kind == "temp" and act.vin == "VIN1"
    assert "3.5" in act.detail


def test_no_alert_at_exact_threshold():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    decision = d.decide([state(temp=24.5)], NOW)  # drift exactly 3.0 -> no alert
    assert decision.actions == []


def test_no_alert_when_cold_drift_within_threshold():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    # Cold drift counts too (absolute value): 21.5 -> 17.0 = 4.5 > 3.
    decision = d.decide([state(temp=17.0)], NOW)
    assert len(decision.actions) == 1
    assert decision.actions[0].kind == "temp"


def test_single_alert_per_excursion_no_respam():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    first = d.decide([state(temp=25.0)], NOW)
    second = d.decide([state(temp=26.0)], NOW)  # worse, still same excursion
    third = d.decide([state(temp=25.2)], NOW)
    assert len(first.actions) == 1
    assert second.actions == [] and third.actions == []


def test_realert_after_recovery():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    d.decide([state(temp=25.0)], NOW)   # alert #1
    cleared = d.decide([state(temp=21.8)], NOW)  # back in range -> re-arm
    assert cleared.actions == []
    again = d.decide([state(temp=25.5)], NOW)    # new excursion -> alert #2
    assert len(again.actions) == 1


def test_missing_temp_skips_with_note():
    d = make_decider()
    decision = d.decide([state(temp=None)], NOW)
    assert decision.actions == []
    assert "temp check skipped" in decision.note


# -- battery --------------------------------------------------------------------

def test_alert_on_low_soc():
    d = make_decider()
    d.decide([state(soc=65.0)], NOW)
    decision = d.decide([state(soc=18.0)], NOW)
    assert len(decision.actions) == 1
    act = decision.actions[0]
    assert act.kind == "soc" and "18%" in act.detail


def test_no_alert_at_exact_soc_min():
    d = make_decider()
    d.decide([state(soc=65.0)], NOW)
    decision = d.decide([state(soc=20.0)], NOW)  # exactly the minimum -> no alert
    assert decision.actions == []


def test_soc_alert_single_per_excursion():
    d = make_decider()
    d.decide([state(soc=65.0)], NOW)
    d.decide([state(soc=18.0)], NOW)
    assert d.decide([state(soc=12.0)], NOW).actions == []  # worse, no re-spam


def test_missing_soc_skips_with_note():
    d = make_decider()
    decision = d.decide([state(soc=None)], NOW)
    assert decision.actions == []
    assert "battery check skipped" in decision.note


def test_temp_and_soc_alert_together():
    d = make_decider()
    d.decide([state(temp=21.5, soc=65.0)], NOW)
    decision = d.decide([state(temp=26.0, soc=15.0)], NOW)
    assert sorted(a.kind for a in decision.actions) == ["soc", "temp"]


# -- session lifecycle -----------------------------------------------------------

def test_dog_end_resets_tracking():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    d.decide([state(temp=26.0)], NOW)  # alert
    assert d.tracked["VIN1"].alerted == {"temp"}
    decision = d.decide([state(dog_on=False)], NOW)
    assert decision.actions == []
    assert "Dog Mode ended" in decision.note
    t = d.tracked["VIN1"]
    assert t.dog_on is False and t.alerted == set() and t.baseline_c is None


def test_new_session_establishes_new_baseline():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    d.decide([state(dog_on=False)], NOW)
    d.decide([state(temp=23.0)], NOW)  # new session, different temp
    assert d.tracked["VIN1"].baseline_c == 23.0


def test_sleeping_car_leaves_state_untouched():
    # A sleeping car is simply absent from this pass's states.
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    baseline = d.tracked["VIN1"].baseline_c
    decision = d.decide([], NOW)
    assert decision.actions == []
    assert d.tracked["VIN1"].baseline_c == baseline
    assert d.tracked["VIN1"].dog_on is True


def test_independent_vehicles():
    d = make_decider()
    d.decide([state(vin="VIN1", temp=21.5), state(vin="VIN2", temp=22.0)], NOW)
    decision = d.decide([
        state(vin="VIN1", temp=26.0),  # alerts
        state(vin="VIN2", temp=22.5),  # fine
    ], NOW)
    assert [a.vin for a in decision.actions] == ["VIN1"]


def test_persistence_roundtrip():
    d = make_decider()
    d.decide([state(temp=21.5)], NOW)
    d.decide([state(temp=26.0)], NOW)
    d2 = make_decider()
    d2.from_dict(d.to_dict())
    t = d2.tracked["VIN1"]
    assert t.dog_on is True and t.baseline_c == 21.5 and t.alerted == {"temp"}
    # The restored decider must NOT re-alert on the same excursion.
    assert d2.decide([state(temp=26.5)], NOW).actions == []
