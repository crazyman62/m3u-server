# run.py
import os
from m3u_server import create_app

# Create the Flask app instance using the app factory
app = create_app()

if __name__ == '__main__':
    # Get configuration from environment variables or use defaults
    use_debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    host = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_RUN_PORT', 5000))

    # --- IMPORTANT ---
    # The reloader MUST be False for the APScheduler to work correctly
    # in a single-process development environment. In a production setup
    # like Gunicorn, you would run multiple workers and the scheduler
    # would typically run in a separate process.
    use_reloader = False

    app.logger.info(f" --- Starting M3U Manager --- ")
    app.logger.info(f" Config: debug={use_debug}, host={host}, port={port}, reloader={use_reloader}")
    app.logger.info(f" Database: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

    # Run the Flask application
    app.run(host=host, port=port, debug=use_debug, threaded=True, use_reloader=use_reloader)
