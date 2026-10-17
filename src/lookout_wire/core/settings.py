"""Settings loaded from LOOKOUT_-prefixed environment variables or a local .env file."""

from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "LOOKOUT_"


class SettingsError(Exception):
    """Raised when settings are missing or invalid. Never carries secret values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Pydantic otherwise embeds every raw input value, including other secrets,
        # in its validation error messages.
        hide_input_in_errors=True,
    )

    alpaca_key_id: SecretStr
    alpaca_secret_key: SecretStr
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_trading_url: str = "https://paper-api.alpaca.markets"

    whatsapp_token: SecretStr
    whatsapp_base_url: str = "https://api.whatsapp.com/agent/v1"

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"

    interval_seconds: float = 120.0
    watchlist_path: Path = Path("watchlist.yaml")
    state_dir: Path = Path("state")
    enabled_capabilities: list[str] = ["watchlist"]

    price_timeout_seconds: float = 5.0
    explain_timeout_seconds: float = 30.0
    send_connect_timeout_seconds: float = 5.0
    send_read_timeout_seconds: float = 15.0
    poll_read_timeout_seconds: float = 30.0

    @property
    def explanation_enabled(self) -> bool:
        return self.gemini_api_key is not None


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        raise SettingsError(_describe(error)) from None


def _describe(error: ValidationError) -> str:
    problems = []
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "settings"
        problems.append(f"{ENV_PREFIX}{field.upper()}: {item['msg']}")
    return "Invalid settings: " + "; ".join(problems)
