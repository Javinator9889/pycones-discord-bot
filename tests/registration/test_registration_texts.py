"""The registration message and form that attendees see (English) and Discord's limits."""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from registration import cog as registration_cog

SPANISH = re.compile(r"[áéíóúñ¿¡]|\b(registro|regístrate|ticket del|escríbenos|gracias)\b", re.I)


@pytest.fixture
def cog(monkeypatch, tmp_path):
    monkeypatch.setenv("PRETIX_TOKEN", "dummy")
    monkeypatch.chdir(tmp_path)  # the cog creates its cache / log files in the working directory
    cog = registration_cog.RegistrationCog(MagicMock())
    cog.pretix_connector.fetch_pretix_data = AsyncMock()
    return cog


async def test_the_welcome_message_is_in_english(cog):
    channel = MagicMock(purge=AsyncMock(), send=AsyncMock())
    cog.bot.get_channel.return_value = channel

    await cog.on_ready()

    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "Welcome to PyConES 2026 on Discord! 🎉🐍"
    assert 'button that says "Register here 👈"' in embed.description
    # the help channel is mentioned by ID, so renaming the channel does not break the message
    assert f"<#{registration_cog.config.REG_HELP_CHANNEL_ID}>" in embed.description
    assert not SPANISH.search(embed.title + embed.description)
    assert len(embed.description) <= 4096  # Discord limit


async def test_the_offline_message_is_in_english(cog):
    channel = MagicMock(purge=AsyncMock(), send=AsyncMock())
    cog.bot.get_channel.return_value = channel

    await cog.cog_unload()

    embed = channel.send.await_args.kwargs["embed"]
    assert "currently offline" in embed.description
    assert not SPANISH.search(embed.description)


def test_the_button_and_the_form_fit_discord_limits_and_are_in_english(cog):
    button = registration_cog.RegistrationButton(parent_cog=cog)
    texts = [
        button.label,
        registration_cog.RegistrationForm.title,
        registration_cog.RegistrationForm.order_field.label,
        registration_cog.RegistrationForm.order_field.placeholder,
        registration_cog.RegistrationForm.name_field.label,
        registration_cog.RegistrationForm.name_field.placeholder,
    ]

    assert button.label == "Register here 👈"
    assert not any(SPANISH.search(t) for t in texts)
    assert len(button.label) <= 80
    assert len(registration_cog.RegistrationForm.title) <= 45
    assert len(registration_cog.RegistrationForm.order_field.label) <= 45
    assert len(registration_cog.RegistrationForm.name_field.label) <= 45
    assert len(registration_cog.RegistrationForm.order_field.placeholder) <= 100
