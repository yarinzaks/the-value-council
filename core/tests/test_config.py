"""Tests for configuration loading and error reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import config
from core.exceptions import ConfigError


def test_missing_env_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When .env is absent, get_settings() should raise ConfigError."""
    fake_env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", fake_env)
    config.reset_settings_cache()
    with pytest.raises(ConfigError, match="No .env file"):
        config.get_settings()


def test_missing_keys_listed_in_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty .env should list every missing key in the error message."""
    fake_env = tmp_path / ".env"
    fake_env.write_text("")  # exists but blank
    monkeypatch.setattr(config, "ENV_FILE", fake_env)
    # Also clear environment vars that might leak from CI
    for key in [
        "GEMINI_API_KEY",
        "FMP_API_KEY",
        "FINNHUB_API_KEY",
        "ALPHA_VANTAGE_KEY",
        "MARKETAUX_API_KEY",
        "SEC_USER_AGENT",
    ]:
        monkeypatch.delenv(key, raising=False)
    config.reset_settings_cache()

    # Force pydantic-settings to read from the temp env file by patching the
    # model_config.
    monkeypatch.setattr(
        config.Settings,
        "model_config",
        {**config.Settings.model_config, "env_file": fake_env},
    )

    with pytest.raises(ConfigError) as exc_info:
        config.get_settings()
    msg = str(exc_info.value)
    for key in (
        "GEMINI_API_KEY",
        "FMP_API_KEY",
        "FINNHUB_API_KEY",
        "ALPHA_VANTAGE_KEY",
        "MARKETAUX_API_KEY",
        "SEC_USER_AGENT",
    ):
        assert key in msg, f"missing key {key} not mentioned"
