import pytest

from lookout_wire.core.settings import SettingsError, load_settings

REQUIRED = {
    "LOOKOUT_ALPACA_KEY_ID": "alpaca-key-id-value",
    "LOOKOUT_ALPACA_SECRET_KEY": "alpaca-secret-value",
    "LOOKOUT_WHATSAPP_TOKEN": "whatsapp-token-value",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LOOKOUT_GEMINI_API_KEY", raising=False)
    return monkeypatch


def test_loads_from_prefixed_environment_variables(env):
    for name, value in REQUIRED.items():
        env.setenv(name, value)
    env.setenv("LOOKOUT_INTERVAL_SECONDS", "3")

    settings = load_settings()

    assert settings.alpaca_key_id.get_secret_value() == "alpaca-key-id-value"
    assert settings.whatsapp_token.get_secret_value() == "whatsapp-token-value"
    assert settings.interval_seconds == 3
    assert settings.alpaca_trading_url == "https://paper-api.alpaca.markets"


def test_loads_from_dotenv_file(env, tmp_path):
    lines = [f"{name}={value}" for name, value in REQUIRED.items()]
    (tmp_path / ".env").write_text("\n".join(lines))

    settings = load_settings()

    assert settings.alpaca_secret_key.get_secret_value() == "alpaca-secret-value"


def test_missing_secret_names_variable_without_leaking_other_secrets(env):
    env.setenv("LOOKOUT_ALPACA_KEY_ID", "alpaca-key-id-value")
    env.setenv("LOOKOUT_ALPACA_SECRET_KEY", "alpaca-secret-value")

    with pytest.raises(SettingsError) as error:
        load_settings()

    message = str(error.value)
    assert "LOOKOUT_WHATSAPP_TOKEN" in message
    assert "alpaca-key-id-value" not in message
    assert "alpaca-secret-value" not in message


def test_explanation_disabled_without_gemini_key(env):
    for name, value in REQUIRED.items():
        env.setenv(name, value)

    settings = load_settings()

    assert settings.explanation_enabled is False


def test_explanation_enabled_with_gemini_key(env):
    for name, value in REQUIRED.items():
        env.setenv(name, value)
    env.setenv("LOOKOUT_GEMINI_API_KEY", "gemini-value")

    assert load_settings().explanation_enabled is True


def test_repr_masks_every_secret(env):
    for name, value in REQUIRED.items():
        env.setenv(name, value)
    env.setenv("LOOKOUT_GEMINI_API_KEY", "gemini-value")

    text = repr(load_settings())

    for value in [*REQUIRED.values(), "gemini-value"]:
        assert value not in text
