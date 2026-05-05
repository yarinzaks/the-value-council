"""Tests for configuration loading and error reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import config
from core.exceptions import ConfigError


def test_missing_env_file_loads_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When .env is absent, get_settings() should still succeed.

    Earlier behavior was to fail fast on a missing .env, which broke
    every fresh deployment (CI, GHA, fresh clone). Now the file is
    optional and env vars / defaults take over. The defaults must be
    safe enough to load without any pre-configuration.
    """
    fake_env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", fake_env)
    # Clear API-key env vars in case CI has them set.
    for key in (
        "GEMINI_API_KEY",
        "FMP_API_KEY",
        "FINNHUB_API_KEY",
        "ALPHA_VANTAGE_KEY",
        "MARKETAUX_API_KEY",
        "SEC_USER_AGENT",
    ):
        monkeypatch.delenv(key, raising=False)
    config.reset_settings_cache()
    monkeypatch.setattr(
        config.Settings,
        "model_config",
        {**config.Settings.model_config, "env_file": fake_env},
    )

    s = config.get_settings()
    # The default sec_user_agent must always be present so SEC fetches
    # have a valid identifier even on a brand-new deployment.
    assert s.sec_user_agent


def test_settings_load_with_empty_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty .env must load cleanly — every API key is optional now.

    The trading runner only requires SEC_USER_AGENT (which has a sane
    default), so a fresh deployment without any secrets must succeed.
    Previously every key was required; that blocked GitHub Actions
    runs without a full set of secrets.
    """
    fake_env = tmp_path / ".env"
    fake_env.write_text("")
    monkeypatch.setattr(config, "ENV_FILE", fake_env)
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
    monkeypatch.setattr(
        config.Settings,
        "model_config",
        {**config.Settings.model_config, "env_file": fake_env},
    )

    s = config.get_settings()
    # SEC_USER_AGENT has a default — must always resolve.
    assert s.sec_user_agent
    # API keys are now optional; with no env, they should be None.
    assert s.gemini_api_key is None
    assert s.fmp_api_key is None
    assert s.finnhub_api_key is None
    assert s.alpha_vantage_key is None
    assert s.marketaux_api_key is None
