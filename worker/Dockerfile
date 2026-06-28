FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends mediainfo exiftool && rm -rf /var/lib/apt/lists/*
COPY worker.py .
ENV PYTHONUNBUFFERED=1
CMD ["python", "worker.py"]
