from datetime import datetime, timezone

from luxctl.core.events import parse_light, parse_lux


def lux_state(value, last_updated="2026-07-21T10:00:00.000000+00:00"):
    return {
        "entity_id": "sensor.south_window_lux",
        "state": value,
        "attributes": {"unit_of_measurement": "lx"},
        "last_updated": last_updated,
    }


def light_state(value, attributes=None,
                last_updated="2026-07-21T10:00:00.000000+00:00"):
    return {
        "entity_id": "light.living_room",
        "state": value,
        "attributes": attributes or {},
        "last_updated": last_updated,
    }


def test_parses_a_numeric_lux_reading():
    reading = parse_lux(lux_state("1234.5"), "south_window")

    assert reading is not None
    assert reading.sensor_id == "south_window"
    assert reading.lux == 1234.5
    assert reading.at == datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def test_parses_integer_valued_lux():
    reading = parse_lux(lux_state("42"), "south_window")

    assert reading is not None
    assert reading.lux == 42.0


def test_parses_zero_lux():
    reading = parse_lux(lux_state("0"), "south_window")

    assert reading is not None
    assert reading.lux == 0.0


def test_rejects_unavailable_sensor():
    assert parse_lux(lux_state("unavailable"), "south_window") is None


def test_rejects_unknown_sensor():
    assert parse_lux(lux_state("unknown"), "south_window") is None


def test_rejects_non_numeric_state():
    assert parse_lux(lux_state("bright-ish"), "south_window") is None


def test_rejects_negative_lux():
    assert parse_lux(lux_state("-5"), "south_window") is None


def test_rejects_missing_state_key():
    assert parse_lux({"last_updated": "2026-07-21T10:00:00+00:00"},
                     "south_window") is None


def test_parses_light_on_with_brightness():
    state = parse_light(light_state("on", {"brightness": 255}), "living_room")

    assert state is not None
    assert state.is_on is True
    assert state.brightness == 1.0


def test_normalises_brightness_from_ha_255_scale():
    state = parse_light(light_state("on", {"brightness": 128}), "living_room")

    assert state is not None
    assert state.brightness == 128 / 255


def test_light_on_without_brightness_attribute_has_null_brightness():
    state = parse_light(light_state("on"), "living_room")

    assert state is not None
    assert state.is_on is True
    assert state.brightness is None


def test_parses_light_off():
    state = parse_light(light_state("off", {"brightness": 200}), "living_room")

    assert state is not None
    assert state.is_on is False
    assert state.brightness is None


def test_rejects_unavailable_light():
    assert parse_light(light_state("unavailable"), "living_room") is None


def test_rejects_nan_lux():
    assert parse_lux(lux_state("nan"), "south_window") is None


def test_rejects_positive_infinity_lux():
    assert parse_lux(lux_state("inf"), "south_window") is None


def test_rejects_negative_infinity_lux():
    assert parse_lux(lux_state("-inf"), "south_window") is None


def test_parse_lux_rejects_missing_last_updated_key():
    state = {
        "entity_id": "sensor.south_window_lux",
        "state": "123.4",
        "attributes": {"unit_of_measurement": "lx"},
    }
    assert parse_lux(state, "south_window") is None


def test_parse_light_rejects_missing_last_updated_key():
    state = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {},
    }
    assert parse_light(state, "living_room") is None


def test_parse_light_treats_non_numeric_brightness_as_none():
    state = parse_light(
        light_state("on", {"brightness": "not-a-number"}), "living_room"
    )

    assert state is not None
    assert state.is_on is True
    assert state.brightness is None


def test_parse_light_treats_null_attributes_dict_as_empty():
    state = light_state("on")
    state["attributes"] = None

    result = parse_light(state, "living_room")

    assert result is not None
    assert result.is_on is True
    assert result.brightness is None
