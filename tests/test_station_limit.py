"""The number of collected stations comes from MAX_STATIONS, not a constant."""
import importlib
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config  # noqa: E402


def _reload_config(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MAX_STATIONS", raising=False)
    else:
        monkeypatch.setenv("MAX_STATIONS", value)
    return importlib.reload(config)


def test_default_is_fifteen(monkeypatch):
    assert _reload_config(monkeypatch, None).MAX_STATIONS == 15


def test_set_from_environment(monkeypatch):
    assert _reload_config(monkeypatch, "8").MAX_STATIONS == 8


def test_never_below_one(monkeypatch):
    assert _reload_config(monkeypatch, "0").MAX_STATIONS == 1


def _collector(monkeypatch, limit, station_count):
    _reload_config(monkeypatch, str(limit))
    import collector

    importlib.reload(collector)
    instance = collector.DataCollector(register_signals=False)
    instance.stations = [types.SimpleNamespace(id=i) for i in range(station_count)]
    return instance


def test_monitored_stations_respects_the_limit(monkeypatch):
    c = _collector(monkeypatch, 5, 20)
    assert [s.id for s in c.monitored_stations()] == [0, 1, 2, 3, 4]


def test_monitored_stations_when_fewer_are_available(monkeypatch):
    c = _collector(monkeypatch, 15, 3)
    assert len(c.monitored_stations()) == 3


def test_collection_requests_only_the_monitored_stations(monkeypatch):
    c = _collector(monkeypatch, 4, 20)
    asked = []

    def fake_get_station_data(ids):
        asked.append(list(ids))
        return []

    c.digitraffic = types.SimpleNamespace(get_station_data=fake_get_station_data)
    c.db = None
    c.collect_weather_data()
    assert asked == [[0, 1, 2, 3]]
