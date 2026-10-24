from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from extensions.organisers.organisers import Organisers
from extensions.organisers.roles import Roles

ROLES = Roles(
    organizers=1,
    volunteers=2,
    volunteers_onsite=0,  # placeholder: not configured
    volunteers_remote=0,
    sponsors=3,
    speakers=4,
    participants=5,
    participants_onsite=0,
    participants_remote=0,
    supporter=6,
)


@pytest.fixture()
def cog() -> Organisers:
    return Organisers(bot=MagicMock(), roles=ROLES)


def make_guild(members_by_role: dict[int, int], member_roles: list[int]) -> MagicMock:
    guild = MagicMock()
    guild.member_count = len(member_roles)
    # every member has the @everyone role plus its own roles
    guild.members = [SimpleNamespace(roles=[0, *roles]) for roles in member_roles]
    guild.get_role.side_effect = lambda role_id: (
        SimpleNamespace(members=[object()] * members_by_role[role_id])
        if role_id in members_by_role
        else None
    )
    return guild


async def test_cog_check_requires_the_organizers_role(cog):
    organizer = SimpleNamespace(
        author=SimpleNamespace(roles=[SimpleNamespace(id=5), SimpleNamespace(id=1)])
    )
    participant = SimpleNamespace(author=SimpleNamespace(roles=[SimpleNamespace(id=5)]))

    assert await cog.cog_check(organizer) is True
    assert await cog.cog_check(participant) is False


def test_counts_cover_every_configured_role(cog):
    guild = make_guild(
        {1: 3, 2: 4, 3: 2, 4: 5, 5: 40, 6: 7},
        member_roles=[[], [5], [5, 6]],
    )

    counts = cog._get_counts(guild)

    assert counts.everyone == 3
    assert counts.not_registered == 1
    assert counts.organizers == 3
    assert counts.volunteers == 4
    assert counts.sponsors == 2
    assert counts.speakers == 5
    assert counts.participants == 40
    assert counts.supporter == 7


def test_unconfigured_roles_count_as_zero(cog):
    # the *_onsite / *_remote roles are placeholders (ID 0) that do not exist in the guild
    guild = make_guild({1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1}, member_roles=[[]])

    counts = cog._get_counts(guild)

    assert counts.volunteers_onsite == 0
    assert counts.volunteers_remote == 0
    assert counts.participants_onsite == 0
    assert counts.participants_remote == 0
