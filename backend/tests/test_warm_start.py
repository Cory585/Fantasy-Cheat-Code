import threading

from fastapi.testclient import TestClient

from app import main
from app.api import dependencies


def test_startup_builds_the_data_in_the_background(monkeypatch):
    # The stand-in blocks until released: startup must finish (and requests be served) while
    # the data is still being built, not wait for it.
    started, release = threading.Event(), threading.Event()

    def slow_warm_up():
        started.set()
        release.wait(5)

    monkeypatch.setattr(dependencies, "warm_up", slow_warm_up)
    monkeypatch.setattr(main, "settings", main.settings.__class__(warm_start=True))
    try:
        with TestClient(main.app) as client:
            assert started.wait(5)
            assert not release.is_set()
            assert client.get("/openapi.json").status_code == 200  # the app answers meanwhile
    finally:
        release.set()


def test_warm_up_never_raises(monkeypatch):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(dependencies._data, "current", broken)
    dependencies.warm_up()  # logged, not raised: the first request retries
