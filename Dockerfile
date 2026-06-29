FROM python:3.12-slim

# Avoid Python writing .pyc files and enable unbuffered logging so output
# shows up immediately in `docker compose logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Application code lives in /app; the process runs from /data so that the
# session/cookie files the tool persists land in a mountable volume,
# keeping them separate from the read-only source and config.
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persisted state (steam_session.json, csmoney_cookies.json) is written to
# the working directory; run from /data so it can be a named volume.
RUN mkdir -p /data
WORKDIR /data

CMD ["python", "/app/main.py"]
