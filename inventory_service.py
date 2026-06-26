import os
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------- Logging --------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------- Service --------------------

class FileInventoryService:
    def __init__(self, db_path='/data/inventory.db'):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Performance + safety
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL,
                filename TEXT NOT NULL,
                filesize INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(filepath, sha256)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_filepath ON file_inventory(filepath)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sha256 ON file_inventory(sha256)')

        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")

    # -------------------- Hashing --------------------

    def calculate_sha256(self, filepath, chunk_size=8192):
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                while chunk := f.read(chunk_size):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Checksum failed for {filepath}: {e}")
            return None

    def process_file(self, filepath):
        try:
            stat = filepath.stat()
            checksum = self.calculate_sha256(filepath)
            if not checksum:
                return None

            return (
                str(filepath),
                filepath.name,
                stat.st_size,
                checksum,
                datetime.now()
            )
        except Exception as e:
            logger.error(f"Processing failed for {filepath}: {e}")
            return None

    # -------------------- Inventory --------------------

    def inventory_files(self, scan_path='/scan', exclude_patterns=None, workers=None):
        exclude_patterns = exclude_patterns or []
        scan_path = Path(scan_path)

        if not scan_path.exists():
            logger.error(f"Scan path does not exist: {scan_path}")
            return

        workers = workers or max(2, os.cpu_count() or 4)
        logger.info(f"Scanning {scan_path} with {workers} worker threads")

        files = []
        for root, dirs, filenames in os.walk(scan_path):
            dirs[:] = [
                d for d in dirs
                if not any(Path(root, d).match(p) for p in exclude_patterns)
            ]
            for name in filenames:
                path = Path(root) / name
                if not any(path.match(p) for p in exclude_patterns):
                    files.append(path)

        logger.info(f"Found {len(files)} files")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        processed = 0
        errors = 0
        batch = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.process_file, f): f for f in files}

            for future in as_completed(futures):
                result = future.result()
                if not result:
                    errors += 1
                    continue

                batch.append(result)
                processed += 1

                if len(batch) >= 100:
                    cursor.executemany('''
                        INSERT OR REPLACE INTO file_inventory
                        (filepath, filename, filesize, sha256, scan_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', batch)
                    conn.commit()
                    batch.clear()
                    logger.info(f"Processed {processed} files")

        if batch:
            cursor.executemany('''
                INSERT OR REPLACE INTO file_inventory
                (filepath, filename, filesize, sha256, scan_date)
                VALUES (?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()

        conn.close()
        logger.info(f"Inventory complete: {processed} files, {errors} errors")

    # -------------------- Queries --------------------

    def get_statistics(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*), SUM(filesize) FROM file_inventory')
        count, size = cursor.fetchone()

        cursor.execute('SELECT COUNT(DISTINCT sha256) FROM file_inventory')
        unique = cursor.fetchone()[0]

        conn.close()

        return {
            'total_files': count or 0,
            'total_size_gb': (size or 0) / (1024**3),
            'unique_files': unique or 0,
            'duplicate_files': (count or 0) - (unique or 0)
        }

    def find_duplicates(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT sha256, filepath, filesize, COUNT(*)
            FROM file_inventory
            GROUP BY sha256
            HAVING COUNT(*) > 1
            ORDER BY filesize DESC
        ''')

        results = cursor.fetchall()
        conn.close()
        return results

# -------------------- CLI --------------------

def main():
    parser = argparse.ArgumentParser(description='Multithreaded File Inventory Service')
    parser.add_argument('--scan-path', default='/scan')
    parser.add_argument('--db-path', default='/data/inventory.db')
    parser.add_argument('--exclude', nargs='*', default=[])
    parser.add_argument('--workers', type=int, help='Number of worker threads')
    parser.add_argument('--stats', action='store_true')
    parser.add_argument('--find-duplicates', action='store_true')

    args = parser.parse_args()

    service = FileInventoryService(db_path=args.db_path)
    service.inventory_files(
        scan_path=args.scan_path,
        exclude_patterns=args.exclude,
        workers=args.workers
    )

    if args.stats:
        stats = service.get_statistics()
        logger.info(f"Files: {stats['total_files']}")
        logger.info(f"Size: {stats['total_size_gb']:.2f} GB")
        logger.info(f"Duplicates: {stats['duplicate_files']}")

    if args.find_duplicates:
        for sha256, path, size, count in service.find_duplicates():
            logger.info(f"{sha256} ({count} copies, {size} bytes) → {path}")

if __name__ == '__main__':
    main()
