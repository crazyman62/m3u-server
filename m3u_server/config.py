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
    INSTANCE_PATH = os.path.join(basedir, 'instance')
    DATABASE_PATH = os.path.join(INSTANCE_PATH, DATABASE_FILENAME)
    Path(INSTANCE_PATH).mkdir(exist_ok=True)

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
