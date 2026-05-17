# Deploying This Downloader on Railway

This repository is nested: the real app is inside `yt-dlp-master/`. Railway is configured to use the root `Dockerfile`, which installs Python explicitly and then runs the nested app through the root launcher.

## Required Variables

Open your Railway service, go to **Variables**, and add:

```text
DOWNLOAD_TOKEN=your-private-token
REQUIRE_DOWNLOAD_TOKEN=true
DOWNLOAD_DIR=/tmp/yt-dlp-downloads
ALLOWED_HOSTS=youtube.com,youtu.be,youtube-nocookie.com
MAX_ACTIVE_DOWNLOADS=1
MAX_DOWNLOAD_MB=512
```

Do not set `PORT`; Railway provides it automatically.

## Deploy Notes

Railway should use the root `railway.toml` file automatically. If you set a custom config path in Railway, use:

```text
/railway.toml
```

The Dockerfile installs the nested app dependencies from `requirements.txt`. It starts the root launcher:

```text
python local_downloader_server.py
```

In Railway service settings, make sure the builder/config is using:

```text
Config path: /railway.toml
Builder: Dockerfile
Dockerfile path: Dockerfile
```

If your Railway service has **Root Directory** set to `/yt-dlp-master`, use this instead:

```text
Config path: /yt-dlp-master/railway.toml
Builder: Dockerfile
Dockerfile path: Dockerfile
```

Downloads are stored in `/tmp/yt-dlp-downloads`, which is temporary. Add a Railway volume if you want hosted downloads to persist.

Only download videos you have permission to download.
