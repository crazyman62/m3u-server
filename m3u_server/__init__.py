# m3u_server/__init__.py
import os
import logging
from flask import Flask, request, render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from .config import Config

# --- Initialize Extensions ---
db = SQLAlchemy()
csrf = CSRFProtect()
scheduler = BackgroundScheduler(daemon=True, timezone="UTC")

def create_app(config_class=Config):
    """
    Creates and configures the Flask application instance.
    This is the application factory pattern.
    """
    # By default, Flask looks for a 'templates' folder in the same directory
    # as the application instance. Our new launch.json ensures this works.
    app = Flask(
        __name__,
        instance_path=config_class.INSTANCE_PATH
    )
    app.config.from_object(config_class)

    # --- Logging Setup ---
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s.%(funcName)s]: %(message)s')
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

    app.logger.info(f"Instance path set to: {app.instance_path}")
    app.logger.info(f"Root path for templates/static: {app.root_path}")

    # --- Initialize Flask Extensions with the App ---
    db.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        # --- Import and Register Blueprints ---
        from .routes.main import main_bp
        from .routes.sources import sources_bp
        from .routes.channels import channels_bp
        from .routes.epg import epg_bp
        from .routes.filters import filters_bp

        app.register_blueprint(main_bp)
        app.register_blueprint(sources_bp, url_prefix='/sources')
        app.register_blueprint(channels_bp, url_prefix='/channels')
        app.register_blueprint(epg_bp, url_prefix='/epg')
        app.register_blueprint(filters_bp, url_prefix='/filters')
        
        app.logger.info("Blueprints registered.")

        # --- Database and Scheduler Initialization ---
        initialize_database_and_scheduler(app)

    # Define a robust error handler
    @app.errorhandler(Exception)
    def handle_exception(e):
        app.logger.error(f"Unhandled exception on {request.path} [{request.method}]", exc_info=e)
        if app.debug:
            raise e
        error_template = """
        <!doctype html><title>500 Internal Server Error</title>
        <h1>Internal Server Error</h1>
        <p>The server encountered an internal error and was unable to complete your request. The error has been logged.</p>
        """
        return render_template_string(error_template), 500

    return app

def initialize_database_and_scheduler(app):
    """
    Ensures database tables exist and initializes/starts the scheduler.
    """
    app.logger.info("Application initialization: Ensuring database tables exist...")
    try:
        from . import models
        db.create_all()
        app.logger.info("SQLAlchemy tables checked/created.")
    except Exception as e:
        app.logger.error(f"Error during initial db.create_all(): {e}", exc_info=True)
        
    if not scheduler.running:
        jobstore_url = app.config['SQLALCHEMY_DATABASE_URI']
        scheduler.add_jobstore(SQLAlchemyJobStore(url=jobstore_url), 'default')
        
        app.logger.info("Reloading and scheduling background jobs...")
        from . import scheduler_jobs
        
        scheduler.remove_all_jobs()
        scheduler.add_job(
            func=scheduler_jobs.scheduled_cleanup_job,
            trigger='cron', hour=4, minute=5, id='daily_cleanup_job',
            name='Daily Cleanup of Old Data', replace_existing=True
        )
        app.logger.info("Scheduled daily cleanup job.")

        scheduler_jobs.schedule_all_source_refreshes()
        scheduler_jobs.schedule_all_epg_refreshes()

        try:
            scheduler.start()
            app.logger.info("APScheduler started successfully.")
        except Exception as e:
            app.logger.error(f"APScheduler failed to start: {e}", exc_info=True)
    else:
        app.logger.info("APScheduler is a'ready running.")
