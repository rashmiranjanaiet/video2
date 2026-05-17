FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV DOWNLOAD_DIR=/tmp/yt-dlp-downloads

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[default]" \
    && mkdir -p /tmp/yt-dlp-downloads

EXPOSE 8000

CMD ["python", "local_downloader_server.py"]
