# Archive Sorting & Ingest Logic

## 1. Scope — What Gets Archived

All content — both existing files on `family_archive` and all new incoming files — enters the archive through the ingest pipeline. There is no separate cleanup or bulk scan phase. Existing content is processed chunk by chunk: drop a batch into `ingest/`, review it in the MAM, approve to `archive/`.

### Accepted types
| Category   | Extensions |
|------------|------------|
| Video      | `.mp4` `.mov` `.avi` `.mkv` `.mxf` `.m4v` `.mpg` `.ts` |
| Audio      | `.mp3` `.wav` `.flac` `.aac` `.m4a` `.aiff` `.ogg` `.opus` |
| Photos     | `.jpg` `.jpeg` `.png` `.heic` `.heif` `.tiff` `.dng` `.raw` `.arw` |
| Documents  | `.pdf` `.txt` `.md` |

### Excluded types
- **Code files** (`.py` `.js` `.cs` etc.) — put in a code repository if worth keeping
- **Installers / executables** — publish to Nexus if worth keeping
- **System / hidden files** (`.DS_Store`, `Thumbs.db`, `$RECYCLE.BIN` contents)
- **Sidecar-only files** (`.AAE` Apple edit files) — discard unless paired with a RAW

> **Preservation note:** AAE files contain Apple Photos edit history. If the original was edited in-camera, discarding the AAE loses those adjustments permanently. When in doubt, keep it.

---

## 2. Source Definitions

### Phone dumps (Mobile Backup)
The Synology backs up mobile devices automatically. Each device maps to a set of initials used in the output filename. The watcher monitors these paths directly and routes files into the ingest pipeline without manual intervention.

| Initials | Owner  | Source path |
|----------|--------|-------------|
| `ag`     | Auston | `/nas/homes/auston/Photos/MobileBackup/iPhone*/` |

> **Note:** Match source paths by glob pattern (`iPhone*`), not exact name. The device name in the path changes each time a phone is upgraded. Additional devices added here when ready.

### Manual ingest (everything else)
All other content — existing `family_archive` files, camera cards, external HDDs — is ingested by manually dropping files or folders into `ingest/`. This applies to existing content being migrated chunk by chunk.

---

## 3. Naming Convention

### Photos, HomeVideos, and dated recordings
All files from these sources are renamed at ingest time:

```
[YYYYMMDD]-[HHMMSS]-[initials].[ext]
```

**Example:** `20260126-113252-ag.jpg`

#### Date/time source priority
1. **EXIF `DateTimeOriginal`** — always preferred (set by the camera/phone at capture time)
2. **File system `date_created`** — fallback if no EXIF
3. **Folder name from source path** — last resort (e.g. `/2023/01/` → `20230101-000000`)

> **Preservation note:** Never use `date_modified` as the date source. File copies and transfers routinely update `date_modified` to the transfer time, making it meaningless as a creation timestamp.

#### Collision handling
If a file with the same output name already exists at the destination:
1. Compare SHA256 — if identical, it is a true duplicate → discard incoming, log it
2. If SHA256 differs (same timestamp, different content) → append a counter: `20260126-113252-ag_2.jpg`

> **Preservation note:** Never silently overwrite. A checksum mismatch on a same-name file means two distinct files share a timestamp — both must be kept.

### Music and Art
Music and art use a different naming and folder convention — see section 4.

---

## 4. Archive Folder Structure

```
archive/
│
├── Photos/
│   └── YYYYMMDD/
│       └── 20260126-113252-ag.jpg
│
├── HomeVideos/                        ← phone clips, family recordings
│   └── YYYYMMDD/
│       └── 20260126-120000-ag.mov
│
├── Audio/
│   ├── Recordings/                    ← ZOOM, field recordings, home recordings
│   │   └── YYYYMMDD/
│   │       └── 20260126-140000-ag.wav
│   └── Music/                         ← albums and music files
│       └── [Artist or Unknown]/
│           └── [Album or Unsorted]/
│               └── original-filename.mp3
│
├── Art/
│   ├── Visual/                        ← still images, photography as art
│   │   └── [Artist]/
│   │       └── work-title.jpg
│   └── Video/                         ← video art, film work
│       └── [Artist]/
│           └── work-title.mp4
│
└── Documents/
    └── YYYYMMDD/
        └── 20260126-090000-ag.pdf
```

**Folder date rules:**
- `Photos/`, `HomeVideos/`, `Audio/Recordings/`, `Documents/` — use creation date (`YYYYMMDD`)
- `Audio/Music/` — use ID3 Artist/Album tags if present; fall back to `Unknown/Unsorted/` and preserve original filename. Metadata can be corrected manually later.
- `Art/` — use Artist hierarchy, not date

> **Preservation note:** Grouping by creation date rather than ingest date keeps the archive browsable chronologically and makes ingest idempotent — re-ingesting the same file resolves to the same destination path and is caught as a duplicate.

---

## 5. Ingest Pipeline

The following sequence runs for every file, whether it arrives via the phone dump watcher or a manual drop into `ingest/`.

```
File detected in ingest/
       │
       ▼
1. Wait for write to complete (file size stable for 5 s)
       │
       ▼
2. Compute SHA256 checksum
       │
       ▼
3. Check against DB
   ├── Duplicate found → move to ingest/duplicates/ + log, stop
   └── Unique → continue
       │
       ▼
4. Extract EXIF / MediaInfo metadata
       │
       ▼
5. Determine category, output filename, and archive path
   (naming convention + folder structure above)
       │
       ▼
6. COPY file to ingest/staging/
       │
       ▼
7. Verify SHA256 of copy matches original
   ├── Mismatch → alert, delete bad copy, leave original in ingest/, stop
   └── Match → continue
       │
       ▼
8. Insert record into DB (status: staged)
   Fields: original_filename, new_filename, source_path,
           staged_path, archive_path, sha256, filesize,
           mediainfo, ingest_date, ingest_source, initials
       │
       ▼
9. Surface in MAM staging review
```

> **Preservation note:** Steps 6–7 use a **copy-then-verify** pattern. The original file in `ingest/` is never deleted until the staged copy is confirmed good by checksum. This protects against disk errors, network interruptions, and NAS write failures.

---

## 6. Staging Review (MAM UI)

Files sit in `ingest/staging/` with status **staged** until reviewed in the MAM. This is the human checkpoint for every file — existing content and new content alike.

### Per-file display
- Preview (thumbnail for photos/video, player for audio)
- Original filename
- Proposed new filename
- Proposed archive path (editable)
- Category badge (Photos / HomeVideos / Audio / Art / Documents) — overridable
- Technical metadata from MediaInfo (resolution, duration, codec, bitrate, etc.)
- Duplicate warning if a near-match exists (same checksum at a different path)
- RAW/JPEG pair indicator if applicable

### Actions
- **Approve** → copy from `ingest/staging/` to `archive/` at the proposed path, verify checksum, update DB status to `archived`, delete from staging
- **Reject** → move to `ingest/rejected/`, update DB status to `rejected` (auto-purged after 30 days)
- **Edit path** → override the auto-generated archive path before approving
- **Edit category** → override the auto-detected category (e.g. flag a video as `HomeVideos` vs `Art/Video`)
- **Approve all** → batch approve all staged files with no conflicts

> **Preservation note:** Rejected files are held in `ingest/rejected/` for 30 days before permanent deletion. Do not skip this window — accidental rejections are common and recoveries from a cold NAS are slow.

---

## 7. RAW + JPEG Pairs

When both a RAW file and a JPEG exist with the same base name (e.g. `IMG_0042.DNG` + `IMG_0042.JPG`):
- Archive **both** files
- Place them in the same destination folder with the same base name and their original extensions
- Link them in the DB via a `related_file_id` field on the `file_inventory` record

**Example:**
```
archive/Photos/20260126/
    20260126-113252-ag.dng
    20260126-113252-ag.jpg    ← same base, different ext, linked in DB
```

---

## 8. Duplicate Policy

| Scenario | Action |
|----------|--------|
| Same SHA256, already in `archive/` | Discard incoming, log it |
| Same SHA256, already in `ingest/staging/` | Discard incoming, log it |
| Same output filename, different SHA256 | Keep both, append counter to incoming |
| Same SHA256, different filename | Treat as duplicate — discard, log original path |

> **Preservation note:** SHA256 is the authoritative duplicate signal — not filename. Files are routinely renamed during transfers, exports, and re-imports. Two files with the same checksum are always the same content.

---

## 9. Decisions Log

| # | Question | Decision |
|---|----------|----------|
| 1 | How does existing content get migrated? | Manually dropped into `ingest/` chunk by chunk, reviewed at staging |
| 2 | HEIC vs JPEG policy | Keep all originals as-is. No transcoding at ingest. |
| 3 | RAW + JPEG pairs | Archive both; link in DB via `related_file_id` |
| 4 | Rejected file retention | 30 days, then auto-purge |
| 5 | HomeVideos vs other video | `HomeVideos/` for phone dump source or manually flagged at staging review; `Art/Video/` for artistic/film work flagged manually |
| 6 | Audio subcategories | `Audio/Recordings/` for field/ZOOM/home recordings; `Audio/Music/` for music — ID3 tags used if present, falls back to `Unknown/Unsorted/` with original filename preserved |
| 7 | Music metadata | Inconsistent/missing metadata is expected. Files land in `Unknown/Unsorted/` and are reorganized manually later. |

---

## 10. Open Items

- [ ] Add `hb` and `lg` phone dump source paths when ready
- [ ] **User authentication** — required before staging review goes live with multiple users
- [ ] Category override UI in staging review — dropdown per file for HomeVideos / Art / etc.
