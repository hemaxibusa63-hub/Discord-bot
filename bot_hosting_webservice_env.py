# ============================================================
# TEAM XYZ - FREE FIRE ESPORTS DISCORD BOT
# Clean Final Version
# Render Web Service + Discord.py + Gemini
# ============================================================

import os
import io
import json
import time
import random
import sqlite3
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ============================================================
# CONFIG
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Change this from Render Environment Variables if needed.
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()

DB_PATH = os.getenv("DB_PATH", "team_xyz.db").strip()

# Private Discord channel used as actual file storage.
STORAGE_CHANNEL_ID = int(
    os.getenv("STORAGE_CHANNEL_ID", "0") or "0"
)

PORT = int(os.getenv("PORT", "10000"))

# Maximum simultaneous Gemini calls.
# Four members can use /ai at the same time.
AI_MAX_CONCURRENT = int(
    os.getenv("AI_MAX_CONCURRENT", "4")
)

AI_RETRIES = 3

# ============================================================
# TEAM XYZ MEMBERS
# ============================================================

TEAM = {
    1549315294856740885: {
        "name": "Nirav",
        "role": "IGL + Primary Rusher",
    },
    1378993981769252966: {
        "name": "KRUTIK",
        "role": "Sniper",
    },
    1549303069756624968: {
        "name": "Dakshit",
        "role": "Supporter",
    },
    1549301330110185533: {
        "name": "Atul",
        "role": "Secondary Rusher",
    },
}


def is_team_member(user_id: int) -> bool:
    return user_id in TEAM


# ============================================================
# DATABASE
# ============================================================

DB_LOCK = threading.RLock()


def db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    with DB_LOCK:
        conn = db()
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS scrims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opponent TEXT,
            scheduled_at TEXT,
            room_id TEXT,
            password TEXT,
            map TEXT,
            notes TEXT,
            result TEXT,
            created_by INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            organizer TEXT,
            scheduled_at TEXT,
            rounds TEXT,
            room_info TEXT,
            result TEXT,
            placement INTEGER,
            points REAL DEFAULT 0.0,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            opponent TEXT,
            scheduled_at TEXT,
            map TEXT,
            placement INTEGER,
            team_kills INTEGER DEFAULT 0,
            result TEXT,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            kills INTEGER DEFAULT 0,
            damage REAL DEFAULT 0.0,
            placement INTEGER DEFAULT 0,
            booyah INTEGER DEFAULT 0,
            survival_seconds INTEGER DEFAULT 0,
            points REAL DEFAULT 0.0,
            match_name TEXT,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            title TEXT,
            content TEXT,
            created_by INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS training (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            title TEXT,
            goals TEXT,
            progress TEXT,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            title TEXT,
            description TEXT,
            achieved_at TEXT,
            created_by INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            scheduled_at TEXT,
            sent INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            content_type TEXT,
            size INTEGER,
            uploader_id INTEGER,
            storage_channel_id INTEGER,
            storage_message_id INTEGER,
            storage_url TEXT,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER,
            content TEXT,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            created_at TEXT
        )
        """)

        conn.commit()
        conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_activity(user_id, action):

    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            INSERT INTO activity
            (user_id, action, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, action, now_iso())
        )
        conn.commit()
        conn.close()


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

ai_semaphore = asyncio.Semaphore(
    AI_MAX_CONCURRENT
)


# ============================================================
# HELPERS
# ============================================================

async def send_long(destination, text, *, ephemeral=False):

    if not text:
        return

    text = str(text)

    chunks = []

    while len(text) > 1900:
        cut = text.rfind("\n", 0, 1900)

        if cut < 500:
            cut = 1900

        chunks.append(text[:cut])
        text = text[cut:].lstrip()

    if text:
        chunks.append(text)

    for i, chunk in enumerate(chunks):

        if isinstance(destination, discord.Interaction):

            if i == 0:
                if destination.response.is_done():
                    await destination.followup.send(
                        chunk,
                        ephemeral=ephemeral
                    )
                else:
                    await destination.response.send_message(
                        chunk,
                        ephemeral=ephemeral
                    )
            else:
                await destination.followup.send(
                    chunk,
                    ephemeral=ephemeral
                )

        else:
            await destination.send(chunk)


def team_only():
    async def predicate(interaction: discord.Interaction):

        if not is_team_member(interaction.user.id):
            raise app_commands.CheckFailure(
                "TEAM XYZ members only."
            )

        return True

    return app_commands.check(predicate)


def member_info(user_id):

    return TEAM.get(
        user_id,
        {
            "name": "Unknown",
            "role": "Team Member"
        }
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print("=" * 60)
    print("TEAM XYZ BOT ONLINE")
    print(f"Logged in as: {bot.user}")
    print(f"Gemini model: {GEMINI_MODEL}")
    print(f"AI concurrent slots: {AI_MAX_CONCURRENT}")
    print("=" * 60)

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print("Slash command sync error:", repr(e))

    if not reminder_loop.is_running():
        reminder_loop.start()

    if not announcement_loop.is_running():
        announcement_loop.start()


# ============================================================
# MESSAGE CONTEXT + FILE STORAGE
# ============================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if not is_team_member(message.author.id):
        return

    # Save only short/relevant team chat context.
    content = (message.content or "").strip()

    if content:
        content = content[:1500]

        with DB_LOCK:
            conn = db()

            conn.execute(
                """
                INSERT INTO chat_context
                (author_id, content, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    message.author.id,
                    content,
                    now_iso()
                )
            )

            # Keep database small.
            conn.execute("""
                DELETE FROM chat_context
                WHERE id NOT IN (
                    SELECT id
                    FROM chat_context
                    ORDER BY id DESC
                    LIMIT 200
                )
            """)

            conn.commit()
            conn.close()

    # Store attachments in private Discord storage channel.
    if message.attachments:

        await store_message_attachments(message)

    await bot.process_commands(message)


async def store_message_attachments(message):

    if not STORAGE_CHANNEL_ID:
        print(
            "STORAGE_CHANNEL_ID not configured; "
            "attachments were not copied."
        )
        return

    channel = bot.get_channel(STORAGE_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(
                STORAGE_CHANNEL_ID
            )
        except Exception as e:
            print("Storage channel error:", repr(e))
            return

    for attachment in message.attachments:

        try:
            data = await attachment.read()

            file_obj = discord.File(
                io.BytesIO(data),
                filename=attachment.filename
            )

            storage_message = await channel.send(
                content=(
                    f"📁 **TEAM XYZ FILE**\n"
                    f"Uploader: {message.author.mention}\n"
                    f"Original message: {message.id}\n"
                    f"File: `{attachment.filename}`"
                ),
                file=file_obj
            )

            with DB_LOCK:
                conn = db()

                conn.execute(
                    """
                    INSERT INTO files
                    (
                        filename,
                        content_type,
                        size,
                        uploader_id,
                        storage_channel_id,
                        storage_message_id,
                        storage_url,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attachment.filename,
                        attachment.content_type or "",
                        attachment.size,
                        message.author.id,
                        STORAGE_CHANNEL_ID,
                        storage_message.id,
                        storage_message.jump_url,
                        now_iso()
                    )
                )

                conn.commit()
                conn.close()

            log_activity(
                message.author.id,
                f"Uploaded file: {attachment.filename}"
            )

        except Exception as e:
            print(
                f"Attachment storage error "
                f"{attachment.filename}: {repr(e)}"
            )


# ============================================================
# /TEAM
# ============================================================

@bot.tree.command(
    name="team",
    description="Show TEAM XYZ roster"
)
@team_only()
async def team(interaction: discord.Interaction):

    lines = [
        "🔥 **TEAM XYZ — FREE FIRE ESPORTS**",
        ""
    ]

    for user_id, info in TEAM.items():

        lines.append(
            f"👤 **{info['name']}**\n"
            f"Role: {info['role']}\n"
            f"Discord ID: `{user_id}`\n"
        )

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# SCRIMS
# ============================================================

@bot.tree.command(
    name="scrim_create",
    description="Create a scrim"
)
@app_commands.describe(
    opponent="Opponent team",
    scheduled_at="UTC date/time e.g. 2026-09-20 18:30",
    room_id="Room ID",
    password="Room password",
    map_name="Map",
    notes="Notes"
)
@team_only()
async def scrim_create(
    interaction,
    opponent: str,
    scheduled_at: str,
    room_id: str = "",
    password: str = "",
    map_name: str = "Bermuda",
    notes: str = ""
):

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO scrims
            (
                opponent,
                scheduled_at,
                room_id,
                password,
                map,
                notes,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opponent,
                scheduled_at,
                room_id,
                password,
                map_name,
                notes,
                interaction.user.id,
                now_iso()
            )
        )

        conn.commit()
        conn.close()

    log_activity(
        interaction.user.id,
        f"Created scrim vs {opponent}"
    )

    await interaction.response.send_message(
        f"⚔️ **Scrim created**\n"
        f"Opponent: **{opponent}**\n"
        f"Time: **{scheduled_at}**\n"
        f"Map: **{map_name}**\n"
        f"Room: `{room_id or 'Not set'}`"
    )


@bot.tree.command(
    name="scrims",
    description="Show scrims"
)
@team_only()
async def scrims(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM scrims
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        conn.close()

    if not rows:
        await interaction.response.send_message(
            "⚔️ No scrims found."
        )
        return

    lines = ["⚔️ **TEAM XYZ SCRIMS**", ""]

    for r in rows:

        lines.append(
            f"**#{r['id']} — {r['opponent']}**\n"
            f"🕒 {r['scheduled_at']}\n"
            f"🗺️ {r['map']}\n"
            f"🏠 Room: `{r['room_id'] or 'N/A'}`\n"
            f"📌 Result: {r['result'] or 'Pending'}\n"
        )

    await send_long(
        interaction,
        "\n".join(lines)
    )


@bot.tree.command(
    name="scrim_result",
    description="Update scrim result"
)
@app_commands.describe(
    scrim_id="Scrim ID",
    result="Result"
)
@team_only()
async def scrim_result(
    interaction,
    scrim_id: int,
    result: str
):

    with DB_LOCK:
        conn = db()

        cur = conn.execute(
            """
            UPDATE scrims
            SET result=?
            WHERE id=?
            """,
            (result, scrim_id)
        )

        conn.commit()
        conn.close()

    if cur.rowcount == 0:
        await interaction.response.send_message(
            "❌ Scrim not found."
        )
        return

    log_activity(
        interaction.user.id,
        f"Updated scrim #{scrim_id}"
    )

    await interaction.response.send_message(
        f"✅ Scrim #{scrim_id} result updated: **{result}**"
    )


# ============================================================
# TOURNAMENTS
# ============================================================

@bot.tree.command(
    name="tournament_create",
    description="Create tournament"
)
@app_commands.describe(
    name="Tournament name",
    organizer="Organizer",
    scheduled_at="UTC date/time",
    rounds="Rounds",
    room_info="Room information",
    notes="Notes"
)
@team_only()
async def tournament_create(
    interaction,
    name: str,
    organizer: str,
    scheduled_at: str,
    rounds: str = "",
    room_info: str = "",
    notes: str = ""
):

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO tournaments
            (
                name,
                organizer,
                scheduled_at,
                rounds,
                room_info,
                notes,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                organizer,
                scheduled_at,
                rounds,
                room_info,
                notes,
                interaction.user.id,
                now_iso()
            )
        )

        conn.commit()
        conn.close()

    await interaction.response.send_message(
        f"🏆 **Tournament created**\n"
        f"Name: **{name}**\n"
        f"Organizer: {organizer}\n"
        f"Time: {scheduled_at}"
    )


@bot.tree.command(
    name="tournaments",
    description="Show tournaments"
)
@team_only()
async def tournaments(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM tournaments
            ORDER BY id DESC
            LIMIT 15
            """
        ).fetchall()

        conn.close()

    if not rows:
        await interaction.response.send_message(
            "🏆 No tournaments found."
        )
        return

    lines = ["🏆 **TEAM XYZ TOURNAMENTS**", ""]

    for r in rows:

        lines.append(
            f"**#{r['id']} — {r['name']}**\n"
            f"👤 Organizer: {r['organizer']}\n"
            f"🕒 {r['scheduled_at']}\n"
            f"🏅 Placement: {r['placement'] or 'Pending'}\n"
            f"⭐ Points: {r['points']}\n"
            f"📌 Result: {r['result'] or 'Pending'}\n"
        )

    await send_long(
        interaction,
        "\n".join(lines)
    )


@bot.tree.command(
    name="tournament_result",
    description="Update tournament result"
)
@team_only()
async def tournament_result(
    interaction,
    tournament_id: int,
    placement: int,
    points: float,
    result: str
):

    with DB_LOCK:
        conn = db()

        cur = conn.execute(
            """
            UPDATE tournaments
            SET placement=?,
                points=?,
                result=?
            WHERE id=?
            """,
            (
                placement,
                points,
                result,
                tournament_id
            )
        )

        conn.commit()
        conn.close()

    if cur.rowcount == 0:
        await interaction.response.send_message(
            "❌ Tournament not found."
        )
        return

    await interaction.response.send_message(
        f"🏆 Tournament updated\n"
        f"Placement: **#{placement}**\n"
        f"Points: **{points}**\n"
        f"Result: **{result}**"
    )


# ============================================================
# MATCHES
# ============================================================

@bot.tree.command(
    name="match_create",
    description="Create match"
)
@team_only()
async def match_create(
    interaction,
    title: str,
    opponent: str,
    scheduled_at: str,
    map_name: str = "Bermuda",
    notes: str = ""
):

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO matches
            (
                title,
                opponent,
                scheduled_at,
                map,
                notes,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                opponent,
                scheduled_at,
                map_name,
                notes,
                interaction.user.id,
                now_iso()
            )
        )

        conn.commit()
        conn.close()

    await interaction.response.send_message(
        f"🎯 **Match created**\n"
        f"Match: **{title}**\n"
        f"Opponent: **{opponent}**\n"
        f"Time: {scheduled_at}\n"
        f"Map: {map_name}"
    )


@bot.tree.command(
    name="matches",
    description="Show matches"
)
@team_only()
async def matches(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM matches
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        conn.close()

    if not rows:
        await interaction.response.send_message(
            "🎯 No matches found."
        )
        return

    lines = ["🎯 **TEAM XYZ MATCHES**", ""]

    for r in rows:

        lines.append(
            f"**#{r['id']} — {r['title']}**\n"
            f"Opponent: {r['opponent']}\n"
            f"Time: {r['scheduled_at']}\n"
            f"Map: {r['map']}\n"
            f"Kills: {r['team_kills']}\n"
            f"Placement: {r['placement'] or 'Pending'}\n"
        )

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# PLAYER STATS
# ============================================================

@bot.tree.command(
    name="stat_add",
    description="Add player statistics"
)
@team_only()
async def stat_add(
    interaction,
    player: discord.Member,
    kills: int,
    damage: float,
    placement: int,
    booyah: int = 0,
    survival_seconds: int = 0,
    points: float = 0.0,
    match_name: str = ""
):

    if player.id not in TEAM:
        await interaction.response.send_message(
            "❌ Player is not a TEAM XYZ member."
        )
        return

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO player_stats
            (
                player_id,
                kills,
                damage,
                placement,
                booyah,
                survival_seconds,
                points,
                match_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player.id,
                kills,
                damage,
                placement,
                booyah,
                survival_seconds,
                points,
                match_name,
                now_iso()
            )
        )

        conn.commit()
        conn.close()

    await interaction.response.send_message(
        f"📊 Stats added for **{player.display_name}**\n"
        f"Kills: {kills}\n"
        f"Damage: {damage}\n"
        f"Placement: #{placement}\n"
        f"Booyah: {booyah}\n"
        f"Points: {points}"
    )


@bot.tree.command(
    name="stats",
    description="Show team statistics"
)
@team_only()
async def stats(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT
                player_id,
                SUM(kills) AS kills,
                SUM(damage) AS damage,
                SUM(booyah) AS booyah,
                SUM(points) AS points,
                COUNT(*) AS games
            FROM player_stats
            GROUP BY player_id
            """
        ).fetchall()

        conn.close()

    lines = ["📊 **TEAM XYZ STATISTICS**", ""]

    for r in rows:

        info = member_info(r["player_id"])

        lines.append(
            f"👤 **{info['name']}** — {info['role']}\n"
            f"Games: {r['games']}\n"
            f"Kills: {r['kills'] or 0}\n"
            f"Damage: {round(r['damage'] or 0, 1)}\n"
            f"Booyah: {r['booyah'] or 0}\n"
            f"Points: {round(r['points'] or 0, 1)}\n"
        )

    if len(lines) == 2:
        lines.append("No statistics yet.")

    await send_long(
        interaction,
        "\n".join(lines)
    )


@bot.tree.command(
    name="player_stats",
    description="Show one player's statistics"
)
@team_only()
async def player_stats(
    interaction,
    player: discord.Member
):

    if player.id not in TEAM:
        await interaction.response.send_message(
            "❌ TEAM XYZ member only."
        )
        return

    with DB_LOCK:
        conn = db()

        r = conn.execute(
            """
            SELECT
                SUM(kills) AS kills,
                SUM(damage) AS damage,
                SUM(booyah) AS booyah,
                SUM(points) AS points,
                COUNT(*) AS games
            FROM player_stats
            WHERE player_id=?
            """,
            (player.id,)
        ).fetchone()

        conn.close()

    info = member_info(player.id)

    await interaction.response.send_message(
        f"📊 **{info['name']} — {info['role']}**\n\n"
        f"Games: {r['games'] or 0}\n"
        f"Kills: {r['kills'] or 0}\n"
        f"Damage: {round(r['damage'] or 0, 1)}\n"
        f"Booyah: {r['booyah'] or 0}\n"
        f"Points: {round(r['points'] or 0, 1)}"
    )


# ============================================================
# STRATEGY
# ============================================================

@bot.tree.command(
    name="strategy_add",
    description="Add strategy"
)
@team_only()
async def strategy_add(
    interaction,
    category: str,
    title: str,
    content: str
):

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO strategies
            (
                category,
                title,
                content,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                category,
                title,
                content,
                interaction.user.id,
                now_iso()
            )
        )

        conn.commit()
        conn.close()

    await interaction.response.send_message(
        f"🧠 Strategy saved: **{title}**"
    )


@bot.tree.command(
    name="strategies",
    description="Show strategies"
)
@team_only()
async def strategies(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM strategies
            ORDER BY id DESC
            LIMIT 15
            """
        ).fetchall()

        conn.close()

    lines = ["🧠 **TEAM XYZ STRATEGIES**", ""]

    for r in rows:

        lines.append(
            f"**{r['title']}**\n"
            f"Category: {r['category']}\n"
            f"{r['content']}\n"
        )

    if len(lines) == 2:
        lines.append("No strategies yet.")

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# TRAINING
# ============================================================

@bot.tree.command(
    name="training_add",
    description="Add training plan"
)
@team_only()
async def training_add(
    interaction,
    category: str,
    title: str,
    goals: str,
    progress: str = "Not started",
    notes: str = ""
):

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO training
            (
                category,
                title,
                goals,
                progress,
                notes,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                title,
                goals,
                progress,
                notes,
                interaction.user.id,
                now_iso()
            )
        )

        conn.commit()
        conn.close()

    await interaction.response.send_message(
        f"🏋️ Training added: **{title}**"
    )


@bot.tree.command(
    name="training",
    description="Show training plans"
)
@team_only()
async def training(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM training
            ORDER BY id DESC
            LIMIT 15
            """
        ).fetchall()

        conn.close()

    lines = ["🏋️ **TEAM XYZ TRAINING**", ""]

    for r in rows:

        lines.append(
            f"**{r['title']}**\n"
            f"Category: {r['category']}\n"
            f"Goals: {r['goals']}\n"
            f"Progress: {r['progress']}\n"
            f"Notes: {r['notes']}\n"
        )

    if len(lines) == 2:
        lines.append("No training plans yet.")

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# ACHIEVEMENTS
# ============================================================

@bot.tree.command(
    name="achievement_add",
    description="Add player achievement"
)
@team_only()
async def achievement_add(
    interaction,
    player: discord.Member,
    title: str,
    description: str
):

    if player.id not in TEAM:
        await interaction.response.send_message(
            "❌ TEAM XYZ member only."
        )
        return

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO achievements
            (
                player_id,
                title,
                description,
                achieved_at,
                created_by
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                player.id,
                title,
                description,
                now_iso(),
                interaction.user.id
            )
        )

        conn.commit()
        conn.close()

    await interaction.response.send_message(
        f"🏅 Achievement added for **{player.display_name}**"
    )


@bot.tree.command(
    name="achievements",
    description="Show achievements"
)
@team_only()
async def achievements(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM achievements
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        conn.close()

    lines = ["🏅 **TEAM XYZ ACHIEVEMENTS**", ""]

    for r in rows:

        info = member_info(r["player_id"])

        lines.append(
            f"🏆 **{info['name']} — {r['title']}**\n"
            f"{r['description']}\n"
        )

    if len(lines) == 2:
        lines.append("No achievements yet.")

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# ANNOUNCEMENT
# ============================================================

@bot.tree.command(
    name="announce",
    description="Send team announcement"
)
@team_only()
async def announce(
    interaction,
    content: str,
    channel: discord.TextChannel = None
):

    target = channel or interaction.channel

    await target.send(
        f"📢 **TEAM XYZ ANNOUNCEMENT**\n\n{content}"
    )

    log_activity(
        interaction.user.id,
        "Sent announcement"
    )

    await interaction.response.send_message(
        "✅ Announcement sent.",
        ephemeral=True
    )


# ============================================================
# FILES
# ============================================================

@bot.tree.command(
    name="files",
    description="Show stored TEAM XYZ files"
)
@team_only()
async def files(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM files
            ORDER BY id DESC
            LIMIT 25
            """
        ).fetchall()

        conn.close()

    if not rows:
        await interaction.response.send_message(
            "📁 No team files stored yet."
        )
        return

    lines = ["📁 **TEAM XYZ FILE STORAGE**", ""]

    for r in rows:

        info = member_info(r["uploader_id"])

        lines.append(
            f"📄 **{r['filename']}**\n"
            f"Uploader: {info['name']}\n"
            f"Size: {r['size']} bytes\n"
            f"[Open storage message]({r['storage_url']})\n"
        )

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# DASHBOARD
# ============================================================

@bot.tree.command(
    name="dashboard",
    description="TEAM XYZ dashboard"
)
@team_only()
async def dashboard(interaction):

    with DB_LOCK:
        conn = db()

        scrims = conn.execute(
            "SELECT COUNT(*) c FROM scrims"
        ).fetchone()["c"]

        tournaments = conn.execute(
            "SELECT COUNT(*) c FROM tournaments"
        ).fetchone()["c"]

        matches = conn.execute(
            "SELECT COUNT(*) c FROM matches"
        ).fetchone()["c"]

        stats_count = conn.execute(
            "SELECT COUNT(*) c FROM player_stats"
        ).fetchone()["c"]

        strategies_count = conn.execute(
            "SELECT COUNT(*) c FROM strategies"
        ).fetchone()["c"]

        training_count = conn.execute(
            "SELECT COUNT(*) c FROM training"
        ).fetchone()["c"]

        files_count = conn.execute(
            "SELECT COUNT(*) c FROM files"
        ).fetchone()["c"]

        conn.close()

    await interaction.response.send_message(
        "📈 **TEAM XYZ DASHBOARD**\n\n"
        f"👥 Members: **4**\n"
        f"⚔️ Scrims: **{scrims}**\n"
        f"🏆 Tournaments: **{tournaments}**\n"
        f"🎯 Matches: **{matches}**\n"
        f"📊 Stat records: **{stats_count}**\n"
        f"🧠 Strategies: **{strategies_count}**\n"
        f"🏋️ Training plans: **{training_count}**\n"
        f"📁 Files: **{files_count}**"
    )


# ============================================================
# ACTIVITY
# ============================================================

@bot.tree.command(
    name="activity",
    description="Show recent team activity"
)
@team_only()
async def activity(interaction):

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM activity
            ORDER BY id DESC
            LIMIT 30
            """
        ).fetchall()

        conn.close()

    lines = ["🕒 **TEAM XYZ ACTIVITY**", ""]

    for r in rows:

        info = member_info(r["user_id"])

        lines.append(
            f"**{info['name']}** — {r['action']}\n"
            f"{r['created_at']}\n"
        )

    if len(lines) == 2:
        lines.append("No activity yet.")

    await send_long(
        interaction,
        "\n".join(lines)
    )


# ============================================================
# GEMINI
# ============================================================

def build_ai_context(user_id):

    info = member_info(user_id)

    with DB_LOCK:
        conn = db()

        scrims = conn.execute(
            """
            SELECT opponent, scheduled_at, map, result, notes
            FROM scrims
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()

        tournaments = conn.execute(
            """
            SELECT name, scheduled_at, placement, points, result
            FROM tournaments
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()

        matches = conn.execute(
            """
            SELECT title, opponent, map,
                   placement, team_kills, result
            FROM matches
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()

        stats = conn.execute(
            """
            SELECT player_id, kills, damage,
                   placement, booyah, points
            FROM player_stats
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

        strategies = conn.execute(
            """
            SELECT category, title, content
            FROM strategies
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()

        training = conn.execute(
            """
            SELECT category, title, goals,
                   progress, notes
            FROM training
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()

        chat = conn.execute(
            """
            SELECT author_id, content, created_at
            FROM chat_context
            ORDER BY id DESC
            LIMIT 12
            """
        ).fetchall()

        conn.close()

    roster = []

    for uid, member in TEAM.items():

        roster.append({
            "name": member["name"],
            "role": member["role"]
        })

    def clean_rows(rows):
        return [
            dict(r)
            for r in rows
        ]

    context = {
        "team": "TEAM XYZ",
        "current_member": info["name"],
        "current_role": info["role"],
        "roster": roster,
        "recent_scrims": clean_rows(scrims),
        "recent_tournaments": clean_rows(tournaments),
        "recent_matches": clean_rows(matches),
        "recent_stats": clean_rows(stats),
        "recent_strategies": clean_rows(strategies),
        "recent_training": clean_rows(training),
        "recent_chat": clean_rows(chat),
    }

    raw = json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":")
    )

    # Hard context limit.
    return raw[:14000]


async def gemini_request(prompt, context):

    try:
        from google import genai

    except ImportError:
        raise RuntimeError(
            "google-genai package is not installed."
        )

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    system_prompt = """
You are the official AI esports assistant for TEAM XYZ,
a Free Fire esports team.

TEAM XYZ has four equal members:

Nirav - IGL + Primary Rusher
KRUTIK - Sniper
Dakshit - Supporter
Atul - Secondary Rusher

Give practical Free Fire esports advice.

Use the supplied team context when relevant.

Never invent:
- match results
- statistics
- tournament results
- player information
- team discussions

If data is missing, say that it is missing.

Adapt advice to the member's role.

Be concise but useful.

The user is asking through the TEAM XYZ Discord bot.
"""

    full_prompt = (
        system_prompt
        + "\n\nTEAM CONTEXT:\n"
        + context
        + "\n\nUSER QUESTION:\n"
        + prompt[:3500]
    )

    last_error = None

    for attempt in range(AI_RETRIES):

        try:

            async with ai_semaphore:

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=GEMINI_MODEL,
                    contents=full_prompt
                )

            text = getattr(
                response,
                "text",
                None
            )

            if not text:
                return (
                    "⚠️ Gemini returned an empty response."
                )

            return text.strip()

        except Exception as e:

            last_error = e

            error_text = str(e).lower()

            is_429 = (
                "429" in error_text
                or "resource_exhausted" in error_text
                or "quota" in error_text
                or "rate limit" in error_text
            )

            is_503 = (
                "503" in error_text
                or "unavailable" in error_text
                or "high demand" in error_text
                or "service unavailable" in error_text
            )

            # IMPORTANT:
            # Quota exhaustion should NOT be retried endlessly.
            if is_429 and (
                "quota" in error_text
                or "exceeded your current quota" in error_text
                or "free_tier" in error_text
            ):
                return (
                    "⚠️ **Gemini quota exhausted.**\n\n"
                    "Your Gemini API project has reached "
                    "its current request quota. "
                    "Please check Google AI Studio/API quota "
                    "and try again after the quota resets."
                )

            if not (is_429 or is_503):
                break

            if attempt >= AI_RETRIES - 1:
                break

            delay = (2 ** attempt) + random.uniform(
                0.5,
                1.5
            )

            await asyncio.sleep(delay)

    print(
        "Gemini request failed:",
        repr(last_error)
    )

    if last_error:

        error_text = str(last_error).lower()

        if "429" in error_text:
            return (
                "⚠️ Gemini request limit reached. "
                "Please try again later."
            )

        if "503" in error_text:
            return (
                "⚠️ Gemini is temporarily unavailable "
                "because the service is busy. "
                "Please try /ai again shortly."
            )

    return (
        "⚠️ AI request failed temporarily. "
        "The Discord bot itself is still running."
    )


# ============================================================
# /AI
# ============================================================

@bot.tree.command(
    name="ai",
    description="Ask the TEAM XYZ AI esports assistant"
)
@app_commands.describe(
    prompt="Your question"
)
@team_only()
async def ai(
    interaction: discord.Interaction,
    prompt: str
):

    if not GEMINI_API_KEY:

        await interaction.response.send_message(
            "❌ `GEMINI_API_KEY` is not configured "
            "in Render Environment Variables.",
            ephemeral=True
        )

        return

    prompt = (prompt or "").strip()

    if not prompt:

        await interaction.response.send_message(
            "❌ Please enter your question.",
            ephemeral=True
        )

        return

    # Defer immediately so Discord does not timeout.
    await interaction.response.defer()

    member = member_info(
        interaction.user.id
    )

    log_activity(
        interaction.user.id,
        f"Used /ai: {prompt[:150]}"
    )

    try:

        context = await asyncio.to_thread(
            build_ai_context,
            interaction.user.id
        )

        answer = await gemini_request(
            prompt,
            context
        )

        header = (
            f"🤖 **TEAM XYZ AI — "
            f"{member['name']} ({member['role']})**\n\n"
        )

        await send_long(
            interaction,
            header + answer
        )

    except Exception as e:

        print(
            "AI command error:",
            repr(e)
        )

        try:

            await interaction.followup.send(
                "⚠️ AI error occurred, "
                "but the TEAM XYZ bot is still running."
            )

        except Exception:
            pass


# ============================================================
# REMINDERS
# ============================================================

@tasks.loop(minutes=1)
async def reminder_loop():

    try:

        now = datetime.now(timezone.utc)

        with DB_LOCK:
            conn = db()

            rows = conn.execute(
                """
                SELECT *
                FROM scrims
                WHERE result IS NULL
                OR result = ''
                """
            ).fetchall()

            conn.close()

        for r in rows:

            try:

                # Expected format:
                # 2026-09-20 18:30
                dt = datetime.strptime(
                    r["scheduled_at"],
                    "%Y-%m-%d %H:%M"
                ).replace(
                    tzinfo=timezone.utc
                )

                seconds = (
                    dt - now
                ).total_seconds()

                if 0 < seconds <= 600:

                    for user_id in TEAM:

                        try:

                            user = bot.get_user(
                                user_id
                            )

                            if user:

                                await user.send(
                                    f"⏰ **TEAM XYZ SCRIM REMINDER**\n"
                                    f"Opponent: **{r['opponent']}**\n"
                                    f"Time: {r['scheduled_at']} UTC\n"
                                    f"Map: {r['map']}\n"
                                    f"Room: `{r['room_id'] or 'N/A'}`"
                                )

                        except Exception:
                            pass

            except Exception:
                continue

    except Exception as e:

        print(
            "Reminder loop error:",
            repr(e)
        )


@reminder_loop.before_loop
async def before_reminder():

    await bot.wait_until_ready()


# ============================================================
# SCHEDULED ANNOUNCEMENTS
# ============================================================

@tasks.loop(minutes=1)
async def announcement_loop():

    try:

        with DB_LOCK:
            conn = db()

            rows = conn.execute(
                """
                SELECT *
                FROM announcements
                WHERE sent=0
                ORDER BY id ASC
                """
            ).fetchall()

            conn.close()

        now = datetime.now(timezone.utc)

        for r in rows:

            try:

                dt = datetime.fromisoformat(
                    r["scheduled_at"]
                )

                if dt.tzinfo is None:
                    dt = dt.replace(
                        tzinfo=timezone.utc
                    )

                if dt <= now:

                    # Send to all members by DM.
                    for user_id in TEAM:

                        try:

                            user = bot.get_user(
                                user_id
                            )

                            if user:

                                await user.send(
                                    "📢 **TEAM XYZ ANNOUNCEMENT**\n\n"
                                    + r["content"]
                                )

                        except Exception:
                            pass

                    with DB_LOCK:
                        conn = db()

                        conn.execute(
                            """
                            UPDATE announcements
                            SET sent=1
                            WHERE id=?
                            """,
                            (r["id"],)
                        )

                        conn.commit()
                        conn.close()

            except Exception:
                continue

    except Exception as e:

        print(
            "Announcement loop error:",
            repr(e)
        )


@announcement_loop.before_loop
async def before_announcement():

    await bot.wait_until_ready()


# ============================================================
# APP COMMAND ERROR HANDLER
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction,
    error
):

    print(
        "APP COMMAND ERROR:",
        repr(error)
    )

    try:

        if isinstance(
            error,
            app_commands.CheckFailure
        ):

            message = (
                "❌ This bot is available only "
                "to TEAM XYZ members."
            )

        else:

            message = (
                "⚠️ Command error occurred. "
                "Please try again."
            )

        if interaction.response.is_done():

            await interaction.followup.send(
                message,
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    except Exception:
        pass


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        body = (
            "TEAM XYZ Discord Bot is running."
        ).encode()

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def log_message(
        self,
        format,
        *args
    ):
        return


def start_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"Health server listening on port {PORT}"
    )

    server.serve_forever()


# ============================================================
# STARTUP
# ============================================================

def main():

    if not DISCORD_TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN is missing."
        )

    init_db()

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
