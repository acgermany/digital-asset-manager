FROM python:3.12-slim

# ffmpeg: audio transcode for preview; mediainfo: media metadata JSON;
# exiftool: EXIF/QuickTime capture metadata; poppler-utils: pdftotext for documents;
# cifs-utils / nfs-common: network shares mounted from the Configure page
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg mediainfo libimage-exiftool-perl poppler-utils cifs-utils nfs-common \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dam ./dam

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# API + hot-folder watcher + workers (one mount namespace, see dam/run.py)
CMD ["python", "-m", "dam.run"]
