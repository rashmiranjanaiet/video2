# Deploying This Downloader on Render

This app can run as a Render Python web service. It is token protected by default on Render, so set a private `DOWNLOAD_TOKEN` when Render asks for it.

## Blueprint Deploy

1. Push this folder to a GitHub repository.
2. In Render, choose **New +** -> **Blueprint**.
3. Select the repository that contains `render.yaml`.
4. When prompted, set `DOWNLOAD_TOKEN` to a private password-like value.
5. Deploy the service, then open the `.onrender.com` URL.
6. Paste the same token into the page's **Access token** field before downloading.

## Manual Web Service Settings

Use these settings if you create a Render web service manually:

```text
Runtime: Python
Build command: python -m pip install --upgrade pip && python -m pip install -e ".[default]"
Start command: python local_downloader_server.py
Health check path: /api/health
```

Recommended environment variables:

```text
PYTHON_VERSION=3.12.10
DOWNLOAD_TOKEN=your-private-token
REQUIRE_DOWNLOAD_TOKEN=true
DOWNLOAD_DIR=/tmp/yt-dlp-downloads
ALLOWED_HOSTS=youtube.com,youtu.be,youtube-nocookie.com
MAX_ACTIVE_DOWNLOADS=1
MAX_DOWNLOAD_MB=512
```

Render's default filesystem is ephemeral, so files in `/tmp/yt-dlp-downloads` can disappear after restarts or redeploys. For persistent hosted downloads, add a paid persistent disk and set `DOWNLOAD_DIR` to a path on that disk, such as `/var/data/downloads`.

Only download videos you have permission to download.
