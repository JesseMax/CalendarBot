# Discord Bot to interact with google calendar
# CalendarBot 1.0
# Created by Jesse Smith
# Discord: DMJesseMax #2197
# Github: https://github.com/JesseMax
# 

from __future__ import annotations

import asyncio
import datetime
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv()  # reads the .env file into environment variables

# --- CONFIGURATION (from environment variables) ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
CALENDAR_ID = os.environ["CALENDAR_ID"]
GUILD_ID = int(os.environ["GUILD_ID"])
CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

log = logging.getLogger("calendar-bot")

# Accepted date formats, tried in order
DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y")

# --- GOOGLE CALENDAR SETUP ---
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
calendar_service = build("calendar", "v3", credentials=creds)


# --- DISCORD SETUP ---
class CalendarBot(commands.Bot):
    async def setup_hook(self):
        # Runs once at startup (unlike on_ready, which can fire on every reconnect).
        # Sync only to your guild for instant command registration.
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %s", GUILD_ID)


intents = discord.Intents.default()
bot = CalendarBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Logged in as %s", bot.user)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Catch anything a command doesn't handle, so it never hangs on 'thinking'."""
    log.error("Unhandled error in /%s", interaction.command.name if interaction.command else "?", exc_info=error)
    message = "❌ Something went wrong. Please try again later."
    if interaction.response.is_done():
        await interaction.followup.send(message)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# --- HELPERS ---
def title_is_game(title: str) -> bool:
    """True if 'game' appears anywhere in the title (Game, Endgame, gameplay, ...)."""
    return "game" in title.lower()


def is_game(event: dict) -> bool:
    return title_is_game(event.get("summary", ""))


def event_date(event: dict) -> str:
    """Return the event's start date as YYYY-MM-DD."""
    raw = event["start"].get("dateTime", event["start"].get("date"))
    return raw.split("T")[0]


def parse_date(text: str) -> datetime.date | None:
    """Parse MM/DD/YYYY or MM/DD/YY. Returns None if invalid."""
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _fetch_events_sync(max_results: int) -> list[dict]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = (
        calendar_service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


async def fetch_events(max_results: int) -> list[dict]:
    """Run the blocking Google API call in a thread so the bot stays responsive."""
    return await asyncio.to_thread(_fetch_events_sync, max_results)


def _insert_event_sync(body: dict) -> dict:
    return calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()


# --- COMMANDS ---
@bot.tree.command(name="list", description="Fetch the next 10 events from the shared calendar.")
async def list_events(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        events = await fetch_events(10)
    except HttpError:
        log.exception("Google Calendar error in /list")
        await interaction.followup.send("❌ Couldn't reach the calendar. Please try again later.")
        return
    except Exception:
        log.exception("Unexpected error in /list")
        await interaction.followup.send("❌ Something went wrong. Please try again later.")
        return

    if not events:
        await interaction.followup.send("No upcoming events found.")
        return

    embed = discord.Embed(
        title="Next 10 Upcoming Events",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    for event in events:
        title = event.get("summary", "Untitled Event")
        icon = "✅" if is_game(event) else "❌"
        embed.add_field(name=f"{icon} {title}", value=event_date(event), inline=False)

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="games", description="Fetch only upcoming game events from the calendar.")
async def game_events(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        # Fetch extra events so we still find games if non-game events come first
        events = await fetch_events(25)
    except HttpError:
        log.exception("Google Calendar error in /games")
        await interaction.followup.send("❌ Couldn't reach the calendar. Please try again later.")
        return
    except Exception:
        log.exception("Unexpected error in /games")
        await interaction.followup.send("❌ Something went wrong. Please try again later.")
        return

    game_list = [e for e in events if is_game(e)][:10]

    if not game_list:
        await interaction.followup.send("No upcoming game events found.")
        return

    lines = ["⚔️ UPCOMING GAMES\n"]
    for event in game_list:
        title = event.get("summary", "Untitled Event")
        lines.append(f"**{title}**\n`{event_date(event)}`\n")

    embed = discord.Embed(description="\n".join(lines), color=discord.Color.green())
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="create", description="Create an all-day event in Google Calendar.")
@app_commands.guild_only()
@app_commands.default_permissions(manage_events=True)
@app_commands.describe(
    title="Name of the event (include 'Game' for D&D events)",
    date="Date in MM/DD/YYYY format (e.g., 10/15/2026)",
)
async def create_event(interaction: discord.Interaction, title: str, date: str):
    await interaction.response.defer()

    parsed = parse_date(date)
    if parsed is None:
        await interaction.followup.send(
            "❌ Invalid date format. Please use `MM/DD/YYYY` (e.g., `10/15/2026`)."
        )
        return

    # Google treats the end date of all-day events as exclusive, so use the next day
    event_body = {
        "summary": title,
        "start": {"date": parsed.isoformat()},
        "end": {"date": (parsed + datetime.timedelta(days=1)).isoformat()},
    }

    try:
        await asyncio.to_thread(_insert_event_sync, event_body)
    except HttpError:
        log.exception("Google Calendar error in /create")
        await interaction.followup.send("❌ Failed to create the event. Please try again later.")
        return
    except Exception:
        log.exception("Unexpected error in /create")
        await interaction.followup.send("❌ Something went wrong. Please try again later.")
        return

    icon = "⚔️" if title_is_game(title) else ""
    embed = discord.Embed(
        title=f"{icon} Event Created Successfully!".strip(),
        description=f"**{title}**\n`{parsed.strftime('%m/%d/%Y')}` (All Day)",
        color=discord.Color.green(),
    )
    await interaction.followup.send(embed=embed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bot.run(DISCORD_TOKEN)
