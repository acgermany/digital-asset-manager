# MAM Roadmap

Phases map to the build order in `architecture.md`. Each item is a discrete, shippable unit of work. Complete phases sequentially — later phases depend on earlier ones.

---

## Phase 1 — DB Migrations

Add ingest lifecycle columns to `file_inventory`. Run once on first deploy after this phase.

- [ ] Write and test migration script (`data/migrate_ingest.py` or SQL file)
- [ ] Add columns: `status`, `original_filename`, `ingest_source`, `initials`, `category`, `staged_path`, `archive_path`, `related_file_id`, `ingest_date`, `archived_date`
- [ ] Add index: `CREATE INDEX idx_status ON file_inventory(status)`
- [ ] Default existing rows to `status='scanned'` (already-scanned files, pre-ingest)
- [ ] Run migration against dev DB, verify schema with `.schema file_inventory`

---

## Phase 2 — `ingest` Job Type in Worker

Extend `worker/worker.py` to handle `ingest` jobs. This is the core processing logic.

- [ ] Add `run_ingest(job)` function
  - [ ] Wait for file write to stabilise (poll size for 5 s)
  - [ ] Compute SHA256
  - [ ] Check DB for duplicate SHA256 — if found, move file to `ingest/duplicates/`, set `status='duplicate'`, stop
  - [ ] Extract EXIF `DateTimeOriginal` (use `exiftool` or `Pillow`)
  - [ ] Extract MediaInfo (reuse existing `run_mediainfo` logic)
  - [ ] Determine category from file extension and `ingest_source`
  - [ ] Build output filename: `YYYYMMDD-HHMMSS-{initials}.{ext}`
  - [ ] Handle collision: compare SHA256 if same name exists; append `_2`, `_3` if different content
  - [ ] Copy file to `ingest/staging/{category}/proposed-name.ext`
  - [ ] Verify SHA256 of copy — on mismatch, delete bad copy, alert via job error, stop
  - [ ] Insert or update `file_inventory` record with `status='staged'`, all metadata fields
  - [ ] Auto-queue `mediainfo` job for the staged file
- [ ] Add `exiftool` (or equivalent) to `worker/Dockerfile`
- [ ] Handle RAW+JPEG pairs: detect matching base names, set `related_file_id` on both records
- [ ] Test manually: drop a file into `ingest/`, trigger job via API, verify staging record

---

## Phase 3 — Watcher Service

New `dam-watcher` container. Detects files dropped into `ingest/` and creates `ingest` jobs automatically.

- [ ] Create `watcher/watcher.py` using the `watchdog` library
  - [ ] Watch `/nas/ingest/` — exclude `staging/`, `duplicates/`, `rejected/` subdirs
  - [ ] On `FileCreatedEvent` or `FileMovedEvent`: insert `ingest` job into `jobs` table
  - [ ] Watch phone dump paths (from `PHONE_SOURCES` env var) with `initials` injected
  - [ ] Parse `PHONE_SOURCES` format: `ag:/nas/homes/auston/Photos/MobileBackup/iPhone*`
- [ ] Create `watcher/Dockerfile`
  - [ ] `FROM python:3.11-slim`, install `watchdog`
- [ ] Add `dam-watcher` service to `docker-compose.yml` and `docker-compose.prod.yml`
  - [ ] Mount `/nas` read-write, `/data` read-write
  - [ ] Env: `DB_PATH`, `INGEST_PATH`, `PHONE_SOURCES`
- [ ] Test: drop a file into `ingest/`, verify job appears in `jobs` table within seconds

---

## Phase 4 — `promote` Job Type in Worker

Move an approved staged file to its final archive path.

- [ ] Add `run_promote(job)` function in `worker/worker.py`
  - [ ] Read `staged_path` and `archive_path` from DB by `file_id`
  - [ ] Create destination directory if it doesn't exist
  - [ ] Copy from `staged_path` to `archive_path`
  - [ ] Verify SHA256 of copy against DB record
  - [ ] On success: delete `staged_path`, update DB `status='archived'`, `archived_date=now()`, `filepath=archive_path`
  - [ ] On checksum mismatch: delete bad copy, set job to error, leave staged file in place
- [ ] Test manually: insert a promote job via API, verify file lands in `archive/` with correct path

---

## Phase 5 — `/api/staged/*` Endpoints

Expose the staging queue to the frontend.

- [ ] `GET /api/staged` — list `file_inventory` where `status='staged'`, paginated
- [ ] `GET /api/staged/{id}` — single staged file with all metadata fields
- [ ] `PATCH /api/staged/{id}/path` — override `archive_path` before approving
- [ ] `PATCH /api/staged/{id}/category` — override `category` before approving
- [ ] `POST /api/staged/{id}/approve` — queue `promote` job; validate no path conflict
- [ ] `POST /api/staged/{id}/reject` — move file to `ingest/rejected/`, set `status='rejected'`
- [ ] `POST /api/staged/approve-all` — batch approve all staged files with no path conflicts
- [ ] `POST /api/ingest` — manually trigger ingest for a given `file_path`
- [ ] `GET /api/ingest/stats` — counts by status (staged / rejected / duplicate / archived)
- [ ] Extend `GET /api/jobs/active` to return all job types, not just scan

---

## Phase 6 — Staging Review UI

New frontend view for reviewing and approving staged files.

- [ ] Add `Ingest` nav item in sidebar
- [ ] Create `frontend/src/components/Ingest.jsx`
  - [ ] Fetch and display staged files (paginated)
  - [ ] Per-file card: original filename → proposed name, proposed archive path (editable), category badge (overridable), MediaInfo strip
  - [ ] Duplicate warning badge if applicable
  - [ ] RAW/JPEG pair indicator
  - [ ] Approve / Reject buttons per file
  - [ ] Approve All button (disabled if any file has a path conflict)
  - [ ] Inline path edit (text input, saves on blur or Enter)
  - [ ] Category override dropdown
- [ ] Update Dashboard stats cards: add staged / rejected / duplicate / archived counts
- [ ] Update Files view: add `status` filter dropdown (all / archived / staged / etc.)

---

## Phase 7 — Auth

Required before staging review is used by more than one person.

- [ ] Decide on mechanism — options in order of effort:
  1. nginx basic auth (single shared password, simplest)
  2. API key per user (header-based, no UI needed)
  3. JWT with login page (full per-user identity)
- [ ] Implement chosen mechanism
- [ ] Gate all `/api/staged/*` and `/api/ingest` endpoints behind auth
- [ ] Document credentials / key distribution

---

## Deferred / Future

These are not blocking any phase above. Add to a phase when the time comes.

- [ ] Add `hb` and `lg` phone dump source paths to `PHONE_SOURCES`
- [ ] Auto-purge `ingest/rejected/` after 30 days (cron job or worker task)
- [ ] LLM metadata enrichment service — calls local LLM to generate descriptive tags per file
- [ ] Semantic search service — embedding index over descriptions for similarity search
- [ ] Distributed job workers — move from SQLite job queue to Redis/Celery if horizontal scale is needed
- [ ] ROADMAP review after Phase 6 ships — reassess priorities based on actual usage
