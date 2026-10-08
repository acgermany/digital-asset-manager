# Scalable processing: queues and workers

## Goal

Speed up or slow down processing (ingest, rendering previews, AI) by adding or removing
workers, and control the pace from the DAM app. Any number of consumers pull from the
same queues; nothing is processed twice; a dead worker's work is picked up by others.

## Shape

```
                    ┌──────────────────────── Postgres ────────────────────────┐
  API / watcher ──▶ │ jobs (one row per item)   queues (pause, caps)            │
  coordinator   ──▶ │ batches (parent of jobs)  workers (registry + heartbeat)  │
                    └───────────▲───────────────────────▲───────────────────────┘
                                │ claim (SKIP LOCKED,   │ heartbeat every 10 s,
                                │  respects caps)       │ reads its overrides
          ┌─────────────────────┴───┐     ┌─────────────┴───────────┐
          │ worker A  (Linux box)   │     │ worker B  (any machine) │   … N workers
          │ queues: ingest,render   │     │ queues: render,vision   │
          │ concurrency: 2          │     │ concurrency: 4          │
          └─────────────────────────┘     └─────────────────────────┘
```

Postgres stays the broker: `FOR UPDATE SKIP LOCKED` already gives exactly-once claiming, and
jobs commit in the same transaction as the data they change. No Redis/RabbitMQ to run.

## Queues

| Queue | Work (one job =) | Bottleneck | Default cluster cap |
|---|---|---|---|
| `ingest` | one hot-folder file | disk / NAS | unlimited |
| `scan` | one catalog scan of a folder | disk | 1 |
| `render` | thumbnails + browser proxy for one asset (ffmpeg) | CPU | unlimited |
| `embed` | up to 32 assets → Ollama embeddings | the Mac | 2 |
| `vision` | one image/video → Ollama vision model | the Mac (GPU) | 1 |
| `speech` | one recording → Whisper server | the Mac (GPU) | 1 |

Per queue (editable in **Configure → Processing**):
- **Enabled**: pause/resume. Paused queues keep their jobs; nothing is claimed.
- **Max running (cluster-wide)**: the pace knob. Enforced at claim time under a per-queue
  advisory lock, so it holds no matter how many workers there are. The Mac-bound queues
  default to 1–2 because adding workers can't make one GPU faster. Raise them only if
  Ollama runs with `OLLAMA_NUM_PARALLEL > 1`.
- **Retries** and **stall timeout** (how long without a heartbeat before a job is reclaimed).

## Jobs and batches

- Every unit of work is a `jobs` row: kind, queue, priority, target (asset/ingest), attempts,
  `run_after` (backoff / waiting on the Mac), `locked_by`, `locked_at`.
- "Describe everything" / "Render previews" / "Re-embed all" creates a **batch**: one
  `batches` row plus one child job per item (inserted set-based in one statement).
  Progress = counts of children by status. Cancel = cancel the queued children.
- When the Mac or Whisper is unreachable, the job is **deferred** (re-queued with
  `run_after` in the future, not counted as a failure). Work resumes on its own.
- Valuable content first: children inherit priority from the asset's tier.

## Workers

`python -m dam.worker` — same image as the app, so it has ffmpeg, mediainfo, exiftool, etc.

| Setting | Env var | Overridable in the app |
|---|---|---|
| Queues to consume | `WORKER_QUEUES` (e.g. `ingest,render`) | yes, per worker |
| Parallel jobs | `WORKER_CONCURRENCY` | yes, per worker |
| Display name | `WORKER_NAME` | — |

- On start, a worker registers in `workers`, mounts the app's network shares (same
  definitions and credentials, via the shared `/data`), then runs N claim loops.
- A heartbeat thread (every 10 s) updates `last_seen`, refreshes `locked_at` on its running
  jobs, and applies overrides set in the app (queues, concurrency, **drain**).
- **Drain** = finish current jobs, claim nothing new (for maintenance / scaling down).
- SIGTERM: stop claiming, give running jobs 30 s, then release them back to the queue.
- **Reclaim**: jobs whose lock hasn't been refreshed within the queue's stall timeout are
  re-queued by any live worker or the coordinator. Workers silent for 5 minutes are
  marked offline.

### The coordinator

One per deployment (inside the `app` container): watches the Mac's embedding model state,
warms/unloads it, and turns "auto" settings (auto-describe, auto-transcribe, auto-embed)
into jobs. It does no heavy work itself.

## Running more workers

Same machine (Linux box):

```bash
docker compose up -d --scale worker=3
```

Another machine: run the worker image with the same mounts and a `DATABASE_URL` pointing at
the Postgres on the Linux box (`docker-compose.worker.yml`). Requirements:
- **Same paths**: files are referenced by their in-container path (`/host/home/…`,
  `/host/drives/…`, `/mnt/dam/<share>`). Mount the same storage at the same paths.
- **Shared `/data`**: renditions cache and share credentials live there. On another machine,
  mount the same NAS folder at `/data` (or at least `/data/cache` and `/data/secrets`).
- **Postgres reachable**: expose 5432 on the LAN only, with a real `DB_PASSWORD`.

## Sizing (Linux box, 8 GB)

- `app` keeps one embedded worker (concurrency 2) so a single-container install still works.
- Each extra worker container: ~150 MB idle; ffmpeg proxy encodes ~300–600 MB each at 720p.
  On 8 GB, 2–3 render jobs at once is the practical ceiling; scale `render` with workers on
  other machines rather than more on the box.
- The Mac queues (`vision`, `speech`, `embed`) are capped by the Mac, not by workers.

## Configuration in the app

- **Configure → Processing**: per-queue enabled / max running / retries / stall timeout,
  and the commands to start more workers.
- **Workflows → Queues**: live view of queues (queued / running / failed / done per hour),
  workers (host, queues, concurrency, running, last seen) with drain / resume / edit,
  and batches with progress and cancel. Includes "Render previews" to pre-build thumbnails
  and proxies across the library.
