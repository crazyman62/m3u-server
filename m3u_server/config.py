# config.py
import os
from pathlib import Path

# Define the base directory of the application
basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    """Base configuration class."""
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'a-very-secret-dev-key-that-should-be-changed')
    
    # --- Database Configuration ---
    DATABASE_FILENAME = 'data.db'

    # Allow overriding the instance path via DATA_DIR environment variable
    # This is useful for Docker setups where the user wants to mount a specific
    # directory for persistence (e.g., /config or /data).
    INSTANCE_PATH = os.environ.get('DATA_DIR', os.path.join(basedir, 'instance'))

    DATABASE_PATH = os.path.join(INSTANCE_PATH, DATABASE_FILENAME)

    # Ensure the instance directory exists
    try:
        Path(INSTANCE_PATH).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create instance directory at {INSTANCE_PATH}: {e}")

    SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    # --- Application-Specific Settings ---
    EPG_DATA_RETENTION_HOURS = 72
    CHANNEL_DATA_RETENTION_DAYS = 3

    # --- New Setting ---
    # Set to True to automatically disable channels that have no EPG data.
    # This job runs once per day.
    DISABLE_CHANNELS_WITHOUT_EPG = True
