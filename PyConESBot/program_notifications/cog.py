import logging
import os

from discord import Client, Embed
from discord.ext import commands, tasks

from configuration import Config
from program_notifications import session_to_embed
from program_notifications.livestream_connector import LivestreamConnector
from program_notifications.models import Session
from program_notifications.program_connector import ProgramConnector

config = Config()
_logger = logging.getLogger(f"bot.{__name__}")


class ProgramNotificationsCog(commands.Cog):
    def __init__(self, bot):
        self.bot: Client = bot
        self.program_connector = ProgramConnector(
            api_url=config.PROGRAM_API_URL,
            timezone_offset=config.TIMEZONE_OFFSET,
            cache_file=config.SCHEDULE_CACHE_FILE,
            simulated_start_time=config.SIMULATED_START_TIME,
            fast_mode=config.FAST_MODE,
            token=os.getenv("PRETALX_API_TOKEN"),
        )

        self.livestream_connector = LivestreamConnector(config.LIVESTREAM_URL_FILE)

        self.notified_sessions = set()
        _logger.info("Cog 'Program Notifications' has been initialized")

    @commands.Cog.listener()
    async def on_ready(self):
        if config.SIMULATED_START_TIME:
            _logger.info("Running in simulated time mode.")
            _logger.info("Will purge all room channels to avoid pile-up of test notifications.")
            await self.purge_all_room_channels()
            _logger.debug(f"Simulated start time: {config.SIMULATED_START_TIME}")
            _logger.debug(f"Fast mode: {config.FAST_MODE}")
        _logger.info("Starting the session notifier...")
        self.notify_sessions.start()
        _logger.info("Cog 'Program Notifications' is ready")

    async def cog_load(self) -> None:
        """
        Start schedule updater task
        """
        _logger.info(
            "Starting the schedule updater and setting the interval for the session notifier..."
        )
        self.fetch_schedule.start()
        self.fetch_livestreams.start()
        self.notify_sessions.change_interval(
            seconds=2 if config.FAST_MODE and config.SIMULATED_START_TIME else 60
        )
        _logger.info("Schedule updater started and interval set for the session notifier")

    async def cog_unload(self) -> None:
        """
        Stop all tasks
        """
        _logger.info("Stopping the schedule updater and the session notifier...")
        self.fetch_schedule.stop()
        self.notify_sessions.stop()
        _logger.info("Stopped the schedule updater and the session notifier")

    @tasks.loop(minutes=5)
    async def fetch_schedule(self):
        _logger.info("Starting the periodic schedule update...")
        await self.program_connector.fetch_schedule()

    @tasks.loop(minutes=5)
    async def fetch_livestreams(self):
        _logger.info("Starting the periodic livestream update...")
        await self.livestream_connector.fetch_livestreams()
        _logger.info("Finished the periodic livestream update.")

    async def set_room_topic(self, room: str, topic: str):
        """
        Set the topic of a room channel
        """
        room = room.lower().replace(" ", "_")
        if room not in config.PROGRAM_CHANNELS:
            return _logger.warning(f'Room "{room}" not found in the configuration.')
        channel_id = config.PROGRAM_CHANNELS[room]["channel_id"]
        channel = self.bot.get_channel(int(channel_id))
        await channel.edit(topic=topic)

    async def notify_room(self, room: str, embed: Embed, content: str = None):
        """
        Send the given notification to the room channel
        """
        room = room.lower().replace(" ", "_")
        if room not in config.PROGRAM_CHANNELS:
            return _logger.warning(f'Room "{room}" not found in the configuration.')
        channel_id = config.PROGRAM_CHANNELS[room]["channel_id"]
        channel = self.bot.get_channel(int(channel_id))
        await channel.send(content=content, embed=embed)

    @tasks.loop()
    async def notify_sessions(self):
        sessions: list[Session] = await self.program_connector.get_upcoming_sessions()
        sessions_to_notify = [
            session for session in sessions if session not in self.notified_sessions
        ]
        first_message = True

        for session in sessions_to_notify:
            if session.is_break:
                continue  # Don't notify break sessions

            livestream_url = await self.livestream_connector.get_livestream_url(
                session.room, session.start.date()
            )

            # Set the channel topic
            await self.set_room_topic(
                session.room,
                f"Livestream: [YouTube]({livestream_url})" if livestream_url else "",
            )

            embed = session_to_embed.create_session_embed(session, livestream_url)

            # # Notify specific rooms
            # for room in session.rooms:
            await self.notify_room(
                session.room, embed, content=f"# Empieza en 5 minutos @ {session.room}"
            )

            # Prefix the first message to the main channel with a header
            if first_message:
                await self.notify_room(
                    "Main Channel", embed, content="# Sesiones que comienzan en 5 minutos:"
                )
                first_message = False
            else:
                await self.notify_room("Main Channel", embed)

            self.notified_sessions.add(session)

    async def purge_all_room_channels(self):
        _logger.info("Purging all room channels...")
        for room in config.PROGRAM_CHANNELS.values():
            channel = self.bot.get_channel(int(room["channel_id"]))
            await channel.purge()
        _logger.info("Purged all room channels channels.")
