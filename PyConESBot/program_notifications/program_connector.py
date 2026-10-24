import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aiofiles
import aiohttp

from program_notifications.models import Conference, Session

_logger = logging.getLogger(f"bot.{__name__}")


class ProgramConnector:
    def __init__(
        self,
        api_url: str,
        timezone_offset: int,
        cache_file: Path,
        simulated_start_time: datetime | None = None,
        fast_mode: bool = False,
        token: str | None = None,
        submissions_api_url: str | None = None,
    ) -> None:
        self._api_url = api_url
        self._submissions_api_url = submissions_api_url
        # answers to the questions of the submissions (e.g. the level) by submission code
        self._answers_by_code: dict[str, list[dict]] = {}
        self._timezone = timezone(timedelta(hours=timezone_offset))
        self._cache_file = cache_file
        self._http_headers = {
            "Accept": "application/json",
            "User-Agent": "PyConESBot",
            # Force English to avoid issues with the API
            "Accept-Language": "en",
        }
        if token:
            self._http_headers["Authorization"] = f"Token {token}"

        # time travel parameters for testing
        self._simulated_start_time = simulated_start_time
        if self._simulated_start_time:
            self._time_multiplier = 60 if fast_mode else 1
            self._real_start_time = datetime.now(tz=self._timezone)

        self._fetch_lock = asyncio.Lock()
        self.sessions_by_day: dict[date, list[Session]] | None = None

    async def parse_schedule(self, schedule: dict) -> dict[date, list[Session]]:
        """
        Parse the schedule data and return a dictionary with
        the sessions grouped by date.
        """
        conference: Conference = Conference.model_validate(schedule["schedule"]["conference"])

        sessions_by_day = {}
        for day in conference.days:
            day_sessions = []
            for sessions in day.rooms.values():
                for session in sessions:
                    if session.is_break:
                        continue
                    day_sessions.append(session)

            sessions_by_day[day.date] = day_sessions

        return sessions_by_day

    async def fetch_schedule(self) -> None:
        """
        Fetch schedule data from the Program API and
        write it to a file in case the API goes down.
        """
        async with self._fetch_lock:
            try:
                async with aiohttp.ClientSession(headers=self._http_headers) as session:
                    async with session.get(self._api_url) as response:
                        response.raise_for_status()
                        schedule = await response.json()

            except aiohttp.ClientError as e:
                _logger.warning(f"Error fetching schedule: {e}.")

                if self.sessions_by_day is not None:
                    _logger.info("Schedule not updated, using the one loaded in memory.")
                    return

                self.sessions_by_day = await self._get_schedule_from_cache()
                _logger.info("Schedule loaded from cache file.")
                return

            _logger.info("Schedule fetched successfully.")

            await self._add_session_answers(schedule)

            # write schedule to file in case the API goes down
            _logger.info(f"Writing schedule to {self._cache_file}...")
            Path(self._cache_file).parent.mkdir(exist_ok=True, parents=True)
            async with aiofiles.open(self._cache_file, "w") as f:
                await f.write(json.dumps(schedule, indent=2))
            _logger.info("Schedule written to cache file.")

            self.sessions_by_day = await self.parse_schedule(schedule)
            _logger.info("Schedule parsed and loaded.")

    async def _add_session_answers(self, schedule: dict) -> None:
        """
        Add the answers to the submission questions (e.g. the level) to the sessions.

        The schedule export never includes the answers, so they are fetched from the
        submissions endpoint of the Pretalx API instead. If that fails, the answers that
        were fetched last are used, so a hiccup does not turn every level into "NA".
        """
        if not self._submissions_api_url:
            return

        try:
            self._answers_by_code = await self._fetch_answers_by_code()
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as e:
            _logger.warning(
                f"Error fetching the session answers: {e!r}. Using the last known ones."
            )

        for day in schedule["schedule"]["conference"]["days"]:
            for sessions in day["rooms"].values():
                for session in sessions:
                    session["answers"] = self._answers_by_code.get(session.get("code"), [])

    async def _fetch_answers_by_code(self) -> dict[str, list[dict]]:
        """
        Fetch the answers of all the submissions, grouped by submission code.

        Each answer has the shape the `Session` model expects: the question ID and the
        (localized) texts of the selected options.
        """
        # The endpoint is public. The request is sent without the API token on purpose: the
        # token has no permission on this endpoint and would make Pretalx reject the request.
        headers = {k: v for k, v in self._http_headers.items() if k != "Authorization"}
        params: dict[str, str] | None = {"expand": "answers,answers.options", "limit": "100"}
        timeout = aiohttp.ClientTimeout(total=30)

        answers_by_code: dict[str, list[dict]] = {}
        url: str | None = self._submissions_api_url
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as http_session:
            while url:
                async with http_session.get(url, params=params) as response:
                    response.raise_for_status()
                    page = await response.json()

                params = None  # the URL of the next page already includes the query
                for submission in page["results"]:
                    answers_by_code[submission["code"]] = [
                        {
                            "question": answer["question"],
                            "answer": answer["answer"],
                            "options": [option["answer"] for option in answer["options"]],
                        }
                        for answer in submission["answers"]
                    ]
                url = page.get("next")

        return answers_by_code

    async def _get_schedule_from_cache(self) -> dict[date, list[Session]]:
        """
        Get the schedule data from the cache file.
        """
        try:
            _logger.info(f"Getting schedule from cache file {self._cache_file}...")
            async with aiofiles.open(self._cache_file, "r") as f:
                schedule = json.loads(await f.read())

            return await self.parse_schedule(schedule)

        except FileNotFoundError:
            _logger.error("Schedule cache file not found and no schedule is already loaded.")

    async def _get_now(self) -> datetime:
        """Get the current time in the conference timezone."""
        if self._simulated_start_time:
            elapsed = datetime.now(tz=self._timezone) - self._real_start_time
            simulated_now = self._simulated_start_time + elapsed * self._time_multiplier
            return simulated_now.astimezone(self._timezone)
        else:
            return datetime.now(tz=self._timezone)

    async def get_sessions_by_date(self, date_now: date) -> list[Session]:
        if self.sessions_by_day is None:
            await self.fetch_schedule()

        sessions_on_day = []

        try:
            sessions_on_day = self.sessions_by_day[date_now]
        except KeyError:
            # debug to keep the logs clean,
            # because this is expected on non-conference days
            _logger.debug(f"No sessions found on {date_now}")
        except TypeError:
            _logger.error("Schedule data is not loaded.")

        return sessions_on_day

    async def get_upcoming_sessions(self) -> list[Session]:
        # upcoming sessions are those that start in 5 minutes or less
        # and the start time is after the current time
        now = await self._get_now()

        if self._simulated_start_time:
            _logger.debug(f"Simulated time now: {now}")

        sessions = await self.get_sessions_by_date(now.date())

        return [
            session for session in sessions if now < session.start <= now + timedelta(minutes=5)
        ]
