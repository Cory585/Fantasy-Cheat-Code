import threading

from fastapi.testclient import TestClient

from app import main
from app.api import dependencies


def test_startup_builds_the_data_in_the_background(monkeypatch):
    warmed = threading.Event()
    monkeypatch.setattr(dependencies, "warm_up", warmed.set)
    monkeypatch.setattr(main, "settings", main.settings.__class__(warm_start=True))
    with TestClient(main.app):
        assert warmed.wait(5)


def test_warm_up_never_raises(monkeypatch):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(dependencies._data, "current", broken)
    dependencies.warm_up()  # logged, not raised: the first request retries
