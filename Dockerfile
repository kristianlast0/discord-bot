FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY music_bot ./music_bot

ENV PYTHONUNBUFFERED=1
# YouTube changes often and yt-dlp keeps up, so update it every time the container starts.
CMD ["sh", "-c", "pip install -q --no-cache-dir -U 'yt-dlp[default,deno]' && exec python -m music_bot"]
