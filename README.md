# Digital Asset Manager

A self-hosted ingest pipeline and catalog for personal media and documents. Drop files into a
hot folder; the app checksums them, removes exact duplicates, holds look-alike copies for
review, extracts metadata, protects family media and financial records, and files everything
into a library.

See [docs/INGEST_POC_PLAN.md](docs/INGEST_POC_PLAN.md) for the full plan and roadmap.

## Running

```bash
docker compose up --build -d
```

Open [http://localhost:3000](http://localhost:3000), go to **Configure**, and choose a hot folder
and a library folder.

| Container | Port | What it runs |
|---|---|---|
| `db` | — | Postgres 16 + pgvector |
| `app` | 8000 | API, hot-folder watcher, coordinator, and an embedded worker (`server/`) |
| `worker` | — | Extra queue consumers; none by default (`docker compose up -d --scale worker=N`) |
| `frontend` | 3000 | React UI (nginx), proxies `/api/` to `app` |

### What the app can see

Containers only see host folders that are mounted into them. `docker-compose.yml` mounts your
home folder and your drives folder; set these in `.env` (see `.env.example`):

| Variable | macOS default | Linux box example |
|---|---|---|
| `HOST_HOME` | `$HOME` | `/srv` |
| `HOST_DRIVES` | `/Volumes` | `/media` (add `:rslave` to the mount for hot-plugged drives) |

NAS and other network shares (SMB/NFS) are added from **Configure → Network shares**. The `app`
container has the `SYS_ADMIN` capability so it can mount them.

## How ingest works

1. A file in the hot folder is picked up once its size stops changing. The first subfolder name
   is its provenance (`Hotfolder/Grandma/photo.jpg` → `Grandma`). An optional `photo.jpg.json`
   sidecar is stored as provenance metadata.
2. It is checksummed (SHA-256) and compared with everything already known:
   - **Same checksum and same name** (case-insensitive) → deleted from the hot folder (or moved
     to `_trash/`), as long as the existing copy is still on disk.
   - **Same checksum, different name** → moved to `_review/` and listed under
     Workflows → Review.
   - **New** → metadata (mediainfo JSON, EXIF, document text) is extracted and the file is
     classified, then moved to `Library/<provenance>/<year>/<month>/`.
3. Classification tiers: **critical** (tax/financial documents) and **precious** (family photos,
   videos, recordings) are protected and flagged for backup; uncertain cases go to review.

The library folder contains a hidden `.dam_library` marker. If it's missing (drive or NAS not
mounted), files wait in the hot folder.

## Web app

- **Dashboard**: inventory, duplicate and protected-content totals; library map (storage by
  provenance and file type, capture-date timeline)
- **Files**: grid / list / similarity-map views; keyword + semantic search (Ollama on the Mac);
  viewer for images (HEIC/RAW converted), video (ffmpeg proxies when needed), PDF, audio, text,
  with an Info panel showing all stored metadata (checksum, ingest history, copies, EXIF,
  mediainfo, tier and reasons, editable tags, history)
- **Workflows**: Ingest (live activity), Review, Duplicates, Scan (catalog existing folders in place),
  AI (photo/video descriptions, Whisper transcripts, embeddings)
- **Configure**: hot folder, library, scan sources, drives, network shares, Ollama

## AI on the Mac

All AI runs on the Mac; the app works without it.

| What | Server | Default model |
|---|---|---|
| Embeddings (semantic search, map) | Ollama | `nomic-embed-text` |
| Photo captions, video summaries | Ollama | `qwen3-vl:8b` |
| Transcripts (videos, voice memos) | whisper.cpp `whisper-server` | `large-v3-turbo` (q5_0) |

```bash
brew install whisper-cpp
whisper-server -m ~/whisper-models/ggml-large-v3-turbo-q5_0.bin --host 127.0.0.1 --port 8178 -l auto
```

Everything (models, prompts, URLs, which file types) is configurable in **Configure → AI**.

## Local network access

The web UI is protected by a login. On first start the app prints a one-time **setup code**
(`docker compose logs app | grep "setup code"`, also in `data/secrets/setup-code`); open the UI
and use it to create the password. Only the web UI is published; the API (8000) and Postgres
(5432) stay bound to localhost.

In `.env`:

```bash
WEB_BIND=0.0.0.0                      # share on the LAN (127.0.0.1 = this machine only)
TLS_HOSTNAMES=dam.local localhost     # names the HTTPS certificate covers
TLS_IPS=127.0.0.1 192.168.1.20        # and addresses (update if the machine's IP changes)
```

**HTTPS.** The web UI is served on 443; port 80 redirects. On first start the frontend creates a
private certificate authority in `data/tls/` (gitignored) that is name-constrained to `.local`,
`localhost` and private IP ranges, so it cannot vouch for real websites. Trust it once per device:
download it from `http://dam.local/dam-ca.crt` (or Configure → Security, which has per-device
steps). The server certificate is re-issued automatically when the names/addresses change.

**Files from other computers.** Folder pickers browse the computer running the DAM, never the
browser's device (browsers don't allow that). From any device, use **Upload** in Workflows → Ingest
(drag files or whole folders; resumable 16 MB chunks). To *watch* a folder on another computer,
share it over SMB and add it in Configure → Network shares.

Advertise it with Bonjour so devices find **http://dam.local**:

- **macOS host** (containers can't multicast to the LAN):
  ```bash
  dns-sd -P "Digital Asset Manager" _https._tcp local 443 dam.local "$(ipconfig getifaddr en0)" path=/
  ```
- **Linux host**: `docker compose --profile mdns up -d` (runs `dam.mdns` with host networking).

## Scaling

Every unit of work (a file to ingest, a preview to render, a photo to describe) is its own job in
a Postgres-backed queue. Any number of workers pull from it, here or on other machines. Pace is
controlled per queue (pause, cluster-wide "max running") in **Configure → Processing** and per
worker (jobs at once, which queues, drain) in **Workflows → Queues**. See
[docs/SCALING.md](docs/SCALING.md).

```bash
docker compose up -d --scale worker=3
```

## Development

```bash
cd server
uv venv -p python3.12 .venv && uv pip install -p .venv -r requirements-dev.txt
docker run -d --name dam-test-db -e POSTGRES_USER=dam -e POSTGRES_PASSWORD=dam \
  -e POSTGRES_DB=dam_test -p 55432:5432 pgvector/pgvector:pg16
.venv/bin/pytest
```

Frontend: `cd frontend && npm ci && npm run dev` (proxies `/api` to `localhost:8000`).
