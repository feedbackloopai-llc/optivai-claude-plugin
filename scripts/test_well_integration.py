#!/usr/bin/env python3
"""
Test script to verify The Well integration is working.
This inserts a test event and verifies it appears in RAW_EVENTS.

Usage:
    python3 test_well_integration.py
"""

import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone


def main():
    # Check dependencies
    try:
        import psycopg2
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Run: pip install psycopg2-binary cryptography")
        sys.exit(1)

    # Load config
    config_path = Path.home() / ".claude" / "hooks" / "auto-logger-config.json"
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        print("Copy config/auto-logger-config.example.json to ~/.claude/hooks/auto-logger-config.json")
        sys.exit(1)

    with open(config_path, 'r') as f:
        config = json.load(f)

    sf_config = config.get("destinations", {}).get("postgresql", {})
    if not sf_config.get("enabled"):
        print("PostgreSQL is disabled in config. Set postgresql.enabled = true")
        sys.exit(1)

    # Load private key
    key_path = Path(sf_config["auth"]["private_key_path"]).expanduser()
    print(f"Loading key from: {key_path}")

    with open(key_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend()
        )

    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    # Connect
    print(f"Connecting to PostgreSQL ({sf_config['account']})...")
    conn = psycopg2.connect(
        account=sf_config["account"],
        user=sf_config["auth"]["user"],
        private_key=private_key_bytes,
        warehouse=sf_config["warehouse"],
        role=sf_config.get("role", "ACCOUNTADMIN")
    )
    print("Connected!")

    cursor = conn.cursor()

    # Test event data
    event_id = f"test-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    tenant_id = sf_config.get("tenant_id", "CLAUDE_CODE")
    source_system = sf_config.get("source_system", "CLAUDE_CODE")
    event_type = "BOT.TOOL.TEST"
    actor_id = "claude-code-test"
    subject_id = "test-integration"
    target_table = sf_config.get("target_table", "YOUR_DW_SCHEMA.LANDING.RAW_EVENTS")

    metadata = {
        "test": True,
        "purpose": "Verify PostgreSQL integration",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Generate hash
    nk_string = f"{tenant_id}|{source_system}|{event_type}|{actor_id}|{subject_id}|{event_id}"
    event_hash = hashlib.sha256(nk_string.encode()).hexdigest()

    print(f"\n{'='*60}")
    print("Inserting test event...")
    print(f"{'='*60}")
    print(f"  TABLE: {target_table}")
    print(f"  EVENT_ID: {event_id}")
    print(f"  EVENT_TYPE: {event_type}")
    print(f"  TENANT_ID: {tenant_id}")

    try:
        cursor.execute(f"""
            INSERT INTO {target_table} (
                EVENT_ID, TENANT_ID, SOURCE_SYSTEM, EVENT_TYPE, EVENT_AT,
                ACTOR_ID, ACTOR_TYPE, SUBJECT_ID, SUBJECT_TYPE,
                METADATA, INGESTED_AT, EVENT_NK_HASH
            )
            SELECT %s, %s, %s, %s, CURRENT_TIMESTAMP(), %s, %s, %s, %s,
                   PARSE_JSON(%s), CURRENT_TIMESTAMP(), %s
        """, (
            event_id, tenant_id, source_system, event_type,
            actor_id, "BOT", subject_id, "TEST",
            json.dumps(metadata), event_hash
        ))
        conn.commit()
        print("\n✓ INSERT successful!")

    except Exception as e:
        print(f"\n✗ INSERT failed: {e}")
        cursor.close()
        conn.close()
        sys.exit(1)

    # Verify
    print(f"\n{'='*60}")
    print("Verifying insert...")
    print(f"{'='*60}")

    cursor.execute(f"""
        SELECT EVENT_ID, EVENT_TYPE, EVENT_AT, METADATA
        FROM {target_table}
        WHERE SOURCE_SYSTEM = %s
        ORDER BY EVENT_AT DESC
        LIMIT 5
    """, (source_system,))

    events = cursor.fetchall()
    if events:
        print(f"✓ Found {len(events)} {source_system} event(s):\n")
        for event in events:
            print(f"  EVENT_ID: {event[0]}")
            print(f"  EVENT_TYPE: {event[1]}")
            print(f"  EVENT_AT: {event[2]}")
            print()
    else:
        print(f"⚠ No {source_system} events found")

    cursor.close()
    conn.close()

    print(f"{'='*60}")
    print("✓ PostgreSQL integration test PASSED!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
