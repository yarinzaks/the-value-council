"""Application configuration loaded from environment variables.

Uses :mod:`pydantic_settings` to pull values from a project-root ``.env``
file (or the live environment). Missing required keys produce a single
:class:`ConfigError` listing every missing variable so users do not have
to fix one error at a time.

Typical usage::

    from core.config import get_settings
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value()
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import ConfigError

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_FILE: Path = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Required keys raise on instantiation when missing. Optional keys
    default to ``None`` so partial setups still work for development.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Required keys -------------------------------------------------------
    # SEC EDGAR's only hard requirement: a User-Agent that identifies who
    # is making requests. Per https://www.sec.gov/os/accessing-edgar-data
    # this must include a name and a contact email. We default to a
    # generic one so the runner works out of the box on machines that
    # haven't set the env var (e.g. fresh GitHub Actions runners). For
    # production use, set ``SEC_USER_AGENT`` in your environment with a
    # real contact email.
    sec_user_agent: str = "The-Value-Council research@example.com"

    # --- Optional keys -------------------------------------------------------
    # Vestigial from when this project planned to use multiple data
    # vendors. The live trading runner uses none of these — only
    # SEC EDGAR (free, no key) and yfinance (free, no key) are required.
    # They stay here so individual playbook scripts that DO use them can
    # opt in via .env without forcing the daily runner to require them.
    gemini_api_key: SecretStr | None = None
    fmp_api_key: SecretStr | None = None
    finnhub_api_key: SecretStr | None = None
    alpha_vantage_key: SecretStr | None = None
    marketaux_api_key: SecretStr | None = None
    tase_client_id: SecretStr | None = None
    tase_client_secret: SecretStr | None = None

    # --- Logging -------------------------------------------------------------
    log_level: str = "INFO"

    # --- Paths ---------------------------------------------------------------
    # ``data_dir`` resolves via :mod:`core.paths` so it honors the
    # ``VALUE_COUNCIL_DATA_DIR`` env var and defaults to a TCC-safe
    # location on macOS.
    project_root: Path = PROJECT_ROOT
    data_dir: Path = Path("/")  # placeholder — overridden below
    logs_dir: Path = PROJECT_ROOT / "logs"
    agents_dir: Path = PROJECT_ROOT / "agents"

    def model_post_init(self, __context: object) -> None:  # type: ignore[override]
        # Defer the import to avoid a cycle at module-load time.
        from core.paths import DATA_ROOT
        # pydantic v2: writing to a frozen-ish field needs object.__setattr__.
        object.__setattr__(self, "data_dir", DATA_ROOT)

    @property
    def tase_enabled(self) -> bool:
        """Whether both TASE credentials are configured."""
        return self.tase_client_id is not None and self.tase_client_secret is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache the global :class:`Settings` instance.

    Wraps Pydantic's :class:`ValidationError` in :class:`ConfigError`
    with a single human-readable message listing every missing or
    invalid key at once.

    Raises:
        ConfigError: When ``.env`` is missing, or required keys are
            missing or invalid.
    """
    if not ENV_FILE.exists():
        raise ConfigError(
            f"No .env file found at {ENV_FILE}.\n"
            f"Fix:\n"
            f"  1. cp {PROJECT_ROOT}/.env.example {ENV_FILE}\n"
            f"  2. Fill in your API keys\n"
            f"  3. Re-run."
        )

    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing: list[str] = []
        invalid: list[str] = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"])
            if err["type"] == "missing":
                missing.append(field.upper())
            else:
                invalid.append(f"{field.upper()} ({err['msg']})")

        lines = ["Configuration error — could not load settings."]
        if missing:
            lines.append("\nMissing required environment variables:")
            lines.extend(f"  - {name}" for name in missing)
        if invalid:
            lines.append("\nInvalid values:")
            lines.extend(f"  - {name}" for name in invalid)
        lines.append(
            "\nFix: edit your .env (copy from .env.example) and re-run."
        )
        raise ConfigError("\n".join(lines)) from exc


def reset_settings_cache() -> None:
    """Reset the cached settings — used in tests after monkeypatching env."""
    get_settings.cache_clear()
