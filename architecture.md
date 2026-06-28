# MAM Architecture

## 1. Current State

### Services
| Container     | Image        | Role |
|---------------|--------------|------|
| `dam-api`     | FastAPI      | REST API, file serving, job management |
| `dam-frontend`| React/nginx  | Web UI |
| `dam-worker`  | Python       | Background job execution |

### Database Tables (SQLite)
| Table          | Purpose |
|----------------|---------|
| `file_inventory` | Every file seen by a scan — filepath, filename, filesize, sha256, scan_date, marked_for_deletion |
| `media_info`   | MediaInfo technical metadata per file |
| `segments`     | In/out point annotations on audio/video files |
| `jobs`         | Job queue shared between API and worker |

### Job Types
| Type        | What it does |
|-------------|--------------|
| `scan`      | Walk a directory, compute SHA256, write to `file_inventory` |
| `mediainfo` | Run `mediainfo` on media files, write to `media_info` |
| `workflow`  | Execute a shell script from `./workflows/` against a set of files |

### Volume Mounts (current)
| Host path              | Container path | Access |
|------------------------|----------------|--------|
| `/Volumes/family_archive` (Mac) / `/mnt/nas` (Ubuntu) | `/scan` | read-only |
| `./data`               | `/data`        | read-write |
| `./workflows`          | `/workflows`   | read-write |

### Frontend Views
- **Dashboard** — inventory stats, duplicate counts
- **Files** — paginated file browser with search, type filter, mark/unmark, preview, workflow trigger
- **Duplicates** — grouped duplicate review, auto-mark
- **Scan** — configure and run inventory scans, monitor progress

### What's missing for the ingest pipeline
The current system can scan and browse files on the NAS but has no concept of:
- File lifecycle stages (staged vs. archived vs. rejected vs. duplicate)
- Automatic ingest from a hot folder
- Renaming and categorising files at ingest time
- A human review step before files are promoted to the archive
- User identity / authentication

---

## 2. Target Architecture

### Services
| Container      | Role | New? |
|----------------|------|------|
| `dam-api`      | REST API — extended with ingest + staging endpoints | Extended |
| `dam-frontend` | Web UI — extended with Ingest/Staging view | Extended |
| `dam-worker`   | Job executor — extended with `ingest` and `promote` job types | Extended |
| `dam-watcher`  | Monitors `ingest/` hot folder, creates `ingest` jobs automatically | **New** |

### Share / Volume Layout
One NAS share (`family_archive`) contains all pipeline stages as subdirectories. All content — new and existing — enters the archive through `ingest/`; nothing is written to the share root by the pipeline.

```
family_archive/            ← single NAS share (mounted at /nas)
├── ingest/                ← hot folder: drop files here to begin ingest
│   ├── staging/           ← copy-verified files awaiting review
│   ├── duplicates/        ← exact dupes caught at ingest
│   └── rejected/          ← files rejected at review (held 30 days)
└── archive/               ← cleaned, organised master library
    ├── Photos/
    ├── HomeVideos/
    ├── Audio/
    ├── Art/
    └── Documents/
```

### Docker Compose Volumes (target)
One mount covers everything. Container sub-paths are resolved from it.

| Host path                 | Container path | Access     | Who uses it |
|---------------------------|----------------|------------|-------------|
| `/mnt/nas/family_archive` | `/nas`         | read-write | api, worker, watcher |
| `./data`                  | `/data`        | read-write | api, worker, watcher |
| `./workflows`             | `/workflows`   | read-write | worker |

Container path conventions derived from `/nas`:
- `/nas/ingest` — hot folder
- `/nas/ingest/staging` — staged files awaiting review
- `/nas/archive` — master library destination

---

## 3. Database Schema Changes

### `file_inventory` — additions via migration
```sql
ALTER TABLE file_inventory ADD COLUMN status TEXT DEFAULT 'scanned';
-- 'scanned' | 'staged' | 'archived' | 'rejected' | 'duplicate'

ALTER TABLE file_inventory ADD COLUMN original_filename TEXT;
-- preserved before any rename

ALTER TABLE file_inventory ADD COLUMN ingest_source TEXT;
-- 'phone_dump' | 'hot_folder' | 'manual'

ALTER TABLE file_inventory ADD COLUMN initials TEXT;
-- 'ag' | 'hb' | 'lg' | null

ALTER TABLE file_inventory ADD COLUMN category TEXT;
-- 'Photos' | 'HomeVideos' | 'Audio/Recordings' | 'Audio/Music'
-- | 'Art/Visual' | 'Art/Video' | 'Documents'

ALTER TABLE file_inventory ADD COLUMN staged_path TEXT;
-- full path in ingest/staging/ after copy-verify

ALTER TABLE file_inventory ADD COLUMN archive_path TEXT;
-- proposed or final path in archive/ (editable at review)

ALTER TABLE file_inventory ADD COLUMN related_file_id INTEGER REFERENCES file_inventory(id);
-- links RAW + JPEG pairs

ALTER TABLE file_inventory ADD COLUMN ingest_date TIMESTAMP;
ALTER TABLE file_inventory ADD COLUMN archived_date TIMESTAMP;
```

### New index
```sql
CREATE INDEX IF NOT EXISTS idx_status ON file_inventory(status);
```

### No new tables needed
The `jobs` table already handles the ingest and promote job types via the `params` JSON field. No structural change needed.

---

## 4. New Job Types

### `ingest`
Triggered by the watcher when a file lands in `ingest/`, or manually via the API.

Sequence:
1. Wait for file write to stabilise (size stable for 5 s)
2. Compute SHA256
3. Check against DB — if duplicate, move to `ingest/duplicates/`, set `status='duplicate'`, stop
4. Extract EXIF (`DateTimeOriginal`) and MediaInfo
5. Determine category (by source path or `ingest_source` param)
6. Build output filename (`YYYYMMDD-HHMMSS-initials.ext`) and proposed `archive_path`
7. Copy to `ingest/staging/[category]/proposed-name.ext`
8. Verify SHA256 of copy — on mismatch, delete bad copy, alert, stop
9. Insert record into `file_inventory` with `status='staged'`, all metadata, `staged_path`, `archive_path`
10. Queue `mediainfo` job for the staged file

Params: `{ file_path, ingest_source, initials }`

### `promote`
Triggered by user approving a staged file in the review UI.

Sequence:
1. Read `staged_path` and `archive_path` from DB
2. Create destination directory if needed
3. Copy from `staged_path` to `archive_path`
4. Verify SHA256 of copy
5. Delete `staged_path` copy
6. Update DB: `status='archived'`, `archived_date=now()`, `filepath=archive_path`

Params: `{ file_id, archive_path }` (archive_path may be overridden by user at review)

---

## 5. New / Changed API Endpoints

### Staging review
```
GET  /api/staged                    list files with status='staged'
GET  /api/staged/{id}               single staged file detail
PATCH /api/staged/{id}/path         override proposed archive_path
POST /api/staged/{id}/approve       queue promote job
POST /api/staged/{id}/reject        move to ingest/rejected/, set status='rejected'
POST /api/staged/approve-all        batch approve all staged (no path conflicts)
```

### Ingest
```
POST /api/ingest                    manually trigger ingest for a specific file path
GET  /api/ingest/stats              counts by status (staged / rejected / duplicate)
```

### Jobs — additions
```
GET  /api/jobs/active               currently returns scan-only — extend to all types
```

### Existing endpoints — no change needed
- All `/api/files/*` endpoints (file browser still works across all statuses)
- All `/api/segments/*` endpoints
- All `/api/workflows/*` endpoints
- `/api/scan/*` scan management
- `/api/duplicates/*` duplicate review

---

## 6. New Watcher Service

A lightweight Python service using the `watchdog` library. Runs as `dam-watcher`.

Responsibilities:
- Watch `/ingest/` (excluding `staging/`, `duplicates/`, `rejected/` subdirs)
- On `file_created` or `file_moved_into` event: create an `ingest` job in the DB
- Phone dump paths (`/nas/homes/auston/Photos/MobileBackup/iPhone*/`) watched separately, with `initials='ag'` injected into the job params

Environment variables:
```
DB_PATH=/data/inventory.db
INGEST_PATH=/nas/ingest
ARCHIVE_PATH=/nas/archive
PHONE_SOURCES=ag:/nas/homes/auston/Photos/MobileBackup/iPhone*
```

The watcher writes to the `jobs` table directly. The existing `dam-worker` picks up and executes `ingest` jobs — no separate executor needed.

---

## 7. Frontend Changes

### New view: Ingest / Staging Review
The primary new screen. Replaces the need for any external tool to review and approve incoming files.

Per-file display:
- Preview (thumbnail / player)
- Original filename → proposed new filename
- Proposed archive path (editable inline)
- Category badge (Photos / HomeVideos / Audio / Art / Documents) — overridable
- Technical metadata strip (resolution, duration, codec) from MediaInfo
- Duplicate / RAW-pair indicator

Actions:
- **Approve** / **Reject** per file
- **Approve all** for unambiguous batch
- **Edit path** inline before approving

### Existing views
| View       | Changes needed |
|------------|---------------|
| Dashboard  | Add staged/rejected/duplicate counts to stats cards |
| Files      | Add `status` filter (show archived only, or all); existing filters unchanged |
| Duplicates | Utility only — duplicates are now caught at ingest, not via manual scan |
| Scan       | Utility only — ad-hoc use, not part of the primary ingest workflow |

### Auth (deferred)
No login currently exists. Must be added before the staging review workflow goes live with multiple users. Options in order of implementation effort: nginx basic auth (quickest), API key per user, JWT. Decision deferred — tracked in `sorting-logic.md` open items.

---

## 8. Build Order

All content enters the archive through the ingest pipeline — there is no separate cleanup phase. Build order:

| Phase | What to build | Depends on |
|-------|---------------|------------|
| 1 | DB migrations (new columns on `file_inventory`) | Nothing — run once |
| 2 | `ingest` job type in worker | DB migrations |
| 3 | `dam-watcher` service | `ingest` job type |
| 4 | `promote` job type in worker | DB migrations |
| 5 | `/api/staged/*` endpoints | DB migrations, promote job |
| 6 | Staging Review UI | `/api/staged/*` endpoints |
| 7 | Auth | Staging Review UI |

Phases 1–4 are backend-only and can be validated via the jobs table and API before any UI work begins.

---

## 9. What Stays the Same

- SQLite as the single database — no vector DB or external DB needed for this pipeline
- The `jobs` table as the coordination point between all services
- MediaInfo extraction — auto-queued after ingest
- Workflow scripts — still triggered from Files view against any set of files
- Segment annotations — unchanged
- File preview / audio transcoding — unchanged
- Nexus image registry and deployment workflow — unchanged
- Scan job type — remains available as an ad-hoc utility (e.g. spot-checking the archive); not a required step in the ingest workflow
