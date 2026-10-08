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
| `app` | 8000 | API, hot-folder watcher and job workers (`server/`) |
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
- **Workflows**: Ingest (live activity), Review, Duplicates, Scan (catalog existing folders in place)
- **Configure**: hot folder, library, scan sources, drives, network shares, Ollama

## Development

```bash
cd server
uv venv -p python3.12 .venv && uv pip install -p .venv -r requirements-dev.txt
docker run -d --name dam-test-db -e POSTGRES_USER=dam -e POSTGRES_PASSWORD=dam \
  -e POSTGRES_DB=dam_test -p 55432:5432 pgvector/pgvector:pg16
.venv/bin/pytest
```

Frontend: `cd frontend && npm ci && npm run dev` (proxies `/api` to `localhost:8000`).
