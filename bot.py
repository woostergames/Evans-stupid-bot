import discord
from discord.ext import commands, tasks
import aiohttp
import os
import json
import asyncio
import datetime
import threading
import yt_dlp
from pathlib import Path
from dotenv import load_dotenv
from http.server import HTTPServer, BaseHTTPRequestHandler

load_dotenv()

COOKIE_FILE = "cookies.txt"
YT_COOKIE   = os.getenv("YT_COOKIE")

if YT_COOKIE:
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(YT_COOKIE)
    print("✅ Cookie file created.")
else:
    print("⚠️ No YT_COOKIE found — downloads may fail on age-restricted videos.")

# ════════════════════════════════════════════════
#               CONFIGURATION
# ════════════════════════════════════════════════

GUILD_ID         = 1476431914851369044
DOWNLOAD_CHANNEL = 1476431915870322834
OWNER_ID         = 1424768569710739619
SETTINGS_FILE    = "settings.json"
DOWNLOADS_DIR    = "downloads"
PORT             = int(os.getenv("PORT", 8080))

GITHUB_RELEASE_URL = "https://api.github.com/repos/evanblokender/iis-stupid-menu-revive/releases/latest"

Path(DOWNLOADS_DIR).mkdir(exist_ok=True)

# ════════════════════════════════════════════════
#        RENDER KEEP-ALIVE WEB SERVER
# ════════════════════════════════════════════════

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<title>Bot Status</title></head>
<body><h1>&#x1F916; Discord Bot</h1><p>Bot is running.</p></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[WEB] Health-check server running on port {PORT}")
    server.serve_forever()

# ════════════════════════════════════════════════
#           SETTINGS (Persistent JSON)
# ════════════════════════════════════════════════

def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {
        "hidden": True,
        "prefix": ".",
        "installer_file": None,
        "installer_filename": None,
        "installer_message": "Here is the latest installer!",
        "welcome_channel": None,
        "welcome_message": "👋 Welcome to the server, {mention}! You are member #{count}.",
        "welcome_enabled": False,
        "leave_channel": None,
        "leave_message": "👋 **{name}** has left the server. We now have {count} members.",
        "leave_enabled": False,
        "alert_channel": None,
        "alert_role": None,
        "alert_last_tag": None,
        "keywords": {},
        "sticky": {},
        "modapp_apply_channel": None,
        "modapp_log_channel": None,
        "trial_mod_role": None,
        "admin_role": None,
        "coowner_role": None,
        "sound_channels": [],
    }

def save_settings(data: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)

settings = load_settings()

for _key, _default in [
    ("alert_channel", None), ("alert_role", None), ("alert_last_tag", None),
    ("keywords", {}), ("sticky", {}), ("modapp_apply_channel", None),
    ("modapp_log_channel", None), ("trial_mod_role", None), ("admin_role", None),
    ("coowner_role", None), ("sound_channels", []),
]:
    if _key not in settings:
        settings[_key] = _default
save_settings(settings)

_active_mp3: set = set()
_active_applications: dict = {}

MOD_APP_QUESTIONS = [
    "👋 **Question 1/5 — Why do you want to become a moderator?**",
    "⭐ **Question 2/5 — Why should we pick you over anyone else?**",
    "🛡️ **Question 3/5 — How would you handle a situation where two members are arguing in chat?**",
    "🕐 **Question 4/5 — How many hours per day/week can you dedicate to moderating?**",
    "🎂 **Question 5/5 — How old are you?** *(You must be 13 or older to apply.)*",
]

MIN_ACCOUNT_AGE_DAYS = 5
MIN_AGE = 13

# ════════════════════════════════════════════════
#                BOT SETUP
# ════════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=lambda b, m: settings.get("prefix", "."),
    intents=intents,
    help_command=None
)

# ════════════════════════════════════════════════
#     ROLE PERMISSION TIER HELPERS
# ════════════════════════════════════════════════

def get_user_tier(member: discord.Member) -> int:
    if member.id == OWNER_ID:
        return 3
    if member.guild_permissions.administrator:
        return 2
    coowner_id = settings.get("coowner_role")
    admin_id   = settings.get("admin_role")
    trial_id   = settings.get("trial_mod_role")
    role_ids   = {r.id for r in member.roles}
    if coowner_id and int(coowner_id) in role_ids:
        return 3
    if admin_id and int(admin_id) in role_ids:
        return 2
    if trial_id and int(trial_id) in role_ids:
        return 1
    return 0

def requires_tier(min_tier: int):
    async def predicate(ctx):
        if ctx.guild is None or ctx.guild.id != GUILD_ID:
            return False
        return get_user_tier(ctx.author) >= min_tier
    return commands.check(predicate)

def in_guild():
    async def predicate(ctx):
        return ctx.guild is not None and ctx.guild.id == GUILD_ID
    return commands.check(predicate)

def is_owner():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)

def in_download_channel():
    async def predicate(ctx):
        return ctx.channel.id == DOWNLOAD_CHANNEL
    return commands.check(predicate)

# ════════════════════════════════════════════════
#   YT-DLP HELPERS (thread pool — never blocks loop)
# ════════════════════════════════════════════════

def _ydl_base_opts() -> dict:
    # Use the 'ios' player client — it serves HLS (m3u8) streams which do NOT
    # require PO Tokens, making it the most reliable client on datacenter IPs
    # (Render, AWS, GCP etc.) that YouTube flags as bots.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["ios"],
            }
        },
    }
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


def _fetch_info_sync(url: str) -> dict:
    opts = {**_ydl_base_opts(), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_mp3_sync(url: str, out_template: str) -> None:
    opts = {
        **_ydl_base_opts(),
        # ios client provides HLS audio — bestaudio picks the best available
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def fetch_info(url: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_info_sync, url)


async def download_mp3(url: str, out_template: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _download_mp3_sync, url, out_template)

# ════════════════════════════════════════════════
#   MOD APPLICATION — BUTTON VIEWS
# ════════════════════════════════════════════════

class ApplyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Create Ticket", style=discord.ButtonStyle.green, custom_id="modapp_create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user  = interaction.user
        guild = interaction.guild
        account_age = (datetime.datetime.now(datetime.timezone.utc) - user.created_at).days
        if account_age < MIN_ACCOUNT_AGE_DAYS:
            await interaction.response.send_message(
                f"❌ Your account must be at least **{MIN_ACCOUNT_AGE_DAYS} days old**. Yours is **{account_age}** day(s).",
                ephemeral=True
            )
            return
        if user.id in _active_applications:
            await interaction.response.send_message("⚠️ You already have an application in progress! Check your DMs.", ephemeral=True)
            return
        try:
            dm = await user.create_dm()
            intro = discord.Embed(
                title="📋 Moderator Application",
                description=(
                    "I'll ask you **5 questions** one at a time.\n\n"
                    "**Requirements:** 13+ years old, account 5+ days old ✅\n\n"
                    "Type your answer after each question. Type `cancel` to quit.\n\nLet's begin! 🚀"
                ),
                color=discord.Color.blurple()
            )
            intro.set_footer(text=f"Server: {guild.name}")
            await dm.send(embed=intro)
            q = discord.Embed(description=MOD_APP_QUESTIONS[0], color=discord.Color.blue())
            q.set_footer(text="Question 1 of 5 • Type your answer below")
            await dm.send(embed=q)
            _active_applications[user.id] = {
                "step": 0, "answers": [], "guild_id": guild.id,
                "dm_channel_id": dm.id, "username": str(user), "user_id": user.id,
            }
            await interaction.response.send_message("✅ **Application started!** Check your DMs.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't DM you. Enable DMs from server members and try again.", ephemeral=True)


class DoneButtonView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=600)
        self.user_id = user_id

    @discord.ui.button(label="✅ I'm Done — Submit Application", style=discord.ButtonStyle.green)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your application!", ephemeral=True)
            return
        app = _active_applications.get(self.user_id)
        if not app:
            await interaction.response.send_message("❌ No active application found.", ephemeral=True)
            return
        await interaction.response.send_message("📤 **Submitting your application...** Thank you!")
        await submit_application(self.user_id, app)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your application!", ephemeral=True)
            return
        _active_applications.pop(self.user_id, None)
        await interaction.response.send_message("❌ Application cancelled.")
        self.stop()


async def submit_application(user_id: int, app: dict):
    log_channel_id = settings.get("modapp_log_channel")
    if not log_channel_id:
        return
    guild = bot.get_guild(app["guild_id"])
    if not guild:
        return
    log_channel = guild.get_channel(int(log_channel_id))
    if not log_channel:
        return
    answers     = app.get("answers", [])
    username    = app.get("username", "Unknown")
    user_id_val = app.get("user_id", user_id)
    embed = discord.Embed(title="📋 New Moderator Application", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
    embed.set_author(name=username)
    embed.add_field(name="Applicant", value=f"<@{user_id_val}> (`{username}`)", inline=True)
    embed.add_field(name="User ID",   value=str(user_id_val), inline=True)
    q_labels = ["Why mod?", "Why you?", "Handling conflicts?", "Hours available?", "Age?"]
    for i, (label, answer) in enumerate(zip(q_labels, answers)):
        embed.add_field(name=f"Q{i+1}: {label}", value=answer[:1024] if answer else "*No answer*", inline=False)
    embed.set_footer(text="Review this application and decide accordingly.")
    await log_channel.send(content="📬 **A new moderator application has been submitted!**", embed=embed)
    try:
        user = await bot.fetch_user(user_id_val)
        dm   = await user.create_dm()
        confirm = discord.Embed(
            title="✅ Application Submitted!",
            description="Your application has been **successfully submitted**!\nOur team will review it and get back to you. 🙏",
            color=discord.Color.green()
        )
        await dm.send(embed=confirm)
    except Exception:
        pass
    _active_applications.pop(user_id, None)

# ════════════════════════════════════════════════
#                   EVENTS
# ════════════════════════════════════════════════

@bot.event
async def on_ready():
    print("╔════════════════════════════════════╗")
    print(f"  ✅  Bot online  : {bot.user}")
    print(f"  🏠  Guild lock  : {GUILD_ID}")
    print(f"  📥  DL channel  : {DOWNLOAD_CHANNEL}")
    print(f"  🌐  Web port    : {PORT}")
    print("╚════════════════════════════════════╝")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{settings['prefix']}help"))
    bot.add_view(ApplyButtonView())
    check_github_release.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument. Try `{settings['prefix']}help`", delete_after=8)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found.", delete_after=8)
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}", delete_after=8)
    else:
        await ctx.send(f"❌ Unexpected error: `{error}`")
        print(f"[ERROR] {error}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        uid = message.author.id
        app = _active_applications.get(uid)
        if app is not None:
            await handle_application_dm(message, app)
            return

    if message.guild and message.guild.id == GUILD_ID:
        sound_channels = settings.get("sound_channels", [])
        if message.channel.id in sound_channels:
            has_attachment = len(message.attachments) > 0
            content        = message.content.strip()
            is_url         = content.startswith("http://") or content.startswith("https://")
            if not has_attachment and not is_url:
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    await message.channel.send(
                        f"🔇 {message.author.mention} — This channel is for **audio/file attachments only**.",
                        delete_after=6
                    )
                except Exception:
                    pass
                return

        content_lower = message.content.lower()
        for keyword, reply in settings.get("keywords", {}).items():
            if keyword.lower() in content_lower:
                await message.reply(reply)
                break

    await bot.process_commands(message)

    if message.guild and message.guild.id == GUILD_ID:
        ch_id       = str(message.channel.id)
        sticky_data = settings.get("sticky", {}).get(ch_id)
        if sticky_data and not message.author.bot:
            old_id = sticky_data.get("last_msg_id")
            if old_id:
                try:
                    old_msg = await message.channel.fetch_message(int(old_id))
                    await old_msg.delete()
                except Exception:
                    pass
            new_msg = await message.channel.send(sticky_data["message"])
            settings["sticky"][ch_id]["last_msg_id"] = new_msg.id
            save_settings(settings)


async def handle_application_dm(message: discord.Message, app: dict):
    uid     = message.author.id
    content = message.content.strip()
    if content.lower() == "cancel":
        _active_applications.pop(uid, None)
        await message.channel.send("❌ Your application has been cancelled.")
        return
    step = app["step"]
    if step == len(MOD_APP_QUESTIONS) - 1:
        try:
            age = int(''.join(filter(str.isdigit, content)))
            if age < MIN_AGE:
                _active_applications.pop(uid, None)
                await message.channel.send(f"❌ You must be **{MIN_AGE}+** to apply. Thank you for your interest!")
                return
        except (ValueError, TypeError):
            await message.channel.send("⚠️ Please enter your age as a number (e.g. `16`).")
            return
    app["answers"].append(content)
    app["step"] += 1
    if app["step"] < len(MOD_APP_QUESTIONS):
        q = discord.Embed(description=MOD_APP_QUESTIONS[app["step"]], color=discord.Color.blue())
        q.set_footer(text=f"Question {app['step'] + 1} of {len(MOD_APP_QUESTIONS)} • Type your answer below")
        await message.channel.send(embed=q)
    else:
        summary = discord.Embed(title="✅ All Questions Answered!", description="**Review your answers:**", color=discord.Color.gold())
        q_labels = ["Why mod?", "Why you over others?", "Handling conflicts?", "Hours available?", "Your age?"]
        for i, (label, ans) in enumerate(zip(q_labels, app["answers"])):
            summary.add_field(name=f"Q{i+1}: {label}", value=ans[:512], inline=False)
        summary.set_footer(text="Click 'I'm Done' to submit or 'Cancel' to discard.")
        await message.channel.send(embed=summary, view=DoneButtonView(uid))

# ════════════════════════════════════════════════
#   ROLE APPROVAL COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="approvetrial")
@in_guild()
@is_owner()
async def approvetrial(ctx, role: discord.Role):
    settings["trial_mod_role"] = role.id
    save_settings(settings)
    embed = discord.Embed(title="✅ Trial Mod Role Set", color=discord.Color.teal())
    embed.add_field(name="Role", value=role.mention)
    embed.add_field(name="Tier", value="Trial Mod (Tier 1)")
    embed.add_field(name="Commands", value="`warn` `mute` `unmute` `slowmode` `lock` `unlock` `purge`", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="approveadmin")
@in_guild()
@is_owner()
async def approveadmin(ctx, role: discord.Role):
    settings["admin_role"] = role.id
    save_settings(settings)
    embed = discord.Embed(title="✅ Admin Role Set", color=discord.Color.blue())
    embed.add_field(name="Role", value=role.mention)
    embed.add_field(name="Tier", value="Admin (Tier 2)")
    await ctx.send(embed=embed)

@bot.command(name="approvecoowner")
@in_guild()
@is_owner()
async def approvecoowner(ctx, role: discord.Role):
    settings["coowner_role"] = role.id
    save_settings(settings)
    embed = discord.Embed(title="✅ Co-Owner Role Set", color=discord.Color.gold())
    embed.add_field(name="Role", value=role.mention)
    embed.add_field(name="Tier", value="Co-Owner (Tier 3)")
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#   SOUND CHANNEL COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="soundchannel")
@in_guild()
@requires_tier(2)
async def soundchannel(ctx, channel: discord.TextChannel):
    sc = settings.get("sound_channels", [])
    if channel.id in sc:
        await ctx.send(f"ℹ️ {channel.mention} is already a sound channel.", delete_after=6)
        return
    sc.append(channel.id)
    settings["sound_channels"] = sc
    save_settings(settings)
    embed = discord.Embed(title="🔊 Sound Channel Set", color=discord.Color.purple())
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="Mode", value="Attachments & links only")
    await ctx.send(embed=embed)
    try:
        notice = discord.Embed(title="🔊 Attachments Only", description="This channel is for **audio files and media only**. Text-only messages are removed.", color=discord.Color.purple())
        await channel.send(embed=notice)
    except Exception:
        pass

@bot.command(name="removesoundchannel")
@in_guild()
@requires_tier(2)
async def removesoundchannel(ctx, channel: discord.TextChannel):
    sc = settings.get("sound_channels", [])
    if channel.id not in sc:
        await ctx.send(f"ℹ️ {channel.mention} is not a sound channel.", delete_after=6)
        return
    sc.remove(channel.id)
    settings["sound_channels"] = sc
    save_settings(settings)
    await ctx.send(f"✅ {channel.mention} is no longer a sound channel.")

@bot.command(name="listsoundchannels")
@in_guild()
@requires_tier(2)
async def listsoundchannels(ctx):
    sc = settings.get("sound_channels", [])
    if not sc:
        await ctx.send("ℹ️ No sound channels configured.")
        return
    mentions = [(ctx.guild.get_channel(cid).mention if ctx.guild.get_channel(cid) else f"`{cid}`") for cid in sc]
    embed = discord.Embed(title="🔊 Sound Channels", description="\n".join(mentions), color=discord.Color.purple())
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#   MOD APPLICATION SETUP
# ════════════════════════════════════════════════

@bot.command(name="embed")
@in_guild()
@requires_tier(2)
async def modapp_embed(ctx, apply_channel: discord.TextChannel, log_channel: discord.TextChannel):
    settings["modapp_apply_channel"] = apply_channel.id
    settings["modapp_log_channel"]   = log_channel.id
    save_settings(settings)
    embed = discord.Embed(
        title="🛡️ Moderator Applications",
        description=(
            "Want to help keep the server safe?\n\n**Click below to apply for Moderator!**\n\n"
            "**Requirements:**\n• 13+ years old\n• Account 5+ days old\n• DMs open\n\n"
            "The bot will DM you **5 questions**. Answer honestly — good luck! 🍀"
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Moderator Application System • Click below to begin")
    await apply_channel.send(embed=embed, view=ApplyButtonView())
    await ctx.send(f"✅ Embed sent to {apply_channel.mention}! Logs → {log_channel.mention}.", delete_after=10)
    try:
        await ctx.message.delete()
    except Exception:
        pass

# ════════════════════════════════════════════════
#         WELCOME & LEAVE
# ════════════════════════════════════════════════

def _format_msg(template: str, member: discord.Member) -> str:
    return (template
        .replace("{mention}", member.mention)
        .replace("{name}",    member.display_name)
        .replace("{tag}",     str(member))
        .replace("{count}",   str(member.guild.member_count))
        .replace("{server}",  member.guild.name))

@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID or not settings.get("welcome_enabled"):
        return
    channel_id = settings.get("welcome_channel")
    if not channel_id:
        return
    channel = member.guild.get_channel(int(channel_id))
    if not channel:
        return
    msg   = _format_msg(settings.get("welcome_message", "👋 Welcome {mention}!"), member)
    embed = discord.Embed(title=f"👋 Welcome to {member.guild.name}!", description=msg, color=discord.Color.green())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Member #", value=str(member.guild.member_count), inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    await channel.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID or not settings.get("leave_enabled"):
        return
    channel_id = settings.get("leave_channel")
    if not channel_id:
        return
    channel = member.guild.get_channel(int(channel_id))
    if not channel:
        return
    msg   = _format_msg(settings.get("leave_message", "👋 **{name}** left the server."), member)
    embed = discord.Embed(title="👋 Member Left", description=msg, color=discord.Color.red())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Members Remaining", value=str(member.guild.member_count), inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    await channel.send(embed=embed)

@bot.command(name="setwelcome")
@in_guild()
@requires_tier(2)
async def setwelcome(ctx, channel: discord.TextChannel, *, message: str = None):
    settings["welcome_channel"] = channel.id
    settings["welcome_enabled"] = True
    if message:
        settings["welcome_message"] = message
    save_settings(settings)
    embed = discord.Embed(title="✅ Welcome Channel Set", color=discord.Color.green())
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="Status",  value="Enabled ✅")
    embed.add_field(name="Message", value=settings["welcome_message"], inline=False)
    await ctx.send(embed=embed)

@bot.command(name="setleave")
@in_guild()
@requires_tier(2)
async def setleave(ctx, channel: discord.TextChannel, *, message: str = None):
    settings["leave_channel"] = channel.id
    settings["leave_enabled"] = True
    if message:
        settings["leave_message"] = message
    save_settings(settings)
    embed = discord.Embed(title="✅ Leave Channel Set", color=discord.Color.orange())
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="Status",  value="Enabled ✅")
    embed.add_field(name="Message", value=settings["leave_message"], inline=False)
    await ctx.send(embed=embed)

@bot.command(name="setwelcomemsg")
@in_guild()
@requires_tier(2)
async def setwelcomemsg(ctx, *, message: str):
    settings["welcome_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Welcome message updated:\n> {message}\n\nVariables: `{{mention}}` `{{name}}` `{{tag}}` `{{count}}` `{{server}}`")

@bot.command(name="setleavemsg")
@in_guild()
@requires_tier(2)
async def setleavemsg(ctx, *, message: str):
    settings["leave_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Leave message updated:\n> {message}\n\nVariables: `{{mention}}` `{{name}}` `{{tag}}` `{{count}}` `{{server}}`")

@bot.command(name="togglewelcome")
@in_guild()
@requires_tier(2)
async def togglewelcome(ctx):
    settings["welcome_enabled"] = not settings.get("welcome_enabled", False)
    save_settings(settings)
    await ctx.send(f"Welcome messages: {'✅ **Enabled**' if settings['welcome_enabled'] else '❌ **Disabled**'}")

@bot.command(name="toggleleave")
@in_guild()
@requires_tier(2)
async def toggleleave(ctx):
    settings["leave_enabled"] = not settings.get("leave_enabled", False)
    save_settings(settings)
    await ctx.send(f"Leave messages: {'✅ **Enabled**' if settings['leave_enabled'] else '❌ **Disabled**'}")

@bot.command(name="welcometest")
@in_guild()
@requires_tier(2)
async def welcometest(ctx):
    channel_id = settings.get("welcome_channel")
    if not channel_id:
        await ctx.send("❌ No welcome channel set.")
        return
    channel = ctx.guild.get_channel(int(channel_id))
    if not channel:
        await ctx.send("❌ Welcome channel not found.")
        return
    msg   = _format_msg(settings.get("welcome_message", "👋 Welcome {mention}!"), ctx.author)
    embed = discord.Embed(title=f"👋 Welcome to {ctx.guild.name}!", description=msg, color=discord.Color.green())
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"[TEST] ID: {ctx.author.id}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Test welcome sent to {channel.mention}!", delete_after=5)

@bot.command(name="leavetest")
@in_guild()
@requires_tier(2)
async def leavetest(ctx):
    channel_id = settings.get("leave_channel")
    if not channel_id:
        await ctx.send("❌ No leave channel set.")
        return
    channel = ctx.guild.get_channel(int(channel_id))
    if not channel:
        await ctx.send("❌ Leave channel not found.")
        return
    msg   = _format_msg(settings.get("leave_message", "👋 **{name}** left the server."), ctx.author)
    embed = discord.Embed(title="👋 Member Left", description=msg, color=discord.Color.red())
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"[TEST] ID: {ctx.author.id}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Test leave sent to {channel.mention}!", delete_after=5)

@bot.command(name="welcomestatus")
@in_guild()
@requires_tier(2)
async def welcomestatus(ctx):
    wc_id = settings.get("welcome_channel")
    lc_id = settings.get("leave_channel")
    wc    = ctx.guild.get_channel(int(wc_id)).mention if wc_id else "Not set"
    lc    = ctx.guild.get_channel(int(lc_id)).mention if lc_id else "Not set"
    embed = discord.Embed(title="⚙️ Welcome & Leave Config", color=discord.Color.blurple())
    embed.add_field(name="Welcome Channel", value=f"{wc}\n{'✅ Enabled' if settings.get('welcome_enabled') else '❌ Disabled'}", inline=True)
    embed.add_field(name="Leave Channel",   value=f"{lc}\n{'✅ Enabled' if settings.get('leave_enabled') else '❌ Disabled'}",   inline=True)
    embed.add_field(name="Welcome Message", value=settings.get("welcome_message", "Not set"), inline=False)
    embed.add_field(name="Leave Message",   value=settings.get("leave_message",   "Not set"), inline=False)
    embed.set_footer(text="Variables: {mention} {name} {tag} {count} {server}")
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#    .setfile / .download
# ════════════════════════════════════════════════

@bot.command(name="setfile")
@in_guild()
@is_owner()
@in_download_channel()
async def setfile(ctx, *, custom_message: str = None):
    if not ctx.message.attachments:
        await ctx.send("❌ Attach a file when using `.setfile`.", delete_after=12)
        return
    attachment = ctx.message.attachments[0]
    save_path  = os.path.join(DOWNLOADS_DIR, "installer_" + attachment.filename)
    await attachment.save(save_path)
    settings["installer_file"]     = save_path
    settings["installer_filename"] = attachment.filename
    settings["installer_message"]  = custom_message or "Here is the latest installer!"
    save_settings(settings)
    await ctx.send(f"✅ Installer set!\n📄 **File:** `{attachment.filename}`\n💬 **Message:** {settings['installer_message']}")

@bot.command(name="download", aliases=["dl", "getfile"])
@in_guild()
@in_download_channel()
async def download_file(ctx):
    file_path = settings.get("installer_file")
    if not file_path or not os.path.exists(file_path):
        await ctx.reply("❌ No installer file has been set yet.", delete_after=10)
        return
    message  = settings.get("installer_message", "Here is the latest installer!")
    filename = settings.get("installer_filename", os.path.basename(file_path))
    await ctx.reply(message, file=discord.File(file_path, filename=filename))

# ════════════════════════════════════════════════
#    .mp3  —  yt-dlp built directly into this bot
# ════════════════════════════════════════════════

@bot.command(name="mp3", aliases=["ytmp3", "convert"])
@in_guild()
@in_download_channel()
async def mp3(ctx, *, url: str = None):
    if not url:
        await ctx.send(f"⚠️ Usage: `{settings['prefix']}mp3 <youtube_url>`")
        return

    if ctx.author.id in _active_mp3:
        await ctx.reply("⏳ You already have a conversion in progress. Please wait.", delete_after=8)
        return
    _active_mp3.add(ctx.author.id)

    status   = await ctx.reply("🔍 Looking up video...")
    out_path = None

    try:
        # Step 1: fetch metadata
        try:
            info = await asyncio.wait_for(fetch_info(url), timeout=30)
        except asyncio.TimeoutError:
            await status.edit(content="❌ Timed out fetching video info. Check the URL and try again.")
            return
        except Exception as e:
            await status.edit(content=f"❌ Could not fetch video info.\n`{e}`")
            return

        title    = info.get("title",    "audio")
        duration = info.get("duration", 0) or 0
        uploader = info.get("uploader", "Unknown")

        if duration > 1500:
            await status.edit(content=f"❌ Video too long ({duration // 60} min). Max is **25 minutes**.")
            return

        safe_name    = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:80] or "audio"
        out_template = os.path.join(DOWNLOADS_DIR, f"{safe_name}.%(ext)s")
        out_path     = os.path.join(DOWNLOADS_DIR, f"{safe_name}.mp3")

        if os.path.exists(out_path):
            os.remove(out_path)

        await status.edit(content=f"⏳ Converting **{title}** to MP3...")

        # Step 2: download + convert
        try:
            await asyncio.wait_for(download_mp3(url, out_template), timeout=300)
        except asyncio.TimeoutError:
            await status.edit(content="❌ Conversion timed out (5 min limit).")
            return
        except Exception as e:
            await status.edit(content=f"❌ Download/conversion failed.\n`{e}`")
            return

        if not os.path.exists(out_path):
            await status.edit(content="❌ MP3 not found after conversion. Is ffmpeg installed in the Docker image?")
            return

        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        if size_mb > 25:
            await status.edit(content=f"❌ File too large ({size_mb:.1f} MB). Discord limit is 25 MB.")
            return

        duration_fmt = str(datetime.timedelta(seconds=int(duration))) if duration else "Unknown"
        await status.edit(content=f"📤 Uploading ({size_mb:.1f} MB)...")

        embed = discord.Embed(title="🎵 YouTube → MP3", color=discord.Color.red())
        embed.add_field(name="Title",    value=title,               inline=True)
        embed.add_field(name="Uploader", value=uploader,            inline=True)
        embed.add_field(name="Duration", value=duration_fmt,        inline=True)
        embed.add_field(name="Size",     value=f"{size_mb:.1f} MB", inline=True)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)

        try:
            await ctx.reply(embed=embed, file=discord.File(out_path, filename=f"{safe_name}.mp3"))
            await status.delete()
        except discord.HTTPException as e:
            await status.edit(content=f"❌ Upload failed: `{e}`")

    finally:
        if out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass
        _active_mp3.discard(ctx.author.id)

# ════════════════════════════════════════════════
#   GITHUB RELEASE ALERT
# ════════════════════════════════════════════════

@bot.command(name="setalert")
@in_guild()
@requires_tier(2)
async def setalert(ctx, channel: discord.TextChannel, role: discord.Role):
    settings["alert_channel"] = channel.id
    settings["alert_role"]    = role.id
    save_settings(settings)
    embed = discord.Embed(title="✅ Release Alert Configured", color=discord.Color.blurple())
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="Role",    value=role.mention)
    embed.set_footer(text="Bot checks GitHub every 5 minutes.")
    await ctx.send(embed=embed)

@tasks.loop(minutes=5)
async def check_github_release():
    alert_channel_id = settings.get("alert_channel")
    alert_role_id    = settings.get("alert_role")
    if not alert_channel_id or not alert_role_id:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GITHUB_RELEASE_URL, headers={"Accept": "application/vnd.github+json"}) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        tag = data.get("tag_name")
        if not tag or tag == settings.get("alert_last_tag"):
            return
        settings["alert_last_tag"] = tag
        save_settings(settings)
        guild   = bot.get_guild(GUILD_ID)
        channel = guild.get_channel(int(alert_channel_id))
        role    = guild.get_role(int(alert_role_id))
        if not channel or not role:
            return
        release_url  = data.get("html_url", "https://github.com/evanblokender/iis-stupid-menu-revive/releases")
        release_body = data.get("body", "").strip()[:400] or "No release notes provided."
        embed = discord.Embed(
            title=f"🆕 Menu updated to version {tag}",
            url=release_url, description=release_body,
            color=discord.Color.green(), timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Version",  value=f"`{tag}`")
        embed.add_field(name="Download", value=f"[GitHub Releases]({release_url})")
        embed.set_footer(text="Run the patcher to update it!")
        await channel.send(content=f"{role.mention}", embed=embed)
    except Exception as e:
        print(f"[ALERT] GitHub check error: {e}")

@check_github_release.before_loop
async def before_check():
    await bot.wait_until_ready()

# ════════════════════════════════════════════════
#   KEYWORD AUTO-REPLY
# ════════════════════════════════════════════════

@bot.command(name="addkeyword")
@in_guild()
@requires_tier(2)
async def addkeyword(ctx, keyword: str, *, reply: str):
    keywords = settings.get("keywords", {})
    keywords[keyword.lower()] = reply
    settings["keywords"] = keywords
    save_settings(settings)
    embed = discord.Embed(title="✅ Keyword Added", color=discord.Color.green())
    embed.add_field(name="Keyword", value=f"`{keyword.lower()}`")
    embed.add_field(name="Reply",   value=reply, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="removekeyword")
@in_guild()
@requires_tier(2)
async def removekeyword(ctx, keyword: str):
    keywords = settings.get("keywords", {})
    key = keyword.lower()
    if key not in keywords:
        await ctx.send(f"❌ Keyword `{key}` not found.", delete_after=8)
        return
    del keywords[key]
    settings["keywords"] = keywords
    save_settings(settings)
    await ctx.send(f"✅ Keyword `{key}` removed.")

@bot.command(name="listkeywords")
@in_guild()
@requires_tier(2)
async def listkeywords(ctx):
    keywords = settings.get("keywords", {})
    if not keywords:
        await ctx.send("ℹ️ No keywords set.")
        return
    embed = discord.Embed(title="📋 Keyword Auto-Replies", color=discord.Color.blurple())
    for kw, reply in keywords.items():
        embed.add_field(name=f"`{kw}`", value=reply[:200], inline=False)
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#   STICKY MESSAGE
# ════════════════════════════════════════════════

@bot.command(name="stick")
@in_guild()
@requires_tier(2)
async def stick(ctx, channel: discord.TextChannel, *, message: str):
    ch_id    = str(channel.id)
    existing = settings.get("sticky", {}).get(ch_id)
    if existing and existing.get("last_msg_id"):
        try:
            old_msg = await channel.fetch_message(int(existing["last_msg_id"]))
            await old_msg.delete()
        except Exception:
            pass
    sent = await channel.send(f"📌 {message}")
    settings.setdefault("sticky", {})[ch_id] = {"message": f"📌 {message}", "last_msg_id": sent.id}
    save_settings(settings)
    embed = discord.Embed(title="📌 Sticky Set", color=discord.Color.gold())
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="Message", value=message[:200], inline=False)
    await ctx.send(embed=embed, delete_after=8)

@bot.command(name="unstick")
@in_guild()
@requires_tier(2)
async def unstick(ctx, channel: discord.TextChannel):
    ch_id  = str(channel.id)
    sticky = settings.get("sticky", {})
    if ch_id not in sticky:
        await ctx.send(f"ℹ️ No sticky in {channel.mention}.", delete_after=8)
        return
    last_id = sticky[ch_id].get("last_msg_id")
    if last_id:
        try:
            old_msg = await channel.fetch_message(int(last_id))
            await old_msg.delete()
        except Exception:
            pass
    del sticky[ch_id]
    settings["sticky"] = sticky
    save_settings(settings)
    await ctx.send(f"✅ Sticky removed from {channel.mention}.")

# ════════════════════════════════════════════════
#   TRIAL MOD COMMANDS  (tier 1+)
# ════════════════════════════════════════════════

@bot.command(name="warn")
@in_guild()
@requires_tier(1)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    embed = discord.Embed(title="⚠️ You have been warned", description=f"**Server:** {ctx.guild.name}\n**Reason:** {reason}", color=discord.Color.yellow())
    try:
        await member.send(embed=embed)
        await ctx.send(f"✅ Warning sent to **{member}**.")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Warning logged, but couldn't DM **{member}**.")

@bot.command(name="mute")
@in_guild()
@requires_tier(1)
async def mute(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason"):
    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.greyple())
    embed.add_field(name="User", value=str(member))
    embed.add_field(name="Duration", value=f"{minutes} min")
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="unmute")
@in_guild()
@requires_tier(1)
async def unmute(ctx, member: discord.Member):
    await member.timeout(None)
    await ctx.send(f"🔊 **{member}** has been unmuted.")

@bot.command(name="slowmode")
@in_guild()
@requires_tier(1)
async def slowmode(ctx, seconds: int = 0):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send("✅ Slowmode **disabled**." if seconds == 0 else f"✅ Slowmode set to **{seconds}s**.")

@bot.command(name="lock")
@in_guild()
@requires_tier(1)
async def lock(ctx):
    ow = ctx.channel.overwrites_for(ctx.guild.default_role)
    ow.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.send("🔒 Channel **locked**.")

@bot.command(name="unlock")
@in_guild()
@requires_tier(1)
async def unlock(ctx):
    ow = ctx.channel.overwrites_for(ctx.guild.default_role)
    ow.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.send("🔓 Channel **unlocked**.")

@bot.command(name="purge")
@in_guild()
@requires_tier(1)
async def purge(ctx, amount: int):
    if not 1 <= amount <= 200:
        await ctx.send("⚠️ Amount must be between 1 and 200.", delete_after=6)
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    m = await ctx.send(f"🗑️ Deleted **{len(deleted)-1}** messages.")
    await asyncio.sleep(4)
    try:
        await m.delete()
    except Exception:
        pass

# ════════════════════════════════════════════════
#   ADMIN COMMANDS  (tier 2+)
# ════════════════════════════════════════════════

@bot.command(name="kick")
@in_guild()
@requires_tier(2)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if member.top_role >= ctx.author.top_role and ctx.author.id != OWNER_ID:
        await ctx.send("❌ You can't kick someone with an equal or higher role.")
        return
    await member.kick(reason=reason)
    embed = discord.Embed(title="👢 Member Kicked", color=discord.Color.orange())
    embed.add_field(name="User", value=str(member))
    embed.add_field(name="Mod",  value=str(ctx.author))
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="ban")
@in_guild()
@requires_tier(2)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if member.top_role >= ctx.author.top_role and ctx.author.id != OWNER_ID:
        await ctx.send("❌ You can't ban someone with an equal or higher role.")
        return
    await member.ban(reason=reason, delete_message_days=0)
    embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.red())
    embed.add_field(name="User", value=str(member))
    embed.add_field(name="Mod",  value=str(ctx.author))
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="unban")
@in_guild()
@requires_tier(2)
async def unban(ctx, *, user_input: str):
    try:
        uid  = int(user_input)
        user = await bot.fetch_user(uid)
        await ctx.guild.unban(user)
        await ctx.send(f"✅ Unbanned **{user}**.")
        return
    except (ValueError, discord.NotFound):
        pass
    banned = [entry async for entry in ctx.guild.bans()]
    for entry in banned:
        if str(entry.user) == user_input or entry.user.name == user_input:
            await ctx.guild.unban(entry.user)
            await ctx.send(f"✅ Unbanned **{entry.user}**.")
            return
    await ctx.send("❌ User not found in the ban list.")

@bot.command(name="announce")
@in_guild()
@requires_tier(2)
async def announce(ctx, channel: discord.TextChannel, *, message: str):
    embed = discord.Embed(description=message, color=discord.Color.gold())
    embed.set_footer(text=f"Announcement by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await channel.send(embed=embed)
    await ctx.send(f"✅ Announcement sent to {channel.mention}.", delete_after=5)
    try:
        await ctx.message.delete()
    except Exception:
        pass

# ════════════════════════════════════════════════
#           OWNER CONFIG COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="setprefix")
@in_guild()
@requires_tier(3)
async def setprefix(ctx, prefix: str):
    settings["prefix"] = prefix
    save_settings(settings)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{prefix}help"))
    await ctx.send(f"✅ Prefix changed to `{prefix}`")

@bot.command(name="sethidden")
@in_guild()
@is_owner()
async def sethidden(ctx, value: str):
    if value.lower() in ("true", "yes", "1", "on"):
        settings["hidden"] = True
        save_settings(settings)
        await ctx.send("✅ Admin commands now **hidden** from `.help`.")
    elif value.lower() in ("false", "no", "0", "off"):
        settings["hidden"] = False
        save_settings(settings)
        await ctx.send("✅ Admin commands now **visible** in `.help`.")
    else:
        await ctx.send("⚠️ Use `true` or `false`.")

@bot.command(name="setinstallermsg")
@in_guild()
@requires_tier(3)
async def setinstallermsg(ctx, *, message: str):
    settings["installer_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Installer message updated:\n> {message}")

# ════════════════════════════════════════════════
#           UTILITY / INFO COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="ping")
@in_guild()
async def ping(ctx):
    await ctx.send(f"🏓 Pong! `{round(bot.latency * 1000)}ms`")

@bot.command(name="serverinfo")
@in_guild()
async def serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=f"📊 {g.name}", color=discord.Color.blurple())
    embed.add_field(name="Members",  value=g.member_count)
    embed.add_field(name="Channels", value=len(g.channels))
    embed.add_field(name="Roles",    value=len(g.roles))
    embed.add_field(name="Owner",    value=str(g.owner))
    embed.add_field(name="Boosts",   value=g.premium_subscription_count)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"))
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await ctx.send(embed=embed)

@bot.command(name="userinfo")
@in_guild()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles  = [r.mention for r in member.roles[1:]]
    embed  = discord.Embed(title=str(member), color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",       value=member.id)
    embed.add_field(name="Bot?",     value=member.bot)
    embed.add_field(name="Top Role", value=member.top_role.mention)
    embed.add_field(name="Joined",   value=member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "N/A")
    embed.add_field(name="Created",  value=member.created_at.strftime("%Y-%m-%d"))
    if roles:
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:10]), inline=False)
    await ctx.send(embed=embed)

@bot.command(name="avatar")
@in_guild()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed  = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#   TIERED HELP COMMAND
# ════════════════════════════════════════════════

@bot.command(name="help")
@in_guild()
async def help_cmd(ctx):
    p    = settings.get("prefix", ".")
    tier = get_user_tier(ctx.author)
    embed = discord.Embed(
        title="📖 Bot Help",
        description=f"**Prefix:** `{p}`  |  Commands available to your role.",
        color=discord.Color.green()
    )
    installer_set = "✅ Set" if settings.get("installer_file") and os.path.exists(settings["installer_file"]) else "❌ Not set"
    embed.add_field(name="📦 Installer", value=f"`{p}download` *(download channel only)* — {installer_set}", inline=False)
    embed.add_field(name="🎵 YouTube → MP3", value=f"`{p}mp3 <url>` · `{p}ytmp3` · `{p}convert` *(download channel only, max 25 min)*", inline=False)
    embed.add_field(name="ℹ️ Info", value=f"`{p}ping` · `{p}serverinfo` · `{p}userinfo` · `{p}avatar`", inline=False)
    if tier >= 1:
        embed.add_field(name="🔰 Trial Mod", value=f"`{p}warn` `{p}mute` `{p}unmute` `{p}slowmode` `{p}lock` `{p}unlock` `{p}purge`", inline=False)
    if tier >= 2:
        embed.add_field(name="🔧 Admin", value=(
            f"`{p}kick` `{p}ban` `{p}unban` `{p}announce` `{p}setalert`\n"
            f"`{p}addkeyword` `{p}removekeyword` `{p}listkeywords`\n"
            f"`{p}stick` `{p}unstick` `{p}setwelcome` `{p}setleave`\n"
            f"`{p}togglewelcome` `{p}toggleleave` `{p}welcometest` `{p}leavetest` `{p}welcomestatus`\n"
            f"`{p}embed` `{p}soundchannel` `{p}removesoundchannel` `{p}listsoundchannels`"
        ), inline=False)
    if tier >= 3:
        embed.add_field(name="👑 Co-Owner / Owner", value=(
            f"`{p}setprefix` `{p}setinstallermsg` `{p}approvetrial` `{p}approveadmin` `{p}approvecoowner`\n"
            f"`{p}setfile` `{p}sethidden`"
        ), inline=False)
    tier_names = {0: "Member", 1: "Trial Mod", 2: "Admin", 3: "Co-Owner/Owner"}
    embed.set_footer(text=f"Your tier: {tier_names[tier]} (Tier {tier})")
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#                     RUN
# ════════════════════════════════════════════════

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌  DISCORD_TOKEN is not set!")
    exit(1)

web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

bot.run(TOKEN)
