#!/usr/bin/env python3
"""
Snowflake Sync Service for Claude Code Activity Logs

Asynchronously syncs local activity logs to Snowflake in batches.
Runs as a separate process - does not impact Claude Code performance.

Usage:
    python snowflake_sync.py              # Continuous sync (foreground)
    python snowflake_sync.py --once       # Single sync pass
    python snowflake_sync.py --dry-run    # Show what would sync without writing
    python snowflake_sync.py --log-file sync.log  # Log to file instead of stdout

Requirements:
    pip install snowflake-connector-python cryptography
"""

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

# Configure logging
logger = logging.getLogger("snowflake_sync")


@dataclass
class SyncState:
    """Tracks sync position across runs"""
    last_file: str = ""
    last_position: int = 0
    last_epoch: int = 0
    last_sync_time: str = ""
    total_synced: int = 0

    @classmethod
    def load(cls, state_file: Path) -> "SyncState":
        """Load state from file or return fresh state"""
        if state_file.exists():
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return cls(**data)
            except Exception as e:
                logger.warning(f"Failed to load state, starting fresh: {e}")
        return cls()

    def save(self, state_file: Path) -> None:
        """Save state to file"""
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def update(self, file: str, position: int, epoch: int, count: int) -> None:
        """Update state after successful sync"""
        self.last_file = file
        self.last_position = position
        self.last_epoch = epoch
        self.last_sync_time = datetime.utcnow().isoformat() + "Z"
        self.total_synced += count


class LogTailer:
    """Reads new entries from local log files"""

    def __init__(self, logs_dir: Path, state: SyncState):
        self.logs_dir = logs_dir
        self.state = state
        self._current_file: Optional[Path] = None
        self._current_position: int = 0

    def get_current_log_file(self) -> Optional[Path]:
        """Get today's log file"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_file = self.logs_dir / f"agent-activity-{date}.log"
        return log_file if log_file.exists() else None

    def _normalize_path(self, path) -> str:
        """Normalize path for consistent comparison across platforms"""
        if path is None:
            return ""
        return str(Path(path).resolve())

    def read_new_entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Read new entries since last sync"""
        entries = []
        log_file = self.get_current_log_file()

        if not log_file:
            return entries

        # Determine starting position (normalize paths for cross-platform comparison)
        current_path = self._normalize_path(log_file)
        state_path = self._normalize_path(self.state.last_file) if self.state.last_file else ""

        if current_path == state_path:
            start_pos = self.state.last_position
        else:
            # New file (day rolled over) - start from beginning
            start_pos = 0
            logger.info(f"New log file detected: {log_file.name}")

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                f.seek(start_pos)

                # Use readline() instead of iteration to allow tell() to work
                while len(entries) < limit:
                    line = f.readline()
                    if not line:  # EOF
                        break

                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping malformed line: {e}")
                        continue

                self._current_file = log_file
                self._current_position = f.tell()

        except Exception as e:
            logger.error(f"Failed to read log file: {e}")

        return entries

    def get_position(self) -> tuple:
        """Get current file and position for state update"""
        return (
            self._normalize_path(self._current_file) if self._current_file else "",
            self._current_position
        )


class SnowflakeWriter:
    """Handles Snowflake connection and batch writes"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._conn = None

    def _get_private_key(self) -> bytes:
        """Load private key for authentication"""
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        key_path = self.config["auth"]["private_key_path"]
        passphrase_env = self.config["auth"].get("private_key_passphrase_env", "")
        passphrase = os.environ.get(passphrase_env, "").encode() if passphrase_env else None

        with open(key_path, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=passphrase,
                backend=default_backend()
            )

        return private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

    def connect(self) -> None:
        """Establish Snowflake connection"""
        if self._conn is not None:
            return

        import snowflake.connector

        logger.info("Connecting to Snowflake...")

        private_key = self._get_private_key()

        self._conn = snowflake.connector.connect(
            account=self.config["account"],
            user=self.config["auth"]["user"],
            private_key=private_key,
            warehouse=self.config["warehouse"],
            database=self.config["database"],
            schema=self.config["schema"]
        )

        logger.info("Connected to Snowflake")

    def write_batch(self, entries: List[Dict[str, Any]], source_file: str) -> int:
        """Write batch of entries to Snowflake"""
        if not entries:
            return 0

        self.connect()
        cursor = self._conn.cursor()

        table = self.config.get("table", "CLAUDE_ACTIVITY_LOG")

        insert_sql = f"""
            INSERT INTO {table} (
                epoch, log_date, log_year, log_month, log_day, log_hour,
                timestamp_utc, time_local, operation, prompt, session_id,
                cwd, project, details, source_file, source_line
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """

        rows = []
        for i, entry in enumerate(entries):
            rows.append((
                entry.get("epoch"),
                entry.get("date"),
                entry.get("year"),
                entry.get("month"),
                entry.get("day"),
                entry.get("hour"),
                entry.get("timestamp"),
                entry.get("time"),
                entry.get("operation"),
                entry.get("prompt"),
                entry.get("session_id"),
                entry.get("cwd"),
                entry.get("project"),
                json.dumps(entry.get("details")) if entry.get("details") else None,
                Path(source_file).name if source_file else None,
                i + 1
            ))

        try:
            cursor.executemany(insert_sql, rows)
            self._conn.commit()
            logger.info(f"Inserted {len(rows)} rows into Snowflake")
            return len(rows)
        except Exception as e:
            logger.error(f"Failed to insert batch: {e}")
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def close(self) -> None:
        """Close Snowflake connection"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Disconnected from Snowflake")


class SyncService:
    """Main orchestrator for log sync"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config = self._load_config(config_path)
        self.logs_dir = self._get_logs_dir()
        self.state_file = self.logs_dir / ".sync_state.json"
        self.state = SyncState.load(self.state_file)
        self.tailer = LogTailer(self.logs_dir, self.state)
        self.writer: Optional[SnowflakeWriter] = None
        self.dry_run = False

        sf_config = self.config.get("destinations", {}).get("snowflake", {})
        if sf_config.get("enabled", False):
            self.writer = SnowflakeWriter(sf_config)

    def _load_config(self, config_path: Optional[Path]) -> Dict[str, Any]:
        """Load configuration"""
        if config_path is None:
            # Default location
            config_path = Path(__file__).parent / "auto-logger-config.json"

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _get_logs_dir(self) -> Path:
        """Get logs directory from config"""
        local_config = self.config.get("destinations", {}).get("local", {})
        log_path = local_config.get("path", "logs")

        base_dir = Path(__file__).parent.parent

        if Path(log_path).is_absolute():
            return Path(log_path)
        return base_dir / log_path

    def run_once(self) -> int:
        """Run a single sync pass, return entries synced"""
        sf_config = self.config.get("destinations", {}).get("snowflake", {})

        if not sf_config.get("enabled", False):
            logger.info("Snowflake sync is disabled in configuration")
            return 0

        batch_size = sf_config.get("sync", {}).get("batch_size", 100)
        entries = self.tailer.read_new_entries(limit=batch_size)

        if not entries:
            logger.debug("No new entries to sync")
            return 0

        logger.info(f"Found {len(entries)} new entries")

        if self.dry_run:
            logger.info("[DRY RUN] Would sync the following entries:")
            for entry in entries[:5]:
                logger.info(f"  - {entry.get('operation')}: {entry.get('prompt', '')[:50]}")
            if len(entries) > 5:
                logger.info(f"  ... and {len(entries) - 5} more")
            return 0

        # Write to Snowflake
        file_path, position = self.tailer.get_position()

        retry_config = sf_config.get("sync", {})
        retry_attempts = retry_config.get("retry_attempts", 3)
        retry_delay = retry_config.get("retry_delay_seconds", 5)

        for attempt in range(retry_attempts):
            try:
                count = self.writer.write_batch(entries, file_path)

                # Update state
                last_epoch = entries[-1].get("epoch", 0) if entries else 0
                self.state.update(file_path, position, last_epoch, count)
                self.state.save(self.state_file)

                return count
            except Exception as e:
                logger.warning(f"Sync attempt {attempt + 1} failed: {e}")
                if attempt < retry_attempts - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error("All retry attempts exhausted")
                    raise

        return 0

    def run_continuous(self) -> None:
        """Run continuous sync loop"""
        sf_config = self.config.get("destinations", {}).get("snowflake", {})

        if not sf_config.get("enabled", False):
            logger.error("Snowflake sync is disabled. Enable it in auto-logger-config.json")
            logger.error("Set destinations.snowflake.enabled = true")
            return

        flush_interval = sf_config.get("sync", {}).get("flush_interval_seconds", 60)

        logger.info(f"Starting continuous sync (interval: {flush_interval}s)")
        logger.info(f"Logs directory: {self.logs_dir}")
        logger.info(f"State file: {self.state_file}")
        logger.info("Press Ctrl+C to stop")

        try:
            while True:
                try:
                    synced = self.run_once()
                    if synced > 0:
                        logger.info(f"Synced {synced} entries (total: {self.state.total_synced})")
                except Exception as e:
                    logger.error(f"Sync error: {e}")

                time.sleep(flush_interval)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.shutdown()

    def shutdown(self) -> None:
        """Clean shutdown"""
        if self.writer:
            self.writer.close()
        self.state.save(self.state_file)
        logger.info(f"Final state saved. Total synced: {self.state.total_synced}")


def setup_logging(log_file: Optional[str] = None, verbose: bool = False) -> None:
    """Configure logging output"""
    level = logging.DEBUG if verbose else logging.INFO

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler (always)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.setLevel(level)


def main():
    parser = argparse.ArgumentParser(
        description="Sync Claude Code activity logs to Snowflake",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python snowflake_sync.py              # Continuous sync
    python snowflake_sync.py --once       # Single sync pass
    python snowflake_sync.py --dry-run    # Preview without writing
    python snowflake_sync.py --log-file sync.log  # Log to file

Configuration:
    Edit .claude/hooks/auto-logger-config.json to configure Snowflake connection.
    Set destinations.snowflake.enabled = true to enable sync.
        """
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync pass and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without writing to Snowflake"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config file (default: .claude/hooks/auto-logger-config.json)"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Log output to file instead of just stdout"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    setup_logging(args.log_file, args.verbose)

    try:
        service = SyncService(args.config)
        service.dry_run = args.dry_run

        if args.once or args.dry_run:
            count = service.run_once()
            if not args.dry_run:
                logger.info(f"Sync complete. Entries synced: {count}")
        else:
            service.run_continuous()

    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
