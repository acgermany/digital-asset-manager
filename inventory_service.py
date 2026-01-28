import os
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FileInventoryService:
    def __init__(self, db_path='/data/inventory.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database with inventory table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
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
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_filepath ON file_inventory(filepath)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sha256 ON file_inventory(sha256)
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")
    
    def calculate_sha256(self, filepath, chunk_size=8192):
        """Calculate SHA256 checksum of a file"""
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                while chunk := f.read(chunk_size):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating checksum for {filepath}: {e}")
            return None
    
    def inventory_files(self, scan_path='/scan', exclude_patterns=None):
        """Scan directory and inventory all files"""
        if exclude_patterns is None:
            exclude_patterns = []
        
        scan_path = Path(scan_path)
        if not scan_path.exists():
            logger.error(f"Scan path does not exist: {scan_path}")
            return
        
        logger.info(f"Starting inventory scan of {scan_path}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        file_count = 0
        error_count = 0
        
        for root, dirs, files in os.walk(scan_path):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if not any(
                Path(root).joinpath(d).match(pattern) 
                for pattern in exclude_patterns
            )]
            
            for filename in files:
                filepath = Path(root) / filename
                
                # Skip if matches exclude pattern
                if any(filepath.match(pattern) for pattern in exclude_patterns):
                    continue
                
                try:
                    file_stat = filepath.stat()
                    filesize = file_stat.st_size
                    
                    logger.info(f"Processing: {filepath}")
                    
                    checksum = self.calculate_sha256(filepath)
                    if checksum is None:
                        error_count += 1
                        continue
                    
                    cursor.execute('''
                        INSERT OR REPLACE INTO file_inventory 
                        (filepath, filename, filesize, sha256, scan_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        str(filepath),
                        filename,
                        filesize,
                        checksum,
                        datetime.now()
                    ))
                    
                    file_count += 1
                    
                    if file_count % 100 == 0:
                        conn.commit()
                        logger.info(f"Processed {file_count} files...")
                
                except Exception as e:
                    logger.error(f"Error processing {filepath}: {e}")
                    error_count += 1
        
        conn.commit()
        conn.close()
        
        logger.info(f"Inventory complete: {file_count} files processed, {error_count} errors")
    
    def get_statistics(self):
        """Get inventory statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*), SUM(filesize) FROM file_inventory')
        count, total_size = cursor.fetchone()
        
        cursor.execute('SELECT COUNT(DISTINCT sha256) FROM file_inventory')
        unique_files = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_files': count or 0,
            'total_size_bytes': total_size or 0,
            'total_size_gb': (total_size or 0) / (1024**3),
            'unique_checksums': unique_files or 0,
            'duplicate_files': (count or 0) - (unique_files or 0)
        }
    
    def find_duplicates(self):
        """Find duplicate files based on SHA256"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT sha256, filepath, filesize, COUNT(*) as count
            FROM file_inventory
            GROUP BY sha256
            HAVING count > 1
            ORDER BY filesize DESC
        ''')
        
        duplicates = cursor.fetchall()
        conn.close()
        
        return duplicates


def main():
    parser = argparse.ArgumentParser(
        description='File Inventory Service - Scan and checksum files'
    )
    parser.add_argument(
        '--scan-path',
        default='/scan',
        help='Path to scan (default: /scan)'
    )
    parser.add_argument(
        '--db-path',
        default='/data/inventory.db',
        help='Database path (default: /data/inventory.db)'
    )
    parser.add_argument(
        '--exclude',
        nargs='*',
        default=[],
        help='Exclude patterns (e.g., *.tmp __pycache__)'
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Show statistics after scan'
    )
    parser.add_argument(
        '--find-duplicates',
        action='store_true',
        help='Find and display duplicate files'
    )
    
    args = parser.parse_args()
    
    service = FileInventoryService(db_path=args.db_path)
    service.inventory_files(scan_path=args.scan_path, exclude_patterns=args.exclude)
    
    if args.stats:
        stats = service.get_statistics()
        logger.info("\n=== Inventory Statistics ===")
        logger.info(f"Total files: {stats['total_files']}")
        logger.info(f"Total size: {stats['total_size_gb']:.2f} GB")
        logger.info(f"Unique files: {stats['unique_checksums']}")
        logger.info(f"Duplicate files: {stats['duplicate_files']}")
    
    if args.find_duplicates:
        duplicates = service.find_duplicates()
        if duplicates:
            logger.info("\n=== Duplicate Files ===")
            for sha256, filepath, filesize, count in duplicates:
                logger.info(f"SHA256: {sha256}")
                logger.info(f"  Size: {filesize} bytes, Count: {count}")


if __name__ == '__main__':
    main()