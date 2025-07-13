# app.py
import os
import sqlite3
import requests
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path # For robust path handling

from flask import (
    Flask, request, jsonify, render_template,
    g, redirect, url_for, flash, abort
)
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import StringField, SubmitField, URLField, IntegerField, SelectField
from wtforms.validators import DataRequired, Optional, URL

# --- Scheduler Imports ---
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.base import JobLookupError

# --- SQLAlchemy Imports ---
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_, and_, desc, asc
from sqlalchemy.orm import make_transient

# --- Define Base Directory ---
basedir = os.path.abspath(os.path.dirname(__file__)) # Directory containing app.py

# --- SQLite Type Converters ---
def convert_timestamp_iso(val):
    """Converts an ISO 8601 timestamp bytes (from DB) to a datetime object."""
    if val is None: return None
    try: return datetime.fromisoformat(val.decode())
    except (ValueError, TypeError, AttributeError) as e:
        try: app.logger.error(f"Failed timestamp conversion: {val} - Error: {e}")
        except RuntimeError: print(f"ERROR: Failed timestamp conversion: {val} - Error: {e}")
        return None

sqlite3.register_converter("timestamp", convert_timestamp_iso)
sqlite3.register_converter("datetime", convert_timestamp_iso)

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a-very-secret-dev-key-that-should-be-changed')
app.config['DATABASE_FILENAME'] = 'data.db' # Just the filename
app.config['DATABASE'] = os.path.join(basedir, app.config['DATABASE_FILENAME']) # Absolute path

# Configure SQLAlchemy using the absolute path URI
db_path = Path(app.config['DATABASE'])
db_uri = 'sqlite:///' + str(db_path.resolve()) # Ensure correct format e.g., 'sqlite:///C:/path/data.db'
app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False # Set True for debugging SQL queries

# Initialize extensions
db_sqla = SQLAlchemy(app)
csrf = CSRFProtect(app)

# --- Global Filter List ---
COMPILED_FILTERS = []

def load_and_compile_filters():
    """Loads regex patterns from a file and compiles them for efficiency."""
    global COMPILED_FILTERS
    COMPILED_FILTERS = []
    filters_file = Path(basedir) / 'disable_filters.txt'
    if filters_file.is_file():
        app.logger.info(f"Loading disable filters from: {filters_file}")
        try:
            with open(filters_file, 'r') as f:
                for line in f:
                    pattern = line.strip()
                    if pattern and not pattern.startswith('#'): # Ignore empty lines and comments
                        # Compile with IGNORECASE for case-insensitive matching
                        COMPILED_FILTERS.append(re.compile(pattern, re.IGNORECASE))
            app.logger.info(f"Successfully loaded and compiled {len(COMPILED_FILTERS)} filter(s).")
        except Exception as e:
            app.logger.error(f"Error reading or compiling filters from {filters_file}: {e}")
    else:
        app.logger.info(f"Filter file not found at {filters_file}. No channel filters will be applied.")

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s]: %(message)s')
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

# --- Scheduler Setup ---
scheduler = BackgroundScheduler(daemon=True, timezone="UTC")

# --- SQLAlchemy Models ---
class Channel(db_sqla.Model):
    __tablename__ = 'channels'
    id = db_sqla.Column(db_sqla.Integer, primary_key=True)
    name = db_sqla.Column(db_sqla.String, nullable=False, index=True) # Index name for lookups
    category = db_sqla.Column(db_sqla.String, index=True) # Index category
    tvg_id = db_sqla.Column(db_sqla.String)
    tvg_logo = db_sqla.Column(db_sqla.String)
    channel_num = db_sqla.Column(db_sqla.Integer)
    enable = db_sqla.Column(db_sqla.Integer, nullable=False, default=1, index=True) # Index enable
    last_seen = db_sqla.Column(db_sqla.DateTime, default=datetime.utcnow, index=True) # Index last_seen
    urls = db_sqla.relationship('Url', backref='channel', lazy=True, cascade="all, delete-orphan")

class Url(db_sqla.Model):
     __tablename__ = 'urls'
     id = db_sqla.Column(db_sqla.Integer, primary_key=True)
     url = db_sqla.Column(db_sqla.String, nullable=False)
     channel_id = db_sqla.Column(db_sqla.Integer, db_sqla.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False, index=True)
     last_seen = db_sqla.Column(db_sqla.DateTime, default=datetime.utcnow, index=True)

class M3uSource(db_sqla.Model):
    __tablename__ = 'm3u_sources'
    id = db_sqla.Column(db_sqla.Integer, primary_key=True)
    url = db_sqla.Column(db_sqla.String, nullable=False, unique=True)
    last_checked = db_sqla.Column(db_sqla.DateTime)
    enabled = db_sqla.Column(db_sqla.Integer, nullable=False, default=1, index=True)
    refresh_interval_hours = db_sqla.Column(db_sqla.Integer, nullable=False, default=24)

# --- Database Utility Functions (Raw SQLite - kept for playlist route) ---
def get_db():
    """Opens a new raw sqlite3 database connection if needed."""
    if 'db' not in g:
        try:
            g.db = sqlite3.connect( app.config['DATABASE'], detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES )
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON;")
            app.logger.debug("Raw SQLite connection opened.")
        except sqlite3.Error as e:
            app.logger.error(f"Failed to connect raw SQLite: {e}"); abort(500)
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the raw sqlite3 database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None: db.close(); app.logger.debug("Raw SQLite connection closed.")
    if error: app.logger.error(f"App context teardown error: {error}")

# --- Forms ---
class AddChannelForm(FlaskForm):
    category = StringField('Category', validators=[Optional()])
    name = StringField('Channel Name', validators=[DataRequired()])
    tvg_id = StringField('TVG ID', validators=[Optional()])
    tvg_logo = URLField('Logo URL', validators=[Optional(), URL(message="Must be a valid URL.")])
    channel_num = IntegerField('Ch Number', validators=[Optional()])
    url = URLField('Stream URL', validators=[DataRequired(), URL()])
    submit = SubmitField('Add Channel')

class SourceM3uForm(FlaskForm):
    m3u_url = URLField('M3U Source URL', validators=[DataRequired(), URL()])
    submit = SubmitField('Add Source URL')

class UpdateIntervalForm(FlaskForm):
     interval_choices = [ (1, '1h'), (3, '3h'), (6, '6h'), (12, '12h'), (24, '24h (Daily)'), (48, '48h'), (168, '168h (Weekly)') ]
     interval = SelectField('Refresh Interval', choices=interval_choices, coerce=int, validators=[DataRequired()])
     submit = SubmitField('Set')

# --- M3U Parsing Helper ---
def parse_extinf(line):
    """Parses an #EXTINF line into a dictionary. Returns None on error."""
    data = {'attributes': {}, 'display_name': ''}; parts = line.split(',', 1)
    if len(parts) != 2: return None
    try:
        attributes_part = parts[0]; data['display_name'] = parts[1].strip()
        pattern = re.compile(r'([a-zA-Z0-9_\-]+)="(.*?)"')
        for key, value in pattern.findall(attributes_part): data['attributes'][key.lower().replace('-', '_')] = value
        if m := re.match(r'#EXTINF:(-?\d+)', attributes_part): data['attributes']['duration'] = int(m.group(1))
        return data
    except Exception as e: app.logger.error(f"Exception parsing EXTINF data '{attributes_part}': {e}"); return None

# --- Filter Helper ---
def should_disable_channel(channel_name):
    """Checks if a channel name matches any of the global compiled filters."""
    if not COMPILED_FILTERS:
        return False
    return any(pattern.search(channel_name) for pattern in COMPILED_FILTERS)

# --- Core Refresh/Cleanup Logic ---
def refresh_single_m3u_source(source_url, source_id):
    """Fetches and processes one M3U source URL using batch DB operations and processing batches."""
    app.logger.info(f"[Refresh:{source_id}] Starting FULL Batch Process: {source_url}")
    overall_start_time = datetime.utcnow()
    overall_counts = {'ch_add': 0, 'url_add': 0, 'ch_upd_attr': 0, 'ch_upd_seen': 0, 'url_upd_seen': 0}
    parsed_m3u_data = {} # Structure: { channel_name: {'attrs': {...}, 'urls': set()} }

    # --- 1. Download and Parse M3U into Memory ---
    try:
        headers = {'User-Agent': 'M3UManagerBot/1.0'}
        response = requests.get(source_url, timeout=300, headers=headers, stream=True)
        response.raise_for_status()
        app.logger.info(f"[Refresh:{source_id}] Download complete, parsing M3U stream...")

        current_extinf_data = None
        line_count = 0
        for line_bytes in response.iter_lines():
            line_count += 1
            if line_count % 50000 == 0:
                 app.logger.debug(f"[Refresh:{source_id}] Parsed {line_count} lines...")
            if not line_bytes: continue
            try:
                line = line_bytes.decode('utf-8', errors='ignore').strip()
            except UnicodeDecodeError:
                app.logger.warning(f"[Refresh:{source_id}] Skipping line {line_count} due to decode error.")
                continue
            if not line: continue

            if line.startswith('#EXTINF:'):
                current_extinf_data = parse_extinf(line)
                continue

            if current_extinf_data and not line.startswith('#'):
                stream_url = line
                attrs = current_extinf_data['attributes']
                display_name = current_extinf_data['display_name']
                channel_name = attrs.get('tvg_name') or display_name or 'Unknown'

                if channel_name not in parsed_m3u_data:
                     parsed_m3u_data[channel_name] = {
                         'attrs': {
                             'category': attrs.get('group_title'), 'tvg_id': attrs.get('tvg_id'),
                             'tvg_logo': attrs.get('tvg_logo'), 'channel_num_str': attrs.get('tvg_chno')
                         }, 'urls': set()
                     }
                parsed_m3u_data[channel_name]['urls'].add(stream_url)
                current_extinf_data = None

        app.logger.info(f"[Refresh:{source_id}] M3U Parsing complete. Found {len(parsed_m3u_data)} unique channel names.")

    except requests.exceptions.RequestException as e:
        app.logger.error(f"[Refresh:{source_id}] Request Error: {e}"); return
    except Exception as e:
        app.logger.error(f"[Refresh:{source_id}] Error during download/parse: {e}", exc_info=True); return

    if not parsed_m3u_data:
        app.logger.warning(f"[Refresh:{source_id}] No valid channel data found in M3U."); return

    # --- 2. Process Data in Batches ---
    m3u_channel_names_list = list(parsed_m3u_data.keys())
    processing_batch_size = 5000
    db_lookup_chunk_size = 500

    for batch_num, i in enumerate(range(0, len(m3u_channel_names_list), processing_batch_size)):
        batch_start_time = datetime.utcnow()
        batch_channel_names = m3u_channel_names_list[i:i + processing_batch_size]
        app.logger.info(f"--- [Refresh:{source_id}] Processing Batch {batch_num + 1} ({len(batch_channel_names)} channels) ---")

        batch_counts = {'ch_add': 0, 'url_add': 0, 'ch_upd_attr': 0, 'ch_upd_seen': 0, 'url_upd_seen': 0}
        existing_channels_map = {}
        existing_urls_map = {}

        with app.app_context():
            try:
                # --- 2a. Fetch Existing Relevant Data for this Batch (IN CHUNKS) ---
                existing_db_channels_list = []
                for j in range(0, len(batch_channel_names), db_lookup_chunk_size):
                    chunk_names = batch_channel_names[j:j + db_lookup_chunk_size]
                    if not chunk_names: continue
                    chunk_results = db_sqla.session.query(Channel)\
                        .filter(Channel.name.in_(chunk_names))\
                        .options(db_sqla.orm.selectinload(Channel.urls))\
                        .all()
                    existing_db_channels_list.extend(chunk_results)

                existing_channels_map = {ch.name: ch for ch in existing_db_channels_list}
                for ch in existing_db_channels_list:
                    existing_urls_map[ch.id] = {u.url for u in ch.urls}

                app.logger.debug(f"[Refresh:{source_id} B:{batch_num+1}] Fetched {len(existing_channels_map)} existing channels for batch.")

                channels_to_add = []
                urls_to_add = []
                updated_channel_ids = set()
                updated_url_ids = set()

                # --- 2b. Compare M3U Data with DB Data & Prepare Changes ---
                for channel_name in batch_channel_names:
                    m3u_data = parsed_m3u_data[channel_name]
                    m3u_attrs = m3u_data['attrs']; m3u_urls = m3u_data['urls']
                    current_channel = existing_channels_map.get(channel_name)

                    channel_num = None
                    if m3u_attrs['channel_num_str']:
                        try: channel_num = int(m3u_attrs['channel_num_str']);
                        except ValueError: pass

                    if current_channel: # Existing Channel
                        updated_channel_ids.add(current_channel.id)
                        is_updated = False

                        # Check for standard attribute updates
                        if current_channel.category != m3u_attrs['category'] or \
                           current_channel.tvg_id != m3u_attrs['tvg_id'] or \
                           current_channel.tvg_logo != m3u_attrs['tvg_logo'] or \
                           current_channel.channel_num != channel_num:
                            current_channel.category = m3u_attrs['category']
                            current_channel.tvg_id = m3u_attrs['tvg_id']
                            current_channel.tvg_logo = m3u_attrs['tvg_logo']
                            current_channel.channel_num = channel_num
                            is_updated = True

                        # Auto-disable filter for existing channels based on name
                        if should_disable_channel(channel_name) and current_channel.enable == 1:
                            current_channel.enable = 0
                            is_updated = True
                            app.logger.info(f"[Refresh:{source_id}] Auto-disabling '{channel_name}' due to matching filter.")

                        if is_updated:
                            batch_counts['ch_upd_attr'] += 1

                        # Process URLs for this existing channel
                        existing_channel_urls = existing_urls_map.get(current_channel.id, set())
                        for url_str in m3u_urls:
                            if url_str not in existing_channel_urls:
                                urls_to_add.append(Url(channel_id=current_channel.id, url=url_str, last_seen=overall_start_time))
                                batch_counts['url_add'] += 1
                            else:
                                url_obj = next((u for u in current_channel.urls if u.url == url_str), None)
                                if url_obj: updated_url_ids.add(url_obj.id)

                    else: # New Channel
                        # Auto-disable filter: set initial status based on name
                        initial_enable_status = 0 if should_disable_channel(channel_name) else 1

                        new_channel = Channel(
                            name=channel_name, category=m3u_attrs['category'], tvg_id=m3u_attrs['tvg_id'],
                            tvg_logo=m3u_attrs['tvg_logo'], channel_num=channel_num, last_seen=overall_start_time,
                            enable=initial_enable_status # Set enable status on creation
                        )
                        channels_to_add.append(new_channel)
                        batch_counts['ch_add'] += 1
                        
                        if initial_enable_status == 0:
                           app.logger.info(f"[Refresh:{source_id}] Adding '{channel_name}' as disabled due to matching filter.")

                app.logger.debug(f"[Refresh:{source_id} B:{batch_num+1}] Prepared: ChAdd={len(channels_to_add)}, UrlAddPending={batch_counts['url_add']}")

                # --- 2c. Perform Bulk Inserts (Channels for this batch) ---
                if channels_to_add:
                    db_sqla.session.add_all(channels_to_add)
                    db_sqla.session.flush()
                    for new_ch in channels_to_add:
                        if new_ch.id:
                             updated_channel_ids.add(new_ch.id)
                             m3u_urls = parsed_m3u_data[new_ch.name]['urls']
                             for url_str in m3u_urls:
                                  urls_to_add.append(Url(channel_id=new_ch.id, url=url_str, last_seen=overall_start_time))
                                  batch_counts['url_add'] += 1
                        else: app.logger.error(f"[Refresh:{source_id} B:{batch_num+1}] Failed to get ID for new channel: {new_ch.name}")

                # --- 2d. Perform Bulk Inserts (URLs for this batch) ---
                if urls_to_add:
                     db_sqla.session.add_all(urls_to_add)

                # --- 2e. Perform Bulk Updates (last_seen for this batch) ---
                if updated_channel_ids:
                     db_sqla.session.query(Channel).filter(Channel.id.in_(updated_channel_ids))\
                         .update({'last_seen': overall_start_time}, synchronize_session=False)
                     batch_counts['ch_upd_seen'] = len(updated_channel_ids) - batch_counts['ch_add']

                if updated_url_ids:
                     db_sqla.session.query(Url).filter(Url.id.in_(updated_url_ids))\
                         .update({'last_seen': overall_start_time}, synchronize_session=False)
                     batch_counts['url_upd_seen'] = len(updated_url_ids)

                # --- 2f. Commit this Batch ---
                db_sqla.session.commit()
                batch_end_time = datetime.utcnow(); batch_duration = batch_end_time - batch_start_time
                app.logger.info(f"[Refresh:{source_id} B:{batch_num+1}] Batch Commit OK. Duration: {batch_duration}. Counts: {batch_counts}")

                for key in overall_counts: overall_counts[key] += batch_counts[key]

            except Exception as e:
                db_sqla.session.rollback()
                app.logger.error(f"[Refresh:{source_id} B:{batch_num+1}] Batch Processing Error: {e}", exc_info=True)
                raise e

    # --- 3. Final Update for Source Timestamp ---
    with app.app_context():
         try:
             source = db_sqla.session.query(M3uSource).get(source_id)
             if source:
                 source.last_checked = overall_start_time
                 db_sqla.session.commit()
             else: app.logger.warning(f"[Refresh:{source_id}] Source ID not found for final timestamp update.")
         except Exception as e:
             db_sqla.session.rollback()
             app.logger.error(f"[Refresh:{source_id}] Error updating source timestamp: {e}", exc_info=True)

    overall_end_time = datetime.utcnow()
    overall_duration = overall_end_time - overall_start_time
    app.logger.info(f"--- [Refresh:{source_id}] FULL Batch Process Finished. Duration: {overall_duration}. Overall Counts: {overall_counts} ---")

def scheduled_cleanup_old_entries():
    """Scheduled task to remove channels and URLs not seen recently."""
    app.logger.info("--- [Cleanup Task] Starting ---")
    cutoff_time = datetime.utcnow() - timedelta(days=2)
    app.logger.info(f"[Cleanup Task] Cutoff time (UTC): {cutoff_time}")
    with app.app_context():
        try:
            urls_deleted = db_sqla.session.query(Url).filter(Url.last_seen < cutoff_time).delete()
            channels_deleted = db_sqla.session.query(Channel).filter( Channel.last_seen < cutoff_time, ~Channel.urls.any() ).delete()
            db_sqla.session.commit()
            app.logger.info(f"[Cleanup Task] Deleted {urls_deleted} URLs, {channels_deleted} channels.")
        except Exception as e: db_sqla.session.rollback(); app.logger.error(f"[Cleanup Task] Error: {e}")
    app.logger.info("--- [Cleanup Task] Finished ---")


# --- Scheduler Helper Functions ---
def schedule_source_refresh_job(source_id, source_url, interval_hours):
    job_id = f'source_refresh_{source_id}'; interval_hours = max(1, interval_hours)
    try: scheduler.add_job(func=refresh_single_m3u_source, trigger=IntervalTrigger(hours=interval_hours), args=[source_url, source_id], id=job_id, name=f'Refresh src {source_id} ({interval_hours}h)', replace_existing=True)
    except Exception as e: app.logger.error(f"Error scheduling job {job_id}: {e}")
def remove_source_refresh_job(source_id):
    job_id = f'source_refresh_{source_id}'
    try: scheduler.remove_job(job_id); app.logger.info(f"Removed job '{job_id}'.")
    except JobLookupError: app.logger.info(f"Job '{job_id}' not found for removal.")
    except Exception as e: app.logger.error(f"Error removing job {job_id}: {e}")
def pause_source_refresh_job(source_id):
     job_id = f'source_refresh_{source_id}';
     try:
         if scheduler.get_job(job_id): scheduler.pause_job(job_id); app.logger.info(f"Paused job '{job_id}'.")
         else: app.logger.warning(f"Job '{job_id}' not found for pausing.")
     except Exception as e: app.logger.error(f"Error pausing job {job_id}: {e}")
def resume_source_refresh_job(source_id):
     job_id = f'source_refresh_{source_id}';
     try:
         job = scheduler.get_job(job_id)
         if job: scheduler.resume_job(job_id); app.logger.info(f"Resumed job '{job_id}'.")
         else:
             app.logger.info(f"Job '{job_id}' missing, rescheduling.");
             with app.app_context():
                 source = db_sqla.session.query(M3uSource).filter_by(id=source_id, enabled=1).first()
                 if source: schedule_source_refresh_job(source_id, source.url, source.refresh_interval_hours)
                 else: app.logger.warning(f"Source ID {source_id} missing/disabled, cannot reschedule.")
     except Exception as e: app.logger.error(f"Error resuming job {job_id}: {e}")


# --- Routes ---

@app.route('/')
def index(): return redirect(url_for('manage_sources'))

@app.route('/playlist.m3u', methods=['GET'])
def get_m3u_playlist():
    """Generates M3U playlist using SQLAlchemy."""
    try:
        results = db_sqla.session.query(Channel, Url)\
            .join(Url, Channel.id == Url.channel_id)\
            .filter(Channel.enable == 1)\
            .order_by(Channel.category, Channel.name, Url.id)\
            .all()
    except Exception as e:
        app.logger.error(f"DB error generating playlist with SQLAlchemy: {e}")
        abort(500, description="Database error generating playlist.")

    m3u_content = ["#EXTM3U"]
    for channel, url_obj in results:
        channel_name = channel.name or 'Unknown'
        display_name = channel_name
        extinf_parts = ['#EXTINF:-1']

        if channel.tvg_id: extinf_parts.append(f'tvg-id="{channel.tvg_id}"')
        extinf_parts.append(f'tvg-name="{channel_name}"')
        if channel.tvg_logo: extinf_parts.append(f'tvg-logo="{channel.tvg_logo}"')
        if channel.category: extinf_parts.append(f'group-title="{channel.category}"')
        if channel.channel_num is not None: extinf_parts.append(f'tvg-chno="{channel.channel_num}"')

        extinf_line = " ".join(extinf_parts) + f",{display_name}"
        m3u_content.append(extinf_line)
        m3u_content.append(str(url_obj.url).strip())

    response_text = "\n".join(m3u_content)
    return response_text.encode('utf-8'), 200, {
        'Content-Type': 'application/vnd.apple.mpegurl; charset=utf-8',
        'Content-Disposition': 'attachment; filename="playlist.m3u"'
    }

@app.route('/add', methods=['GET', 'POST'])
def add_channel_manual():
    """Manual channel/URL addition using SQLAlchemy."""
    form = AddChannelForm()
    if form.validate_on_submit():
        try:
            channel = db_sqla.session.query(Channel).filter(Channel.name == form.name.data).first()
            if channel: channel_id = channel.id; flash(f'Channel exists. Adding URL.', 'info')
            else:
                new_channel = Channel(name=form.name.data, category=form.category.data, tvg_id=form.tvg_id.data, tvg_logo=form.tvg_logo.data, channel_num=form.channel_num.data, last_seen=datetime.utcnow())
                db_sqla.session.add(new_channel); db_sqla.session.flush(); channel_id = new_channel.id; flash(f'Channel added.', 'success')
            if channel_id:
                 url_exists = db_sqla.session.query(Url).filter(Url.channel_id == channel_id, Url.url == form.url.data).first()
                 if not url_exists: new_url = Url(url=form.url.data, channel_id=channel_id, last_seen=datetime.utcnow()); db_sqla.session.add(new_url); flash(f'URL added.', 'success')
                 else: flash(f'URL already exists for this channel.', 'warning')
            db_sqla.session.commit(); return redirect(url_for('add_channel_manual'))
        except Exception as e: db_sqla.session.rollback(); app.logger.error(f"Manual Add Error: {e}"); flash(f'Error: {e}', 'danger')
    return render_template('add_form.html', form=form, title="Add Channel Manually")

@app.route('/sources/add', methods=['GET', 'POST'])
def add_source():
    """Adds a new M3U source URL and schedules it."""
    form = SourceM3uForm()
    if form.validate_on_submit():
        try:
            exists = db_sqla.session.query(M3uSource).filter(M3uSource.url == form.m3u_url.data).first()
            if not exists:
                new_source = M3uSource(url=form.m3u_url.data, refresh_interval_hours=24); db_sqla.session.add(new_source); db_sqla.session.flush(); source_id = new_source.id; db_sqla.session.commit()
                schedule_source_refresh_job(source_id, new_source.url, new_source.refresh_interval_hours); flash(f'Source added. Scheduled daily.', 'success')
            else: flash(f'Source URL already exists.', 'warning')
            return redirect(url_for('manage_sources'))
        except Exception as e: db_sqla.session.rollback(); app.logger.error(f"Add Source Error: {e}"); flash(f'Error: {e}', 'danger')
    return render_template('source_form.html', form=form, title="Add M3U Source URL")

@app.route('/sources', methods=['GET'])
def manage_sources():
    """Displays M3U source URLs and allows management."""
    try: sources = db_sqla.session.query(M3uSource).order_by(M3uSource.id).all()
    except Exception as e: app.logger.error(f"Manage Sources Error: {e}"); flash(f'DB error: {e}', 'danger'); sources = []
    interval_form = UpdateIntervalForm()
    return render_template('manage_sources.html', sources=sources, interval_form=interval_form, title="Manage M3U Sources")

@app.route('/sources/delete/<int:source_id>', methods=['POST'])
def delete_source(source_id):
    """Removes job and deletes source URL."""
    remove_source_refresh_job(source_id)
    try:
        source = db_sqla.session.query(M3uSource).get(source_id)
        if source: db_sqla.session.delete(source); db_sqla.session.commit(); flash('Source URL deleted.', 'success')
        else: flash('Source URL not found.', 'warning')
    except Exception as e: db_sqla.session.rollback(); app.logger.error(f"Delete Source Error: {e}"); flash(f'DB error: {e}', 'danger')
    return redirect(url_for('manage_sources'))

@app.route('/sources/toggle/<int:source_id>', methods=['POST'])
def toggle_source(source_id):
    """Toggles source enabled status and pauses/resumes job."""
    try:
        source = db_sqla.session.query(M3uSource).get(source_id)
        if not source: abort(404)
        source.enabled = 0 if source.enabled == 1 else 1; db_sqla.session.commit()
        if source.enabled == 1: resume_source_refresh_job(source_id); flash(f'Source enabled; job active/rescheduled.', 'success')
        else: pause_source_refresh_job(source_id); flash(f'Source disabled; job paused.', 'success')
    except Exception as e: db_sqla.session.rollback(); app.logger.error(f"Toggle Source Error: {e}"); flash(f'DB error: {e}', 'danger')
    return redirect(url_for('manage_sources'))

@app.route('/sources/refresh/<int:source_id>', methods=['POST'])
def force_refresh_source(source_id):
    """Triggers immediate background refresh for a source."""
    source = db_sqla.session.query(M3uSource).filter_by(id=source_id, enabled=1).first()
    if source:
        app.logger.info(f"Manual refresh request for source ID {source_id}")
        try:
             run_time = datetime.utcnow() + timedelta(seconds=2); job_id = f'manual_refresh_{source_id}_{run_time.isoformat()}'
             scheduler.add_job(func=refresh_single_m3u_source, args=[source.url, source.id], id=job_id, name=f'Manual Refresh {source_id}', replace_existing=False, next_run_time=run_time)
             flash(f'Manual refresh scheduled for source {source_id}.', 'info')
        except Exception as e: app.logger.error(f"Schedule Manual Refresh Error: {e}"); flash(f'Failed: {e}', 'danger')
    else: flash(f'Source ID {source_id} not found/disabled.', 'warning')
    return redirect(url_for('manage_sources'))

@app.route('/sources/update_interval/<int:source_id>', methods=['POST'])
def update_source_interval(source_id):
    """Updates refresh interval and reschedules job."""
    form = UpdateIntervalForm(request.form)
    if form.validate():
        new_interval = form.interval.data
        try:
            source = db_sqla.session.query(M3uSource).get(source_id)
            if source:
                source.refresh_interval_hours = new_interval; db_sqla.session.commit()
                if source.enabled: schedule_source_refresh_job(source_id, source.url, new_interval); flash(f'Interval updated to {new_interval}h. Job rescheduled.', 'success')
                else: remove_source_refresh_job(source_id); flash(f'Interval updated to {new_interval}h. Source disabled.', 'info')
            else: flash('Source not found.', 'warning')
        except Exception as e: db_sqla.session.rollback(); app.logger.error(f"Update Interval Error: {e}"); flash(f'Error: {e}', 'danger')
    else:
        for field, errors in form.errors.items(): flash(f"Interval Error: {', '.join(errors)}", 'danger')
    return redirect(url_for('manage_sources'))

# --- Manage Channels Page and API (DataTables) ---
@app.route('/manage', methods=['GET'])
def manage_channels():
    """Renders the page structure for the channels table."""
    return render_template('manage_channels.html', title="Manage Channels")

@app.route('/api/channels/data', methods=['POST'])
def get_channels_data():
    """API endpoint called by DataTables to fetch channel data."""
    try:
        draw = request.form.get('draw', type=int, default=1)
        start = request.form.get('start', type=int, default=0)
        length = request.form.get('length', type=int, default=50)
        search_value = request.form.get('search[value]', default='').strip()
        order_column_index = request.form.get('order[0][column]', type=int, default=1)
        order_direction = request.form.get('order[0][dir]', default='asc')

        column_map = { 0: None, 1: Channel.name, 2: Channel.category, 3: Channel.enable, 4: None }
        order_column = column_map.get(order_column_index)

        query = db_sqla.session.query(Channel); records_total = db_sqla.session.query(func.count(Channel.id)).scalar()
        if search_value: query = query.filter( or_( func.lower(Channel.name).like(f"%{search_value.lower()}%"), func.lower(Channel.category).like(f"%{search_value.lower()}%") ) )
        records_filtered = query.order_by(None).count()
        query = query.order_by(desc(Channel.enable))
        if order_column is not None: query = query.order_by(asc(order_column) if order_direction == 'asc' else desc(order_column))
        query = query.order_by(asc(Channel.id)); channels_page = query.offset(start).limit(length).all()

        data = [{ "select_ch": f'<input type="checkbox" class="form-check-input channel-checkbox" value="{ch.id}">',
                  "name": ch.name or '', "category": ch.category or 'N/A',
                  "status": '<span class="badge bg-success">Enabled</span>' if ch.enable else '<span class="badge bg-secondary">Disabled</span>',
                  "actions": f'<button type="button" class="btn btn-outline-secondary btn-sm toggle-btn" data-channel-id="{ch.id}">{"Disable" if ch.enable else "Enable"}</button>'
                } for ch in channels_page ]
        return jsonify({"draw": draw, "recordsTotal": records_total, "recordsFiltered": records_filtered, "data": data})
    except Exception as e:
        app.logger.error(f"Error in /api/channels/data: {e}", exc_info=True)
        return jsonify({"draw": request.form.get('draw', type=int, default=1), "recordsTotal": 0, "recordsFiltered": 0, "data": [], "error": "Server error."})

@app.route('/channel/toggle/<int:channel_id>', methods=['POST'])
def toggle_channel_enable(channel_id):
    """Toggles the enable status of a single channel."""
    try:
        channel = db_sqla.session.query(Channel).get(channel_id)
        if not channel:
             if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'status': 'error', 'message': 'Not found.'}), 404
             else: flash(f'Channel ID {channel_id} not found.', 'danger'); abort(404)
        channel.enable = 0 if channel.enable == 1 else 1; db_sqla.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'status': 'success', 'new_state': channel.enable})
        else: flash(f'Channel status updated.', 'success'); return redirect(url_for('manage_channels'))
    except Exception as e:
        db_sqla.session.rollback(); app.logger.error(f"Toggle Channel Error {channel_id}: {e}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'status': 'error', 'message': f'Error: {e}'}), 500
        else: flash(f'Error toggling channel: {e}', 'danger'); return redirect(url_for('manage_channels'))

@app.route('/channels/bulk_update', methods=['POST'])
def bulk_update_channels():
    """Handles bulk enabling/disabling channels."""
    action = request.form.get('action')
    selected_ids_str = request.form.getlist('channel_ids')

    if not action or action not in ['enable', 'disable', 'disable_all']:
        flash('Invalid action specified.', 'danger')
        return redirect(url_for('manage_channels'))

    updated_count = 0
    try:
        if action == 'disable_all':
            app.logger.info("Executing 'disable_all' channels action.")
            updated_count = db_sqla.session.query(Channel).update({"enable": 0}, synchronize_session=False)
            db_sqla.session.commit()
            flash(f'Successfully disabled all {updated_count} channels.', 'warning')

        elif action in ['enable', 'disable']:
            if not selected_ids_str:
                flash('No channels selected for enable/disable action.', 'warning')
                return redirect(url_for('manage_channels'))

            new_status = 1 if action == 'enable' else 0
            try:
                valid_ids = [int(id_str) for id_str in selected_ids_str]
                if valid_ids:
                    updated_count = db_sqla.session.query(Channel)\
                        .filter(Channel.id.in_(valid_ids))\
                        .update({"enable": new_status}, synchronize_session=False)
                    db_sqla.session.commit()
                    flash(f'{updated_count} channels {action}d.', 'success')
                else:
                    flash('No valid channel IDs were selected.', 'warning')
            except ValueError:
                flash('Invalid channel IDs provided.', 'danger')
        else:
             flash('Unknown bulk action.', 'danger')

    except Exception as e:
        db_sqla.session.rollback()
        app.logger.error(f"Bulk update error (Action: {action}): {e}", exc_info=True)
        flash(f'Error during bulk update: {e}', 'danger')

    return redirect(url_for('manage_channels'))

# --- Initialize Database Tables and Scheduler ---
def initialize_app():
    """Ensures tables exist and initializes scheduler jobs."""
    load_and_compile_filters() # Load filters on startup
    with app.app_context():
        app.logger.info("Application initialization: Ensuring database tables exist...")
        try:
            db_sqla.create_all()
            app.logger.info("SQLAlchemy tables checked/created.")
        except Exception as e:
            app.logger.error(f"Error during initial db_sqla.create_all(): {e}")

        app.logger.info("Initializing/Reloading scheduler jobs...")
        try:
            scheduler.add_job( func=scheduled_cleanup_old_entries, trigger='cron', hour=4, minute=5, id='cleanup_job', name='Daily Cleanup', replace_existing=True )
            app.logger.info("Scheduled cleanup job (Daily at 04:05 UTC).")
        except Exception as e: app.logger.error(f"Error scheduling cleanup job: {e}")

        enabled_sources_count = 0
        try:
            sources = db_sqla.session.query(M3uSource).filter_by(enabled=1).all()
            current_job_ids = {job.id for job in scheduler.get_jobs()}
            db_source_job_ids = set()
            for source in sources:
                job_id = f'source_refresh_{source.id}'; db_source_job_ids.add(job_id)
                schedule_source_refresh_job(source.id, source.url, source.refresh_interval_hours)
                enabled_sources_count += 1
            jobs_to_remove = current_job_ids - db_source_job_ids
            for job_id in jobs_to_remove:
                 if job_id.startswith('source_refresh_'): remove_source_refresh_job(job_id.split('_')[-1])
        except Exception as e: app.logger.error(f"Error scheduling source jobs during init: {e}")
        app.logger.info(f"Scheduler initialization complete. Jobs scheduled for {enabled_sources_count} sources.")

# Call initialization function once before starting scheduler or app
initialize_app()

# Start scheduler only if not already running
if not scheduler.running:
    try: scheduler.start(); app.logger.info("APScheduler started successfully.")
    except Exception as e: app.logger.error(f"APScheduler failed to start: {e}")


# --- Run Application ---
if __name__ == '__main__':
    use_debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    use_reloader = False # MUST be False for BackgroundScheduler in this setup
    app.logger.info(f" --- Starting M3U Manager --- ")
    app.logger.info(f" Config: debug={use_debug}, host=0.0.0.0, port=5000, reloader={use_reloader}")
    app.logger.info(f" Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    app.run(host='0.0.0.0', port=5000, debug=use_debug, threaded=True, use_reloader=use_reloader)