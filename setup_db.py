# setup_db.py
import os
import sqlite3
from datetime import datetime

# --- Define Base Directory and Absolute DB Path ---
basedir = os.path.abspath(os.path.dirname(__file__)) # Directory containing setup_db.py
DATABASE = os.path.join(basedir, 'data.db') # Absolute path to data.db
# --- End Path Definition ---

def init_db():
    """Initializes the database schema using an absolute path."""
    # Use the absolute DATABASE path defined above
    conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    cursor = conn.cursor()
    conn.execute("PRAGMA foreign_keys = ON;") # Enable foreign key constraints

    print(f"Initializing database schema at: {DATABASE}")

    # --- Define Table Schemas (CREATE TABLE IF NOT EXISTS) ---
    # These ensure the basic tables exist for raw SQL access or initial setup.
    # SQLAlchemy's create_all() in app.py will use the Model definitions.

    # m3u_sources Table Schema
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS m3u_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        last_checked DATETIME,
        enabled INTEGER DEFAULT 1 NOT NULL,
        refresh_interval_hours INTEGER DEFAULT 24 NOT NULL
    );
    ''')
    print("Table 'm3u_sources' schema ensured.")

    # channels Table Schema
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT,
        tvg_id TEXT,
        tvg_logo TEXT,
        channel_num INTEGER,
        enable INTEGER DEFAULT 1 NOT NULL,
        last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    ''')
    print("Table 'channels' schema ensured.")

    # urls Table Schema
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        channel_id INTEGER NOT NULL,
        last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (channel_id) REFERENCES channels (id) ON DELETE CASCADE
    );
    ''')
    print("Table 'urls' schema ensured.")

    # --- Schema Migration Helper Function (for adding columns to existing tables) ---
    def add_column_if_not_exists(table_name, column_name, column_definition):
        """Checks if a column exists and adds it if it doesn't."""
        try:
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = [column[1] for column in cursor.fetchall()]
            if column_name not in columns:
                print(f"Attempting to add column '{column_name}' to table '{table_name}'...")
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition};")
                print(f"Column '{column_name}' added successfully.")
            # else:
            #     print(f"Column '{column_name}' already exists in '{table_name}'.")
        except sqlite3.Error as e:
             print(f"Could not check/add column '{column_name}' for table '{table_name}': {e}")


    # Add this inside the init_db() function in setup_db.py, after the table creations
# and before the add_column_if_not_exists calls

    # --- Add Indexes (CREATE INDEX IF NOT EXISTS) ---
    print("Ensuring necessary indexes exist...")
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_channels_name ON channels (name);
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_channels_category ON channels (category);
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_channels_enable ON channels (enable);
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_channels_last_seen ON channels (last_seen);
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_urls_channel_id ON urls (channel_id);
    ''')
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_urls_last_seen ON urls (last_seen);
    ''')
    # -->> ADD THIS COMPOSITE INDEX <<--
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_urls_channel_url ON urls (channel_id, url);
    ''')
    print("Indexes checked/created.")

# (rest of the setup_db.py file remains the same)
    # --- Apply Migrations (Add columns if missing from existing tables) ---
    # Useful if running against a DB created by an older version.
    print("Checking for necessary column additions (migrations)...")
    add_column_if_not_exists("channels", "last_seen", "DATETIME DEFAULT CURRENT_TIMESTAMP")
    add_column_if_not_exists("channels", "enable", "INTEGER DEFAULT 1 NOT NULL")
    add_column_if_not_exists("urls", "last_seen", "DATETIME DEFAULT CURRENT_TIMESTAMP")
    add_column_if_not_exists("m3u_sources", "enabled", "INTEGER DEFAULT 1 NOT NULL")
    add_column_if_not_exists("m3u_sources", "last_checked", "DATETIME")
    add_column_if_not_exists("m3u_sources", "refresh_interval_hours", "INTEGER DEFAULT 24 NOT NULL")

    print("Committing schema changes.")
    conn.commit()
    conn.close()
    print(f"Database initialization/check complete for {DATABASE}")

if __name__ == '__main__':
    # Consider backing up DB before running, especially if making schema changes
    # if os.path.exists(DATABASE):
    #     print(f"WARN: Database '{DATABASE}' exists.")
    init_db()