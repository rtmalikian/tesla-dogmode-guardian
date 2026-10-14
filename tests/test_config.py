"""Config parsing tests."""
import pytest

from tesla_dogmode_guardian.config import ConfigError, load_settings

BASE = {
    "TESLA_CLIENT_ID": "id",
    "TESLA_CLIENT_SECRET": "secret",
    "TCC_VEHICLES": "Daily=VIN1,Second=VIN2",
}


def test_vehicle_parsing():
    s = load_settings(dict(BASE))
    assert [(v.name, v.vin) for v in s.vehicles] == [("Daily", "VIN1"), ("Second", "VIN2")]


def test_dog_defaults():
    s = load_settings(dict(BASE))
    assert s.dog_temp_drift_c == 3.0
    assert s.dog_soc_min == 20.0
    assert s.dog_setpoint_c is None
    assert s.ntfy_topic == ""
    assert s.poll_interval_seconds == 300


def test_dog_settings_parsed():
    s = load_settings({**BASE, "TCC_DOG_TEMP_DRIFT_C": "4.5",
                       "TCC_DOG_SOC_MIN": "15",
                       "TCC_DOG_SETPOINT_C": "21.5",
                       "TCC_NTFY_TOPIC": "my-secret-topic",
                       "TCC_POLL_INTERVAL_SECONDS": "600"})
    assert s.dog_temp_drift_c == 4.5
    assert s.dog_soc_min == 15.0
    assert s.dog_setpoint_c == 21.5
    assert s.ntfy_topic == "my-secret-topic"
    assert s.poll_interval_seconds == 600


def test_missing_vehicles_rejected():
    env = dict(BASE)
    del env["TCC_VEHICLES"]
    with pytest.raises(ConfigError):
        load_settings(env)


def test_region_validated():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TESLA_REGION": "mars"})


def test_bad_drift_rejected():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCC_DOG_TEMP_DRIFT_C": "zero"})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCC_DOG_TEMP_DRIFT_C": "-1"})


def test_bad_soc_min_rejected():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCC_DOG_SOC_MIN": "150"})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCC_DOG_SOC_MIN": "0"})


def test_empty_setpoint_is_none():
    s = load_settings({**BASE, "TCC_DOG_SETPOINT_C": ""})
    assert s.dog_setpoint_c is None


def test_no_ntfy_warns_but_loads(caplog):
    with caplog.at_level("WARNING", logger="tcc-dog"):
        s = load_settings(dict(BASE))
    assert s.ntfy_topic == ""
    assert "logged only" in caplog.text
