import os

import pytest


def pytest_configure(config):
    """Polish for the whole run, also for fixtures shared by a module: the reference frames and the messages the tests
    check are Polish, whatever the language of the machine. Tests of English set it themselves."""
    os.environ["LANGUAGE"] = "pl"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep tests away from the user's cache and ~/.config/gpxfilm.toml, and run them in Polish, the language of the reference
    frames and of the messages they check, whatever the language of the machine."""
    monkeypatch.setenv("LANGUAGE", "pl")
    monkeypatch.setenv("GPXFILM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
