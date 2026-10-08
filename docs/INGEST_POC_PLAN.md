# Ingest Pipeline POC — Gap Analysis & Plan

Branch: `feature/ingest-pipeline-poc`

## 1. Where the repo is today

| Piece | What it does | Notes |
|---|---|---|
| `api/main.py` (664 lines) | FastAPI monolith: stats, duplicates + scoring, files, segments, preview/transcode, scan | All logic lives in route handlers. Scan runs in an in-process thread; its state is in memory and is lost on restart. |
| `inventory_service.py` + root `Dockerfile` | Older CLI scanner | Not used by `docker-compose.yml`. Its schema differs from `api/main.py`. Superseded. |
| `frontend/` | React/Vite SPA: Dashboard, Files, Duplicates, Scan, Preview, Segments | Tab state is held in `useState`, with no router. |
| `copyData.py`, `mark_duplicates.py` | One-off pandas scripts | `mark_duplicates.py` is broken. |
| Storage | SQLite `data/inventory.db`: `file_inventory` + `segments` | Keyed by path. There's no "asset" separate from a file location. |
| Tests | None | |

The repo today is a **catalog-in-place** tool. The POC needs an **ingest pipeline**: files arrive, get processed once, and the system makes decisions about them.

## 2. Requirements → gaps

| Requirement | Exists? | Gap |
|---|---|---|
| Hot folder + background service | ❌ | Needs a `watcher` container with a read-write hot folder mount |
| Queue + workers running a workflow | ⚠️ | Hashing exists, but only inside the API process. Needs a durable queue and separate worker containers. |
| Filename, provenance, ingest date, checksum, size | ⚠️ | Provenance and ingest date are missing |
| mediainfo JSON | ❌ | `mediainfo` isn't installed and there's nowhere to store the JSON |
| Early exact-duplicate handling (delete / review) | ❌ | Duplicates are only found in a report after the scan. Needs an inline decision, a delete path and a review queue. |
| **Near-duplicate detection** | ❌ | Needs perceptual fingerprints and similarity grouping |
| **Decision model (open Jev alternative)** | ❌ | Needs a decision service, a decision log and a review loop |
| Embeddings via the Mac's OpenAI-compatible API | ❌ | Needs an AI gateway, batch jobs and model-availability handling |
| On-demand semantic search with a loading state | ❌ | Needs a model status/warm-up API and UI states |
| Agent-ready | ❌ | Needs a service layer separate from HTTP, plus an audit log |
| Logical web app layout | ⚠️ | Needs a router and new views |

## 3. Deployment topology

```
┌──────────────────────────── LINUX BOX (docker compose) ────────────────────────────┐
│                                                                                     │
│  /srv/dam/hotfolder/<provenance>/…   (also shared via SMB so you can drop from Mac) │
│        │ inotify + size-stable check                                                │
│        ▼                                                                            │
│  ┌──────────┐ enqueue  ┌──────────────────────┐  claim (SKIP LOCKED) ┌───────────┐  │
│  │ watcher  │────────▶ │  Postgres + pgvector │ ◀─────────────────── │ worker ×N │  │
│  └──────────┘          │  assets, ingests,    │ ──── results ──────▶ │ hash,     │  │
│                        │  jobs, reviews,      │                      │ mediainfo,│  │
│  ┌──────────┐          │  fingerprints,       │                      │ fingerpr.,│  │
│  │ frontend │──▶ api ─▶│  decisions,          │                      │ library   │  │
│  └──────────┘          │  embeddings, events  │                      └───────────┘  │
│                        └──────────────────────┘                                     │
│                                   ▲                     ┌──────────────┐            │
│                         ai-worker ┼────────────────────▶│ decide (Laya)│ CPU        │
│                         (embed batches, AI gateway)     └──────────────┘            │
│  /srv/dam/library/…  (accepted files; preview/playback served from here)           │
└───────────────────────────────────┼────────────────────────────────────────────────┘
                                    │ OpenAI-compatible API (LAN / Tailscale)
                                    ▼
                    ┌────────────── MAC ───────────────┐
                    │ Ollama / LM Studio                │
                    │  /v1/embeddings  (embedding model)│
                    │  /v1/chat/completions (LLM)       │
                    │  model may be unloaded or asleep  │
                    └───────────────────────────────────┘
```

Containers on the Linux box are `db`, `api`, `frontend`, `watcher`, `worker` (scalable), `ai-worker` and `decide`.

### Why this shape

- **One box holds files, checksums and the DB.** Multi-GB files are hashed where they live and never cross the network, and preview/playback works because the library is local to the API.
- **Postgres + pgvector**: one service for data, the queue (`FOR UPDATE SKIP LOCKED`) and vectors.
- **The Mac is an optional accelerator, not a dependency.** Ingest, checksums, dedupe and near-dupe fingerprinting never wait on it. AI jobs sit as `pending_ai` until the Mac is reachable and the model is loaded.
- **All AI calls go through one AI gateway module** (`core/ai.py`). It uses the OpenAI-compatible API, so the Mac runtime can be swapped (Ollama, LM Studio, llama.cpp, vLLM) with `AI_BASE_URL`, `EMBED_MODEL` and `LLM_MODEL` settings.
- **The decision model runs on the Linux box.** Laya is CPU-friendly, so value decisions keep working when the Mac is off.

## 4. Data model

```
assets              id, sha256 UNIQUE, filesize, mime_type, is_media, mediainfo JSONB,
                    library_path, value_tier (critical|precious|standard|low|junk),
                    protected, needs_backup, exif JSONB, doc_features JSONB,
                    first_seen_at
ingests             id, asset_id, original_filename, original_path, provenance,
                    provenance_meta JSONB, ingest_date, sha256, filesize,
                    status (queued|processing|accepted|duplicate_deleted|
                            duplicate_review|failed), error, completed_at
jobs                id, ingest_id/asset_id, step, status (queued|running|done|failed|
                    pending_ai), attempts, run_after, locked_by, locked_at, error
duplicate_reviews   id, kind (exact_name_differs|near_duplicate|low_value),
                    ingest_id, asset_ids[], status, suggestion JSONB,
                    resolution, resolved_by, resolved_at
fingerprints        asset_id, kind (phash|video_phash|chromaprint|simhash), value, frames
near_dup_groups     id, method, members (asset_id, similarity), status
decisions           id, asset_id, question, model, model_version, inputs JSONB,
                    output JSONB, confidence, created_at,
                    human_override JSONB   ← labeled data for fine-tuning later
embeddings          asset_id, model, dims, source_hash, embedding vector  UNIQUE(asset_id, model)
ai_status           (single row) reachable, embed_model_loaded, last_checked, last_error
segments            (existing, re-keyed to asset_id)
locations           (existing NAS scan results: path → asset_id)
events              id, ts, actor (system|user|agent|model), action, refs, details JSONB
```

## 5. Ingest workflow

Each step is a `jobs` row with retries. **[core]** marks the steps that must complete for a file to be ingested. **[AI]** steps are deferred when the Mac isn't available.

1. **Detect [core]** (watcher). Uses inotify on native Linux, which is reliable, plus a size/mtime-stable window to handle copies still in progress. A polling fallback covers network mounts. Provenance comes from the subfolder name, with an optional `<file>.json` sidecar for more detail.
2. **Hash [core].** Streaming sha256 and filesize.
3. **Exact-duplicate decision [core]**, inside one transaction:
   - unknown sha256 → create the asset and continue
   - sha256 and filename match, and the existing copy is verified on disk → delete the incoming file (`DELETE_MODE=trash` to start) and log an event
   - sha256 matches but the name differs → move to `hotfolder/_review/` and open a `duplicate_reviews` row
4. **mediainfo [core].** `mediainfo --Output=JSON` → JSONB. `is_media` is set from the Video and Audio tracks.
5. **File into library [core].** Move to `library/<provenance>/<yyyy>/<mm>/<filename>`.
6. **Fingerprint [core, no AI]** (section 6). Compute a perceptual fingerprint, then check it against existing assets for near-duplicates.
7. **Value decision [decide service]** (section 7).
8. **Embed [AI, batch]** (section 8).

## 6. Near-duplicate detection

These methods are content-aware and need no GPU. They run in the normal worker.

| Media | Method | Tooling | Catches |
|---|---|---|---|
| Images | 64-bit pHash + dHash, Hamming distance ≤ ~8 | `imagehash` (Pillow) | Resizes, recompression, format changes, small edits |
| Video | Sample N keyframes with ffmpeg → pHash each → compare as a sequence | ffmpeg + `imagehash` | Re-encodes, different bitrate/resolution, trims, proxies vs masters |
| Audio | Chromaprint fingerprint, then fingerprint similarity | `fpcalc` (libchromaprint) | Different encodes or bitrates of the same recording, WAV vs MP3 |
| Docs/text | SimHash / MinHash over extracted text | `datasketch` | Revised versions of the same document |
| Anything (2nd signal) | Embedding cosine similarity ≥ threshold | pgvector | Same scene from a different angle, near-identical takes. Only available after embedding. |

- Lookups use an index (BK-tree or bucketed prefix in Postgres), so a new file isn't compared against every asset.
- Matches form `near_dup_groups`. The **decision model suggests which copy to keep**, using mediainfo (resolution, bitrate, duration, codec), the existing path/filename scoring, and the value tier. Those suggestions open a `near_duplicate` review.
- **Near-duplicates are never auto-deleted.** At most they are auto-suggested, and a human confirms. Bulk "accept all suggestions above X confidence" is available in the UI.

## 7. Decision model (open-source Jev alternative)

Jev-style models take structured state plus a question and return a **typed** decision rather than prose. **Laya** (ModernBERT-large, about 421M parameters, CPU-friendly) is the leading open alternative. It runs in its own `decide` container behind a small interface (`core/decide.py`), so it can be swapped for another open model (NanoJev, SemIf/OpenJev and similar) or for the Mac LLM with a JSON schema.

### Value tiers (what "valuable" means here)

| Tier | What it is | Handling |
|---|---|---|
| `critical` | **Tax and financial documents**: returns, W-2/1099, IRS letters, bank, brokerage and retirement statements, mortgage, insurance, receipts kept for taxes | `protected`, never deleted (not even exact dupes: an exact dupe of a critical asset goes to review instead), restricted view, flagged for backup, **content is not sent to the Mac** unless allowed (see section 8) |
| `precious` | **Family photos, home videos, voice recordings** | `protected`, never auto-deleted, embedded first, flagged for backup, near-dupes always need a human |
| `standard` | Everything else worth keeping | Normal rules |
| `low` / `junk` | Screenshots, app caches, thumbnails, downloads duplicated elsewhere | Low-value review list. Never deleted without confirmation. |

### Classification: cheap rules first, the model for the rest

1. **Rules** run on every file, in milliseconds, on the Linux box:
   - **Photos/videos:** EXIF/QuickTime metadata says a phone or consumer camera took it (`Make`/`Model`: Apple, Samsung, Google, Canon…), there's an original capture date, and possibly GPS → candidate `precious`. No camera metadata, screenshot dimensions or a `Screenshot` filename → candidate `low`.
   - **Audio:** voice-memo patterns (iPhone `.m4a` from Voice Memos, recorder devices in mediainfo) → candidate `precious`.
   - **Documents:** text extracted with `pdftotext`, or OCR (`tesseract`) for scans, is checked against financial patterns: form numbers (1040, W-2, 1099-*, 1098, K-1), "Internal Revenue Service", "statement period", account/routing number layouts, EIN/SSN-shaped tokens (only detected, never stored), plus filename and folder hints (`taxes/2023`) → candidate `critical`.
2. **The decision model (Laya)** gets the rule signals plus metadata and confirms or decides the tier, along with a confidence score. It handles the ambiguous cases rules can't, such as a PDF named `scan0003.pdf`.
3. **Low confidence → review.** Anything that is possibly `critical` or `precious` but below the confidence threshold goes to review rather than being auto-tiered down. The asymmetry is deliberate: wrongly tiering a tax return as `standard` is worse than one extra review click.

**Decisions it makes:**

| Question | Input | Typed output | Effect |
|---|---|---|---|
| `value_tier` | rule signals, filename, provenance, path, EXIF/mediainfo summary, extracted-text features (not raw text), review history | `critical | precious | standard | low | junk`, confidence, reason | Applies the handling in the tier table above |
| `near_dup_keep` | the group's members with mediainfo and scores | `keep: asset_id`, `drop: [ids]`, confidence | Suggested resolution on the review |
| `review_triage` | an open review item | `auto_resolve | needs_human`, priority | Orders the Review queue so valuable items come first |

- Every call is recorded in `decisions`: inputs, output, model version and confidence.
- When you override a decision in the UI, the override is stored. That builds a labeled dataset for fine-tuning Laya on your own content later.
- Confidence thresholds per decision type (in Settings) control what is auto-applied and what is only suggested. Deletions are always suggestion-only.
- Laya's actual serving interface and question format need to be checked against its repo in phase 6. The `decide.py` adapter isolates that work.

## 8. Embeddings & on-demand semantic search (Mac model)

### Model availability state machine (`ai_status`, polled by `ai-worker`)

```
offline ──(Mac reachable)──▶ unloaded ──(enable / batch start)──▶ loading ──▶ ready
   ▲                              ▲                                              │
   └──────(unreachable)───────────┴────────(idle unload / disabled)──────────────┘
```

### Ollama on the Mac

- **Expose Ollama to the LAN:** set `OLLAMA_HOST=0.0.0.0` (for the menu-bar app: `launchctl setenv OLLAMA_HOST 0.0.0.0`, then restart Ollama). Ollama has **no built-in authentication**, so restrict port 11434 to the Linux box's IP with the macOS firewall or pf, or put both machines on Tailscale and bind to the Tailscale address.
- **Embedding model:** `nomic-embed-text` (768 dims, small and fast) to start. `mxbai-embed-large` (1024 dims) is an alternative if quality matters more. Calls go through Ollama's OpenAI-compatible `/v1/embeddings`.
- **Later, for photo search:** text embeddings of metadata alone won't find "kids at the beach". A batch job can use an Ollama **vision model** (e.g. `qwen2.5vl`, `gemma3`, `llama3.2-vision`) to caption photos and keyframes. The caption is then embedded, which makes family photos searchable by what's in them. Recordings would need transcripts, which Ollama doesn't do; `whisper.cpp` on the Mac would.
- **Sensitive content policy:** `critical` (financial) documents are embedded from **metadata only** by default (filename, folder, detected form type and year, e.g. "1099-INT 2023"), not their full text. An `EMBED_SENSITIVE_TEXT=false` setting controls this. The Mac is your own machine on your LAN, so turning it on is reasonable, but it's an explicit choice.

### Detection and control

- **Detection:** `GET {AI_BASE_URL}/api/tags` (Ollama's model list) shows whether the Mac is reachable and the model is installed. `GET /api/ps` shows whether it is **loaded in memory** and until when.
- **Enable semantic search** (a UI toggle): `POST /api/ai/semantic/enable` sends a warm-up embedding call with `keep_alive: "30m"`, which makes Ollama load the model. The status moves to `loading`, and the UI shows **"Loading embedding model…"** with elapsed time. When `/api/ps` lists the model, the status becomes `ready`. Each search refreshes `keep_alive`, so the model stays loaded while you're using it and unloads on its own after 30 idle minutes. The UI receives status updates via SSE, or by polling `/api/ai/status`.
- **Disable:** a request with `keep_alive: 0` unloads the model immediately and frees Mac memory.
- **Search box behavior:**

| State | Search behavior |
|---|---|
| `ready` | Hybrid search: the query is embedded on the Mac, then pgvector cosine results are merged with keyword and metadata matches |
| `loading` | Keyword results shown immediately, a "Loading embedding model…" banner, and semantic results replace them once ready |
| `unloaded` / `offline` | Keyword and metadata search only, with an "Enable semantic search" button (disabled and explained when offline) |

### Batch embedding jobs
- Run from the UI ("Embed pending" / "Re-embed all for model X") or on a schedule, for example nightly when the Mac is likely free.
- `ai-worker` loads the model if needed, then embeds in chunks of about 32: highest value tier first, then oldest pending. Each chunk is committed on its own, so the job is **resumable**.
- If the Mac disappears mid-run, the job pauses in `pending_ai` and resumes later.
- The text template is built on the server from filename, provenance, path, a mediainfo summary, segment labels and (later) transcripts. A `source_hash` skips assets whose text hasn't changed.
- Embeddings are stored per `(asset, model)`, with one HNSW index per model and dimension, so switching models means a re-embed batch, not a migration.
- Later batch types follow the same pattern: Whisper transcripts and image or keyframe captions, using the Mac LLM or a vision model.

## 8a. Linux box resource budget (2.8 GHz, 8 GB RAM)

8 GB is enough, but every service gets a memory limit in compose, and the heavy work is batched rather than run in parallel.

| Service | Memory limit | Notes |
|---|---|---|
| Host OS + Docker + SMB | ~1.5 GB | |
| `db` (Postgres + pgvector) | 1 GB | `shared_buffers=256MB`, `work_mem=16MB`. HNSW over ~100k × 768-dim vectors is about 300 MB. |
| `api` | 384 MB | Audio-transcode ffmpeg runs here on demand |
| `frontend` (nginx) | 64 MB | |
| `watcher` | 128 MB | |
| `worker` ×2 | 512 MB each | Hashing is disk-bound, so 2 workers saturate a single disk. ffmpeg keyframe sampling and OCR are the CPU spikes. |
| `ai-worker` | 256 MB | Only makes HTTP calls to the Mac |
| `decide` (Laya, ONNX int8) | ~1.0–1.5 GB **while loaded** | Loaded on demand and unloaded after idle. Runs as a batch, not on every file inline. |
| **Headroom** | ~1.5–2 GB | Page cache for hashing |

Scheduling rules:
- **Core pipeline steps** (hash, dedupe, mediainfo, library move) run immediately, so new files are safe and catalogued within seconds to minutes.
- **Heavy steps** (OCR, video fingerprinting, the Laya decision batch) go through a `heavy` queue with **concurrency 1**, and run at `nice` priority so they never starve the UI.
- **The Laya batch** is triggered when the `heavy` queue has N pending items or on a timer. It loads the model, classifies everything pending, then unloads. On a ~2.8 GHz CPU expect roughly 50–200 ms per item for short inputs. That's fine for batches, but too slow to hold up ingest.
- **Fallback if 8 GB proves tight:** run the decision model on the Mac instead, either Laya there or an Ollama LLM using a JSON schema. The `decide.py` adapter makes that a config switch. Benchmark Laya in phase 6 before committing either way.

## 8b. Security & safety (personal + financial data)

- **Web UI requires a login:** a single-user password plus a session cookie for the POC. Bind it to the LAN or Tailscale only, never port-forwarded.
- **The `critical` tier gets a restricted view.** Its previews and full text appear only after re-confirming the password, and are never shown in thumbnails or search snippets.
- **Never store the sensitive numbers themselves.** Text extraction keeps document features (form type, year, issuer), and SSN- or account-number-shaped strings are detected and **redacted** before anything is stored or embedded.
- **Protection is enforced in `core/`, not just the UI.** `protected` assets can't be deleted through any code path, including the agent/MCP path, without an explicit unprotect event.
- **Backup flag:** `critical` and `precious` assets show up in a "needs backup" view. Wiring in an actual backup target (a second disk, B2 or similar) is a later phase, but the plan calls it out because these files are irreplaceable.
- **Disk encryption:** consider LUKS for the library volume on the Linux box, since it will hold tax documents.

## 9. Code layout

```
server/
  dam/
    config.py  db.py  models.py           SQLAlchemy + Alembic
    core/                                 business logic, no HTTP (API, workers, MCP reuse)
      ingest.py  dedupe.py  scoring.py  mediainfo.py  library.py
      fingerprint.py  neardup.py
      ai.py        OpenAI-compatible gateway + model status/warm-up
      decide.py    decision-model adapter (Laya first)
      embed.py  search.py  reviews.py  assets.py  events.py
    queue.py                              enqueue / claim / complete / fail / defer
    watcher.py   worker.py   ai_worker.py entrypoints (python -m dam.…)
    api/routers/ assets ingests reviews neardups search ai decisions segments media inventory
  migrations/  tests/
  scripts/import_sqlite.py                one-time import of data/inventory.db
decide/                                   Dockerfile wrapping Laya as an HTTP service
frontend/                                 extended (react-router)
docker-compose.yml                        db, api, frontend, watcher, worker, ai-worker, decide
.env.example                              HOTFOLDER, LIBRARY, AI_BASE_URL, EMBED_MODEL,
                                          LLM_MODEL, DELETE_MODE, thresholds
```

Retire `inventory_service.py`, the root `Dockerfile`, `copyData.py` and `mark_duplicates.py`. The scoring logic moves into `core/scoring.py`.

## 10. Web app layout

| Section | View | Contents |
|---|---|---|
| **Overview** | Dashboard | Assets/size, ingest rate, queue depth, open reviews by kind, **critical / precious counts and backup status**, AI status chip |
| **Ingest** | Activity | Live feed of each ingest with per-step status pills, plus retry |
| | Queue | Jobs by step and status, including `pending_ai` |
| **Library** | Assets | Filter by provenance, type, date and value tier, with a protected badge. Quick views: **Family** (precious) and **Financial** (critical, behind re-auth). |
| | Asset detail | Preview (existing), mediainfo tree, segments (existing), ingests and locations, decisions history, near-duplicates |
| **Review** | Exact dupes (name differs) | Side by side, with keep both / delete incoming / alias |
| | Near-duplicates | Visual comparison (thumbnails, waveforms, mediainfo diff), suggested keep, bulk accept |
| | Classification | Items the classifier thinks may be critical or precious but isn't confident about (top of queue), then low/junk confirmations |
| **Search** | Search | Keyword search always available. Semantic toggle with loading state (section 8). |
| **AI** | Models | Mac reachability, model load state, enable/disable, embedding coverage, run batch |
| | Decisions | Decision log, override rate, thresholds per decision type |
| **Settings** | | Paths, delete mode, near-dupe thresholds, NAS catalog scan (existing) |

## 11. Agent readiness

- All mutations go through `core/` and write `events` with an `actor`.
- A later MCP server exposes `search_assets`, `get_asset`, `list_reviews`, `resolve_review`, `explain_decision` and `annotate_segment`. It can run on the Mac next to the LLM and call the Linux API.
- The decision log plus human overrides give an agent both context and precedent.

## 12. Phased plan

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Foundation** | `server/` restructure, Postgres+pgvector, Alembic, compose with all services stubbed, `.env` | `docker compose up` on the Linux box, and existing UI works against Postgres |
| **1. Schema + import** | Models, `import_sqlite.py` | Existing inventory is visible in Library/Duplicates |
| **2. Watcher + queue + core pipeline** | Detect → hash → exact dedupe → mediainfo → library | All three exact-dupe branches are tested and work with real files dropped via SMB |
| **3. UI: Ingest, Review, Asset detail + login** | Router, views, single-user auth | Exact-dupe reviews can be resolved in the browser, which requires a login |
| **4. Near-duplicates** | Fingerprints (image, video, audio), grouping, review UI | Re-encoded and resized copies are grouped correctly on a test set |
| **5. AI gateway + embeddings + search** | `ai_status`, enable/warm-up, loading UI, batch embed, hybrid search | Toggle on → "Loading…" → semantic results. Turning the Mac off degrades to keyword search. |
| **6. Classification + decision model** | Tier rules (EXIF, voice memo, financial-doc text/OCR with redaction), `decide` container (Laya, batch, ONNX int8), near-dup keep suggestions, decision log + overrides | A test set of family photos and tax PDFs lands in `precious`/`critical` and is protected; Laya fits in the memory budget or is moved to the Mac |
| **7. Agent** | MCP server over `core` | Claude can search, explain a decision and resolve a review |

Phases 0 to 2 make up the core POC. Phases 4, 5 and 6 are independent and can be built in any order. If protecting critical and precious files matters most early on, the **rule-based tiering** from phase 6 can be pulled forward into phase 2 cheaply: it needs no model, just EXIF and text rules.

## 13. Decided

- **Value:** `critical` = tax/financial documents; `precious` = family photos, videos and recordings (section 7).
- **AI runtime:** Ollama on the Mac, via its OpenAI-compatible API (section 8).
- **Linux box:** 2.8 GHz, 8 GB. Memory limits, 2 workers, heavy steps batched (section 8a).

## 14. Open decisions

1. **Embedding model:** start with `nomic-embed-text`? Run `ollama pull nomic-embed-text` on the Mac.
2. **Network:** same LAN with a firewall rule, or Tailscale? Tailscale is simpler to lock down given Ollama has no auth.
3. **Hot folder access:** SMB share from the Linux box, mounted on the Mac?
4. **Delete mode:** start with `trash` and auto-purge after 30 days? (Never applies to critical/precious.)
5. **Exact-dupe filename match:** case-insensitive? Should copy patterns such as `IMG_1234 (1).JPG` count as the same name?
6. **Where do financial-document full texts get embedded:** metadata only (default), or full text to the Mac?
7. **Linux box storage:** how much disk is there, and is it one drive or several? This affects the library location, the trash and whether LUKS is practical.
8. **CPU core count** (`nproc`): decides whether to run 2 or 3 workers.
