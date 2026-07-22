import pytest

from luxctl.shell.config import ConfigError, load_config

VALID = """
[ha]
url = "ws://homeassistant.local:8123/api/websocket"

[watchdog]
stale_after_s = 600

[[sensors]]
id = "south_window"
entity_id = "sensor.south_window_lux"
placement = "window"

[[sensors]]
id = "living_room"
entity_id = "sensor.living_room_lux"
placement = "room"

[[lights]]
id = "living_room_main"
entity_id = "light.hue_living_1"
"""


def write(tmp_path, text):
    path = tmp_path / "sensors.toml"
    path.write_text(text)
    return path


def test_loads_a_valid_config(tmp_path):
    config = load_config(write(tmp_path, VALID))

    assert config.ha_url == "ws://homeassistant.local:8123/api/websocket"
    assert config.stale_after_s == 600
    assert len(config.sensors) == 2
    assert len(config.lights) == 1


def test_maps_sensor_entity_ids_to_stable_ids(tmp_path):
    config = load_config(write(tmp_path, VALID))

    assert config.entity_to_sensor() == {
        "sensor.south_window_lux": "south_window",
        "sensor.living_room_lux": "living_room",
    }


def test_maps_light_entity_ids_to_stable_ids(tmp_path):
    config = load_config(write(tmp_path, VALID))

    assert config.entity_to_light() == {"light.hue_living_1": "living_room_main"}


def test_preserves_placement(tmp_path):
    config = load_config(write(tmp_path, VALID))
    placements = {s.id: s.placement for s in config.sensors}

    assert placements == {"south_window": "window", "living_room": "room"}


def test_lights_are_optional(tmp_path):
    text = VALID.split("[[lights]]")[0]
    config = load_config(write(tmp_path, text))

    assert config.lights == ()


def test_stale_after_defaults_when_absent(tmp_path):
    text = VALID.replace("[watchdog]\nstale_after_s = 600\n", "")
    config = load_config(write(tmp_path, text))

    assert config.stale_after_s == 600


def test_rejects_unknown_placement(tmp_path):
    text = VALID.replace('placement = "window"', 'placement = "ceiling"')

    with pytest.raises(ConfigError, match="placement"):
        load_config(write(tmp_path, text))


def test_rejects_duplicate_sensor_ids(tmp_path):
    text = VALID.replace('id = "living_room"', 'id = "south_window"')

    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write(tmp_path, text))


def test_rejects_config_with_no_sensors(tmp_path):
    text = VALID.split("[[sensors]]")[0]

    with pytest.raises(ConfigError, match="at least one sensor"):
        load_config(write(tmp_path, text))


def test_rejects_missing_ha_url(tmp_path):
    text = VALID.replace('url = "ws://homeassistant.local:8123/api/websocket"', "")

    with pytest.raises(ConfigError, match="url"):
        load_config(write(tmp_path, text))


def test_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.toml")
