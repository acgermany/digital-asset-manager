# Digital Asset Manager

A Docker-based service that scans storage locations, calculates SHA256 checksums, and logs file metadata to a persistent SQLite database. Includes a web UI for browsing inventory, detecting duplicates, and marking files for deletion.

## Features

- Recursively scans directories
- Calculates SHA256 checksums for all files
- Skips files that already exist in database (by filepath)
- Records: filepath, filename, filesize, sha256, date_created, date_modified, scan_date
- Persistent SQLite database
- No record inserted until checksum is successfully computed
- **Web UI** for inventory stats, duplicate review, and file management

## Web App

The web app runs two services via docker-compose:

| Service | Port | Description |
|---------|------|-------------|
| `api` | 8000 | FastAPI backend — serves all `/api/` endpoints |
| `frontend` | 3000 | React SPA — nginx-served, proxies `/api/` to the backend |

### Starting the web app

```bash
docker-compose up --build
```

Then open [http://localhost:3000](http://localhost:3000).

### Views

- **Dashboard** — inventory stats (total files, size, duplicate counts) and live scan progress
- **Duplicates** — paginated duplicate groups with per-file scoring; mark individual files or auto-mark all but the best copy
- **Scan** — configure scan path and worker count, start a background scan, monitor live progress

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stats` | Inventory counts and size metrics |
| `GET` | `/api/files` | Paginated file list with search/filter |
| `POST` | `/api/files/{id}/mark` | Toggle deletion mark (`?mark=true\|false`) |
| `GET` | `/api/duplicates` | Paginated duplicate groups with scoring |
| `POST` | `/api/duplicates/{sha256}/auto-mark` | Mark all but highest-scored file for deletion |
| `POST` | `/api/scan/start` | Start background scan (`?scan_path&workers`) |
| `GET` | `/api/scan/status` | Live scan progress |

> Files marked for deletion are flagged in the database only — no files are deleted by the app.

## Quick Start

### 1. Setup Files

Create a project directory with these files:
```bash
mkdir file-inventory
cd file-inventory
```

Save the following files:
- `inventory_service.py` - Main Python service
- `Dockerfile` - Docker image definition
- `docker-compose.yml` - Docker Compose configuration

### 2. Configure

Edit `docker-compose.yml` and update the volume mount:
```yaml
volumes:
  - /path/to/your/files:/scan:ro  # Change this to your directory
  - ./data:/data                   # Persistent database storage
```

### 3. Build and Run

```bash
# Build the Docker image
docker-compose build

# Run a one-time scan
docker-compose up file-inventory

# Or run periodic scans (daily)
docker-compose up -d file-inventory-cron
```

## Usage Examples

### Basic Scan
```bash
docker-compose run --rm file-inventory
```

### Scan with Statistics
```bash
docker-compose run --rm file-inventory --stats
```

### Custom Paths
```bash
docker-compose run --rm file-inventory \
  --scan-path /scan \
  --db-path /data/inventory.db \
  --stats
```

### Direct Docker Run
```bash
docker build -t file-inventory .

docker run --rm \
  -v /path/to/scan:/scan:ro \
  -v $(pwd)/data:/data \
  file-inventory \
  --scan-path /scan \
  --stats
```

## How It Works

1. Service starts and connects to SQLite database at `/data/inventory.db`
2. Walks through all files in the scan directory
3. For each file:
   - Checks if filepath already exists in database → **skips if found**
   - Gets file size
   - Calculates SHA256 checksum
   - Gets file creation and modification dates
   - **Only after successful checksum**: inserts record into database
4. Database commits every 100 files for performance
5. Final commit and summary

## Database Schema

```sql
CREATE TABLE file_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    filesize INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    date_created TIMESTAMP,
    date_modified TIMESTAMP,
    scan_date TIMESTAMP NOT NULL
);
```

**Fields:**
- `filepath` - Full path to file (UNIQUE - prevents duplicates)
- `filename` - Just the filename
- `filesize` - File size in bytes
- `sha256` - SHA256 checksum hash
- `date_created` - File creation date (platform dependent)
- `date_modified` - File last modification date
- `scan_date` - When this record was created

## Querying the Database

```bash
# Access the database file
sqlite3 data/inventory.db

# Example queries:
.mode column
.headers on

-- View all records
SELECT * FROM file_inventory LIMIT 10;

-- Count total files
SELECT COUNT(*) as total_files FROM file_inventory;

-- Total size
SELECT SUM(filesize) / 1024.0 / 1024.0 / 1024.0 as total_gb FROM file_inventory;

-- Recent scans
SELECT * FROM file_inventory ORDER BY scan_date DESC LIMIT 10;

-- Large files
SELECT filepath, filesize / 1024.0 / 1024.0 as size_mb 
FROM file_inventory 
ORDER BY filesize DESC 
LIMIT 20;

-- Files by extension
SELECT 
    SUBSTR(filename, INSTR(filename, '.')) as extension,
    COUNT(*) as count,
    SUM(filesize) / 1024.0 / 1024.0 as total_mb
FROM file_inventory
WHERE filename LIKE '%.%'
GROUP BY extension
ORDER BY count DESC;
```

## Command Line Options

- `--scan-path PATH` - Directory to scan (default: /scan)
- `--db-path PATH` - Database file path (default: /data/inventory.db)
- `--stats` - Show statistics after scan

## Incremental Scans

The service is designed for incremental scanning:

- **First run**: Scans all files, creates all records
- **Subsequent runs**: Only processes new files
- Files already in database (by filepath) are skipped
- Perfect for running periodically to catch new files

Example workflow:
```bash
# Initial scan of 10,000 files
docker-compose up file-inventory
# Processes: 10,000 files

# Add 100 new files to directory
# Run again
docker-compose up file-inventory
# Processes: 100 files, skips 10,000
```

## Performance Notes

- SHA256 calculated using 8KB chunks (memory efficient)
- Database commits every 100 files
- Files checked against database before processing
- Only successful checksums result in database records
- Large directories: expect ~1-5 files/second depending on file sizes

## Date Field Notes

- `date_modified` - Reliable on all platforms (st_mtime)
- `date_created` - Platform dependent:
  - **macOS/Windows**: Actual creation time (st_birthtime)
  - **Linux**: Falls back to change time (st_ctime), not true creation time
- Both stored as TIMESTAMP in database

## Persistent Storage

The database is stored in `./data/inventory.db` which is mounted as a Docker volume:

```yaml
volumes:
  - ./data:/data  # Database persists here
```

This means:
- Database survives container restarts
- Can be backed up by copying `./data/inventory.db`
- Can be queried directly from host machine
- Multiple scans accumulate data in same database

## Logging

All output goes to stdout:
- `INFO` - Files being processed, progress updates, summary
- `ERROR` - Files that couldn't be processed
- `DEBUG` - Skipped files (requires debug logging level)

## Troubleshooting

**No files processed:**
- Check volume mount: `docker-compose run --rm file-inventory ls -la /scan`
- Verify scan path is correct

**All files skipped:**
- Files already exist in database
- Delete database to rescan: `rm data/inventory.db`

**Permission errors:**
- Ensure Docker can read files
- Mount as read-only is fine: `/path:/scan:ro`

**Database locked:**
- Stop all containers accessing the database
- Only one write process at a time

## Security

- Mount scan directories as read-only (`:ro`)
- No network access required
- Database stored locally
- No external dependencies

## Backup

```bash
# Backup database
cp data/inventory.db data/inventory.db.backup

# Or use SQLite backup
sqlite3 data/inventory.db ".backup data/inventory.db.backup"
```