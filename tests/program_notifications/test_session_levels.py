"""Tests for the session level, which comes from the Pretalx submissions endpoint."""

from datetime import date

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from program_notifications import session_to_embed
from program_notifications.models import Session, level_validator
from program_notifications.program_connector import ProgramConnector

DAY = date(2026, 11, 7)
LEVEL_QUESTION = 6818
OPTIONS = {
    "basic": {"en": "Basic (no previous knowledge is necessary)", "es": "Básica (...)"},
    "intermediate": {"en": "Intermediate (the related bases are needed)", "es": "Intermedia (...)"},
    "advanced": {"en": "Advanced (deeper knowledge is needed)", "es": "Avanzada (...)"},
}


def make_session(code: str, session_id: int) -> dict:
    # the schedule export does not contain any answers
    return {
        "type": "Charla",
        "id": session_id,
        "code": code,
        "slug": f"talk-{code}",
        "title": f"Talk {code}",
        "room": "Aula Magna",
        "persons": [{"code": "P1", "avatar": None, "public_name": "Ada", "url": "https://p/ada"}],
        "track": "Desarrollo web",
        "url": f"https://pretalx.com/pycones-2026/talk/{code}/",
        "abstract": "Abstract",
        "date": "2026-11-07T09:30:00+01:00",
        "duration": "00:35",
    }


SCHEDULE = {
    "schedule": {
        "conference": {
            "title": "PyConES 2026",
            "start": "2026-11-06",
            "end": "2026-11-08",
            "days": [
                {
                    "index": 2,
                    "date": "2026-11-07",
                    "rooms": {
                        "Aula Magna": [make_session("AAA111", 1), make_session("BBB222", 2)],
                        "Aula IA": [make_session("CCC333", 3)],
                    },
                }
            ],
        }
    }
}


def make_submission(code: str, level: str | None) -> dict:
    """A submission as returned by `?expand=answers,answers.options`."""
    answers = []
    if level is not None:
        answers.append(
            {
                "id": 1,
                "question": LEVEL_QUESTION,
                "answer": OPTIONS[level]["es"],
                "options": [{"id": 12821, "answer": OPTIONS[level]}],
            }
        )
    return {"code": code, "answers": answers}


class PretalxMock:
    def __init__(self) -> None:
        self.fail_submissions = False
        self.requests: dict[str, list] = {"schedule": [], "submissions": []}
        # two pages, to cover the pagination
        self.pages = [
            [make_submission("AAA111", "basic"), make_submission("BBB222", "advanced")],
            [make_submission("CCC333", "intermediate")],
        ]

    async def schedule(self, request: web.Request) -> web.Response:
        self.requests["schedule"].append(request)
        return web.json_response(SCHEDULE)

    async def submissions(self, request: web.Request) -> web.Response:
        self.requests["submissions"].append(request)
        if self.fail_submissions:
            return web.Response(status=500)
        page = int(request.query.get("page", "1"))
        next_url = None
        if page < len(self.pages):
            next_url = str(request.url.with_query({**request.query, "page": str(page + 1)}))
        return web.json_response({"results": self.pages[page - 1], "next": next_url})


@pytest.fixture
async def pretalx(aiohttp_client, unused_tcp_port_factory):
    mock = PretalxMock()
    app = web.Application()
    app.router.add_get("/schedule", mock.schedule)
    app.router.add_get("/submissions/", mock.submissions)
    client = await aiohttp_client(TestServer(app, port=unused_tcp_port_factory()))
    mock.client = client
    return mock


def make_connector(pretalx: PretalxMock, tmp_path, *, with_submissions: bool = True):
    return ProgramConnector(
        api_url=str(pretalx.client.make_url("/schedule")),
        submissions_api_url=(
            str(pretalx.client.make_url("/submissions/")) if with_submissions else None
        ),
        timezone_offset=1,
        cache_file=tmp_path / "schedule.json",
        token="MY_PRETALX_TOKEN",
    )


def levels(connector: ProgramConnector) -> dict[str, str | None]:
    return {s.slug: s.level for s in connector.sessions_by_day[DAY]}


async def test_levels_come_from_the_submissions_endpoint(pretalx, tmp_path):
    connector = make_connector(pretalx, tmp_path)

    await connector.fetch_schedule()

    assert levels(connector) == {
        "talk-AAA111": "basic",
        "talk-BBB222": "advanced",
        "talk-CCC333": "intermediate",  # second page
    }
    assert len(pretalx.requests["submissions"]) == 2


async def test_the_api_token_is_not_sent_to_the_submissions_endpoint(pretalx, tmp_path):
    connector = make_connector(pretalx, tmp_path)

    await connector.fetch_schedule()

    # the token is needed for the export but makes the submissions endpoint reject the request
    assert pretalx.requests["schedule"][0].headers["Authorization"] == "Token MY_PRETALX_TOKEN"
    assert all("Authorization" not in r.headers for r in pretalx.requests["submissions"])


async def test_answers_are_stored_in_the_schedule_cache(pretalx, tmp_path):
    await make_connector(pretalx, tmp_path).fetch_schedule()

    # a restarted bot that cannot reach Pretalx still knows the levels
    offline = make_connector(pretalx, tmp_path)
    offline.sessions_by_day = await offline._get_schedule_from_cache()

    assert levels(offline)["talk-AAA111"] == "basic"


async def test_level_is_na_if_the_submissions_endpoint_fails(pretalx, tmp_path):
    pretalx.fail_submissions = True
    connector = make_connector(pretalx, tmp_path)

    await connector.fetch_schedule()

    assert set(levels(connector).values()) == {"NA"}


async def test_last_known_levels_are_kept_if_a_later_fetch_fails(pretalx, tmp_path):
    connector = make_connector(pretalx, tmp_path)
    await connector.fetch_schedule()

    pretalx.fail_submissions = True
    await connector.fetch_schedule()

    assert levels(connector)["talk-BBB222"] == "advanced"


async def test_submissions_are_not_requested_if_not_configured(pretalx, tmp_path):
    connector = make_connector(pretalx, tmp_path, with_submissions=False)

    await connector.fetch_schedule()

    assert pretalx.requests["submissions"] == []
    assert set(levels(connector).values()) == {None}


@pytest.mark.parametrize(
    "answers,expected",
    [
        ([{"question": LEVEL_QUESTION, "options": [OPTIONS["basic"]]}], "basic"),
        ([{"question": LEVEL_QUESTION, "options": [OPTIONS["advanced"]]}], "advanced"),
        ([], "NA"),
        ([{"question": 1234, "options": [OPTIONS["basic"]]}], "NA"),  # another question
        ([{"question": LEVEL_QUESTION, "options": []}], "NA"),  # nothing selected
        ([{"question": LEVEL_QUESTION, "options": [{"en": "Expert (new option)"}]}], "NA"),
    ],
)
def test_level_validator(answers, expected):
    assert level_validator(answers) == expected


def make_embed(level: str | None):
    data = make_session("AAA111", 1)
    if level is not None:
        data["answers"] = [{"question": LEVEL_QUESTION, "options": [OPTIONS[level]]}]
    return session_to_embed.create_session_embed(Session.model_validate(data), None)


@pytest.mark.parametrize(
    "level,color,name",
    [
        ("basic", session_to_embed.LevelColors.BASIC.value, "Básico"),
        ("intermediate", session_to_embed.LevelColors.INTERMEDIATE.value, "Intermedio"),
        ("advanced", session_to_embed.LevelColors.ADVANCED.value, "Avanzado"),
    ],
)
def test_embed_shows_the_level(level, color, name):
    embed = make_embed(level)

    assert embed.color.value == color
    assert [(f.name, f.value) for f in embed.fields if f.name == "Nivel"] == [("Nivel", name)]


def test_embed_without_a_level_is_grey_and_has_no_level_field():
    embed = make_embed(None)  # e.g. a schedule cached before the levels were fetched

    assert embed.color.value == session_to_embed.LevelColors.NA.value
    assert "Nivel" not in [f.name for f in embed.fields]
