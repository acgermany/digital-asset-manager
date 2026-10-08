FROM python:3.12-slim

# mediainfo: media metadata JSON; exiftool: EXIF/QuickTime capture metadata;
# poppler-utils: pdftotext for documents; cifs-utils / nfs-common: network shares mounted
# from the Configure page; libheif-examples: heif-convert for iPhone HEIC photos
RUN apt-get update && apt-get install -y --no-install-recommends \
        mediainfo libimage-exiftool-perl poppler-utils cifs-utils nfs-common libheif-examples \
        ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg/ffprobe: static GPL build (includes libx264 for proxies). Debian's 7.1 can't open
# some QuickTime files (e.g. DVCPRO50 tape captures with alias data references); 8.1 can.
ARG FFMPEG_VERSION=8.1
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) arch=linux64 ;; arm64) arch=linuxarm64 ;; *) echo "unsupported arch"; exit 1 ;; \
    esac; \
    name="ffmpeg-n${FFMPEG_VERSION}-latest-${arch}-gpl-${FFMPEG_VERSION}"; \
    base="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"; \
    cd /tmp; \
    curl -fsSLO "${base}/${name}.tar.xz"; \
    curl -fsSL "${base}/checksums.sha256" | grep " ${name}.tar.xz$" | sha256sum -c -; \
    tar -xJf "${name}.tar.xz"; \
    mv "${name}/bin/ffmpeg" "${name}/bin/ffprobe" /usr/local/bin/; \
    rm -rf /tmp/${name}*; \
    ffmpeg -hide_banner -version | head -1; ffmpeg -hide_banner -encoders | grep -q libx264

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dam ./dam

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# API + hot-folder watcher + workers (one mount namespace, see dam/run.py)
CMD ["python", "-m", "dam.run"]
