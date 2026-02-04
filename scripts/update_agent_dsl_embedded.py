#!/usr/bin/env python3
"""
Script to safely update the Main agent DSL in MySQL.

This version embeds the template JSON directly so it can run inside the container
without needing the file to be mounted.

Usage:
    cat scripts/update_agent_dsl_embedded.py | docker exec -i docker-ragflow-gpu-1 python -
"""

import json
import os
import sys

# Configuration
CANVAS_ID = "a0aaa8d1fc2611f0ab2ba6f4b3787fc9"

# Template JSON will be loaded from file when generating this script
TEMPLATE_JSON = """__TEMPLATE_PLACEHOLDER__"""


def get_db_connection():
    """Get MySQL database connection."""
    try:
        import pymysql
    except ImportError:
        print("pymysql not found, trying MySQLdb...")
        import MySQLdb
        pymysql = MySQLdb

    # Inside container, use service_conf.yaml or environment variables
    host = os.environ.get("MYSQL_HOST", "mysql")
    port = int(os.environ.get("MYSQL_PORT", 5455))
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DBNAME", "rag_flow")

    # If running inside container, try to read from service_conf.yaml
    if os.path.exists("/ragflow/conf/service_conf.yaml"):
        try:
            import yaml
            with open("/ragflow/conf/service_conf.yaml") as f:
                conf = yaml.safe_load(f)
                if "mysql" in conf:
                    mysql_conf = conf["mysql"]
                    host = mysql_conf.get("host", host)
                    port = int(mysql_conf.get("port", port))
                    user = mysql_conf.get("user", user)
                    password = mysql_conf.get("password", password)
                    database = mysql_conf.get("name", database)
        except Exception as e:
            print(f"Warning: Could not read service_conf.yaml: {e}")

    print(f"Connecting to MySQL at {host}:{port} as {user}...")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
    )


def load_template():
    """Load template from embedded JSON."""
    print("Loading embedded template...")
    data = json.loads(TEMPLATE_JSON)

    # Extract ALL required DSL fields
    # The Canvas class requires: components, graph, history, retrieval, memory, globals, variables
    required_keys = [
        "components", "graph", "history", "retrieval", "memory",
        "globals", "variables", "path", "messages", "edges"
    ]

    dsl = {}
    for key in required_keys:
        if key in data:
            dsl[key] = data[key]
        else:
            # Provide defaults for required fields
            if key == "history":
                dsl[key] = []
            elif key == "retrieval":
                dsl[key] = []
            elif key == "memory":
                dsl[key] = []
            elif key == "globals":
                dsl[key] = {"sys.query": "", "sys.user_id": "", "sys.conversation_turns": 0, "sys.files": []}
            elif key == "variables":
                dsl[key] = {}

    return dsl


def verify_canvas_exists(cursor, canvas_id):
    """Check if the canvas exists in the database."""
    cursor.execute(
        "SELECT id, title, user_id FROM user_canvas WHERE id = %s",
        (canvas_id,)
    )
    result = cursor.fetchone()

    if result:
        print(f"Found canvas: id={result[0]}, title={result[1]}, user_id={result[2]}")
        return True
    return False


def backup_current_dsl(cursor, canvas_id):
    """Get the current DSL for backup purposes."""
    cursor.execute(
        "SELECT dsl FROM user_canvas WHERE id = %s",
        (canvas_id,)
    )
    result = cursor.fetchone()
    return result[0] if result else None


def update_dsl(cursor, canvas_id, dsl):
    """Update the DSL in the database using parameterized query."""
    # Serialize DSL to JSON string with proper escaping
    dsl_json = json.dumps(dsl, ensure_ascii=False, separators=(",", ":"))

    print(f"DSL JSON size: {len(dsl_json)} bytes")

    # Use parameterized query to avoid SQL injection and ensure proper escaping
    cursor.execute(
        "UPDATE user_canvas SET dsl = %s, update_time = NOW(), update_date = CURDATE() WHERE id = %s",
        (dsl_json, canvas_id)
    )

    return cursor.rowcount


def main():
    """Main function to update the agent DSL."""
    print("=" * 60)
    print("Agent DSL Update Script")
    print("=" * 60)

    try:
        # Load the template
        dsl = load_template()
        print(f"Loaded DSL with {len(dsl.get('components', {}))} components")

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            # Verify canvas exists
            if not verify_canvas_exists(cursor, CANVAS_ID):
                print(f"ERROR: Canvas not found with ID: {CANVAS_ID}")
                print("\nAvailable canvases:")
                cursor.execute("SELECT id, title FROM user_canvas LIMIT 10")
                for row in cursor.fetchall():
                    print(f"  - {row[0]}: {row[1]}")
                sys.exit(1)

            # Backup current DSL
            print("\nBacking up current DSL...")
            current_dsl = backup_current_dsl(cursor, CANVAS_ID)
            if current_dsl:
                print("Current DSL backed up to memory (not saved to file in container)")

            # Update the DSL
            print("\nUpdating DSL...")
            rows_affected = update_dsl(cursor, CANVAS_ID, dsl)

            if rows_affected > 0:
                conn.commit()
                print(f"SUCCESS: Updated {rows_affected} row(s)")
            else:
                print("WARNING: No rows were updated")

            # Verify the update
            print("\nVerifying update...")
            cursor.execute(
                "SELECT dsl FROM user_canvas WHERE id = %s",
                (CANVAS_ID,)
            )
            result = cursor.fetchone()
            if result and result[0]:
                stored_dsl = result[0]
                if isinstance(stored_dsl, str):
                    # Test if it parses correctly
                    try:
                        json.loads(stored_dsl)
                        print("VERIFIED: DSL is valid JSON")
                    except json.JSONDecodeError as e:
                        print(f"ERROR: Stored DSL is invalid JSON: {e}")
                else:
                    print("VERIFIED: DSL is stored as JSON object")

        finally:
            cursor.close()
            conn.close()
            print("\nDatabase connection closed")

        print("\n" + "=" * 60)
        print("Update complete!")
        print("=" * 60)

    except Exception as e:
        print(f"Unexpected ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
