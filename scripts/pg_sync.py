#!/usr/bin/env python3
"""
PostgreSQL Sync Service for Claude Code Activity Logs

Background daemon that syncs local per-project logs to PostgreSQL (Neon).
Runs independently - does not impact Claude Code performance.

Usage:
    python3 pg_sync.py              # Continuous sync (foreground)
    python3 pg_sync.py --daemon     # Run as background daemon
    python3 pg_sync.py --once       # Single sync pass
    python3 pg_sync.py --dry-run    # Preview without writing
    python3 pg_sync.py --status     # Check sync status

Requirements:
    pip install psycopg2-binary
"""

import os
import sys
import json
import time
import logging
import argparse
import signal
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict, field

logger = logging.getLogger("pg_sync")


@dataclass
class SyncState:
    """Tracks sync position across runs for each project"""
    projects: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_sync_time: str = ""
    total_synced: int = 0

    @classmethod
    def load(cls, state_file: Path) -> "SyncState":
        if state_file.exists():
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    state = cls()
                    state.projects = data.get("projects", {})
                    state.last_sync_time = data.get("last_sync_time", "")
                    state.total_synced = data.get("total_synced", 0)
                    return state
            except Exception as e:
                logger.warning(f"Failed to load state, starting fresh: {e}")
        return cls()

    def save(self, state_file: Path) -> None:
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "projects": self.projects,
                    "last_sync_time": self.last_sync_time,
                    "total_synced": self.total_synced
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def get_project_position(self, project_path: str) -> tuple:
        proj = self.projects.get(project_path, {})
        return proj.get("last_file", ""), proj.get("last_position", 0)

    def update_project(self, project_path: str, file: str, position: int, count: int) -> None:
        if project_path not in self.projects:
            self.projects[project_path] = {}
        self.projects[project_path].update({
            "last_file": file,
            "last_position": position,
            "last_sync": datetime.now(timezone.utc).isoformat()
        })
        self.last_sync_time = datetime.now(timezone.utc).isoformat()
        self.total_synced += count


class ProjectScanner:
    """Scans for Claude Code projects with logs to sync"""

    def __init__(self, scan_paths: List[Path]):
        self.scan_paths = scan_paths

    def find_projects_with_logs(self) -> List[Path]:
        projects = []
        for scan_path in self.scan_paths:
            if not scan_path.exists():
                continue
            if list(scan_path.glob("agent-activity-*.log")):
                projects.append(scan_path)
                continue
            log_dir = scan_path / ".claude" / "logs"
            if log_dir.exists():
                projects.append(scan_path)
            try:
                for item in scan_path.iterdir():
                    if item.is_dir() and not item.name.startswith('.'):
                        log_dir = item / ".claude" / "logs"
                        if log_dir.exists():
                            projects.append(item)
            except PermissionError:
                continue
        return projects


class LogTailer:
    """Reads new entries from a project's log files"""

    def __init__(self, project_path: Path, state: SyncState):
        self.project_path = project_path
        direct_logs = project_path / f"agent-activity-{datetime.now().strftime('%Y-%m-%d')}.log"
        if direct_logs.exists() or list(project_path.glob("agent-activity-*.log")):
            self.logs_dir = project_path
        else:
            self.logs_dir = project_path / ".claude" / "logs"
        self.state = state
        self._current_file: Optional[Path] = None
        self._current_position: int = 0

    def get_all_log_files(self) -> List[Path]:
        files = list(self.logs_dir.glob("agent-activity-*.log"))
        files.extend(self.logs_dir.glob("beads-events-*.log"))

        def extract_date(f: Path) -> str:
            name = f.stem
            for prefix in ["agent-activity-", "beads-events-"]:
                if name.startswith(prefix):
                    return name.replace(prefix, "")
            return name

        return sorted(files, key=extract_date)

    def get_unsynced_files(self) -> List[Path]:
        all_files = self.get_all_log_files()
        last_file, last_pos = self.state.get_project_position(str(self.project_path))

        if not last_file:
            return all_files

        last_filename = Path(last_file).name
        unsynced = []
        found_last = False

        for f in all_files:
            if f.name == last_filename:
                found_last = True
                file_size = f.stat().st_size
                if last_pos < file_size:
                    unsynced.append(f)
            elif found_last:
                unsynced.append(f)
            elif f.name > last_filename:
                unsynced.append(f)

        return unsynced

    def read_new_entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        entries = []
        files_to_process = self.get_unsynced_files()
        if not files_to_process:
            return entries

        logger.info(f"[{self.project_path.name}] {len(files_to_process)} file(s) to process")

        for log_file in files_to_process:
            if len(entries) >= limit:
                break

            last_file, last_pos = self.state.get_project_position(str(self.project_path))
            start_pos = last_pos if str(log_file) == last_file else 0

            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    f.seek(start_pos)
                    while len(entries) < limit:
                        line = f.readline()
                        if not line:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            entry['_source_project'] = str(self.project_path)
                            entry['_source_file'] = log_file.name
                            entries.append(entry)
                        except json.JSONDecodeError:
                            continue

                    self._current_file = log_file
                    self._current_position = f.tell()

            except Exception as e:
                logger.error(f"Failed to read {log_file}: {e}")

        return entries

    def get_position(self) -> tuple:
        return (
            str(self._current_file) if self._current_file else "",
            self._current_position
        )


class PostgresWriter:
    """Handles PostgreSQL connection and batch writes to landing.raw_events"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._conn = None

    def connect(self) -> None:
        if self._conn is not None:
            return
        import psycopg2
        logger.info("Connecting to PostgreSQL...")
        try:
            self._conn = psycopg2.connect(self.database_url)
            self._conn.autocommit = False
            logger.info("Connected to PostgreSQL successfully")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            raise

    def _generate_event_hash(self, tenant_id: str, source_system: str, event_type: str,
                              actor_id: str, subject_id: str, event_id: str) -> str:
        nk_string = f"{tenant_id}|{source_system}|{event_type}|{actor_id}|{subject_id}|{event_id}"
        return hashlib.sha256(nk_string.encode()).hexdigest()

    def _map_operation_to_event_type(self, operation: str) -> str:
        if operation.startswith('BEAD_') or operation.startswith('DEPENDENCY_') or operation.startswith('MOLECULE_'):
            return operation
        if operation == "session_summary":
            return "SESSION.SUMMARY"
        op_upper = operation.upper().replace(".", "_")
        if operation.startswith("user."):
            return f"USER.PROMPT.{op_upper.replace('USER_', '')}"
        elif operation.startswith("tool."):
            return f"BOT.TOOL.{op_upper.replace('TOOL_', '')}"
        else:
            return f"BOT.{op_upper}"

    def _is_beads_event(self, entry: Dict[str, Any]) -> bool:
        event_type = entry.get("event_type", "")
        return (
            event_type.startswith("BEAD_") or
            event_type.startswith("DEPENDENCY_") or
            event_type.startswith("MOLECULE_")
        )

    def _transform_entry(self, entry: Dict[str, Any]) -> tuple:
        if self._is_beads_event(entry):
            event_type = entry.get("event_type", "BEAD_UNKNOWN")
            bead_id = entry.get("bead_id", "unknown")
            event_id = f"bead-{int(time.time())}-{bead_id}"
            actor_id = entry.get("actor_id", "unknown")
            subject_id = bead_id
            subject_type = "BEAD"
            event_at = entry.get("timestamp", datetime.now(timezone.utc).isoformat())
            metadata = entry.get("metadata", {})
            metadata["session_id"] = entry.get("session_id", "")
            metadata["source_project"] = entry.get("_source_project")
        else:
            project = entry.get("project", "unknown")
            user = entry.get("user", "unknown")
            operation = entry.get("operation", "unknown")
            session_id = entry.get("session_id", "")
            event_id = f"cc-{entry.get('epoch', int(time.time()))}-{session_id[-8:] if session_id else 'nosess'}"
            event_type = self._map_operation_to_event_type(operation)
            actor_id = f"claude-code-{user}"
            subject_id = project
            subject_type = "PROJECT"
            event_at = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
            metadata = {
                "operation": operation,
                "prompt": entry.get("prompt"),
                "session_id": session_id,
                "user": user,
                "project": project,
                "cwd": entry.get("cwd"),
                "timestamp": entry.get("timestamp"),
                "time_local": entry.get("time"),
                "date": entry.get("date"),
                "details": entry.get("details"),
                "source_project": entry.get("_source_project"),
            }
            provider = entry.get("provider", {})
            if provider:
                metadata["provider"] = provider

        metadata = {k: v for k, v in metadata.items() if v is not None}

        tenant_id = "CLAUDE_CODE"
        source_system = "CLAUDE_CODE"
        actor_type = "BOT"

        event_hash = self._generate_event_hash(
            tenant_id, source_system, event_type, actor_id, subject_id, event_id
        )

        return (
            event_id, tenant_id, source_system, event_type, event_at,
            actor_id, actor_type, subject_id, subject_type,
            json.dumps(metadata), event_hash,
        )

    def write_batch(self, entries: List[Dict[str, Any]]) -> int:
        if not entries:
            return 0

        self.connect()
        cursor = self._conn.cursor()

        insert_sql = """
            INSERT INTO landing.raw_events (
                event_id, tenant_id, source_system, event_type, event_at,
                actor_id, actor_type, subject_id, subject_type,
                metadata, event_nk_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (event_id) DO NOTHING
        """

        try:
            inserted = 0
            for entry in entries:
                row = self._transform_entry(entry)
                cursor.execute(insert_sql, row)
                inserted += cursor.rowcount
            self._conn.commit()
            logger.info(f"Inserted {inserted} events into landing.raw_events")
            return inserted
        except Exception as e:
            logger.error(f"Failed to insert batch: {e}")
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Disconnected from PostgreSQL")


def _get_database_url() -> str:
    """Get PostgreSQL connection string from env or config."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    config_path = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        pg = config.get("destinations", {}).get("postgresql", {})
        if pg.get("connection_string"):
            return pg["connection_string"]
    raise RuntimeError(
        "No DATABASE_URL env var and no postgresql.connection_string in auto-logger-config.json"
    )


class SyncService:
    """Main orchestrator for multi-project log sync"""

    def __init__(self, config_path: Optional[Path] = None, scan_paths: Optional[List[Path]] = None):
        self.config = self._load_config(config_path)

        if scan_paths is None:
            scan_paths = [
                Path.home() / "Documents",
                Path.home() / "Projects",
                Path.home() / "Code",
                Path.cwd(),
                Path.home() / ".claude" / "logs",
            ]
        self.scan_paths = scan_paths

        self.state_file = Path.home() / ".claude" / "logs" / ".pg_sync_state.json"
        self.state = SyncState.load(self.state_file)
        self.scanner = ProjectScanner(scan_paths)
        self.writer: Optional[PostgresWriter] = None
        self.dry_run = False
        self._running = True

        pg_config = self.config.get("destinations", {}).get("postgresql", {})
        if pg_config.get("enabled", False):
            db_url = pg_config.get("connection_string") or os.environ.get("DATABASE_URL", "")
            if db_url:
                self.writer = PostgresWriter(db_url)

    def _load_config(self, config_path: Optional[Path]) -> Dict[str, Any]:
        if config_path is None:
            config_path = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
        if not config_path.exists():
            return {"destinations": {"postgresql": {"enabled": False}}}
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def run_once(self) -> int:
        pg_config = self.config.get("destinations", {}).get("postgresql", {})
        if not pg_config.get("enabled", False):
            logger.info("PostgreSQL sync is disabled in configuration")
            return 0

        projects = self.scanner.find_projects_with_logs()
        logger.info(f"Found {len(projects)} project(s) with logs")
        if not projects:
            return 0

        total_synced = 0
        batch_size = pg_config.get("sync", {}).get("batch_size", 100)

        for project_path in projects:
            if not self._running:
                break

            tailer = LogTailer(project_path, self.state)
            entries = tailer.read_new_entries(limit=batch_size)
            if not entries:
                continue

            logger.info(f"[{project_path.name}] Found {len(entries)} new entries")

            if self.dry_run:
                for entry in entries[:3]:
                    logger.info(f"  - {entry.get('operation')}: {entry.get('prompt', '')[:50]}")
                if len(entries) > 3:
                    logger.info(f"  ... and {len(entries) - 3} more")
                continue

            retry_config = pg_config.get("sync", {})
            retry_attempts = retry_config.get("retry_attempts", 3)
            retry_delay = retry_config.get("retry_delay_seconds", 5)

            for attempt in range(retry_attempts):
                try:
                    count = self.writer.write_batch(entries)
                    file_path, position = tailer.get_position()
                    self.state.update_project(str(project_path), file_path, position, count)
                    self.state.save(self.state_file)
                    total_synced += count
                    break
                except Exception as e:
                    logger.warning(f"[{project_path.name}] Sync attempt {attempt + 1} failed: {e}")
                    if attempt < retry_attempts - 1:
                        time.sleep(retry_delay)
                        if self.writer:
                            self.writer.close()
                    else:
                        logger.error(f"[{project_path.name}] All retry attempts exhausted")

        return total_synced

    def run_continuous(self) -> None:
        pg_config = self.config.get("destinations", {}).get("postgresql", {})
        if not pg_config.get("enabled", False):
            logger.error("PostgreSQL sync is disabled. Enable it in auto-logger-config.json")
            return

        flush_interval = pg_config.get("sync", {}).get("flush_interval_seconds", 60)
        logger.info(f"Starting continuous sync (interval: {flush_interval}s)")
        logger.info(f"Scanning: {[str(p) for p in self.scan_paths]}")
        logger.info("Press Ctrl+C to stop")

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        while self._running:
            try:
                synced = self.run_once()
                if synced > 0:
                    logger.info(f"Synced {synced} entries (total: {self.state.total_synced})")
            except Exception as e:
                logger.error(f"Sync error: {e}")

            for _ in range(flush_interval):
                if not self._running:
                    break
                time.sleep(1)

        self.shutdown()

    def _handle_signal(self, signum, frame):
        logger.info("Received shutdown signal...")
        self._running = False

    def shutdown(self) -> None:
        if self.writer:
            self.writer.close()
        self.state.save(self.state_file)
        logger.info(f"Final state saved. Total synced: {self.state.total_synced}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "total_synced": self.state.total_synced,
            "last_sync": self.state.last_sync_time,
            "projects_tracked": len(self.state.projects),
            "projects": {
                Path(k).name: {
                    "last_sync": v.get("last_sync", "never"),
                    "last_file": Path(v.get("last_file", "")).name if v.get("last_file") else "none"
                }
                for k, v in self.state.projects.items()
            }
        }


def setup_logging(log_file: Optional[str] = None, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.setLevel(level)


def main():
    parser = argparse.ArgumentParser(
        description="Sync Claude Code activity logs to PostgreSQL (Neon)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 pg_sync.py              # Continuous sync
    python3 pg_sync.py --daemon     # Background daemon
    python3 pg_sync.py --once       # Single sync pass
    python3 pg_sync.py --dry-run    # Preview without writing
    python3 pg_sync.py --status     # Check sync status

Configuration:
    Edit ~/.claude/hooks/auto-logger-config.json to configure PostgreSQL.
    Set destinations.postgresql.enabled = true to enable sync.
        """
    )

    parser.add_argument("--once", action="store_true", help="Single sync pass and exit")
    parser.add_argument("--daemon", action="store_true", help="Run as background daemon")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--status", action="store_true", help="Show current sync status")
    parser.add_argument("--config", type=Path, help="Path to config file")
    parser.add_argument("--log-file", type=str, help="Log output to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--scan-path", type=Path, action="append", help="Additional paths to scan")

    args = parser.parse_args()
    setup_logging(args.log_file, args.verbose)

    try:
        scan_paths = args.scan_path if args.scan_path else None
        service = SyncService(args.config, scan_paths)
        service.dry_run = args.dry_run

        if args.status:
            status = service.get_status()
            print(json.dumps(status, indent=2))
            return

        if args.daemon:
            import subprocess
            log_file = args.log_file or str(Path.home() / ".claude" / "logs" / "pg_sync_daemon.log")
            cmd = [sys.executable, __file__, "--log-file", log_file]
            if args.config:
                cmd.extend(["--config", str(args.config)])
            if args.verbose:
                cmd.append("--verbose")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            print(f"Started daemon with PID {proc.pid}")
            print(f"Logs: {log_file}")
            return

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
