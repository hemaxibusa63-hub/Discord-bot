import os
import io
import sqlite3
import threading
import asyncio
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from google import genai
except ImportError:
    genai = None

# ============================================================
# TEAM XYZ - FREE FIRE ESPORTS DISCORD BOT
# Render FREE WEB SERVICE
# ============================================================

BRAND = "TEAM XYZ"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_FALLBACK_MODELS = [
    x.strip() for x in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash").split(",")
    if x.strip()
]
DB_PATH = os.getenv("DB_PATH", "team_xyz.db").strip() or "team_xyz.db"

try:
    STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0").strip() or "0")
except ValueError:
    STORAGE_CHANNEL_ID = 0

TEAM = {
    1549315294856740885: {"name": "Nirav", "role": "IGL + Primary Rusher"},
    1378993981769252966: {"name": "KRUTIK", "role": "Sniper"},
    1549303069756624968: {"name": "Dakshit", "role": "Supporter"},
    1549301330110185533: {"name": "Atul", "role": "Secondary Rusher"},
}
TEAM_IDS = set(TEAM)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

# ============================================================
# DATABASE
# ============================================================

db_lock = threading.RLock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or utcnow()).isoformat()


def execute(sql, params=(), fetch=False):
    with db_lock:
        cur = db.cursor()
        cur.execute(sql, params)
        result = cur.fetchall() if fetch else []
        db.commit()
        return result


def init_db():
    tables = [
        """CREATE TABLE IF NOT EXISTS scrims(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opponent TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            room_id TEXT DEFAULT '',
            password TEXT DEFAULT '',
            map TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            result TEXT DEFAULT '',
            reminder_sent INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS tournaments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            organizer TEXT DEFAULT '',
            scheduled_at TEXT DEFAULT '',
            rounds TEXT DEFAULT '',
            room_info TEXT DEFAULT '',
            result TEXT DEFAULT '',
            placement INTEGER DEFAULT 0,
            points REAL DEFAULT 0.0,
            notes TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS matches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            opponent TEXT DEFAULT '',
            match_type TEXT DEFAULT 'scrim',
            scheduled_at TEXT DEFAULT '',
            map TEXT DEFAULT '',
            room_id TEXT DEFAULT '',
            result TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS player_stats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER,
            user_id INTEGER NOT NULL,
            kills INTEGER DEFAULT 0,
            damage REAL DEFAULT 0.0,
            placement INTEGER DEFAULT 0,
            booyah INTEGER DEFAULT 0,
            survival REAL DEFAULT 0.0,
            points REAL DEFAULT 0.0,
            notes TEXT DEFAULT '',
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS strategies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS training(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            goal TEXT NOT NULL,
            scheduled_at TEXT DEFAULT '',
            progress TEXT DEFAULT 'Not started',
            notes TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS achievements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            user_id INTEGER,
            achieved_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS announcements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            scheduled_at TEXT DEFAULT '',
            channel_id INTEGER,
            sent INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_type TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            uploader_id INTEGER,
            uploaded_at TEXT,
            storage_channel_id INTEGER,
            storage_message_id INTEGER,
            storage_url TEXT DEFAULT ''
        )""",

        """CREATE TABLE IF NOT EXISTS chat_context(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            created_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS activity(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_at TEXT
        )""",
    ]

    for table in tables:
        execute(table)


def log_activity(user_id, action, details=""):
    execute(
        """INSERT INTO activity(user_id,action,details,created_at)
           VALUES(?,?,?,?)""",
        (user_id, action, str(details)[:2000], iso()),
    )


def parse_datetime(value):
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def member_info(user_id):
    return TEAM.get(user_id)


def member_text(user_id):
    info = TEAM.get(user_id)
    if not info:
        return str(user_id)
    return f"{info['name']} ({info['role']})"


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

gemini_client = None

if GEMINI_API_KEY and genai is not None:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        print("Gemini initialization error:", exc)


async def team_check(interaction: discord.Interaction):
    if interaction.user.id not in TEAM_IDS:
        raise app_commands.CheckFailure(
            "Only TEAM XYZ members can use this bot."
        )
    return True


def team_only():
    return app_commands.check(team_check)


@bot.event
async def on_ready():
    print("=" * 50)
    print(f"{BRAND} ONLINE")
    print(f"Discord user: {bot.user}")
    print(f"Gemini model: {GEMINI_MODEL}")
    print(f"Storage channel: {STORAGE_CHANNEL_ID or 'NOT SET'}")
    print("=" * 50)

    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as exc:
        print("Slash command sync error:", exc)

    if not reminder_loop.is_running():
        reminder_loop.start()

    if not announcement_loop.is_running():
        announcement_loop.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.author.id in TEAM_IDS:
        if message.attachments:
            await save_attachments(message)

        if message.content.strip():
            execute(
                """INSERT INTO chat_context
                   (user_id,content,channel_id,message_id,created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    message.author.id,
                    message.content[:4000],
                    message.channel.id,
                    message.id,
                    iso(),
                ),
            )

    await bot.process_commands(message)


# ============================================================
# HELP
# ============================================================

@tree.command(name="help", description="Show TEAM XYZ bot commands")
@team_only()
async def help_command(interaction: discord.Interaction):
    text = f"""🔥 **{BRAND} — FREE FIRE ESPORTS BOT**

👥 `/team`
⚔️ `/scrim_create`
⚔️ `/scrims`
⚔️ `/scrim_result`

🏆 `/tournament_create`
🏆 `/tournaments`
🏆 `/tournament_result`

🎯 `/match_create`
🎯 `/matches`

📊 `/stat_add`
📊 `/stats`
📊 `/player_stats`

🧠 `/strategy_add`
🧠 `/strategies`

🏋️ `/training_add`
🏋️ `/training`

🏅 `/achievement_add`
🏅 `/achievements`

📢 `/announce`

📁 `/files`

📈 `/dashboard`
🕒 `/activity`

🤖 `/ai`

All 4 TEAM XYZ members have equal/full access.
No owner panel."""
    await interaction.response.send_message(text, ephemeral=True)


# ============================================================
# TEAM
# ============================================================

@tree.command(name="team", description="Show TEAM XYZ roster")
@team_only()
async def team_command(interaction: discord.Interaction):
    lines = ["🔥 **TEAM XYZ ROSTER**", ""]
    for uid, info in TEAM.items():
        lines.append(
            f"• **{info['name']}** — {info['role']} — <@{uid}>"
        )
    await interaction.response.send_message("\n".join(lines))


# ============================================================
# SCRIMS
# ============================================================

@tree.command(name="scrim_create", description="Create a scrim")
@app_commands.describe(
    opponent="Opponent/team",
    scheduled_at="UTC ISO time, e.g. 2026-09-20T18:30:00+00:00",
    room_id="Room ID",
    password="Room password",
    map="Map",
    notes="Notes",
)
@team_only()
async def scrim_create(
    interaction: discord.Interaction,
    opponent: str,
    scheduled_at: str,
    room_id: str = "",
    password: str = "",
    map: str = "",
    notes: str = "",
):
    try:
        dt = parse_datetime(scheduled_at)
    except Exception:
        await interaction.response.send_message(
            "❌ Invalid time. Use ISO format.",
            ephemeral=True,
        )
        return

    execute(
        """INSERT INTO scrims
           (opponent,scheduled_at,room_id,password,map,notes,created_by,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            opponent,
            dt.isoformat(),
            room_id,
            password,
            map,
            notes,
            interaction.user.id,
            iso(),
        ),
    )

    log_activity(interaction.user.id, "scrim_create", opponent)

    await interaction.response.send_message(
        f"⚔️ **Scrim created**\n"
        f"Opponent: **{opponent}**\n"
        f"Time UTC: `{dt.isoformat()}`\n"
        f"Map: `{map or '-'}`\n"
        f"Room: `{room_id or '-'}`"
    )


@tree.command(name="scrims", description="Show scrims")
@team_only()
async def scrims_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM scrims ORDER BY scheduled_at ASC LIMIT 20",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("⚔️ No scrims found.")
        return

    lines = ["⚔️ **SCRIMS**", ""]
    for row in rows:
        lines.append(
            f"**#{row['id']} {row['opponent']}**\n"
            f"Time: `{row['scheduled_at']}`\n"
            f"Map: `{row['map'] or '-'}` | Room: `{row['room_id'] or '-'}`\n"
            f"Result: `{row['result'] or 'Pending'}`"
        )

    await interaction.response.send_message("\n".join(lines))


@tree.command(name="scrim_result", description="Update scrim result")
@team_only()
async def scrim_result(
    interaction: discord.Interaction,
    scrim_id: int,
    result: str,
):
    rows = execute(
        "SELECT id FROM scrims WHERE id=?",
        (scrim_id,),
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message(
            "❌ Scrim not found.",
            ephemeral=True,
        )
        return

    execute(
        "UPDATE scrims SET result=?, reminder_sent=1 WHERE id=?",
        (result, scrim_id),
    )

    await interaction.response.send_message(
        f"✅ Scrim **#{scrim_id}** result updated: **{result}**"
    )


# ============================================================
# TOURNAMENTS
# ============================================================

@tree.command(name="tournament_create", description="Create tournament")
@team_only()
async def tournament_create(
    interaction: discord.Interaction,
    name: str,
    organizer: str = "",
    scheduled_at: str = "",
    rounds: str = "",
    room_info: str = "",
    notes: str = "",
):
    if scheduled_at:
        try:
            scheduled_at = parse_datetime(scheduled_at).isoformat()
        except Exception:
            await interaction.response.send_message(
                "❌ Invalid time.",
                ephemeral=True,
            )
            return

    execute(
        """INSERT INTO tournaments
           (name,organizer,scheduled_at,rounds,room_info,notes,created_by,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            name,
            organizer,
            scheduled_at,
            rounds,
            room_info,
            notes,
            interaction.user.id,
            iso(),
        ),
    )

    await interaction.response.send_message(
        f"🏆 Tournament **{name}** created."
    )


@tree.command(name="tournaments", description="Show tournaments")
@team_only()
async def tournaments_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM tournaments ORDER BY id DESC LIMIT 20",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("🏆 No tournaments.")
        return

    lines = ["🏆 **TOURNAMENTS**", ""]
    for row in rows:
        lines.append(
            f"**#{row['id']} {row['name']}**\n"
            f"Organizer: `{row['organizer'] or '-'}`\n"
            f"Time: `{row['scheduled_at'] or '-'}`\n"
            f"Placement: `{row['placement'] or '-'}` | "
            f"Points: `{row['points']:.1f}`\n"
            f"Result: `{row['result'] or 'Pending'}`"
        )

    await interaction.response.send_message("\n".join(lines))


@tree.command(name="tournament_result", description="Update tournament result")
@team_only()
async def tournament_result(
    interaction: discord.Interaction,
    tournament_id: int,
    result: str = "",
    placement: int = 0,
    points: float = 0.0,
):
    rows = execute(
        "SELECT id FROM tournaments WHERE id=?",
        (tournament_id,),
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message(
            "❌ Tournament not found.",
            ephemeral=True,
        )
        return

    execute(
        """UPDATE tournaments
           SET result=?, placement=?, points=?
           WHERE id=?""",
        (result, placement, points, tournament_id),
    )

    await interaction.response.send_message(
        f"✅ Tournament **#{tournament_id}** updated.\n"
        f"Placement: **{placement}**\n"
        f"Points: **{points:.1f}**"
    )


# ============================================================
# MATCHES
# ============================================================

@tree.command(name="match_create", description="Create match")
@team_only()
async def match_create(
    interaction: discord.Interaction,
    title: str,
    opponent: str = "",
    match_type: str = "scrim",
    scheduled_at: str = "",
    map: str = "",
    room_id: str = "",
    notes: str = "",
):
    if scheduled_at:
        try:
            scheduled_at = parse_datetime(scheduled_at).isoformat()
        except Exception:
            await interaction.response.send_message(
                "❌ Invalid time.",
                ephemeral=True,
            )
            return

    execute(
        """INSERT INTO matches
           (title,opponent,match_type,scheduled_at,map,room_id,notes,created_by,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            title,
            opponent,
            match_type,
            scheduled_at,
            map,
            room_id,
            notes,
            interaction.user.id,
            iso(),
        ),
    )

    await interaction.response.send_message(
        f"🎯 Match **{title}** created."
    )


@tree.command(name="matches", description="Show matches")
@team_only()
async def matches_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM matches ORDER BY id DESC LIMIT 25",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("🎯 No matches.")
        return

    lines = ["🎯 **MATCHES**", ""]
    for row in rows:
        lines.append(
            f"**#{row['id']} {row['title']}**\n"
            f"Type: `{row['match_type']}` | Opponent: `{row['opponent'] or '-'}`\n"
            f"Time: `{row['scheduled_at'] or '-'}`\n"
            f"Result: `{row['result'] or 'Pending'}`"
        )

    await interaction.response.send_message("\n".join(lines))


# ============================================================
# PLAYER STATS
# ============================================================

@tree.command(name="stat_add", description="Add player match stats")
@team_only()
async def stat_add(
    interaction: discord.Interaction,
    user: discord.User,
    kills: int = 0,
    damage: float = 0.0,
    placement: int = 0,
    booyah: int = 0,
    survival: float = 0.0,
    points: float = 0.0,
    match_id: int = 0,
    notes: str = "",
):
    if user.id not in TEAM_IDS:
        await interaction.response.send_message(
            "❌ User is not a TEAM XYZ member.",
            ephemeral=True,
        )
        return

    if match_id:
        rows = execute(
            "SELECT id FROM matches WHERE id=?",
            (match_id,),
            fetch=True,
        )
        if not rows:
            await interaction.response.send_message(
                "❌ Match not found.",
                ephemeral=True,
            )
            return

    execute(
        """INSERT INTO player_stats
           (match_id,user_id,kills,damage,placement,booyah,
            survival,points,notes,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            match_id or None,
            user.id,
            kills,
            damage,
            placement,
            booyah,
            survival,
            points,
            notes,
            iso(),
        ),
    )

    await interaction.response.send_message(
        f"📊 Stats saved for **{TEAM[user.id]['name']}**\n"
        f"Kills: `{kills}` | Damage: `{damage:.1f}` | "
        f"Points: `{points:.1f}`"
    )


@tree.command(name="stats", description="Show team stats")
@team_only()
async def stats_command(interaction: discord.Interaction):
    rows = execute(
        """SELECT user_id,
                  COUNT(*) games,
                  COALESCE(SUM(kills),0) kills,
                  COALESCE(SUM(damage),0) damage,
                  COALESCE(SUM(booyah),0) booyah,
                  COALESCE(SUM(points),0) points
           FROM player_stats
           GROUP BY user_id
           ORDER BY points DESC""",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("📊 No stats yet.")
        return

    lines = ["📊 **TEAM STATS**", ""]
    for row in rows:
        if row["user_id"] not in TEAM:
            continue
        info = TEAM[row["user_id"]]
        lines.append(
            f"**{info['name']}** — {info['role']}\n"
            f"Games: `{row['games']}` | Kills: `{row['kills']}` | "
            f"Damage: `{row['damage']:.1f}` | "
            f"Booyah: `{row['booyah']}` | Points: `{row['points']:.1f}`"
        )

    await interaction.response.send_message("\n".join(lines))


@tree.command(name="player_stats", description="Show player stats")
@team_only()
async def player_stats_command(
    interaction: discord.Interaction,
    user: discord.User,
):
    if user.id not in TEAM_IDS:
        await interaction.response.send_message(
            "❌ Not a TEAM XYZ member.",
            ephemeral=True,
        )
        return

    rows = execute(
        """SELECT COUNT(*) games,
                  COALESCE(SUM(kills),0) kills,
                  COALESCE(SUM(damage),0) damage,
                  COALESCE(SUM(booyah),0) booyah,
                  COALESCE(SUM(points),0) points
           FROM player_stats WHERE user_id=?""",
        (user.id,),
        fetch=True,
    )

    row = rows[0]
    info = TEAM[user.id]

    await interaction.response.send_message(
        f"📊 **{info['name']} — {info['role']}**\n"
        f"Games: `{row['games']}`\n"
        f"Kills: `{row['kills']}`\n"
        f"Damage: `{row['damage']:.1f}`\n"
        f"Booyah: `{row['booyah']}`\n"
        f"Points: `{row['points']:.1f}`"
    )


# ============================================================
# STRATEGY
# ============================================================

@tree.command(name="strategy_add", description="Save strategy")
@team_only()
async def strategy_add(
    interaction: discord.Interaction,
    category: str,
    title: str,
    content: str,
):
    execute(
        """INSERT INTO strategies
           (category,title,content,created_by,created_at)
           VALUES(?,?,?,?,?)""",
        (
            category,
            title,
            content,
            interaction.user.id,
            iso(),
        ),
    )

    await interaction.response.send_message(
        f"🧠 Strategy **{title}** saved."
    )


@tree.command(name="strategies", description="Show strategies")
@team_only()
async def strategies_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM strategies ORDER BY id DESC LIMIT 25",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("🧠 No strategies.")
        return

    lines = ["🧠 **TEAM STRATEGIES**", ""]
    for row in rows:
        lines.append(
            f"**#{row['id']} {row['title']}** [{row['category']}]\n"
            f"{row['content'][:700]}"
        )

    await interaction.response.send_message("\n".join(lines))


# ============================================================
# TRAINING
# ============================================================

@tree.command(name="training_add", description="Create training plan")
@team_only()
async def training_add(
    interaction: discord.Interaction,
    category: str,
    goal: str,
    scheduled_at: str = "",
    progress: str = "Not started",
    notes: str = "",
):
    if scheduled_at:
        try:
            scheduled_at = parse_datetime(scheduled_at).isoformat()
        except Exception:
            await interaction.response.send_message(
                "❌ Invalid time.",
                ephemeral=True,
            )
            return

    execute(
        """INSERT INTO training
           (category,goal,scheduled_at,progress,notes,created_by,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            category,
            goal,
            scheduled_at,
            progress,
            notes,
            interaction.user.id,
            iso(),
        ),
    )

    await interaction.response.send_message(
        f"🏋️ Training goal **{goal}** saved."
    )


@tree.command(name="training", description="Show training plans")
@team_only()
async def training_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM training ORDER BY id DESC LIMIT 25",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("🏋️ No training plans.")
        return

    lines = ["🏋️ **TRAINING PLANS**", ""]
    for row in rows:
        lines.append(
            f"**#{row['id']} {row['category']}** — {row['goal']}\n"
            f"Time: `{row['scheduled_at'] or '-'}` | "
            f"Progress: `{row['progress'] or '-'}`"
        )

    await interaction.response.send_message("\n".join(lines))


# ============================================================
# ACHIEVEMENTS
# ============================================================

@tree.command(name="achievement_add", description="Add achievement")
@team_only()
async def achievement_add(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    user: discord.User = None,
):
    if user and user.id not in TEAM_IDS:
        await interaction.response.send_message(
            "❌ Not a TEAM XYZ member.",
            ephemeral=True,
        )
        return

    execute(
        """INSERT INTO achievements
           (title,description,user_id,achieved_at)
           VALUES(?,?,?,?)""",
        (
            title,
            description,
            user.id if user else None,
            iso(),
        ),
    )

    await interaction.response.send_message(
        f"🏅 Achievement **{title}** added."
    )


@tree.command(name="achievements", description="Show achievements")
@team_only()
async def achievements_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM achievements ORDER BY id DESC LIMIT 30",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message("🏅 No achievements.")
        return

    lines = ["🏅 **ACHIEVEMENTS**", ""]
    for row in rows:
        who = (
            member_text(row["user_id"])
            if row["user_id"]
            else "TEAM XYZ"
        )
        lines.append(
            f"• **{row['title']}** — {who}\n"
            f"  {row['description'] or ''}"
        )

    await interaction.response.send_message("\n".join(lines))


# ============================================================
# ANNOUNCEMENTS
# ============================================================

@tree.command(name="announce", description="Send or schedule announcement")
@team_only()
async def announce_command(
    interaction: discord.Interaction,
    content: str,
    channel: discord.TextChannel = None,
    scheduled_at: str = "",
):
    target = channel or interaction.channel

    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message(
            "❌ Text channel required.",
            ephemeral=True,
        )
        return

    if scheduled_at:
        try:
            dt = parse_datetime(scheduled_at)
        except Exception:
            await interaction.response.send_message(
                "❌ Invalid time.",
                ephemeral=True,
            )
            return

        execute(
            """INSERT INTO announcements
               (content,scheduled_at,channel_id,created_by,created_at)
               VALUES(?,?,?,?,?)""",
            (
                content,
                dt.isoformat(),
                target.id,
                interaction.user.id,
                iso(),
            ),
        )

        await interaction.response.send_message(
            f"📢 Scheduled for `{dt.isoformat()}` in {target.mention}."
        )
    else:
        await target.send(
            f"📢 **{BRAND} ANNOUNCEMENT**\n{content}"
        )
        await interaction.response.send_message(
            "✅ Announcement sent.",
            ephemeral=True,
        )


# ============================================================
# FILE STORAGE
# ============================================================

async def save_attachments(message: discord.Message):
    if not STORAGE_CHANNEL_ID:
        print("Attachment received but STORAGE_CHANNEL_ID is not configured.")
        return

    try:
        storage = bot.get_channel(STORAGE_CHANNEL_ID)

        if storage is None:
            storage = await bot.fetch_channel(STORAGE_CHANNEL_ID)

        if not isinstance(storage, discord.TextChannel):
            print("Storage channel is not a text channel.")
            return

        for attachment in message.attachments:
            try:
                data = await attachment.read()
                discord_file = discord.File(
                    io.BytesIO(data),
                    filename=attachment.filename,
                )

                sent = await storage.send(
                    content=(
                        f"📦 **{BRAND} STORAGE**\n"
                        f"Uploader: <@{message.author.id}> "
                        f"({TEAM[message.author.id]['role']})\n"
                        f"Original message: `{message.id}`"
                    ),
                    file=discord_file,
                )

                stored_attachment = (
                    sent.attachments[0]
                    if sent.attachments
                    else None
                )

                execute(
                    """INSERT INTO files
                       (file_name,file_type,file_size,uploader_id,
                        uploaded_at,storage_channel_id,
                        storage_message_id,storage_url)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        attachment.filename,
                        attachment.content_type or "unknown",
                        attachment.size,
                        message.author.id,
                        iso(),
                        storage.id,
                        sent.id,
                        stored_attachment.url
                        if stored_attachment
                        else "",
                    ),
                )

                log_activity(
                    message.author.id,
                    "file_upload",
                    attachment.filename,
                )

            except Exception as exc:
                print("Attachment upload error:", exc)

    except Exception as exc:
        print("Storage channel error:", exc)


@tree.command(name="files", description="Show team stored files")
@team_only()
async def files_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM files ORDER BY id DESC LIMIT 25",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message(
            "📁 No stored files."
        )
        return

    lines = ["📁 **TEAM XYZ FILES**", ""]

    for row in rows:
        lines.append(
            f"**#{row['id']} {row['file_name']}**\n"
            f"Type: `{row['file_type'] or '-'}` | "
            f"Size: `{row['file_size']} bytes`\n"
            f"By: <@{row['uploader_id']}>\n"
            f"{row['storage_url'] or 'No storage URL'}"
        )

    await interaction.response.send_message("\n".join(lines))


# ============================================================
# DASHBOARD
# ============================================================

@tree.command(name="dashboard", description="Show team dashboard")
@team_only()
async def dashboard_command(interaction: discord.Interaction):
    tables = [
        "scrims",
        "tournaments",
        "matches",
        "player_stats",
        "strategies",
        "training",
        "achievements",
        "files",
    ]

    counts = {}

    for table in tables:
        rows = execute(
            f"SELECT COUNT(*) AS c FROM {table}",
            fetch=True,
        )
        counts[table] = rows[0]["c"]

    await interaction.response.send_message(
        f"📈 **{BRAND} DASHBOARD**\n\n"
        f"Members: **4**\n"
        f"Scrims: **{counts['scrims']}**\n"
        f"Tournaments: **{counts['tournaments']}**\n"
        f"Matches: **{counts['matches']}**\n"
        f"Stat records: **{counts['player_stats']}**\n"
        f"Strategies: **{counts['strategies']}**\n"
        f"Training: **{counts['training']}**\n"
        f"Achievements: **{counts['achievements']}**\n"
        f"Files: **{counts['files']}**\n"
        f"Storage: **{'Configured' if STORAGE_CHANNEL_ID else 'Not configured'}**"
    )


@tree.command(name="activity", description="Show recent activity")
@team_only()
async def activity_command(interaction: discord.Interaction):
    rows = execute(
        "SELECT * FROM activity ORDER BY id DESC LIMIT 25",
        fetch=True,
    )

    if not rows:
        await interaction.response.send_message(
            "🕒 No activity."
        )
        return

    lines = ["🕒 **RECENT ACTIVITY**", ""]

    for row in rows:
        lines.append(
            f"• `{row['created_at']}` — "
            f"<@{row['user_id']}> — "
            f"**{row['action']}** — "
            f"{row['details'] or ''}"
        )

    await interaction.response.send_message("\n".join(lines))


# ============================================================
# GEMINI AI
# ============================================================

def make_ai_context(user_id: int):
    """Build a compact, bounded AI context to reduce Gemini request load."""
    info = TEAM[user_id]
    parts = [
        f"Team: {BRAND}",
        f"Current member: {info['name']}",
        f"Current member role: {info['role']}",
        "ROSTER:",
    ]
    for uid, member in TEAM.items():
        parts.append(f"- {member['name']} | {member['role']} | ID {uid}")

    def add_rows(title, sql, formatter=str):
        parts.append(f"\\n{title}:")
        for row in execute(sql, fetch=True):
            parts.append(formatter(row))

    add_rows("RECENT SCRIMS", """SELECT opponent,scheduled_at,map,result,notes FROM scrims ORDER BY id DESC LIMIT 6""", lambda r: str(dict(r)))
    add_rows("RECENT TOURNAMENTS", """SELECT name,scheduled_at,result,placement,points,notes FROM tournaments ORDER BY id DESC LIMIT 4""", lambda r: str(dict(r)))
    add_rows("RECENT MATCHES", """SELECT title,opponent,match_type,scheduled_at,map,result,notes FROM matches ORDER BY id DESC LIMIT 6""", lambda r: str(dict(r)))
    add_rows("PLAYER STATS", """SELECT user_id,COUNT(*) games,SUM(kills) kills,ROUND(SUM(damage),1) damage,SUM(booyah) booyah,SUM(points) points FROM player_stats GROUP BY user_id""", lambda r: f"{member_text(r['user_id'])}: {dict(r)}")
    add_rows("RECENT STRATEGIES", """SELECT category,title,content FROM strategies ORDER BY id DESC LIMIT 8""", lambda r: str(dict(r)))
    add_rows("RECENT TRAINING", """SELECT category,goal,scheduled_at,progress,notes FROM training ORDER BY id DESC LIMIT 8""", lambda r: str(dict(r)))
    add_rows("RECENT TEAM CHAT", """SELECT user_id,content,created_at FROM chat_context ORDER BY id DESC LIMIT 12""", lambda r: f"{member_text(r['user_id'])}: {str(r['content'])[:700]}")

    # Final character cap keeps even a busy team from generating oversized prompts.
    return "\n".join(parts)[:14000]


# Up to four AI requests can be processed concurrently, matching the four TEAM XYZ
# members. A request never blocks another member indefinitely.
ai_slots = asyncio.Semaphore(4)


def is_transient_gemini_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(key in text for key in (
        "503", "unavailable", "high demand", "429", "rate limit",
        "resource exhausted", "temporarily unavailable", "overloaded",
        "service unavailable", "deadline exceeded",
    ))


def split_discord_text(text: str, limit: int = 1900):
    text = str(text or "")
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


async def _gemini_call(model: str, prompt: str):
    return await asyncio.to_thread(
        gemini_client.models.generate_content,
        model=model,
        contents=prompt,
    )


async def generate_ai_response(prompt: str):
    """Concurrent Gemini calls with bounded retries and model fallback."""
    async with ai_slots:
        models_to_try = []
        for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
            if model and model not in models_to_try:
                models_to_try.append(model)

        last_error = None
        # Each member gets an independent task. The semaphore allows all four
        # TEAM XYZ members to be processed concurrently.
        for model_index, model in enumerate(models_to_try):
            for attempt in range(4):
                try:
                    return await _gemini_call(model, prompt)
                except Exception as exc:
                    last_error = exc
                    if not is_transient_gemini_error(exc):
                        raise
                    if attempt >= 3:
                        break
                    # Jitter avoids synchronized retry bursts when Gemini is busy.
                    delay = min(16.0, (2 ** attempt) + __import__("random").uniform(0.2, 1.0))
                    await asyncio.sleep(delay)

            # If the primary model is overloaded, try the configured fallback.
            if model_index < len(models_to_try) - 1:
                await asyncio.sleep(1.0)

        raise last_error or RuntimeError("Gemini request failed")


@tree.command(
    name="ai",
    description="Ask TEAM XYZ Gemini esports assistant",
)
@app_commands.describe(question="Your question")
@team_only()
async def ai_command(
    interaction: discord.Interaction,
    question: str,
):
    if not GEMINI_API_KEY:
        await interaction.response.send_message(
            "❌ GEMINI_API_KEY is not configured in Render.", ephemeral=True
        )
        return
    if gemini_client is None:
        await interaction.response.send_message(
            "❌ Gemini client is unavailable. Check API key and package.", ephemeral=True
        )
        return

    question = (question or "").strip()
    if not question:
        await interaction.response.send_message("❌ Please enter a question.", ephemeral=True)
        return
    question = question[:3500]

    await interaction.response.defer(thinking=True)

    info = TEAM[interaction.user.id]
    context = make_ai_context(interaction.user.id)
    prompt = f"""You are the official AI esports coach for {BRAND}.
The user explicitly invoked /ai. Do not react to normal chat automatically.
Give practical Free Fire esports advice. Know the four players and their roles.
Tailor the answer to the requesting member when relevant.
Never invent team facts; say when data is unavailable.
Keep the answer useful and concise.

REQUESTING MEMBER: {info['name']}
ROLE: {info['role']}

TEAM CONTEXT:
{context}

USER QUESTION:
{question}
"""

    try:
        response = await generate_ai_response(prompt)
        answer = (getattr(response, "text", None) or "").strip()
        if not answer:
            answer = "Gemini returned no text. Please try again."

        chunks = split_discord_text(answer, 1800)
        await interaction.followup.send(
            f"🤖 **TEAM XYZ AI — {info['name']}**\n{chunks[0]}"
        )
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk)
        log_activity(interaction.user.id, "ai", question[:300])

    except Exception as exc:
        if is_transient_gemini_error(exc):
            message = (
                "⚠️ Gemini is temporarily busy/unavailable even after automatic retries "
                "and fallback. Please try /ai again shortly."
            )
        else:
            message = "❌ Gemini request failed, but the TEAM XYZ bot is still running. Please try again."
        await interaction.followup.send(message)


# ============================================================
# REMINDERS
# ============================================================

@tasks.loop(seconds=30)
async def reminder_loop():
    current = utcnow()
    limit = current + timedelta(minutes=10)

    rows = execute(
        """SELECT * FROM scrims
           WHERE reminder_sent=0
           AND result=''
           ORDER BY scheduled_at LIMIT 50""",
        fetch=True,
    )

    for row in rows:
        try:
            scheduled = parse_datetime(row["scheduled_at"])
        except Exception:
            continue

        if current <= scheduled <= limit:
            for uid in TEAM_IDS:
                try:
                    user = bot.get_user(uid)

                    if user is None:
                        user = await bot.fetch_user(uid)

                    await user.send(
                        f"⏰ **TEAM XYZ SCRIM REMINDER**\n"
                        f"Opponent: **{row['opponent']}**\n"
                        f"Time UTC: `{row['scheduled_at']}`\n"
                        f"Map: `{row['map'] or '-'}`\n"
                        f"Room: `{row['room_id'] or '-'}`"
                    )
                except Exception as exc:
                    print("Reminder DM error:", exc)

            execute(
                "UPDATE scrims SET reminder_sent=1 WHERE id=?",
                (row["id"],),
            )


@reminder_loop.before_loop
async def before_reminders():
    await bot.wait_until_ready()


@tasks.loop(seconds=30)
async def announcement_loop():
    current = utcnow()

    rows = execute(
        """SELECT * FROM announcements
           WHERE sent=0
           AND scheduled_at!=''
           ORDER BY id LIMIT 50""",
        fetch=True,
    )

    for row in rows:
        try:
            scheduled = parse_datetime(row["scheduled_at"])
        except Exception:
            continue

        if scheduled <= current:
            try:
                channel = bot.get_channel(row["channel_id"])

                if channel is None:
                    channel = await bot.fetch_channel(row["channel_id"])

                if isinstance(channel, discord.TextChannel):
                    await channel.send(
                        f"📢 **{BRAND} ANNOUNCEMENT**\n"
                        f"{row['content']}"
                    )

                    execute(
                        "UPDATE announcements SET sent=1 WHERE id=?",
                        (row["id"],),
                    )
            except Exception as exc:
                print("Scheduled announcement error:", exc)


@announcement_loop.before_loop
async def before_announcements():
    await bot.wait_until_ready()


# ============================================================
# ERROR HANDLING
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CheckFailure):
        message = "❌ Only TEAM XYZ members can use this bot."
    else:
        message = f"❌ Command error: `{type(error).__name__}: {str(error)[:500]}`"
        print("Command error:", repr(error))

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )
    except Exception:
        pass


# ============================================================
# RENDER WEB SERVICE HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b"TEAM XYZ Discord Bot is running."

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    print(f"Render health server listening on 0.0.0.0:{port}")

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )
    health_thread.start()

    try:
        bot.run(TOKEN)
    finally:
        with db_lock:
            db.close()


if __name__ == "__main__":
    main()
