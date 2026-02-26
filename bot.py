import discord
from discord.ext import commands
import yt_dlp
import os
import json
import asyncio
import datetime
import threading
from pathlib import Path
from dotenv import load_dotenv
from http.server import HTTPServer, BaseHTTPRequestHandler

load_dotenv()

# ════════════════════════════════════════════════
#               CONFIGURATION
# ════════════════════════════════════════════════

GUILD_ID         = 1476431914851369044
DOWNLOAD_CHANNEL = 1476431915870322834
OWNER_ID         = 1424768569710739619
SETTINGS_FILE    = "settings.json"
DOWNLOADS_DIR    = "downloads"
PORT             = int(os.getenv("PORT", 8080))

Path(DOWNLOADS_DIR).mkdir(exist_ok=True)

# ════════════════════════════════════════════════
#        RENDER KEEP-ALIVE WEB SERVER
#   Render requires a service to bind to a port.
#   This tiny HTTP server serves / so Render knows
#   the bot is alive and running.
# ════════════════════════════════════════════════

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Bot Status</title>
</head>
<body>
  <h1>🤖 Discord Bot</h1>
  <p>Bot is running.</p>
</body>
</html>
""".encode("utf-8")

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
        "leave_enabled": False
    }

def save_settings(data: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)

settings = load_settings()

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
#               CHECK DECORATORS
# ════════════════════════════════════════════════

def in_guild():
    async def predicate(ctx):
        return ctx.guild is not None and ctx.guild.id == GUILD_ID
    return commands.check(predicate)

def is_owner():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)

def is_admin_or_owner():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID:
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        return False
    return commands.check(predicate)

def in_download_channel():
    async def predicate(ctx):
        return ctx.channel.id == DOWNLOAD_CHANNEL
    return commands.check(predicate)

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
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name=f"{settings['prefix']}help"
    ))

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

# ════════════════════════════════════════════════
#         WELCOME & LEAVE EVENTS
# ════════════════════════════════════════════════

@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    if not settings.get("welcome_enabled"):
        return
    channel_id = settings.get("welcome_channel")
    if not channel_id:
        return
    channel = member.guild.get_channel(int(channel_id))
    if not channel:
        return

    msg = settings.get("welcome_message", "👋 Welcome {mention}!")
    msg = msg.replace("{mention}", member.mention)
    msg = msg.replace("{name}",    member.display_name)
    msg = msg.replace("{tag}",     str(member))
    msg = msg.replace("{count}",   str(member.guild.member_count))
    msg = msg.replace("{server}",  member.guild.name)

    embed = discord.Embed(
        title=f"👋 Welcome to {member.guild.name}!",
        description=msg,
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account Created",
                    value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Member #",
                    value=str(member.guild.member_count), inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    await channel.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    if not settings.get("leave_enabled"):
        return
    channel_id = settings.get("leave_channel")
    if not channel_id:
        return
    channel = member.guild.get_channel(int(channel_id))
    if not channel:
        return

    msg = settings.get("leave_message", "👋 **{name}** left the server.")
    msg = msg.replace("{mention}", member.mention)
    msg = msg.replace("{name}",    member.display_name)
    msg = msg.replace("{tag}",     str(member))
    msg = msg.replace("{count}",   str(member.guild.member_count))
    msg = msg.replace("{server}",  member.guild.name)

    embed = discord.Embed(
        title="👋 Member Left",
        description=msg,
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Members Remaining",
                    value=str(member.guild.member_count), inline=True)
    embed.set_footer(text=f"ID: {member.id}")
    await channel.send(embed=embed)

# ════════════════════════════════════════════════
#   WELCOME / LEAVE SETUP COMMANDS  (admin/owner)
# ════════════════════════════════════════════════

@bot.command(name="setwelcome")
@in_guild()
@is_admin_or_owner()
async def setwelcome(ctx, channel: discord.TextChannel, *, message: str = None):
    """
    [ADMIN] Set the welcome channel (and optional custom message).

    Usage:
      .setwelcome #welcome
      .setwelcome #welcome Hey {mention}, welcome to {server}! You are member #{count}.

    Variables: {mention} {name} {tag} {count} {server}
    """
    settings["welcome_channel"] = channel.id
    settings["welcome_enabled"] = True
    if message:
        settings["welcome_message"] = message
    save_settings(settings)

    embed = discord.Embed(title="✅ Welcome Channel Set", color=discord.Color.green())
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Status",  value="Enabled ✅",    inline=True)
    embed.add_field(name="Message", value=settings["welcome_message"], inline=False)
    embed.set_footer(text="Use .togglewelcome to enable/disable • .setwelcomemsg to change message only")
    await ctx.send(embed=embed)

@bot.command(name="setleave")
@in_guild()
@is_admin_or_owner()
async def setleave(ctx, channel: discord.TextChannel, *, message: str = None):
    """
    [ADMIN] Set the leave channel (and optional custom message).

    Usage:
      .setleave #goodbye
      .setleave #goodbye {name} has left us. We now have {count} members.

    Variables: {mention} {name} {tag} {count} {server}
    """
    settings["leave_channel"] = channel.id
    settings["leave_enabled"] = True
    if message:
        settings["leave_message"] = message
    save_settings(settings)

    embed = discord.Embed(title="✅ Leave Channel Set", color=discord.Color.orange())
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Status",  value="Enabled ✅",    inline=True)
    embed.add_field(name="Message", value=settings["leave_message"], inline=False)
    embed.set_footer(text="Use .toggleleave to enable/disable • .setleavemsg to change message only")
    await ctx.send(embed=embed)

@bot.command(name="setwelcomemsg")
@in_guild()
@is_admin_or_owner()
async def setwelcomemsg(ctx, *, message: str):
    """[ADMIN] Change the welcome message text only (keep existing channel)."""
    settings["welcome_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Welcome message updated:\n> {message}\n\nVariables: `{{mention}}` `{{name}}` `{{tag}}` `{{count}}` `{{server}}`")

@bot.command(name="setleavemsg")
@in_guild()
@is_admin_or_owner()
async def setleavemsg(ctx, *, message: str):
    """[ADMIN] Change the leave message text only (keep existing channel)."""
    settings["leave_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Leave message updated:\n> {message}\n\nVariables: `{{mention}}` `{{name}}` `{{tag}}` `{{count}}` `{{server}}`")

@bot.command(name="togglewelcome")
@in_guild()
@is_admin_or_owner()
async def togglewelcome(ctx):
    """[ADMIN] Toggle welcome messages on or off."""
    settings["welcome_enabled"] = not settings.get("welcome_enabled", False)
    save_settings(settings)
    state = "✅ **Enabled**" if settings["welcome_enabled"] else "❌ **Disabled**"
    await ctx.send(f"Welcome messages: {state}")

@bot.command(name="toggleleave")
@in_guild()
@is_admin_or_owner()
async def toggleleave(ctx):
    """[ADMIN] Toggle leave messages on or off."""
    settings["leave_enabled"] = not settings.get("leave_enabled", False)
    save_settings(settings)
    state = "✅ **Enabled**" if settings["leave_enabled"] else "❌ **Disabled**"
    await ctx.send(f"Leave messages: {state}")

@bot.command(name="welcometest")
@in_guild()
@is_admin_or_owner()
async def welcometest(ctx):
    """[ADMIN] Test the welcome message using your own account."""
    channel_id = settings.get("welcome_channel")
    if not channel_id:
        await ctx.send("❌ No welcome channel set. Use `.setwelcome #channel` first.")
        return
    channel = ctx.guild.get_channel(int(channel_id))
    if not channel:
        await ctx.send("❌ Welcome channel not found. Set it again with `.setwelcome`.")
        return

    # Temporarily trigger the welcome for the command author
    msg = settings.get("welcome_message", "👋 Welcome {mention}!")
    msg = msg.replace("{mention}", ctx.author.mention)
    msg = msg.replace("{name}",    ctx.author.display_name)
    msg = msg.replace("{tag}",     str(ctx.author))
    msg = msg.replace("{count}",   str(ctx.guild.member_count))
    msg = msg.replace("{server}",  ctx.guild.name)

    embed = discord.Embed(
        title=f"👋 Welcome to {ctx.guild.name}!",
        description=msg,
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="Account Created",
                    value=ctx.author.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Member #", value=str(ctx.guild.member_count), inline=True)
    embed.set_footer(text=f"[TEST] ID: {ctx.author.id}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Test welcome sent to {channel.mention}!", delete_after=5)

@bot.command(name="leavetest")
@in_guild()
@is_admin_or_owner()
async def leavetest(ctx):
    """[ADMIN] Test the leave message using your own account."""
    channel_id = settings.get("leave_channel")
    if not channel_id:
        await ctx.send("❌ No leave channel set. Use `.setleave #channel` first.")
        return
    channel = ctx.guild.get_channel(int(channel_id))
    if not channel:
        await ctx.send("❌ Leave channel not found. Set it again with `.setleave`.")
        return

    msg = settings.get("leave_message", "👋 **{name}** left the server.")
    msg = msg.replace("{mention}", ctx.author.mention)
    msg = msg.replace("{name}",    ctx.author.display_name)
    msg = msg.replace("{tag}",     str(ctx.author))
    msg = msg.replace("{count}",   str(ctx.guild.member_count))
    msg = msg.replace("{server}",  ctx.guild.name)

    embed = discord.Embed(title="👋 Member Left", description=msg, color=discord.Color.red())
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="Members Remaining", value=str(ctx.guild.member_count), inline=True)
    embed.set_footer(text=f"[TEST] ID: {ctx.author.id}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Test leave sent to {channel.mention}!", delete_after=5)

@bot.command(name="welcomestatus")
@in_guild()
@is_admin_or_owner()
async def welcomestatus(ctx):
    """[ADMIN] View current welcome & leave configuration."""
    wc_id = settings.get("welcome_channel")
    lc_id = settings.get("leave_channel")

    wc = ctx.guild.get_channel(int(wc_id)).mention if wc_id else "Not set"
    lc = ctx.guild.get_channel(int(lc_id)).mention if lc_id else "Not set"

    embed = discord.Embed(title="⚙️ Welcome & Leave Config", color=discord.Color.blurple())
    embed.add_field(name="Welcome Channel",
                    value=f"{wc}\n{'✅ Enabled' if settings.get('welcome_enabled') else '❌ Disabled'}",
                    inline=True)
    embed.add_field(name="Leave Channel",
                    value=f"{lc}\n{'✅ Enabled' if settings.get('leave_enabled') else '❌ Disabled'}",
                    inline=True)
    embed.add_field(name="Welcome Message",
                    value=settings.get("welcome_message", "Not set"), inline=False)
    embed.add_field(name="Leave Message",
                    value=settings.get("leave_message", "Not set"), inline=False)
    embed.set_footer(text="Variables: {mention} {name} {tag} {count} {server}")
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#    .setfile  (OWNER ONLY — sets installer)
# ════════════════════════════════════════════════

@bot.command(name="setfile")
@in_guild()
@is_owner()
@in_download_channel()
async def setfile(ctx, *, custom_message: str = None):
    """[OWNER] Attach a file to set it as the installer. Optionally add a custom message."""
    if not ctx.message.attachments:
        await ctx.send(
            "❌ Please **attach a file** when using `.setfile`.\n"
            "Example: Upload your installer and type `.setfile Here is v2.0!`",
            delete_after=12
        )
        return

    attachment = ctx.message.attachments[0]
    save_path = os.path.join(DOWNLOADS_DIR, "installer_" + attachment.filename)
    await attachment.save(save_path)

    settings["installer_file"]     = save_path
    settings["installer_filename"] = attachment.filename
    settings["installer_message"]  = custom_message or "Here is the latest installer!"
    save_settings(settings)

    await ctx.send(
        f"✅ Installer file set!\n"
        f"📄 **File:** `{attachment.filename}`\n"
        f"💬 **Message:** {settings['installer_message']}\n\n"
        f"Anyone who runs `.download` in this channel will receive it."
    )

# ════════════════════════════════════════════════
#    .download  (channel-locked — sends file)
# ════════════════════════════════════════════════

@bot.command(name="download", aliases=["dl", "getfile"])
@in_guild()
@in_download_channel()
async def download_file(ctx):
    """Get the installer file. Only works in the designated channel."""
    file_path = settings.get("installer_file")

    if not file_path or not os.path.exists(file_path):
        await ctx.reply(
            "❌ No installer file has been set yet.\n"
            "An admin must use `.setfile` (with a file attached) to set one.",
            delete_after=10
        )
        return

    message  = settings.get("installer_message", "Here is the latest installer!")
    filename = settings.get("installer_filename", os.path.basename(file_path))
    await ctx.reply(message, file=discord.File(file_path, filename=filename))

# ════════════════════════════════════════════════
#    .mp3  (channel-locked — YouTube to MP3)
# ════════════════════════════════════════════════

def _run_ydl(opts: dict, url: str):
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

def _get_info(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

@bot.command(name="mp3", aliases=["ytmp3", "convert"])
@in_guild()
@in_download_channel()
async def mp3(ctx, *, url: str = None):
    """Convert a YouTube video to MP3. Only works in the designated channel."""
    if not url:
        await ctx.send(f"⚠️ Usage: `{settings['prefix']}mp3 <youtube_url>`")
        return

    status = await ctx.reply("🔍 Looking up video...")

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: _get_info(url))
    except Exception as e:
        await status.edit(content=f"❌ Could not find video.\n`{e}`")
        return

    title    = info.get("title", "audio")
    duration = info.get("duration", 0)
    uploader = info.get("uploader", "Unknown")

    if duration and duration > 1500:
        await status.edit(content=f"❌ Video too long ({duration//60} min). Max is **25 minutes**.")
        return

    safe_name = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:80]
    out_path  = os.path.join(DOWNLOADS_DIR, f"{safe_name}.mp3")

    await status.edit(content=f"⏳ Converting **{title}** to MP3...")

    ydl_opts = {
       'cookiefile': 'cookies.txt',
        "format": "bestaudio/best",
        "outtmpl": os.path.join(DOWNLOADS_DIR, f"{safe_name}.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
    except Exception as e:
        await status.edit(content=f"❌ Conversion failed.\n`{e}`")
        return

    if not os.path.exists(out_path):
        matches = list(Path(DOWNLOADS_DIR).glob(f"{safe_name}*.mp3"))
        out_path = str(matches[0]) if matches else None

    if not out_path or not os.path.exists(out_path):
        await status.edit(content="❌ MP3 not found after conversion. Is FFmpeg installed?")
        return

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    if size_mb > 25:
        os.remove(out_path)
        await status.edit(content=f"❌ File too large ({size_mb:.1f} MB). Discord limit is 25 MB.")
        return

    duration_fmt = str(datetime.timedelta(seconds=duration)) if duration else "Unknown"
    await status.edit(content=f"📤 Uploading ({size_mb:.1f} MB)...")

    embed = discord.Embed(title="🎵 YouTube → MP3", color=discord.Color.red())
    embed.add_field(name="Title",    value=title,             inline=True)
    embed.add_field(name="Uploader", value=uploader,          inline=True)
    embed.add_field(name="Duration", value=duration_fmt,      inline=True)
    embed.add_field(name="Size",     value=f"{size_mb:.1f} MB", inline=True)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)

    try:
        await ctx.reply(embed=embed, file=discord.File(out_path, filename=f"{safe_name}.mp3"))
        await status.delete()
    except discord.HTTPException as e:
        await status.edit(content=f"❌ Upload failed: `{e}`")
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

# ════════════════════════════════════════════════
#              ADMIN COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="purge")
@in_guild()
@is_admin_or_owner()
async def purge(ctx, amount: int):
    """[ADMIN] Delete N messages (max 200)."""
    if not 1 <= amount <= 200:
        await ctx.send("⚠️ Amount must be between 1 and 200.", delete_after=6)
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    m = await ctx.send(f"🗑️ Deleted **{len(deleted)-1}** messages.")
    await asyncio.sleep(4)
    try: await m.delete()
    except: pass

@bot.command(name="kick")
@in_guild()
@is_admin_or_owner()
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """[ADMIN] Kick a member."""
    if member.top_role >= ctx.author.top_role and ctx.author.id != OWNER_ID:
        await ctx.send("❌ You can't kick someone with an equal or higher role.")
        return
    await member.kick(reason=reason)
    embed = discord.Embed(title="👢 Member Kicked", color=discord.Color.orange())
    embed.add_field(name="User",   value=str(member),     inline=True)
    embed.add_field(name="Mod",    value=str(ctx.author), inline=True)
    embed.add_field(name="Reason", value=reason,          inline=False)
    await ctx.send(embed=embed)

@bot.command(name="ban")
@in_guild()
@is_admin_or_owner()
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """[ADMIN] Ban a member."""
    if member.top_role >= ctx.author.top_role and ctx.author.id != OWNER_ID:
        await ctx.send("❌ You can't ban someone with an equal or higher role.")
        return
    await member.ban(reason=reason, delete_message_days=0)
    embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.red())
    embed.add_field(name="User",   value=str(member),     inline=True)
    embed.add_field(name="Mod",    value=str(ctx.author), inline=True)
    embed.add_field(name="Reason", value=reason,          inline=False)
    await ctx.send(embed=embed)

@bot.command(name="unban")
@in_guild()
@is_admin_or_owner()
async def unban(ctx, *, user_input: str):
    """[ADMIN] Unban by user ID or Name#0000."""
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

@bot.command(name="mute")
@in_guild()
@is_admin_or_owner()
async def mute(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason"):
    """[ADMIN] Timeout a member. Default 10 minutes."""
    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.greyple())
    embed.add_field(name="User",     value=str(member),     inline=True)
    embed.add_field(name="Duration", value=f"{minutes} min", inline=True)
    embed.add_field(name="Reason",   value=reason,           inline=False)
    await ctx.send(embed=embed)

@bot.command(name="unmute")
@in_guild()
@is_admin_or_owner()
async def unmute(ctx, member: discord.Member):
    """[ADMIN] Remove timeout."""
    await member.timeout(None)
    await ctx.send(f"🔊 **{member}** has been unmuted.")

@bot.command(name="slowmode")
@in_guild()
@is_admin_or_owner()
async def slowmode(ctx, seconds: int = 0):
    """[ADMIN] Set slowmode (0 to disable)."""
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send("✅ Slowmode **disabled**." if seconds == 0 else f"✅ Slowmode set to **{seconds}s**.")

@bot.command(name="lock")
@in_guild()
@is_admin_or_owner()
async def lock(ctx):
    """[ADMIN] Lock the current channel."""
    ow = ctx.channel.overwrites_for(ctx.guild.default_role)
    ow.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.send("🔒 Channel **locked**.")

@bot.command(name="unlock")
@in_guild()
@is_admin_or_owner()
async def unlock(ctx):
    """[ADMIN] Unlock the current channel."""
    ow = ctx.channel.overwrites_for(ctx.guild.default_role)
    ow.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=ow)
    await ctx.send("🔓 Channel **unlocked**.")

@bot.command(name="announce")
@in_guild()
@is_admin_or_owner()
async def announce(ctx, channel: discord.TextChannel, *, message: str):
    """[ADMIN] Send an announcement embed to a channel."""
    embed = discord.Embed(description=message, color=discord.Color.gold())
    embed.set_footer(text=f"Announcement by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await channel.send(embed=embed)
    await ctx.send(f"✅ Announcement sent to {channel.mention}.", delete_after=5)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="warn")
@in_guild()
@is_admin_or_owner()
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """[ADMIN] Send a warning DM to a member."""
    embed = discord.Embed(
        title="⚠️ You have been warned",
        description=f"**Server:** {ctx.guild.name}\n**Reason:** {reason}",
        color=discord.Color.yellow()
    )
    try:
        await member.send(embed=embed)
        await ctx.send(f"✅ Warning sent to **{member}**.")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Warning logged, but couldn't DM **{member}** (DMs disabled).")

# ════════════════════════════════════════════════
#           OWNER CONFIG COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="setprefix")
@in_guild()
@is_owner()
async def setprefix(ctx, prefix: str):
    """[OWNER] Change the bot's command prefix."""
    settings["prefix"] = prefix
    save_settings(settings)
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name=f"{prefix}help"
    ))
    await ctx.send(f"✅ Prefix changed to `{prefix}`")

@bot.command(name="sethidden")
@in_guild()
@is_owner()
async def sethidden(ctx, value: str):
    """[OWNER] Toggle admin commands in .help (true/false)."""
    if value.lower() in ("true", "yes", "1", "on"):
        settings["hidden"] = True
        save_settings(settings)
        await ctx.send("✅ Admin commands are now **hidden** from `.help`.")
    elif value.lower() in ("false", "no", "0", "off"):
        settings["hidden"] = False
        save_settings(settings)
        await ctx.send("✅ Admin commands are now **visible** in `.help`.")
    else:
        await ctx.send("⚠️ Use `true` or `false`.")

@bot.command(name="setinstallermsg")
@in_guild()
@is_owner()
async def setinstallermsg(ctx, *, message: str):
    """[OWNER] Change the message sent with .download replies."""
    settings["installer_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Installer reply message updated:\n> {message}")

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
    embed.add_field(name="Members",  value=g.member_count, inline=True)
    embed.add_field(name="Channels", value=len(g.channels), inline=True)
    embed.add_field(name="Roles",    value=len(g.roles),   inline=True)
    embed.add_field(name="Owner",    value=str(g.owner),   inline=True)
    embed.add_field(name="Boosts",   value=g.premium_subscription_count, inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await ctx.send(embed=embed)

@bot.command(name="userinfo")
@in_guild()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles  = [r.mention for r in member.roles[1:]]
    embed  = discord.Embed(title=str(member), color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",       value=member.id,  inline=True)
    embed.add_field(name="Bot?",     value=member.bot, inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Joined",   value=member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "N/A", inline=True)
    embed.add_field(name="Created",  value=member.created_at.strftime("%Y-%m-%d"), inline=True)
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
#                 HELP COMMAND
# ════════════════════════════════════════════════

@bot.command(name="help")
@in_guild()
async def help_cmd(ctx):
    p      = settings.get("prefix", ".")
    hidden = settings.get("hidden", True)

    embed = discord.Embed(
        title="📖 Bot Help",
        description=f"**Prefix:** `{p}`  |  Guild-locked bot.",
        color=discord.Color.green()
    )

    installer_set = "✅ Set" if settings.get("installer_file") and os.path.exists(settings["installer_file"]) else "❌ Not set"
    embed.add_field(
        name="📦 Installer Download",
        value=(f"`{p}download` — Get the installer *(download channel only)*\n"
               f"Current installer: **{installer_set}**"),
        inline=False
    )
    embed.add_field(
        name="🎵 YouTube → MP3",
        value=(f"`{p}mp3 <url>` — Convert YouTube to MP3\n"
               f"Aliases: `{p}ytmp3`, `{p}convert`\n"
               f"*(Download channel only)*"),
        inline=False
    )
    embed.add_field(
        name="ℹ️ Info",
        value=(f"`{p}ping` · `{p}serverinfo` · `{p}userinfo [@user]` · `{p}avatar [@user]`"),
        inline=False
    )

    if not hidden:
        embed.add_field(
            name="👋 Welcome & Leave",
            value=(
                f"`{p}setwelcome #channel [msg]` — Set welcome channel\n"
                f"`{p}setleave #channel [msg]` — Set leave channel\n"
                f"`{p}setwelcomemsg <msg>` — Update welcome message\n"
                f"`{p}setleavemsg <msg>` — Update leave message\n"
                f"`{p}togglewelcome` / `{p}toggleleave` — Enable/disable\n"
                f"`{p}welcometest` / `{p}leavetest` — Preview messages\n"
                f"`{p}welcomestatus` — View current config\n"
                f"Variables: `{{mention}}` `{{name}}` `{{count}}` `{{server}}`"
            ),
            inline=False
        )
        embed.add_field(
            name="🔧 Admin Commands",
            value=(
                f"`{p}purge <n>` · `{p}kick` · `{p}ban` · `{p}unban`\n"
                f"`{p}mute [@user] [mins]` · `{p}unmute`\n"
                f"`{p}slowmode [s]` · `{p}lock` · `{p}unlock`\n"
                f"`{p}announce #channel <msg>` · `{p}warn <@user>`"
            ),
            inline=False
        )
        embed.add_field(
            name="👑 Owner Commands",
            value=(
                f"`{p}setfile` *(attach file)* `[msg]` — Set installer\n"
                f"`{p}setinstallermsg <msg>` — Change download reply\n"
                f"`{p}setprefix <prefix>` — Change prefix\n"
                f"`{p}sethidden true/false` — Toggle admin visibility"
            ),
            inline=False
        )
    else:
        embed.set_footer(text="Admin & welcome commands hidden • Ask an admin for help")

    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#                     RUN
# ════════════════════════════════════════════════

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌  DISCORD_TOKEN is not set!")
    print("    Add it as an environment variable on Render (or in .env locally).")
    exit(1)

# Start web server in background thread (required for Render)
web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()

# Start the bot
bot.run(TOKEN)# ════════════════════════════════════════════════
#     .mp3  (channel-locked — YouTube to MP3)
# ════════════════════════════════════════════════

def _run_ydl(opts: dict, url: str):
    """Blocking yt-dlp call — runs in executor thread."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

def _get_info(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

@bot.command(name="mp3", aliases=["ytmp3", "convert"])
@in_guild()
@in_download_channel()
async def mp3(ctx, *, url: str = None):
    """
    Convert a YouTube video to MP3 and send it.
    Only works in the designated download channel.

    Usage: .mp3 <youtube_url>
    """
    if not url:
        await ctx.send(
            "⚠️ Please provide a YouTube URL.\n"
            f"Usage: `{settings['prefix']}mp3 <youtube_url>`"
        )
        return

    # ── Step 1: Validate & get info ──
    status = await ctx.reply("🔍 Looking up video...")

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: _get_info(url))
    except Exception as e:
        await status.edit(content=f"❌ Could not find video.\n`{e}`")
        return

    title    = info.get("title", "audio")
    duration = info.get("duration", 0)
    uploader = info.get("uploader", "Unknown")

    # ── Step 2: Duration guard (25 min) ──
    if duration and duration > 1500:
        mins = duration // 60
        await status.edit(content=f"❌ Video is too long ({mins} min). Max is **25 minutes**.")
        return

    safe_name = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:80]
    out_path  = os.path.join(DOWNLOADS_DIR, f"{safe_name}.mp3")

    await status.edit(content=f"⏳ Converting **{title}** to MP3...")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(DOWNLOADS_DIR, f"{safe_name}.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }

    # ── Step 3: Download + convert ──
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _run_ydl(ydl_opts, url))
    except Exception as e:
        await status.edit(content=f"❌ Conversion failed.\n`{e}`")
        return

    # ── Step 4: Locate the output file ──
    if not os.path.exists(out_path):
        matches = list(Path(DOWNLOADS_DIR).glob(f"{safe_name}*.mp3"))
        if matches:
            out_path = str(matches[0])
        else:
            await status.edit(content="❌ MP3 file not found after conversion. Is FFmpeg installed?")
            return

    # ── Step 5: Size check (Discord 25 MB limit) ──
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    if size_mb > 25:
        os.remove(out_path)
        await status.edit(
            content=f"❌ File is too large ({size_mb:.1f} MB). Discord limit is 25 MB.\n"
                    "Try a shorter video."
        )
        return

    # ── Step 6: Upload ──
    duration_fmt = str(datetime.timedelta(seconds=duration)) if duration else "Unknown"
    await status.edit(content=f"📤 Uploading... ({size_mb:.1f} MB)")

    embed = discord.Embed(
        title="🎵 YouTube → MP3",
        color=discord.Color.red()
    )
    embed.add_field(name="Title",    value=title,        inline=True)
    embed.add_field(name="Uploader", value=uploader,     inline=True)
    embed.add_field(name="Duration", value=duration_fmt, inline=True)
    embed.add_field(name="Size",     value=f"{size_mb:.1f} MB", inline=True)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)

    try:
        await ctx.reply(
            embed=embed,
            file=discord.File(out_path, filename=f"{safe_name}.mp3")
        )
        await status.delete()
    except discord.HTTPException as e:
        await status.edit(content=f"❌ Upload failed: `{e}`")
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

# ════════════════════════════════════════════════
#              ADMIN COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="purge")
@in_guild()
@is_admin_or_owner()
async def purge(ctx, amount: int):
    """[ADMIN] Delete N messages in this channel (max 200)."""
    if not 1 <= amount <= 200:
        await ctx.send("⚠️ Amount must be between 1 and 200.", delete_after=6)
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    m = await ctx.send(f"🗑️ Deleted **{len(deleted) - 1}** messages.")
    await asyncio.sleep(4)
    try:
        await m.delete()
    except:
        pass

@bot.command(name="kick")
@in_guild()
@is_admin_or_owner()
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """[ADMIN] Kick a member from the server."""
    if member.top_role >= ctx.author.top_role and ctx.author.id != OWNER_ID:
        await ctx.send("❌ You can't kick someone with an equal or higher role.")
        return
    await member.kick(reason=reason)
    embed = discord.Embed(title="👢 Member Kicked", color=discord.Color.orange())
    embed.add_field(name="User",   value=str(member), inline=True)
    embed.add_field(name="Mod",    value=str(ctx.author), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="ban")
@in_guild()
@is_admin_or_owner()
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """[ADMIN] Ban a member from the server."""
    if member.top_role >= ctx.author.top_role and ctx.author.id != OWNER_ID:
        await ctx.send("❌ You can't ban someone with an equal or higher role.")
        return
    await member.ban(reason=reason, delete_message_days=0)
    embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.red())
    embed.add_field(name="User",   value=str(member), inline=True)
    embed.add_field(name="Mod",    value=str(ctx.author), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="unban")
@in_guild()
@is_admin_or_owner()
async def unban(ctx, *, user_input: str):
    """[ADMIN] Unban a user. Use their ID or Name#0000."""
    # Try by ID first
    try:
        uid = int(user_input)
        user = await bot.fetch_user(uid)
        await ctx.guild.unban(user)
        await ctx.send(f"✅ Unbanned **{user}**.")
        return
    except (ValueError, discord.NotFound):
        pass

    # Try by name#disc
    banned = [entry async for entry in ctx.guild.bans()]
    for entry in banned:
        if str(entry.user) == user_input or entry.user.name == user_input:
            await ctx.guild.unban(entry.user)
            await ctx.send(f"✅ Unbanned **{entry.user}**.")
            return

    await ctx.send("❌ User not found in the ban list.")

@bot.command(name="mute")
@in_guild()
@is_admin_or_owner()
async def mute(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason"):
    """[ADMIN] Timeout a member. Default 10 minutes."""
    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.greyple())
    embed.add_field(name="User",     value=str(member), inline=True)
    embed.add_field(name="Duration", value=f"{minutes} min", inline=True)
    embed.add_field(name="Reason",   value=reason, inline=False)
    await ctx.send(embed=embed)

@bot.command(name="unmute")
@in_guild()
@is_admin_or_owner()
async def unmute(ctx, member: discord.Member):
    """[ADMIN] Remove timeout from a member."""
    await member.timeout(None)
    await ctx.send(f"🔊 **{member}** has been unmuted.")

@bot.command(name="slowmode")
@in_guild()
@is_admin_or_owner()
async def slowmode(ctx, seconds: int = 0):
    """[ADMIN] Set slowmode on this channel. Use 0 to disable."""
    await ctx.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await ctx.send("✅ Slowmode **disabled**.")
    else:
        await ctx.send(f"✅ Slowmode set to **{seconds} seconds**.")

@bot.command(name="lock")
@in_guild()
@is_admin_or_owner()
async def lock(ctx):
    """[ADMIN] Lock the current channel (deny @everyone from sending)."""
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🔒 Channel **locked**.")

@bot.command(name="unlock")
@in_guild()
@is_admin_or_owner()
async def unlock(ctx):
    """[ADMIN] Unlock the current channel."""
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🔓 Channel **unlocked**.")

@bot.command(name="announce")
@in_guild()
@is_admin_or_owner()
async def announce(ctx, channel: discord.TextChannel, *, message: str):
    """[ADMIN] Send an announcement to a specific channel."""
    embed = discord.Embed(description=message, color=discord.Color.gold())
    embed.set_footer(text=f"Announcement by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await channel.send(embed=embed)
    await ctx.send(f"✅ Announcement sent to {channel.mention}.", delete_after=5)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="warn")
@in_guild()
@is_admin_or_owner()
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    """[ADMIN] Send a warning DM to a member."""
    embed = discord.Embed(
        title="⚠️ You have been warned",
        description=f"**Server:** {ctx.guild.name}\n**Reason:** {reason}",
        color=discord.Color.yellow()
    )
    try:
        await member.send(embed=embed)
        await ctx.send(f"✅ Warning sent to **{member}**.")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Warning logged, but couldn't DM **{member}** (DMs disabled).")

# ════════════════════════════════════════════════
#           OWNER CONFIG COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="setprefix")
@in_guild()
@is_owner()
async def setprefix(ctx, prefix: str):
    """[OWNER] Change the bot's command prefix."""
    settings["prefix"] = prefix
    save_settings(settings)
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name=f"{prefix}help"
    ))
    await ctx.send(f"✅ Prefix changed to `{prefix}`")

@bot.command(name="sethidden")
@in_guild()
@is_owner()
async def sethidden(ctx, value: str):
    """[OWNER] Toggle whether admin commands show in .help (true/false)."""
    if value.lower() in ("true", "yes", "1", "on"):
        settings["hidden"] = True
        save_settings(settings)
        await ctx.send("✅ Admin commands are now **hidden** from `.help`.")
    elif value.lower() in ("false", "no", "0", "off"):
        settings["hidden"] = False
        save_settings(settings)
        await ctx.send("✅ Admin commands are now **visible** in `.help`.")
    else:
        await ctx.send("⚠️ Use `true` or `false`.")

@bot.command(name="setinstallermsg")
@in_guild()
@is_owner()
async def setinstallermsg(ctx, *, message: str):
    """[OWNER] Change the message sent with .download replies."""
    settings["installer_message"] = message
    save_settings(settings)
    await ctx.send(f"✅ Installer reply message updated:\n> {message}")

# ════════════════════════════════════════════════
#              UTILITY / INFO COMMANDS
# ════════════════════════════════════════════════

@bot.command(name="ping")
@in_guild()
async def ping(ctx):
    """Check the bot's response latency."""
    await ctx.send(f"🏓 Pong! `{round(bot.latency * 1000)}ms`")

@bot.command(name="serverinfo")
@in_guild()
async def serverinfo(ctx):
    """Display information about this server."""
    g = ctx.guild
    embed = discord.Embed(title=f"📊 {g.name}", color=discord.Color.blurple())
    embed.add_field(name="Members",  value=g.member_count,          inline=True)
    embed.add_field(name="Channels", value=len(g.channels),         inline=True)
    embed.add_field(name="Roles",    value=len(g.roles),            inline=True)
    embed.add_field(name="Owner",    value=str(g.owner),            inline=True)
    embed.add_field(name="Boosts",   value=g.premium_subscription_count, inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await ctx.send(embed=embed)

@bot.command(name="userinfo")
@in_guild()
async def userinfo(ctx, member: discord.Member = None):
    """Display information about a user."""
    member = member or ctx.author
    roles  = [r.mention for r in member.roles[1:]]  # skip @everyone
    embed = discord.Embed(title=str(member), color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",       value=member.id,  inline=True)
    embed.add_field(name="Bot?",     value=member.bot, inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "N/A", inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    if roles:
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:10]), inline=False)
    await ctx.send(embed=embed)

@bot.command(name="avatar")
@in_guild()
async def avatar(ctx, member: discord.Member = None):
    """Show a user's full avatar."""
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#                 HELP COMMAND
# ════════════════════════════════════════════════

@bot.command(name="help")
@in_guild()
async def help_cmd(ctx):
    """Show the help menu."""
    p      = settings.get("prefix", ".")
    hidden = settings.get("hidden", True)

    embed = discord.Embed(
        title="📖 Bot Help",
        description=f"**Prefix:** `{p}`  |  All commands only work in this server.",
        color=discord.Color.green()
    )

    # ── Download / Installer ──
    installer_set = "✅ Set" if settings.get("installer_file") and os.path.exists(settings["installer_file"]) else "❌ Not set"
    embed.add_field(
        name="📦 Installer Download",
        value=(
            f"`{p}download` — Get the installer file *(#{DOWNLOAD_CHANNEL} only)*\n"
            f"Current installer: **{installer_set}**"
        ),
        inline=False
    )

    # ── YouTube MP3 ──
    embed.add_field(
        name="🎵 YouTube → MP3",
        value=(
            f"`{p}mp3 <url>` — Convert a YouTube video to MP3\n"
            f"Aliases: `{p}ytmp3`, `{p}convert`\n"
            f"*(Only works in <#{DOWNLOAD_CHANNEL}>)*"
        ),
        inline=False
    )

    # ── Info ──
    embed.add_field(
        name="ℹ️ Info",
        value=(
            f"`{p}ping` — Bot latency\n"
            f"`{p}serverinfo` — Server stats\n"
            f"`{p}userinfo [@user]` — User details\n"
            f"`{p}avatar [@user]` — Show avatar"
        ),
        inline=False
    )

    # ── Admin (hidden unless sethidden false) ──
    if not hidden:
        embed.add_field(
            name="🔧 Admin Commands",
            value=(
                f"`{p}purge <n>` — Delete N messages\n"
                f"`{p}kick <@user> [reason]` — Kick member\n"
                f"`{p}ban <@user> [reason]` — Ban member\n"
                f"`{p}unban <id/tag>` — Unban member\n"
                f"`{p}mute <@user> [mins] [reason]` — Timeout member\n"
                f"`{p}unmute <@user>` — Remove timeout\n"
                f"`{p}slowmode [seconds]` — Set slowmode\n"
                f"`{p}lock` / `{p}unlock` — Lock/unlock channel\n"
                f"`{p}announce #channel <msg>` — Send announcement\n"
                f"`{p}warn <@user> [reason]` — DM a warning"
            ),
            inline=False
        )
        embed.add_field(
            name="👑 Owner Commands",
            value=(
                f"`{p}setfile` *(attach file)* `[message]` — Set installer file\n"
                f"`{p}setinstallermsg <msg>` — Change the reply message for `.download`\n"
                f"`{p}setprefix <prefix>` — Change command prefix\n"
                f"`{p}sethidden <true/false>` — Toggle admin cmd visibility"
            ),
            inline=False
        )
    else:
        embed.set_footer(text="Admin commands hidden • Ask an admin for help")

    await ctx.send(embed=embed)

# ════════════════════════════════════════════════
#                     RUN
# ════════════════════════════════════════════════

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌  DISCORD_TOKEN environment variable is not set!")
    print("    Create a .env file with:  DISCORD_TOKEN=your_token_here")
    exit(1)

bot.run(TOKEN)
