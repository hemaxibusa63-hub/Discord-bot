import os
import io
import json
import sqlite3
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands, tasks

# Gemini
try:
    from google import genai
except ImportError:
    genai = None


# ============================================================
# TEAM XYZ CONFIG
# ============================================================

BRAND = "TEAM XYZ"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# User-selected Gemini model
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "team_xyz.db"
).strip()

STORAGE_CHANNEL_ID = os.getenv(
    "STORAGE_CHANNEL_ID",
    ""
).strip()

PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# TEAM MEMBERS
# ALL MEMBERS HAVE IDENTICAL ACCESS
# ============================================================

TEAM_MEMBERS = {
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


# ============================================================
# DATABASE
# ============================================================

db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        conn = db()

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            discord_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            bio TEXT DEFAULT '',
            goals TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scrims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opponent TEXT NOT NULL,
            match_time TEXT NOT NULL,
            room_id TEXT DEFAULT '',
            password TEXT DEFAULT '',
            map TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            result TEXT DEFAULT 'Scheduled',
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            organizer TEXT DEFAULT '',
            start_time TEXT NOT NULL,
            rounds TEXT DEFAULT '',
            room_info TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            result TEXT DEFAULT 'Scheduled',
            placement TEXT DEFAULT '',
            points REAL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_type TEXT NOT NULL,
            title TEXT NOT NULL,
            match_time TEXT NOT NULL,
            opponent TEXT DEFAULT '',
            map TEXT DEFAULT '',
            result TEXT DEFAULT '',
            placement INTEGER DEFAULT 0,
            points REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            match_id INTEGER DEFAULT 0,
            kills INTEGER DEFAULT 0,
            damage REAL DEFAULT 0,
            placement INTEGER DEFAULT 0,
            booyah INTEGER DEFAULT 0,
            survival_seconds INTEGER DEFAULT 0,
            points REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS training (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER DEFAULT 0,
            training_type TEXT NOT NULL,
            goal TEXT DEFAULT '',
            schedule TEXT DEFAULT '',
            progress TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            send_at TEXT DEFAULT '',
            channel_id INTEGER DEFAULT 0,
            message_id INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Draft',
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_type TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            uploader_id INTEGER NOT NULL,
            storage_channel_id INTEGER DEFAULT 0,
            storage_message_id INTEGER DEFAULT 0,
            url TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            date TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        """)

        # Add fixed team members
        now = utc_now()

        for uid, info in TEAM_MEMBERS.items():
            conn.execute("""
                INSERT OR IGNORE INTO members
                (discord_id, name, role, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                uid,
                info["name"],
                info["role"],
                now
            ))

        conn.commit()
        conn.close()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def add_activity(user_id, action, details=""):
    with db_lock:
        conn = db()
        conn.execute("""
            INSERT INTO activity
            (user_id, action, details, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            user_id,
            action,
            details,
            utc_now()
        ))
        conn.commit()
        conn.close()


def execute(sql, params=()):
    with db_lock:
        conn = db()
        cur = conn.execute(sql, params)
        conn.commit()
        last_id = cur.lastrowid
        conn.close()
        return last_id


def fetchone(sql, params=()):
    with db_lock:
        conn = db()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return row


def fetchall(sql, params=()):
    with db_lock:
        conn = db()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# ACCESS
# ============================================================

def is_team_member(user_id):
    return user_id in TEAM_MEMBERS


async def require_member(interaction: discord.Interaction):
    if not is_team_member(interaction.user.id):
        await interaction.response.send_message(
            "❌ Ye bot sirf TEAM XYZ members ke liye hai.",
            ephemeral=True
        )
        return False

    return True


# ============================================================
# EMBEDS
# ============================================================

def embed(title, description="", color=discord.Color.blurple()):
    e = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    e.set_footer(text=BRAND)
    return e


# ============================================================
# GEMINI
# ============================================================

gemini_client = None


def init_gemini():
    global gemini_client

    if not GEMINI_API_KEY:
        print("[GEMINI] GEMINI_API_KEY not configured.")
        return

    if genai is None:
        print("[GEMINI] google-genai package missing.")
        return

    try:
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )
        print(
            f"[GEMINI] Ready | Model: {GEMINI_MODEL}"
        )
    except Exception as e:
        print("[GEMINI] Init error:", repr(e))


def build_team_context(user_id):
    member = TEAM_MEMBERS.get(user_id)

    context = f"""
You are the AI esports assistant for {BRAND}.

GAME:
Free Fire esports.

CURRENT MEMBER:
Name: {member['name'] if member else 'Unknown'}
Role: {member['role'] if member else 'Unknown'}

TEAM ROSTER:
"""

    for uid, info in TEAM_MEMBERS.items():
        context += (
            f"- {info['name']} | "
            f"{info['role']} | Discord ID {uid}\n"
        )

    scrims = fetchall("""
        SELECT opponent, match_time, map, result, notes
        FROM scrims
        ORDER BY id DESC
        LIMIT 10
    """)

    context += "\nRECENT SCRIMS:\n"

    for s in scrims:
        context += (
            f"- Opponent: {s['opponent']} | "
            f"Time: {s['match_time']} | "
            f"Map: {s['map']} | "
            f"Result: {s['result']} | "
            f"Notes: {s['notes']}\n"
        )

    tournaments = fetchall("""
        SELECT name, start_time, result, placement, points
        FROM tournaments
        ORDER BY id DESC
        LIMIT 10
    """)

    context += "\nTOURNAMENTS:\n"

    for t in tournaments:
        context += (
            f"- {t['name']} | "
            f"{t['start_time']} | "
            f"Result: {t['result']} | "
            f"Placement: {t['placement']} | "
            f"Points: {t['points']}\n"
        )

    strategies = fetchall("""
        SELECT category, title, content
        FROM strategies
        ORDER BY id DESC
        LIMIT 10
    """)

    context += "\nSTRATEGIES:\n"

    for s in strategies:
        context += (
            f"- [{s['category']}] "
            f"{s['title']}: {s['content']}\n"
        )

    training = fetchall("""
        SELECT player_id, training_type, goal,
               progress, notes
        FROM training
        ORDER BY id DESC
        LIMIT 15
    """)

    context += "\nTRAINING:\n"

    for tr in training:
        player = TEAM_MEMBERS.get(
            tr["player_id"],
            {}
        ).get("name", "Team")

        context += (
            f"- {player} | "
            f"{tr['training_type']} | "
            f"Goal: {tr['goal']} | "
            f"Progress: {tr['progress']} | "
            f"Notes: {tr['notes']}\n"
        )

    stats = fetchall("""
        SELECT player_id,
               SUM(kills) AS kills,
               SUM(damage) AS damage,
               SUM(points) AS points,
               SUM(booyah) AS booyah,
               COUNT(*) AS games
        FROM player_stats
        GROUP BY player_id
    """)

    context += "\nPLAYER STATISTICS:\n"

    for st in stats:
        name = TEAM_MEMBERS.get(
            st["player_id"],
            {}
        ).get("name", str(st["player_id"]))

        context += (
            f"- {name}: "
            f"Games={st['games']}, "
            f"Kills={st['kills']}, "
            f"Damage={st['damage']}, "
            f"Points={st['points']}, "
            f"Booyah={st['booyah']}\n"
        )

    # Relevant team chat context
    chats = fetchall("""
        SELECT user_id, message, created_at
        FROM chat_context
        ORDER BY id DESC
        LIMIT 30
    """)

    context += "\nRECENT TEAM DISCUSSION CONTEXT:\n"

    for c in chats:
        name = TEAM_MEMBERS.get(
            c["user_id"],
            {}
        ).get("name", str(c["user_id"]))

        context += (
            f"- {name}: {c['message']}\n"
        )

    return context


async def ask_gemini(user_id, question):
    if gemini_client is None:
        return (
            "❌ Gemini configured nahi hai.\n\n"
            "Render Environment Variables mein "
            "`GEMINI_API_KEY` set karo."
        )

    context = build_team_context(user_id)

    prompt = f"""
{context}

AI RULES:
- Act as a professional Free Fire esports coach.
- Understand the player's TEAM XYZ role.
- Give practical tactical advice.
- If the user is Nirav, IGL/rusher context is important.
- If the user is KRUTIK, sniper context is important.
- If the user is Dakshit, support context is important.
- If the user is Atul, secondary-rusher context is important.
- Use team data when relevant.
- Do not invent team statistics.
- If information is missing, clearly say that it is missing.
- Be concise but useful.
- Answer in Hinglish unless the user asks for another language.

USER QUESTION:
{question}
"""

    try:
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt
        )

        text = getattr(response, "text", None)

        if not text:
            return "❌ Gemini ne empty response diya."

        return text[:3900]

    except Exception as e:
        print("[GEMINI ERROR]", repr(e))

        return (
            "❌ Gemini request failed.\n\n"
            f"Model: `{GEMINI_MODEL}`\n"
            "API key/model availability check karo."
        )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():
    print("=" * 60)
    print(f"{BRAND} BOT ONLINE")
    print(f"Bot: {bot.user}")
    print(f"Guilds: {len(bot.guilds)}")
    print(f"Gemini Model: {GEMINI_MODEL}")
    print("=" * 60)

    try:
        synced = await bot.tree.sync()
        print(
            f"[SLASH] Synced {len(synced)} commands."
        )
    except Exception as e:
        print("[SLASH ERROR]", repr(e))

    if not reminder_loop.is_running():
        reminder_loop.start()

    if not announcement_loop.is_running():
        announcement_loop.start()


# ============================================================
# CHAT CONTEXT
# ============================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if is_team_member(message.author.id):
        content = message.content.strip()

        if content:
            # Only store useful team discussion.
            # AI is NOT called automatically.
            if len(content) <= 2000:
                execute("""
                    INSERT INTO chat_context
                    (user_id, channel_id, message, created_at)
                    VALUES (?, ?, ?, ?)
                """, (
                    message.author.id,
                    message.channel.id,
                    content,
                    utc_now()
                ))

    await bot.process_commands(message)


# ============================================================
# /ai
# ============================================================

@bot.tree.command(
    name="ai",
    description="Ask TEAM XYZ Gemini esports AI"
)
@app_commands.describe(
    question="Apna esports question"
)
async def ai_command(
    interaction: discord.Interaction,
    question: str
):
    if not await require_member(interaction):
        return

    await interaction.response.defer()

    answer = await ask_gemini(
        interaction.user.id,
        question
    )

    add_activity(
        interaction.user.id,
        "AI",
        question[:500]
    )

    await interaction.followup.send(
        embed(
            "🤖 TEAM XYZ AI",
            answer,
            discord.Color.green()
        )
    )


# ============================================================
# /team
# ============================================================

team_group = app_commands.Group(
    name="team",
    description="TEAM XYZ roster commands"
)


@team_group.command(
    name="roster",
    description="Show TEAM XYZ roster"
)
async def team_roster(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    text = ""

    for uid, info in TEAM_MEMBERS.items():
        text += (
            f"**{info['name']}**\n"
            f"Role: `{info['role']}`\n"
            f"Discord ID: `{uid}`\n\n"
        )

    await interaction.response.send_message(
        embed(
            "👥 TEAM XYZ ROSTER",
            text
        )
    )


@team_group.command(
    name="profile",
    description="Show player profile"
)
@app_commands.describe(
    player="Player name"
)
async def team_profile(
    interaction: discord.Interaction,
    player: str
):
    if not await require_member(interaction):
        return

    found = None

    for uid, info in TEAM_MEMBERS.items():
        if info["name"].lower() == player.lower():
            found = (uid, info)
            break

    if not found:
        await interaction.response.send_message(
            "❌ Player nahi mila.",
            ephemeral=True
        )
        return

    uid, info = found

    row = fetchone("""
        SELECT bio, goals
        FROM members
        WHERE discord_id=?
    """, (uid,))

    bio = row["bio"] if row else ""
    goals = row["goals"] if row else ""

    await interaction.response.send_message(
        embed(
            f"👤 {info['name']}",
            f"**Role:** {info['role']}\n\n"
            f"**Bio:** {bio or 'Not added'}\n\n"
            f"**Goals:** {goals or 'Not added'}"
        )
    )


@team_group.command(
    name="update",
    description="Update your own team profile"
)
@app_commands.describe(
    bio="Profile bio",
    goals="Current goals"
)
async def team_update(
    interaction: discord.Interaction,
    bio: str = "",
    goals: str = ""
):
    if not await require_member(interaction):
        return

    execute("""
        UPDATE members
        SET bio=?, goals=?
        WHERE discord_id=?
    """, (
        bio[:1000],
        goals[:1000],
        interaction.user.id
    ))

    add_activity(
        interaction.user.id,
        "Profile Updated"
    )

    await interaction.response.send_message(
        "✅ Profile update ho gaya."
    )


# ============================================================
# SCRIMS
# ============================================================

scrim_group = app_commands.Group(
    name="scrim",
    description="TEAM XYZ scrim management"
)


@scrim_group.command(
    name="create",
    description="Create a scrim"
)
@app_commands.describe(
    opponent="Opponent/team",
    match_time="Date/time text",
    room_id="Room ID",
    password="Room password",
    map_name="Map",
    notes="Notes"
)
async def scrim_create(
    interaction: discord.Interaction,
    opponent: str,
    match_time: str,
    room_id: str = "",
    password: str = "",
    map_name: str = "",
    notes: str = ""
):
    if not await require_member(interaction):
        return

    sid = execute("""
        INSERT INTO scrims
        (opponent, match_time, room_id, password,
         map, notes, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        opponent,
        match_time,
        room_id,
        password,
        map_name,
        notes,
        interaction.user.id,
        utc_now()
    ))

    add_activity(
        interaction.user.id,
        "Scrim Created",
        f"#{sid} {opponent}"
    )

    await interaction.response.send_message(
        embed(
            "⚔️ SCRIM CREATED",
            f"**ID:** `{sid}`\n"
            f"**Opponent:** {opponent}\n"
            f"**Time:** {match_time}\n"
            f"**Room:** `{room_id or 'Not set'}`\n"
            f"**Password:** `{password or 'Not set'}`\n"
            f"**Map:** {map_name or 'Not set'}\n"
            f"**Notes:** {notes or 'None'}",
            discord.Color.orange()
        )
    )


@scrim_group.command(
    name="list",
    description="Show upcoming scrims"
)
async def scrim_list(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM scrims
        ORDER BY id DESC
        LIMIT 15
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 Koi scrim nahi hai."
        )
        return

    text = ""

    for s in rows:
        text += (
            f"**#{s['id']} — {s['opponent']}**\n"
            f"🕒 {s['match_time']}\n"
            f"🗺️ {s['map'] or 'N/A'}\n"
            f"📊 {s['result']}\n\n"
        )

    await interaction.response.send_message(
        embed("⚔️ SCRIM LIST", text)
    )


@scrim_group.command(
    name="result",
    description="Update scrim result"
)
@app_commands.describe(
    scrim_id="Scrim ID",
    result="Result e.g. WIN / LOSS / DRAW",
    notes="Result notes"
)
async def scrim_result(
    interaction: discord.Interaction,
    scrim_id: int,
    result: str,
    notes: str = ""
):
    if not await require_member(interaction):
        return

    row = fetchone(
        "SELECT * FROM scrims WHERE id=?",
        (scrim_id,)
    )

    if not row:
        await interaction.response.send_message(
            "❌ Scrim nahi mila.",
            ephemeral=True
        )
        return

    execute("""
        UPDATE scrims
        SET result=?, notes=?
        WHERE id=?
    """, (
        result[:100],
        notes[:1000],
        scrim_id
    ))

    add_activity(
        interaction.user.id,
        "Scrim Result Updated",
        str(scrim_id)
    )

    await interaction.response.send_message(
        f"✅ Scrim `#{scrim_id}` result updated: **{result}**"
    )


# ============================================================
# TOURNAMENTS
# ============================================================

tournament_group = app_commands.Group(
    name="tournament",
    description="Tournament management"
)


@tournament_group.command(
    name="create",
    description="Create tournament"
)
@app_commands.describe(
    name="Tournament name",
    organizer="Organizer",
    start_time="Start time",
    rounds="Rounds",
    room_info="Room information",
    notes="Notes"
)
async def tournament_create(
    interaction: discord.Interaction,
    name: str,
    start_time: str,
    organizer: str = "",
    rounds: str = "",
    room_info: str = "",
    notes: str = ""
):
    if not await require_member(interaction):
        return

    tid = execute("""
        INSERT INTO tournaments
        (name, organizer, start_time, rounds,
         room_info, notes, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        organizer,
        start_time,
        rounds,
        room_info,
        notes,
        interaction.user.id,
        utc_now()
    ))

    add_activity(
        interaction.user.id,
        "Tournament Created",
        name
    )

    await interaction.response.send_message(
        embed(
            "🏆 TOURNAMENT CREATED",
            f"**ID:** `{tid}`\n"
            f"**Name:** {name}\n"
            f"**Organizer:** {organizer or 'N/A'}\n"
            f"**Start:** {start_time}\n"
            f"**Rounds:** {rounds or 'N/A'}\n"
            f"**Room:** {room_info or 'N/A'}\n"
            f"**Notes:** {notes or 'None'}"
        )
    )


@tournament_group.command(
    name="list",
    description="Show tournaments"
)
async def tournament_list(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM tournaments
        ORDER BY id DESC
        LIMIT 15
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 No tournaments."
        )
        return

    text = ""

    for t in rows:
        text += (
            f"**#{t['id']} — {t['name']}**\n"
            f"🕒 {t['start_time']}\n"
            f"📊 {t['result']}\n"
            f"🏅 Placement: {t['placement'] or 'N/A'}\n"
            f"⭐ Points: {t['points']}\n\n"
        )

    await interaction.response.send_message(
        embed("🏆 TOURNAMENTS", text)
    )


@tournament_group.command(
    name="result",
    description="Update tournament result"
)
@app_commands.describe(
    tournament_id="Tournament ID",
    result="Result",
    placement="Final placement",
    points="Points"
)
async def tournament_result(
    interaction: discord.Interaction,
    tournament_id: int,
    result: str,
    placement: str = "",
    points: float = 0
):
    if not await require_member(interaction):
        return

    row = fetchone(
        "SELECT * FROM tournaments WHERE id=?",
        (tournament_id,)
    )

    if not row:
        await interaction.response.send_message(
            "❌ Tournament nahi mila.",
            ephemeral=True
        )
        return

    execute("""
        UPDATE tournaments
        SET result=?, placement=?, points=?
        WHERE id=?
    """, (
        result,
        placement,
        points,
        tournament_id
    ))

    add_activity(
        interaction.user.id,
        "Tournament Result Updated",
        str(tournament_id)
    )

    await interaction.response.send_message(
        embed(
            "🏆 TOURNAMENT RESULT UPDATED",
            f"**Tournament:** {row['name']}\n"
            f"**Result:** {result}\n"
            f"**Placement:** {placement or 'N/A'}\n"
            f"**Points:** {points}"
        )
    )


# ============================================================
# MATCHES
# ============================================================

match_group = app_commands.Group(
    name="match",
    description="Match management"
)


@match_group.command(
    name="create",
    description="Create a match"
)
@app_commands.describe(
    title="Match title",
    match_type="Scrim/Tournament/Training",
    match_time="Date/time",
    opponent="Opponent",
    map_name="Map",
    notes="Notes"
)
async def match_create(
    interaction: discord.Interaction,
    title: str,
    match_type: str,
    match_time: str,
    opponent: str = "",
    map_name: str = "",
    notes: str = ""
):
    if not await require_member(interaction):
        return

    mid = execute("""
        INSERT INTO matches
        (match_type, title, match_time, opponent,
         map, notes, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        match_type,
        title,
        match_time,
        opponent,
        map_name,
        notes,
        interaction.user.id,
        utc_now()
    ))

    await interaction.response.send_message(
        f"✅ Match created: `#{mid}` — **{title}**"
    )


@match_group.command(
    name="result",
    description="Add match result"
)
@app_commands.describe(
    match_id="Match ID",
    result="WIN/LOSS/DRAW/etc.",
    placement="Placement",
    points="Team points"
)
async def match_result(
    interaction: discord.Interaction,
    match_id: int,
    result: str,
    placement: int = 0,
    points: float = 0
):
    if not await require_member(interaction):
        return

    row = fetchone(
        "SELECT * FROM matches WHERE id=?",
        (match_id,)
    )

    if not row:
        await interaction.response.send_message(
            "❌ Match nahi mila.",
            ephemeral=True
        )
        return

    execute("""
        UPDATE matches
        SET result=?, placement=?, points=?
        WHERE id=?
    """, (
        result,
        placement,
        points,
        match_id
    ))

    await interaction.response.send_message(
        embed(
            "📊 MATCH RESULT",
            f"**{row['title']}**\n"
            f"Result: **{result}**\n"
            f"Placement: **{placement or 'N/A'}**\n"
            f"Points: **{points}**"
        )
    )


# ============================================================
# PLAYER STATS
# ============================================================

stats_group = app_commands.Group(
    name="stats",
    description="Player statistics"
)


@stats_group.command(
    name="add",
    description="Add player match stats"
)
@app_commands.describe(
    player="Player name",
    kills="Kills",
    damage="Damage",
    placement="Placement",
    booyah="1 if Booyah else 0",
    survival="Survival seconds",
    points="Points",
    notes="Notes"
)
async def stats_add(
    interaction: discord.Interaction,
    player: str,
    kills: int = 0,
    damage: float = 0,
    placement: int = 0,
    booyah: int = 0,
    survival: int = 0,
    points: float = 0,
    notes: str = ""
):
    if not await require_member(interaction):
        return

    player_id = None

    for uid, info in TEAM_MEMBERS.items():
        if info["name"].lower() == player.lower():
            player_id = uid
            break

    if player_id is None:
        await interaction.response.send_message(
            "❌ Player nahi mila.",
            ephemeral=True
        )
        return

    execute("""
        INSERT INTO player_stats
        (player_id, kills, damage, placement,
         booyah, survival_seconds, points,
         notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        player_id,
        kills,
        damage,
        placement,
        1 if booyah else 0,
        survival,
        points,
        notes,
        utc_now()
    ))

    await interaction.response.send_message(
        f"✅ Stats added for **{TEAM_MEMBERS[player_id]['name']}**"
    )


@stats_group.command(
    name="player",
    description="Show player statistics"
)
@app_commands.describe(
    player="Player name"
)
async def stats_player(
    interaction: discord.Interaction,
    player: str
):
    if not await require_member(interaction):
        return

    player_id = None

    for uid, info in TEAM_MEMBERS.items():
        if info["name"].lower() == player.lower():
            player_id = uid
            break

    if player_id is None:
        await interaction.response.send_message(
            "❌ Player nahi mila.",
            ephemeral=True
        )
        return

    row = fetchone("""
        SELECT
            COUNT(*) AS games,
            COALESCE(SUM(kills), 0) AS kills,
            COALESCE(SUM(damage), 0) AS damage,
            COALESCE(SUM(booyah), 0) AS booyah,
            COALESCE(SUM(points), 0) AS points,
            COALESCE(AVG(kills), 0) AS avg_kills,
            COALESCE(AVG(damage), 0) AS avg_damage
        FROM player_stats
        WHERE player_id=?
    """, (player_id,))

    name = TEAM_MEMBERS[player_id]["name"]

    await interaction.response.send_message(
        embed(
            f"📊 {name} STATISTICS",
            f"**Games:** {row['games']}\n"
            f"**Total Kills:** {row['kills']}\n"
            f"**Average Kills:** {row['avg_kills']:.2f}\n"
            f"**Total Damage:** {row['damage']:.0f}\n"
            f"**Average Damage:** {row['avg_damage']:.0f}\n"
            f"**Booyahs:** {row['booyah']}\n"
            f"**Total Points:** {row['points']:.1f}"
        )
    )


@stats_group.command(
    name="team",
    description="Show team statistics"
)
async def stats_team(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    row = fetchone("""
        SELECT
            COUNT(*) AS games,
            COALESCE(SUM(kills), 0) AS kills,
            COALESCE(SUM(damage), 0) AS damage,
            COALESCE(SUM(booyah), 0) AS booyah,
            COALESCE(SUM(points), 0) AS points
        FROM player_stats
    """)

    await interaction.response.send_message(
        embed(
            "📊 TEAM XYZ STATISTICS",
            f"**Recorded Games:** {row['games']}\n"
            f"**Kills:** {row['kills']}\n"
            f"**Damage:** {row['damage']:.0f}\n"
            f"**Booyahs:** {row['booyah']}\n"
            f"**Points:** {row['points']:.1f}"
        )
    )


# ============================================================
# STRATEGY
# ============================================================

strategy_group = app_commands.Group(
    name="strategy",
    description="Team strategy management"
)


@strategy_group.command(
    name="add",
    description="Add strategy"
)
@app_commands.describe(
    category="Drop/Rotation/Rush/Sniper/Support/Endzone",
    title="Strategy title",
    content="Strategy details"
)
async def strategy_add(
    interaction: discord.Interaction,
    category: str,
    title: str,
    content: str
):
    if not await require_member(interaction):
        return

    sid = execute("""
        INSERT INTO strategies
        (category, title, content, created_by, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        category,
        title,
        content,
        interaction.user.id,
        utc_now()
    ))

    await interaction.response.send_message(
        f"🧠 Strategy `#{sid}` saved."
    )


@strategy_group.command(
    name="list",
    description="Show strategies"
)
async def strategy_list(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM strategies
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 No strategies."
        )
        return

    text = ""

    for s in rows:
        text += (
            f"**#{s['id']} [{s['category']}] "
            f"{s['title']}**\n"
            f"{s['content'][:500]}\n\n"
        )

    await interaction.response.send_message(
        embed("🧠 TEAM STRATEGIES", text)
    )


# ============================================================
# TRAINING
# ============================================================

training_group = app_commands.Group(
    name="training",
    description="Training/practice management"
)


@training_group.command(
    name="add",
    description="Add training plan"
)
@app_commands.describe(
    training_type="Aim/Sniper/Rush/1v1/Custom Room/etc.",
    goal="Training goal",
    schedule="Schedule",
    player="Player name",
    notes="Notes"
)
async def training_add(
    interaction: discord.Interaction,
    training_type: str,
    goal: str,
    schedule: str = "",
    player: str = "",
    notes: str = ""
):
    if not await require_member(interaction):
        return

    player_id = 0

    if player:
        for uid, info in TEAM_MEMBERS.items():
            if info["name"].lower() == player.lower():
                player_id = uid
                break

    tid = execute("""
        INSERT INTO training
        (player_id, training_type, goal, schedule,
         notes, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        player_id,
        training_type,
        goal,
        schedule,
        notes,
        interaction.user.id,
        utc_now()
    ))

    await interaction.response.send_message(
        f"🎯 Training plan `#{tid}` created."
    )


@training_group.command(
    name="progress",
    description="Update training progress"
)
@app_commands.describe(
    training_id="Training ID",
    progress="Progress"
)
async def training_progress(
    interaction: discord.Interaction,
    training_id: int,
    progress: str
):
    if not await require_member(interaction):
        return

    row = fetchone(
        "SELECT * FROM training WHERE id=?",
        (training_id,)
    )

    if not row:
        await interaction.response.send_message(
            "❌ Training plan nahi mila.",
            ephemeral=True
        )
        return

    execute("""
        UPDATE training
        SET progress=?
        WHERE id=?
    """, (
        progress[:1000],
        training_id
    ))

    await interaction.response.send_message(
        f"✅ Training `#{training_id}` progress updated."
    )


@training_group.command(
    name="list",
    description="Show training plans"
)
async def training_list(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM training
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 No training plans."
        )
        return

    text = ""

    for t in rows:
        player = TEAM_MEMBERS.get(
            t["player_id"],
            {}
        ).get("name", "Team")

        text += (
            f"**#{t['id']} — {player}**\n"
            f"🎯 {t['training_type']}\n"
            f"Goal: {t['goal']}\n"
            f"Progress: {t['progress'] or 'Not updated'}\n"
            f"Schedule: {t['schedule'] or 'N/A'}\n\n"
        )

    await interaction.response.send_message(
        embed("🎯 TRAINING", text)
    )


# ============================================================
# ACHIEVEMENTS
# ============================================================

achievement_group = app_commands.Group(
    name="achievement",
    description="Team achievements"
)


@achievement_group.command(
    name="add",
    description="Add achievement"
)
@app_commands.describe(
    title="Achievement",
    description="Details",
    player="Player"
)
async def achievement_add(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    player: str = ""
):
    if not await require_member(interaction):
        return

    player_id = 0

    if player:
        for uid, info in TEAM_MEMBERS.items():
            if info["name"].lower() == player.lower():
                player_id = uid
                break

    aid = execute("""
        INSERT INTO achievements
        (player_id, title, description, date,
         created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        player_id,
        title,
        description,
        utc_now(),
        interaction.user.id,
        utc_now()
    ))

    await interaction.response.send_message(
        f"🏅 Achievement `#{aid}` added."
    )


@achievement_group.command(
    name="list",
    description="Show achievements"
)
async def achievement_list(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM achievements
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 No achievements."
        )
        return

    text = ""

    for a in rows:
        player = TEAM_MEMBERS.get(
            a["player_id"],
            {}
        ).get("name", "TEAM XYZ")

        text += (
            f"🏅 **{a['title']}**\n"
            f"Player: {player}\n"
            f"{a['description']}\n\n"
        )

    await interaction.response.send_message(
        embed("🏅 ACHIEVEMENTS", text)
    )


# ============================================================
# FILE STORAGE
# ============================================================

async def get_storage_channel():
    if not STORAGE_CHANNEL_ID:
        return None

    try:
        cid = int(STORAGE_CHANNEL_ID)
    except ValueError:
        return None

    channel = bot.get_channel(cid)

    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception:
            return None

    return channel


@bot.tree.command(
    name="upload",
    description="Upload file/media to TEAM XYZ storage"
)
async def upload_file(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    await interaction.response.send_message(
        "📁 File upload ke liye Discord mein **file attachment ke saath** "
        "command message bhejna required hai.\n\n"
        "Is command ko attachment ke bina use nahi kiya ja sakta.",
        ephemeral=True
    )


@bot.tree.command(
    name="files",
    description="Show stored team files"
)
async def files_command(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM files
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 Storage mein koi file metadata nahi hai."
        )
        return

    text = ""

    for f in rows:
        uploader = TEAM_MEMBERS.get(
            f["uploader_id"],
            {}
        ).get("name", str(f["uploader_id"]))

        text += (
            f"📁 **{f['file_name']}**\n"
            f"Type: {f['file_type'] or 'N/A'}\n"
            f"Uploader: {uploader}\n"
            f"Link: {f['url'] or 'Storage reference saved'}\n\n"
        )

    await interaction.response.send_message(
        embed("📁 TEAM XYZ FILES", text)
    )


# ============================================================
# ANNOUNCEMENT
# ============================================================

announcement_group = app_commands.Group(
    name="announce",
    description="Team announcements"
)


@announcement_group.command(
    name="send",
    description="Send announcement in current channel"
)
@app_commands.describe(
    title="Announcement title",
    content="Announcement content"
)
async def announce_send(
    interaction: discord.Interaction,
    title: str,
    content: str
):
    if not await require_member(interaction):
        return

    await interaction.response.send_message(
        embed(
            f"📢 {title}",
            content,
            discord.Color.gold()
        )
    )

    add_activity(
        interaction.user.id,
        "Announcement",
        title
    )


@announcement_group.command(
    name="schedule",
    description="Schedule an announcement"
)
@app_commands.describe(
    title="Title",
    content="Content",
    send_at="ISO time e.g. 2026-09-20T15:00:00+00:00"
)
async def announce_schedule(
    interaction: discord.Interaction,
    title: str,
    content: str,
    send_at: str
):
    if not await require_member(interaction):
        return

    try:
        datetime.fromisoformat(send_at)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid ISO datetime.",
            ephemeral=True
        )
        return

    aid = execute("""
        INSERT INTO announcements
        (title, content, send_at, channel_id,
         status, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        content,
        send_at,
        interaction.channel.id,
        "Scheduled",
        interaction.user.id,
        utc_now()
    ))

    await interaction.response.send_message(
        f"📢 Announcement `#{aid}` scheduled."
    )


# ============================================================
# DASHBOARD
# ============================================================

@bot.tree.command(
    name="dashboard",
    description="Show TEAM XYZ dashboard"
)
async def dashboard(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    scrims = fetchone(
        "SELECT COUNT(*) AS c FROM scrims"
    )["c"]

    tournaments = fetchone(
        "SELECT COUNT(*) AS c FROM tournaments"
    )["c"]

    matches = fetchone(
        "SELECT COUNT(*) AS c FROM matches"
    )["c"]

    stats = fetchone(
        "SELECT COUNT(*) AS c FROM player_stats"
    )["c"]

    strategies = fetchone(
        "SELECT COUNT(*) AS c FROM strategies"
    )["c"]

    training = fetchone(
        "SELECT COUNT(*) AS c FROM training"
    )["c"]

    files_count = fetchone(
        "SELECT COUNT(*) AS c FROM files"
    )["c"]

    achievements = fetchone(
        "SELECT COUNT(*) AS c FROM achievements"
    )["c"]

    await interaction.response.send_message(
        embed(
            "📈 TEAM XYZ DASHBOARD",
            f"👥 **Players:** {len(TEAM_MEMBERS)}\n"
            f"⚔️ **Scrims:** {scrims}\n"
            f"🏆 **Tournaments:** {tournaments}\n"
            f"🎮 **Matches:** {matches}\n"
            f"📊 **Stat Records:** {stats}\n"
            f"🧠 **Strategies:** {strategies}\n"
            f"🎯 **Training Plans:** {training}\n"
            f"📁 **Files:** {files_count}\n"
            f"🏅 **Achievements:** {achievements}"
        )
    )


# ============================================================
# ACTIVITY
# ============================================================

@bot.tree.command(
    name="activity",
    description="Show recent team activity"
)
async def activity_command(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    rows = fetchall("""
        SELECT *
        FROM activity
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await interaction.response.send_message(
            "📭 No activity."
        )
        return

    text = ""

    for a in rows:
        name = TEAM_MEMBERS.get(
            a["user_id"],
            {}
        ).get("name", str(a["user_id"]))

        text += (
            f"**{name}** — {a['action']}\n"
            f"{a['details']}\n\n"
        )

    await interaction.response.send_message(
        embed("📜 TEAM ACTIVITY", text)
    )


# ============================================================
# HELP
# ============================================================

@bot.tree.command(
    name="help",
    description="Show TEAM XYZ bot commands"
)
async def help_command(
    interaction: discord.Interaction
):
    if not await require_member(interaction):
        return

    text = """
**👥 TEAM**
`/team roster`
`/team profile`
`/team update`

**⚔️ SCRIMS**
`/scrim create`
`/scrim list`
`/scrim result`

**🏆 TOURNAMENT**
`/tournament create`
`/tournament list`
`/tournament result`

**🎮 MATCH**
`/match create`
`/match result`

**📊 STATS**
`/stats add`
`/stats player`
`/stats team`

**🧠 STRATEGY**
`/strategy add`
`/strategy list`

**🎯 TRAINING**
`/training add`
`/training progress`
`/training list`

**🏅 ACHIEVEMENTS**
`/achievement add`
`/achievement list`

**📢 ANNOUNCEMENTS**
`/announce send`
`/announce schedule`

**📁 STORAGE**
`/files`

**📈 SYSTEM**
`/dashboard`
`/activity`

**🤖 AI**
`/ai <question>`

AI automatically run nahi hota.
AI sirf `/ai` command par Gemini use karta hai.
"""

    await interaction.response.send_message(
        embed(
            "🤖 TEAM XYZ BOT",
            text
        )
    )


# ============================================================
# REMINDER SYSTEM
# ============================================================

def parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


@tasks.loop(minutes=1)
async def reminder_loop():
    now = datetime.now(timezone.utc)

    rows = fetchall("""
        SELECT *
        FROM scrims
        WHERE result='Scheduled'
    """)

    for s in rows:
        dt = parse_time(s["match_time"])

        if not dt:
            continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        diff = dt - now

        if timedelta(minutes=0) <= diff <= timedelta(minutes=5):
            for guild in bot.guilds:
                for uid in TEAM_MEMBERS:
                    member = guild.get_member(uid)

                    if member:
                        try:
                            await member.send(
                                f"⏰ **TEAM XYZ SCRIM REMINDER**\n\n"
                                f"Opponent: **{s['opponent']}**\n"
                                f"Time: **{s['match_time']}**\n"
                                f"Map: **{s['map'] or 'N/A'}**\n"
                                f"Room: `{s['room_id'] or 'N/A'}`\n"
                                f"Password: `{s['password'] or 'N/A'}`"
                            )
                        except Exception:
                            pass


# ============================================================
# SCHEDULED ANNOUNCEMENTS
# ============================================================

@tasks.loop(minutes=1)
async def announcement_loop():
    now = datetime.now(timezone.utc)

    rows = fetchall("""
        SELECT *
        FROM announcements
        WHERE status='Scheduled'
    """)

    for a in rows:
        dt = parse_time(a["send_at"])

        if not dt:
            continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        if now >= dt:
            channel = bot.get_channel(
                int(a["channel_id"])
            )

            if channel:
                try:
                    msg = await channel.send(
                        embed(
                            f"📢 {a['title']}",
                            a["content"],
                            discord.Color.gold()
                        )
                    )

                    execute("""
                        UPDATE announcements
                        SET status='Sent', message_id=?
                        WHERE id=?
                    """, (
                        msg.id,
                        a["id"]
                    ))

                except Exception as e:
                    print(
                        "[ANNOUNCEMENT ERROR]",
                        repr(e)
                    )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = json.dumps({
            "status": "online",
            "bot": BRAND,
            "discord": str(bot.user)
                if bot.user else "starting",
            "gemini_model": GEMINI_MODEL
        }).encode()

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"[WEB] Health server running on port {PORT}"
    )

    server.serve_forever()


# ============================================================
# COMMAND GROUP REGISTRATION
# ============================================================

bot.tree.add_command(team_group)
bot.tree.add_command(scrim_group)
bot.tree.add_command(tournament_group)
bot.tree.add_command(match_group)
bot.tree.add_command(stats_group)
bot.tree.add_command(strategy_group)
bot.tree.add_command(training_group)
bot.tree.add_command(achievement_group)
bot.tree.add_command(announcement_group)


# ============================================================
# START
# ============================================================

def main():
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN environment variable missing."
        )

    init_db()
    init_gemini()

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
