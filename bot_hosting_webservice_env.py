# ============================================================
# KRUTIK CYBER EXPERT
# Discord Owner Management Bot
#
# Features:
#   👑 Owner-only panel
#   📢 Announcements
#   📅 Scheduled announcements
#   👋 Welcome system
#   🔗 File/Link system
#   📊 Dashboard
#   💾 SQLite database
#
# Python: 3.10+
# Library: discord.py 2.5+
# ============================================================

import os
import sqlite3
import secrets
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks


# ============================================================
# CONFIG
# ============================================================

# RENDER ENVIRONMENT CONFIG
# Render Dashboard -> Your Service -> Environment
#
# Add these Environment Variables:
#   DISCORD_TOKEN = your Discord bot token
#   OWNER_ID      = your Discord User ID
#
# The bot token is NOT stored in this source code.
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "krutik_discord.db").strip()

BRAND = "TEAM XYZ BOT"

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN missing. Add DISCORD_TOKEN in Render Environment Variables."
    )

if not OWNER_ID_RAW.isdigit():
    raise RuntimeError(
        "OWNER_ID missing/invalid. Add your numeric Discord User ID in Render Environment Variables."
    )

OWNER_ID = int(OWNER_ID_RAW)


# ============================================================
# INTENTS
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (guild_id, key)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    content TEXT,
    attachment_url TEXT,
    attachment_name TEXT,
    send_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    attachment_url TEXT,
    created_at TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0
)
""")

db.commit()


# ============================================================
# DATABASE HELPERS
# ============================================================

def set_setting(guild_id: int, key: str, value: str):
    db.execute("""
        INSERT INTO settings(guild_id, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, key)
        DO UPDATE SET value=excluded.value
    """, (guild_id, key, value))
    db.commit()


def get_setting(guild_id: int, key: str, default=None):
    row = db.execute("""
        SELECT value
        FROM settings
        WHERE guild_id=? AND key=?
    """, (guild_id, key)).fetchone()

    if row:
        return row["value"]

    return default


def delete_setting(guild_id: int, key: str):
    db.execute("""
        DELETE FROM settings
        WHERE guild_id=? AND key=?
    """, (guild_id, key))
    db.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(user: discord.User) -> bool:
    return user.id == OWNER_ID


async def owner_only(interaction: discord.Interaction) -> bool:
    if interaction.user.id != OWNER_ID:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ You don't have permission to use this.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ You don't have permission to use this.",
                    ephemeral=True,
                )
        except Exception:
            pass

        return False

    return True


# ============================================================
# EMBED
# ============================================================

def make_embed(title: str, description: str):
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
    )


# ============================================================
# OWNER PANEL
# ============================================================

class OwnerPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Announcement",
        emoji="📢",
        style=discord.ButtonStyle.primary,
        custom_id="kce_announcement",
    )
    async def announcement_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await owner_only(interaction):
            return

        await interaction.response.send_modal(
            AnnouncementModal()
        )

    @discord.ui.button(
        label="Schedule",
        emoji="📅",
        style=discord.ButtonStyle.primary,
        custom_id="kce_schedule",
    )
    async def schedule_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await owner_only(interaction):
            return

        await interaction.response.send_modal(
            ScheduleModal()
        )

    @discord.ui.button(
        label="Welcome",
        emoji="👋",
        style=discord.ButtonStyle.success,
        custom_id="kce_welcome",
    )
    async def welcome_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await owner_only(interaction):
            return

        await interaction.response.send_modal(
            WelcomeModal()
        )

    @discord.ui.button(
        label="Link / File",
        emoji="🔗",
        style=discord.ButtonStyle.secondary,
        custom_id="kce_link",
    )
    async def link_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await owner_only(interaction):
            return

        await interaction.response.send_modal(
            LinkModal()
        )

    @discord.ui.button(
        label="Dashboard",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        custom_id="kce_dashboard",
    )
    async def dashboard_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await owner_only(interaction):
            return

        await show_dashboard(interaction)


# ============================================================
# ANNOUNCEMENT MODAL
# ============================================================

class AnnouncementModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="📢 New Announcement")

        self.channel_id = discord.ui.TextInput(
            label="Channel ID",
            placeholder="Example: 123456789012345678",
            required=True,
            max_length=30,
        )

        self.message = discord.ui.TextInput(
            label="Message",
            placeholder="Announcement message...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
        )

        self.add_item(self.channel_id)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):

        if not await owner_only(interaction):
            return

        try:
            channel_id = int(str(self.channel_id.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid channel ID.",
                ephemeral=True,
            )
            return

        channel = bot.get_channel(channel_id)

        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                channel = None

        if channel is None:
            await interaction.response.send_message(
                "❌ Channel not found.",
                ephemeral=True,
            )
            return

        try:
            sent = await channel.send(
                content=str(self.message.value),
                allowed_mentions=discord.AllowedMentions.none(),
            )

            await interaction.response.send_message(
                f"✅ Announcement sent.\n\n"
                f"📢 Channel: {channel.mention}\n"
                f"🆔 Message ID: `{sent.id}`",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot doesn't have permission to send messages there.",
                ephemeral=True,
            )

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Send failed:\n`{str(e)[:1500]}`",
                ephemeral=True,
            )


# ============================================================
# SCHEDULE MODAL
# ============================================================

class ScheduleModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="📅 Schedule Announcement")

        self.channel_id = discord.ui.TextInput(
            label="Channel ID",
            placeholder="123456789012345678",
            required=True,
        )

        self.send_at = discord.ui.TextInput(
            label="UTC Date & Time",
            placeholder="2026-09-20 20:00",
            required=True,
        )

        self.message = discord.ui.TextInput(
            label="Message",
            placeholder="Scheduled announcement...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
        )

        self.add_item(self.channel_id)
        self.add_item(self.send_at)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):

        if not await owner_only(interaction):
            return

        try:
            channel_id = int(str(self.channel_id.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid channel ID.",
                ephemeral=True,
            )
            return

        try:
            dt = datetime.strptime(
                str(self.send_at.value).strip(),
                "%Y-%m-%d %H:%M",
            ).replace(tzinfo=timezone.utc)

        except ValueError:
            await interaction.response.send_message(
                "❌ Date format wrong.\n"
                "Use: `YYYY-MM-DD HH:MM`\n"
                "Time is UTC.",
                ephemeral=True,
            )
            return

        if dt <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ Schedule time must be in the future.",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild.id if interaction.guild else 0

        db.execute("""
            INSERT INTO schedules(
                guild_id,
                channel_id,
                content,
                attachment_url,
                attachment_name,
                send_at,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id,
            channel_id,
            str(self.message.value),
            None,
            None,
            dt.isoformat(),
            "scheduled",
            now_iso(),
        ))

        db.commit()

        await interaction.response.send_message(
            "✅ Announcement scheduled.\n\n"
            f"📢 Channel ID: `{channel_id}`\n"
            f"📅 UTC: `{dt.strftime('%Y-%m-%d %H:%M')}`",
            ephemeral=True,
        )


# ============================================================
# WELCOME MODAL
# ============================================================

class WelcomeModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="👋 Welcome Settings")

        self.channel_id = discord.ui.TextInput(
            label="Welcome Channel ID",
            placeholder="123456789012345678",
            required=True,
        )

        self.message = discord.ui.TextInput(
            label="Welcome Message",
            placeholder="Welcome {user} to {server}!",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
        )

        self.add_item(self.channel_id)
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):

        if not await owner_only(interaction):
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Use this inside a server.",
                ephemeral=True,
            )
            return

        try:
            channel_id = int(str(self.channel_id.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid channel ID.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(channel_id)

        if channel is None:
            await interaction.response.send_message(
                "❌ Channel not found in this server.",
                ephemeral=True,
            )
            return

        set_setting(
            interaction.guild.id,
            "welcome_channel",
            str(channel_id),
        )

        set_setting(
            interaction.guild.id,
            "welcome_message",
            str(self.message.value),
        )

        set_setting(
            interaction.guild.id,
            "welcome_enabled",
            "1",
        )

        await interaction.response.send_message(
            "✅ Welcome system enabled.\n\n"
            f"📢 Channel: {channel.mention}\n"
            f"💬 Message: `{self.message.value}`",
            ephemeral=True,
        )


# ============================================================
# LINK / FILE MODAL
# ============================================================

class LinkModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="🔗 Link / File")

        self.name = discord.ui.TextInput(
            label="Content Name",
            placeholder="Example: My File",
            required=True,
            max_length=100,
        )

        self.message_id = discord.ui.TextInput(
            label="Discord Message ID",
            placeholder="Message containing the file",
            required=True,
        )

        self.channel_id = discord.ui.TextInput(
            label="Channel ID",
            placeholder="Channel containing the message",
            required=True,
        )

        self.add_item(self.name)
        self.add_item(self.message_id)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):

        if not await owner_only(interaction):
            return

        try:
            message_id = int(str(self.message_id.value).strip())
            channel_id = int(str(self.channel_id.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid ID.",
                ephemeral=True,
            )
            return

        channel = bot.get_channel(channel_id)

        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                channel = None

        if channel is None:
            await interaction.response.send_message(
                "❌ Channel not found.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            await interaction.response.send_message(
                "❌ Message not found.",
                ephemeral=True,
            )
            return

        attachment_url = None

        if message.attachments:
            attachment_url = message.attachments[0].url

        token = secrets.token_urlsafe(8)

        guild_id = (
            interaction.guild.id
            if interaction.guild
            else 0
        )

        db.execute("""
            INSERT INTO links(
                guild_id,
                token,
                channel_id,
                message_id,
                name,
                attachment_url,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            guild_id,
            token,
            channel_id,
            message_id,
            str(self.name.value),
            attachment_url,
            now_iso(),
        ))

        db.commit()

        jump_url = message.jump_url

        result = (
            "✅ Link created!\n\n"
            f"📦 Name: **{self.name.value}**\n"
            f"🔑 ID: `{token}`\n"
            f"🔗 Discord Link:\n{jump_url}\n"
        )

        if attachment_url:
            result += f"\n📎 File URL:\n{attachment_url}"

        await interaction.response.send_message(
            result,
            ephemeral=True,
        )


# ============================================================
# WELCOME EVENT
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):

    guild_id = member.guild.id

    enabled = get_setting(
        guild_id,
        "welcome_enabled",
        "0",
    )

    if enabled != "1":
        return

    channel_id_raw = get_setting(
        guild_id,
        "welcome_channel",
    )

    message_template = get_setting(
        guild_id,
        "welcome_message",
        "👋 Welcome {user} to {server}!",
    )

    if not channel_id_raw:
        return

    try:
        channel_id = int(channel_id_raw)
    except ValueError:
        return

    channel = member.guild.get_channel(channel_id)

    if channel is None:
        return

    text = message_template

    text = text.replace(
        "{user}",
        member.mention,
    )

    text = text.replace(
        "{username}",
        member.name,
    )

    text = text.replace(
        "{server}",
        member.guild.name,
    )

    text = text.replace(
        "{user_id}",
        str(member.id),
    )

    try:
        await channel.send(
            text,
            allowed_mentions=discord.AllowedMentions(
                users=True
            ),
        )
    except Exception:
        pass


# ============================================================
# SCHEDULE WORKER
# ============================================================

@tasks.loop(seconds=10)
async def schedule_worker():

    rows = db.execute("""
        SELECT *
        FROM schedules
        WHERE status='scheduled'
        ORDER BY id ASC
        LIMIT 50
    """).fetchall()

    current = datetime.now(timezone.utc)

    for row in rows:

        try:
            send_at = datetime.fromisoformat(
                row["send_at"]
            )

            if send_at > current:
                continue

            channel = bot.get_channel(
                int(row["channel_id"])
            )

            if channel is None:
                try:
                    channel = await bot.fetch_channel(
                        int(row["channel_id"])
                    )
                except Exception:
                    channel = None

            if channel is None:
                db.execute("""
                    UPDATE schedules
                    SET status='failed'
                    WHERE id=?
                """, (row["id"],))

                db.commit()
                continue

            try:
                await channel.send(
                    content=row["content"] or "",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

                db.execute("""
                    UPDATE schedules
                    SET status='sent'
                    WHERE id=?
                """, (row["id"],))

                db.commit()

            except Exception:
                db.execute("""
                    UPDATE schedules
                    SET status='failed'
                    WHERE id=?
                """, (row["id"],))

                db.commit()

        except Exception:
            db.execute("""
                UPDATE schedules
                SET status='failed'
                WHERE id=?
            """, (row["id"],))

            db.commit()


@schedule_worker.before_loop
async def before_schedule_worker():
    await bot.wait_until_ready()


# ============================================================
# DASHBOARD
# ============================================================

async def show_dashboard(
    interaction: discord.Interaction
):

    users = 0
    schedules = db.execute("""
        SELECT COUNT(*) AS c
        FROM schedules
    """).fetchone()["c"]

    scheduled = db.execute("""
        SELECT COUNT(*) AS c
        FROM schedules
        WHERE status='scheduled'
    """).fetchone()["c"]

    sent = db.execute("""
        SELECT COUNT(*) AS c
        FROM schedules
        WHERE status='sent'
    """).fetchone()["c"]

    links = db.execute("""
        SELECT COUNT(*) AS c
        FROM links
    """).fetchone()["c"]

    accesses = db.execute("""
        SELECT COALESCE(SUM(access_count), 0) AS c
        FROM links
    """).fetchone()["c"]

    if interaction.guild:
        try:
            users = interaction.guild.member_count
        except Exception:
            users = 0

    embed = discord.Embed(
        title=f"📊 {BRAND} Dashboard",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="👥 Server Members",
        value=str(users),
        inline=True,
    )

    embed.add_field(
        name="📢 Scheduled Total",
        value=str(schedules),
        inline=True,
    )

    embed.add_field(
        name="📅 Pending",
        value=str(scheduled),
        inline=True,
    )

    embed.add_field(
        name="✅ Sent",
        value=str(sent),
        inline=True,
    )

    embed.add_field(
        name="🔗 Links",
        value=str(links),
        inline=True,
    )

    embed.add_field(
        name="👁️ Link Access",
        value=str(accesses),
        inline=True,
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# ============================================================
# SLASH COMMAND: /panel
# ============================================================

@bot.tree.command(
    name="panel",
    description="Open the owner panel.",
)
async def panel(interaction: discord.Interaction):

    if not await owner_only(interaction):
        return

    embed = discord.Embed(
        title=f"👑 {BRAND}",
        description=(
            "## OWNER PANEL\n\n"
            "📢 **Announcement**\n"
            "Send an announcement.\n\n"
            "📅 **Schedule**\n"
            "Schedule an announcement for a future time.\n\n"
            "👋 **Welcome**\n"
            "Configure automatic member welcome.\n\n"
            "🔗 **Link / File**\n"
            "Create a saved link for a Discord message/file.\n\n"
            "📊 **Dashboard**\n"
            "View bot statistics."
        ),
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(
        embed=embed,
        view=OwnerPanel(),
        ephemeral=True,
    )


# ============================================================
# SLASH COMMAND: /schedule
# ============================================================

@bot.tree.command(
    name="schedule",
    description="Show scheduled announcements.",
)
async def schedule_command(
    interaction: discord.Interaction
):

    if not await owner_only(interaction):
        return

    rows = db.execute("""
        SELECT *
        FROM schedules
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    if not rows:
        await interaction.response.send_message(
            "📅 No scheduled announcements.",
            ephemeral=True,
        )
        return

    lines = []

    for row in rows:
        lines.append(
            f"`#{row['id']}` • "
            f"`{row['status']}` • "
            f"<t:{int(datetime.fromisoformat(row['send_at']).timestamp())}:F>"
        )

    embed = make_embed(
        "📅 Scheduled Announcements",
        "\n".join(lines),
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# ============================================================
# SLASH COMMAND: /cancel_schedule
# ============================================================

@bot.tree.command(
    name="cancel_schedule",
    description="Cancel a scheduled announcement.",
)
async def cancel_schedule(
    interaction: discord.Interaction,
    schedule_id: int,
):

    if not await owner_only(interaction):
        return

    row = db.execute("""
        SELECT *
        FROM schedules
        WHERE id=?
    """, (schedule_id,)).fetchone()

    if not row:
        await interaction.response.send_message(
            "❌ Schedule not found.",
            ephemeral=True,
        )
        return

    db.execute("""
        UPDATE schedules
        SET status='cancelled'
        WHERE id=?
    """, (schedule_id,))

    db.commit()

    await interaction.response.send_message(
        f"🗑️ Schedule `{schedule_id}` cancelled.",
        ephemeral=True,
    )


# ============================================================
# SLASH COMMAND: /welcome_off
# ============================================================

@bot.tree.command(
    name="welcome_off",
    description="Disable welcome system.",
)
async def welcome_off(
    interaction: discord.Interaction
):

    if not await owner_only(interaction):
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Use this inside a server.",
            ephemeral=True,
        )
        return

    set_setting(
        interaction.guild.id,
        "welcome_enabled",
        "0",
    )

    await interaction.response.send_message(
        "✅ Welcome system disabled.",
        ephemeral=True,
    )


# ============================================================
# SLASH COMMAND: /links
# ============================================================

@bot.tree.command(
    name="links",
    description="Show saved links.",
)
async def links_command(
    interaction: discord.Interaction
):

    if not await owner_only(interaction):
        return

    rows = db.execute("""
        SELECT *
        FROM links
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    if not rows:
        await interaction.response.send_message(
            "🔗 No saved links.",
            ephemeral=True,
        )
        return

    lines = []

    for row in rows:
        lines.append(
            f"`{row['token']}` — "
            f"**{row['name']}** — "
            f"👁️ `{row['access_count']}`"
        )

    embed = make_embed(
        "🔗 Saved Links",
        "\n".join(lines),
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


# ============================================================
# SLASH COMMAND: /delete_link
# ============================================================

@bot.tree.command(
    name="delete_link",
    description="Delete a saved link.",
)
async def delete_link(
    interaction: discord.Interaction,
    token: str,
):

    if not await owner_only(interaction):
        return

    row = db.execute("""
        SELECT *
        FROM links
        WHERE token=?
    """, (token,)).fetchone()

    if not row:
        await interaction.response.send_message(
            "❌ Link not found.",
            ephemeral=True,
        )
        return

    db.execute("""
        DELETE FROM links
        WHERE token=?
    """, (token,))

    db.commit()

    await interaction.response.send_message(
        f"🗑️ Link `{token}` deleted.",
        ephemeral=True,
    )


# ============================================================
# BASIC COMMANDS
# ============================================================

@bot.command()
async def help(ctx):

    if ctx.author.id != OWNER_ID:
        return

    await ctx.send(
        f"👑 **{BRAND}**\n\n"
        "`/panel` — Owner Panel\n"
        "`/schedule` — Scheduled announcements\n"
        "`/cancel_schedule ID` — Cancel schedule\n"
        "`/links` — Saved links\n"
        "`/delete_link TOKEN` — Delete link\n"
        "`/welcome_off` — Disable welcome"
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print("=" * 55)
    print(f"{BRAND}")
    print(f"Logged in as: {bot.user}")
    print(f"Owner ID: {OWNER_ID}")
    print("=" * 55)

    try:
        synced = await bot.tree.sync()
        print(f"Slash commands synced: {len(synced)}")
    except Exception as e:
        print("Slash command sync error:", e)

    if not schedule_worker.is_running():
        schedule_worker.start()


# ============================================================
# RENDER WEB SERVICE HEALTH SERVER
# ============================================================

# Render Web Services expect an HTTP server listening on the PORT
# environment variable. This lightweight server keeps the web service
# healthy while the Discord bot runs in the background.

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"KRUTIK CYBER EXPERT Discord Bot is running."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[WEB] Health server listening on port {port}")
    server.serve_forever()


health_thread = threading.Thread(target=start_health_server, daemon=True)
health_thread.start()


# ============================================================
# RUN
# ============================================================

# Discord can temporarily return HTTP 429 when too many login/API
# requests happen in a short period.  Do NOT immediately restart the
# process in a tight loop, because that can make the temporary block worse.
#
# This runner:
#   - catches Discord HTTP 429 during startup/login
#   - waits before retrying
#   - uses increasing backoff between failed attempts
#   - stops after several consecutive rate-limit failures
#   - lets normal fatal errors exit normally
#
# The hosting manager can then decide when to start the bot again.

MAX_429_RETRIES = 5
INITIAL_429_WAIT = 60
MAX_429_WAIT = 900


def is_discord_429_error(exc: Exception) -> bool:
    return (
        isinstance(exc, discord.HTTPException)
        and getattr(exc, "status", None) == 429
    )


def run_bot_safely():
    consecutive_429 = 0
    wait_seconds = INITIAL_429_WAIT

    while True:
        try:
            print("[BOT] Starting Discord client...")
            bot.run(TOKEN)
            # A clean return means Discord client stopped normally.
            print("[BOT] Discord client stopped normally.")
            return

        except discord.LoginFailure as exc:
            print("[BOT] Discord login failed.")
            print(f"[BOT] {exc}")
            print("[BOT] Check that the Discord bot token is correct.")
            return

        except discord.HTTPException as exc:
            if not is_discord_429_error(exc):
                print("[BOT] Discord HTTP error:")
                print(repr(exc))
                return

            consecutive_429 += 1

            if consecutive_429 > MAX_429_RETRIES:
                print("=" * 60)
                print("[BOT] DISCORD 429 RATE LIMIT")
                print("[BOT] Too many consecutive global rate-limit failures.")
                print("[BOT] Stopping instead of creating a restart loop.")
                print("[BOT] Wait before starting this bot again.")
                print("=" * 60)
                return

            # discord.py may expose retry_after for a rate-limit response.
            retry_after = getattr(exc, "retry_after", None)

            if isinstance(retry_after, (int, float)) and retry_after > 0:
                sleep_seconds = min(float(retry_after), MAX_429_WAIT)
            else:
                sleep_seconds = min(wait_seconds, MAX_429_WAIT)

            print("=" * 60)
            print("[BOT] Discord returned HTTP 429.")
            print(f"[BOT] Rate-limit attempt: {consecutive_429}/{MAX_429_RETRIES}")
            print(f"[BOT] Waiting {sleep_seconds:.0f} seconds before retry.")
            print("[BOT] Immediate restart is intentionally disabled.")
            print("=" * 60)

            # Close any partially-created client state before retrying.
            try:
                if not bot.is_closed():
                    asyncio.run(bot.close())
            except Exception:
                pass

            # Blocking sleep is intentional here: this is outside the
            # Discord event loop and prevents a rapid process restart loop.
            import time
            time.sleep(sleep_seconds)

            wait_seconds = min(wait_seconds * 2, MAX_429_WAIT)

        except KeyboardInterrupt:
            print("[BOT] Stopped by user.")
            return

        except Exception as exc:
            print("[BOT] Fatal bot error:")
            print(repr(exc))
            return


try:
    run_bot_safely()
finally:
    db.close()
