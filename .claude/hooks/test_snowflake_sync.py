#!/usr/bin/env python3
"""
Test suite for Snowflake Sync Service

Run with: pytest test_snowflake_sync.py -v
"""

import os
import sys
import json
import time
import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, mock_open

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Mock snowflake.connector BEFORE importing snowflake_sync
# This prevents "ModuleNotFoundError: No module named 'snowflake'"
mock_snowflake = MagicMock()
mock_snowflake.connector = MagicMock()
sys.modules['snowflake'] = mock_snowflake
sys.modules['snowflake.connector'] = mock_snowflake.connector

from snowflake_sync import (
    SyncState,
    LogTailer,
    SnowflakeWriter,
    SyncService,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_log_entries():
    """Sample log entries for testing"""
    base_epoch = int(time.time())
    return [
        {
            "epoch": base_epoch,
            "date": "2025-12-10",
            "year": 2025,
            "month": 12,
            "day": 10,
            "hour": 14,
            "timestamp": "2025-12-10T14:00:00.000000Z",
            "time": "14:00:00",
            "operation": "read",
            "prompt": "read: test.py",
            "session_id": "session-20251210-140000-abc123",
            "cwd": "/test/project",
            "project": "test-project",
            "details": {"tool_name": "Read", "file_path": "/test/test.py"}
        },
        {
            "epoch": base_epoch + 1,
            "date": "2025-12-10",
            "year": 2025,
            "month": 12,
            "day": 10,
            "hour": 14,
            "timestamp": "2025-12-10T14:00:01.000000Z",
            "time": "14:00:01",
            "operation": "bash",
            "prompt": "List files",
            "session_id": "session-20251210-140001-def456",
            "cwd": "/test/project",
            "project": "test-project",
            "details": {"tool_name": "Bash", "command": "ls -la", "description": "List files"}
        },
        {
            "epoch": base_epoch + 2,
            "date": "2025-12-10",
            "year": 2025,
            "month": 12,
            "day": 10,
            "hour": 14,
            "timestamp": "2025-12-10T14:00:02.000000Z",
            "time": "14:00:02",
            "operation": "write",
            "prompt": "write: output.txt",
            "session_id": "session-20251210-140002-ghi789",
            "cwd": "/test/project",
            "project": "test-project",
            "details": {"tool_name": "Write", "file_path": "/test/output.txt"}
        }
    ]


@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    return {
        "enabled": True,
        "destinations": {
            "local": {
                "enabled": True,
                "path": "logs"
            },
            "snowflake": {
                "enabled": True,
                "account": "test-account.us-east-1",
                "warehouse": "TEST_WH",
                "database": "TEST_DB",
                "schema": "TEST_SCHEMA",
                "table": "CLAUDE_ACTIVITY_LOG",
                "auth": {
                    "type": "key_pair",
                    "user": "TEST_USER",
                    "private_key_path": "/path/to/key.p8",
                    "private_key_passphrase_env": "TEST_PASSPHRASE"
                },
                "sync": {
                    "batch_size": 100,
                    "flush_interval_seconds": 60,
                    "retry_attempts": 3,
                    "retry_delay_seconds": 1
                }
            }
        }
    }


@pytest.fixture
def log_file(temp_dir, sample_log_entries):
    """Create a log file with sample entries"""
    date = datetime.now().strftime("%Y-%m-%d")
    log_path = temp_dir / f"agent-activity-{date}.log"

    with open(log_path, 'w', encoding='utf-8') as f:
        for entry in sample_log_entries:
            f.write(json.dumps(entry) + "\n")

    return log_path


@pytest.fixture
def config_file(temp_dir, sample_config):
    """Create a config file"""
    config_path = temp_dir / "auto-logger-config.json"
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(sample_config, f)
    return config_path


# =============================================================================
# SyncState Tests
# =============================================================================

class TestSyncState:
    """Tests for SyncState class"""

    def test_default_state(self):
        """Test default state values"""
        state = SyncState()
        assert state.last_file == ""
        assert state.last_position == 0
        assert state.last_epoch == 0
        assert state.last_sync_time == ""
        assert state.total_synced == 0

    def test_save_and_load(self, temp_dir):
        """Test state persistence"""
        state_file = temp_dir / ".sync_state.json"

        # Create and save state
        state = SyncState(
            last_file="test.log",
            last_position=1234,
            last_epoch=1733839514,
            last_sync_time="2025-12-10T14:00:00Z",
            total_synced=100
        )
        state.save(state_file)

        # Load state
        loaded = SyncState.load(state_file)

        assert loaded.last_file == "test.log"
        assert loaded.last_position == 1234
        assert loaded.last_epoch == 1733839514
        assert loaded.last_sync_time == "2025-12-10T14:00:00Z"
        assert loaded.total_synced == 100

    def test_load_missing_file(self, temp_dir):
        """Test loading from non-existent file returns default state"""
        state_file = temp_dir / "nonexistent.json"
        state = SyncState.load(state_file)

        assert state.last_file == ""
        assert state.last_position == 0

    def test_load_corrupt_file(self, temp_dir):
        """Test loading from corrupt file returns default state"""
        state_file = temp_dir / ".sync_state.json"
        with open(state_file, 'w') as f:
            f.write("not valid json {{{")

        state = SyncState.load(state_file)
        assert state.last_file == ""
        assert state.last_position == 0

    def test_update(self):
        """Test state update method"""
        state = SyncState()
        state.update("test.log", 500, 1733839514, 50)

        assert state.last_file == "test.log"
        assert state.last_position == 500
        assert state.last_epoch == 1733839514
        assert state.total_synced == 50
        assert state.last_sync_time != ""

    def test_update_accumulates_total(self):
        """Test that total_synced accumulates"""
        state = SyncState(total_synced=100)
        state.update("test.log", 500, 1733839514, 50)

        assert state.total_synced == 150


# =============================================================================
# LogTailer Tests
# =============================================================================

class TestLogTailer:
    """Tests for LogTailer class"""

    def test_read_new_entries(self, temp_dir, log_file, sample_log_entries):
        """Test reading entries from log file"""
        state = SyncState()
        tailer = LogTailer(temp_dir, state)

        entries = tailer.read_new_entries(limit=10)

        assert len(entries) == 3
        assert entries[0]["operation"] == "read"
        assert entries[1]["operation"] == "bash"
        assert entries[2]["operation"] == "write"

    def test_read_respects_limit(self, temp_dir, log_file):
        """Test that limit parameter is respected"""
        state = SyncState()
        tailer = LogTailer(temp_dir, state)

        entries = tailer.read_new_entries(limit=2)

        assert len(entries) == 2

    def test_read_from_position(self, temp_dir, log_file):
        """Test reading from a specific position"""
        # First, read all to get position
        state = SyncState()
        tailer = LogTailer(temp_dir, state)
        entries1 = tailer.read_new_entries(limit=2)
        file_path, position = tailer.get_position()

        # Update state with position
        state.last_file = file_path
        state.last_position = position

        # Create new tailer with updated state
        tailer2 = LogTailer(temp_dir, state)
        entries2 = tailer2.read_new_entries(limit=10)

        # Should only get remaining entry
        assert len(entries2) == 1
        assert entries2[0]["operation"] == "write"

    def test_read_empty_directory(self, temp_dir):
        """Test reading from directory with no log files"""
        state = SyncState()
        tailer = LogTailer(temp_dir, state)

        entries = tailer.read_new_entries()

        assert len(entries) == 0

    def test_skip_malformed_entries(self, temp_dir):
        """Test that malformed JSON lines are skipped"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = temp_dir / f"agent-activity-{date}.log"

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('{"valid": "entry", "operation": "test"}\n')
            f.write('not valid json\n')
            f.write('{"another": "valid", "operation": "test2"}\n')

        state = SyncState()
        tailer = LogTailer(temp_dir, state)
        entries = tailer.read_new_entries()

        assert len(entries) == 2

    def test_get_position(self, temp_dir, log_file):
        """Test position tracking"""
        state = SyncState()
        tailer = LogTailer(temp_dir, state)

        tailer.read_new_entries(limit=1)
        file_path, position = tailer.get_position()

        assert file_path != ""
        assert position > 0


# =============================================================================
# SnowflakeWriter Tests (Mocked)
# =============================================================================

class TestSnowflakeWriter:
    """Tests for SnowflakeWriter class with mocked Snowflake"""

    @pytest.fixture
    def writer_config(self, sample_config):
        """Get just the Snowflake config portion"""
        return sample_config["destinations"]["snowflake"]

    @pytest.fixture(autouse=True)
    def reset_snowflake_mock(self):
        """Reset the snowflake mock before each test"""
        mock_snowflake.connector.connect.reset_mock()
        yield

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_connect(self, mock_get_key, writer_config):
        """Test Snowflake connection"""
        mock_get_key.return_value = b'fake_key_bytes'
        mock_conn = MagicMock()
        mock_snowflake.connector.connect.return_value = mock_conn

        writer = SnowflakeWriter(writer_config)
        writer.connect()

        mock_snowflake.connector.connect.assert_called_once()
        call_kwargs = mock_snowflake.connector.connect.call_args[1]
        assert call_kwargs['account'] == 'test-account.us-east-1'
        assert call_kwargs['user'] == 'TEST_USER'
        assert call_kwargs['warehouse'] == 'TEST_WH'

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_write_batch(self, mock_get_key, writer_config, sample_log_entries):
        """Test batch write to Snowflake"""
        mock_get_key.return_value = b'fake_key_bytes'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.return_value = mock_conn

        writer = SnowflakeWriter(writer_config)
        count = writer.write_batch(sample_log_entries, "test.log")

        assert count == 3
        mock_cursor.executemany.assert_called_once()
        mock_conn.commit.assert_called_once()

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_write_empty_batch(self, mock_get_key, writer_config):
        """Test writing empty batch returns 0"""
        mock_get_key.return_value = b'fake_key_bytes'

        writer = SnowflakeWriter(writer_config)
        count = writer.write_batch([], "test.log")

        assert count == 0
        # Connect should not be called for empty batch
        mock_snowflake.connector.connect.assert_not_called()

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_write_batch_failure_rollback(self, mock_get_key, writer_config, sample_log_entries):
        """Test rollback on insert failure"""
        mock_get_key.return_value = b'fake_key_bytes'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.executemany.side_effect = Exception("Insert failed")
        mock_conn.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.return_value = mock_conn

        writer = SnowflakeWriter(writer_config)

        with pytest.raises(Exception, match="Insert failed"):
            writer.write_batch(sample_log_entries, "test.log")

        mock_conn.rollback.assert_called_once()

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_close(self, mock_get_key, writer_config):
        """Test connection close"""
        mock_get_key.return_value = b'fake_key_bytes'
        mock_conn = MagicMock()
        mock_snowflake.connector.connect.return_value = mock_conn

        writer = SnowflakeWriter(writer_config)
        writer.connect()
        writer.close()

        mock_conn.close.assert_called_once()

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_connection_reuse(self, mock_get_key, writer_config):
        """Test that connection is reused"""
        mock_get_key.return_value = b'fake_key_bytes'
        mock_conn = MagicMock()
        mock_snowflake.connector.connect.return_value = mock_conn

        writer = SnowflakeWriter(writer_config)
        writer.connect()
        writer.connect()
        writer.connect()

        # Should only connect once
        assert mock_snowflake.connector.connect.call_count == 1


# =============================================================================
# SyncService Integration Tests
# =============================================================================

class TestSyncService:
    """Integration tests for SyncService"""

    def test_disabled_snowflake(self, temp_dir, sample_config):
        """Test service with Snowflake disabled"""
        sample_config["destinations"]["snowflake"]["enabled"] = False

        config_path = temp_dir / "config.json"
        with open(config_path, 'w') as f:
            json.dump(sample_config, f)

        # Create logs dir structure
        hooks_dir = temp_dir / "hooks"
        hooks_dir.mkdir()
        logs_dir = temp_dir / "logs"
        logs_dir.mkdir()

        # Move config to hooks dir
        final_config = hooks_dir / "auto-logger-config.json"
        with open(final_config, 'w') as f:
            json.dump(sample_config, f)

        with patch.object(Path, 'parent', new_callable=lambda: property(lambda self: temp_dir)):
            service = SyncService(final_config)
            count = service.run_once()

        assert count == 0

    @patch('snowflake_sync.SnowflakeWriter')
    def test_run_once_with_entries(self, mock_writer_class, temp_dir, sample_config, sample_log_entries):
        """Test single sync pass with entries"""
        # Setup mock
        mock_writer = MagicMock()
        mock_writer.write_batch.return_value = 3
        mock_writer_class.return_value = mock_writer

        # Create directory structure
        logs_dir = temp_dir / "logs"
        logs_dir.mkdir()

        # Create log file
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = logs_dir / f"agent-activity-{date}.log"
        with open(log_path, 'w', encoding='utf-8') as f:
            for entry in sample_log_entries:
                f.write(json.dumps(entry) + "\n")

        # Create config
        hooks_dir = temp_dir / "hooks"
        hooks_dir.mkdir()
        config_path = hooks_dir / "auto-logger-config.json"
        with open(config_path, 'w') as f:
            json.dump(sample_config, f)

        # Patch to use temp directory
        with patch.object(SyncService, '_get_logs_dir', return_value=logs_dir):
            service = SyncService(config_path)
            service.writer = mock_writer
            count = service.run_once()

        assert count == 3
        mock_writer.write_batch.assert_called_once()

    def test_dry_run_mode(self, temp_dir, sample_config, sample_log_entries):
        """Test dry run doesn't write to Snowflake"""
        # Create directory structure
        logs_dir = temp_dir / "logs"
        logs_dir.mkdir()

        # Create log file
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = logs_dir / f"agent-activity-{date}.log"
        with open(log_path, 'w', encoding='utf-8') as f:
            for entry in sample_log_entries:
                f.write(json.dumps(entry) + "\n")

        # Create config
        hooks_dir = temp_dir / "hooks"
        hooks_dir.mkdir()
        config_path = hooks_dir / "auto-logger-config.json"
        with open(config_path, 'w') as f:
            json.dump(sample_config, f)

        with patch.object(SyncService, '_get_logs_dir', return_value=logs_dir):
            service = SyncService(config_path)
            service.dry_run = True
            service.writer = MagicMock()
            count = service.run_once()

        assert count == 0
        service.writer.write_batch.assert_not_called()


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Tests for error handling scenarios"""

    @pytest.fixture(autouse=True)
    def reset_snowflake_mock(self):
        """Reset the snowflake mock before each test"""
        mock_snowflake.connector.connect.reset_mock()
        mock_snowflake.connector.connect.side_effect = None
        yield

    @patch('snowflake_sync.SnowflakeWriter._get_private_key')
    def test_retry_on_connection_failure(self, mock_get_key, sample_config, sample_log_entries):
        """Test retry logic on connection failure"""
        mock_get_key.return_value = b'fake_key_bytes'

        # Fail twice, succeed third time
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_snowflake.connector.connect.side_effect = [
            Exception("Connection failed"),
            Exception("Connection failed"),
            mock_conn
        ]

        sf_config = sample_config["destinations"]["snowflake"]
        sf_config["sync"]["retry_delay_seconds"] = 0.1  # Fast retry for test

        writer = SnowflakeWriter(sf_config)

        # First two calls should fail
        with pytest.raises(Exception):
            writer.connect()
        with pytest.raises(Exception):
            writer.connect()

        # Reset the connection state
        writer._conn = None
        mock_snowflake.connector.connect.side_effect = None
        mock_snowflake.connector.connect.return_value = mock_conn

        # Third should succeed
        writer.connect()
        assert writer._conn is not None

    def test_missing_config_file(self, temp_dir):
        """Test handling of missing config file"""
        with pytest.raises(FileNotFoundError):
            SyncService(temp_dir / "nonexistent.json")

    def test_state_survives_crash(self, temp_dir, sample_log_entries):
        """Test that state is preserved if service crashes"""
        logs_dir = temp_dir / "logs"
        logs_dir.mkdir()
        state_file = logs_dir / ".sync_state.json"

        # Simulate state after some syncing
        state = SyncState(
            last_file="test.log",
            last_position=500,
            last_epoch=1733839514,
            total_synced=100
        )
        state.save(state_file)

        # Verify state persists
        loaded = SyncState.load(state_file)
        assert loaded.total_synced == 100
        assert loaded.last_position == 500


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases"""

    def test_empty_log_file(self, temp_dir):
        """Test handling of empty log file"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = temp_dir / f"agent-activity-{date}.log"
        log_path.touch()  # Create empty file

        state = SyncState()
        tailer = LogTailer(temp_dir, state)
        entries = tailer.read_new_entries()

        assert len(entries) == 0

    def test_entries_with_missing_fields(self, temp_dir):
        """Test handling entries with missing optional fields"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = temp_dir / f"agent-activity-{date}.log"

        # Entry with minimal fields
        minimal_entry = {
            "epoch": 1733839514,
            "operation": "test"
        }

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(minimal_entry) + "\n")

        state = SyncState()
        tailer = LogTailer(temp_dir, state)
        entries = tailer.read_new_entries()

        assert len(entries) == 1
        assert entries[0]["operation"] == "test"

    def test_very_long_prompt(self, temp_dir):
        """Test handling of entries with very long prompts"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = temp_dir / f"agent-activity-{date}.log"

        long_entry = {
            "epoch": 1733839514,
            "operation": "test",
            "prompt": "x" * 10000  # Very long prompt
        }

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(long_entry) + "\n")

        state = SyncState()
        tailer = LogTailer(temp_dir, state)
        entries = tailer.read_new_entries()

        assert len(entries) == 1
        assert len(entries[0]["prompt"]) == 10000

    def test_unicode_content(self, temp_dir):
        """Test handling of Unicode content"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = temp_dir / f"agent-activity-{date}.log"

        unicode_entry = {
            "epoch": 1733839514,
            "operation": "test",
            "prompt": "Hello 世界 🌍 émojis"
        }

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(unicode_entry, ensure_ascii=False) + "\n")

        state = SyncState()
        tailer = LogTailer(temp_dir, state)
        entries = tailer.read_new_entries()

        assert len(entries) == 1
        assert "世界" in entries[0]["prompt"]
        assert "🌍" in entries[0]["prompt"]

    def test_large_batch(self, temp_dir):
        """Test handling of large number of entries"""
        date = datetime.now().strftime("%Y-%m-%d")
        log_path = temp_dir / f"agent-activity-{date}.log"

        # Create 1000 entries
        with open(log_path, 'w', encoding='utf-8') as f:
            for i in range(1000):
                entry = {"epoch": 1733839514 + i, "operation": f"test_{i}"}
                f.write(json.dumps(entry) + "\n")

        state = SyncState()
        tailer = LogTailer(temp_dir, state)

        # Read in batches
        all_entries = []
        while True:
            entries = tailer.read_new_entries(limit=100)
            if not entries:
                break
            all_entries.extend(entries)
            file_path, pos = tailer.get_position()
            state.last_file = file_path
            state.last_position = pos

        assert len(all_entries) == 1000


# =============================================================================
# Run tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
