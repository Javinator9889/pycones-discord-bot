from datetime import date

import pytest

from program_notifications.livestream_connector import LivestreamConnector


def make_connector(tmp_path, content: str) -> LivestreamConnector:
    livestreams_file = tmp_path / "livestreams.toml"
    livestreams_file.write_text(content, encoding="utf-8")
    return LivestreamConnector(livestreams_file)


async def test_livestream_url_for_a_room_and_date(tmp_path):
    connector = make_connector(
        tmp_path,
        '[rooms.paraninfo]\nname = "Paraninfo Perk"\n"2026-11-07" = "https://example.com/live"\n',
    )

    assert await connector.get_livestream_url("Paraninfo Perk", date(2026, 11, 7)) == (
        "https://example.com/live"
    )
    assert await connector.get_livestream_url("Paraninfo Perk", date(2026, 11, 8)) is None
    assert await connector.get_livestream_url("Aula Magna", date(2026, 11, 7)) is None


@pytest.mark.parametrize("content", ["", "[rooms]\n"])
async def test_a_file_without_livestreams_is_valid(tmp_path, content):
    # the deployment creates an empty file: the notifications must work without livestreams
    connector = make_connector(tmp_path, content)

    assert await connector.get_livestream_url("Paraninfo Perk", date(2026, 11, 7)) is None
