# setup_db.py
import os
import sys
from pathlib import Path

# Add the project root to the Python path to allow for package imports
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from m3u_server import create_app, db

def init_db():
    """
    Initializes the database schema using the application factory and SQLAlchemy.
    This script should be run from the command line to set up the database.
    """
    print("Creating Flask app for database initialization...")
    app = create_app()
    
    with app.app_context():
        print(f"Initializing database schema at: {app.config['SQLALCHEMY_DATABASE_URI']}")
        
        # This will create all tables based on the models defined in your application
        db.create_all()
        
        print("Database schema ensured.")
        
        # You could also add initial data here if needed, for example:
        # from m3u_server.models import Filter
        # if not Filter.query.first():
        #     print("Adding default filters...")
        #     default_filter = Filter(pattern="^US:", description="Example filter for US channels", enabled=True)
        #     db.session.add(default_filter)
        #     db.session.commit()
        #     print("Default filters added.")

    print("Database initialization complete.")

if __name__ == '__main__':
    # This allows you to run `python setup_db.py` from your terminal
    init_db()
