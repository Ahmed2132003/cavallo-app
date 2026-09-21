FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps needed to build psycopg2 and friends. libmagic1 is Part
# P-013's requirement: python-magic is a thin ctypes wrapper around the
# real libmagic C library, which isn't bundled in python:3.12-slim.
# ffmpeg is Part P-042's requirement: real video transcoding + thumbnail
# extraction for the Reel model's async pipeline (transcode_reel task).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libmagic1 \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Overridden per-service by docker-compose.yml (web / celery_worker / celery_beat).
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]