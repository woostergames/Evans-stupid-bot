# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── Install ffmpeg (required by yt-dlp to convert audio to MP3) ──────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ── Set working directory ─────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Always upgrade yt-dlp to latest at build time ────────────────────────────
# YouTube frequently breaks older versions; keeping it current is essential.
RUN pip install --no-cache-dir --upgrade yt-dlp

# ── Copy bot code ─────────────────────────────────────────────────────────────
COPY bot.py .

# ── Create downloads folder ───────────────────────────────────────────────────
RUN mkdir -p downloads

# ── Run the bot ───────────────────────────────────────────────────────────────
CMD ["python", "bot.py"]
