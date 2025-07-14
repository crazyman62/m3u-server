# m3u_server/scheduler_jobs.py
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from flask import current_app
from sqlalchemy.orm import selectinload
from sqlalchemy import or_

# Import the app factory and extensions from the main package
from . import db, scheduler, create_app
from .models import M3uSource, EpgSource, Channel, Url, EpgData, Filter

# --- Helper Functions ---

def parse_xmltv_datetime(dt_str):
    """Parses XMLTV timestamp into a timezone-aware datetime object."""
    try:
        parts = dt_str.strip().split(' ')
        dt_part = parts[0]
        dt = datetime.strptime(dt_part, '%Y%m%d%H%M%S')
        if len(parts) > 1:
            offset_str = parts[1]
            offset_hours = int(offset_str[1:3])
            offset_minutes = int(offset_str[3:5])
            offset_delta = timedelta(hours=offset_hours, minutes=offset_minutes)
            if offset_str[0] == '-':
                offset_delta = -offset_delta
            tz = timezone(offset_delta)
            return dt.replace(tzinfo=tz)
        else:
            return dt.replace(tzinfo=timezone.utc)
    except (ValueError, IndexError) as e:
        print(f"ERROR: Failed to parse XMLTV datetime string '{dt_str}': {e}")
        return None

def normalize_name(name):
    """Creates a simplified version of a name for fuzzy matching."""
    if not name:
        return ""
    return re.sub(r'[^a-z0-9]', '', name.lower())

# --- Job Scheduling Helpers ---

def schedule_all_source_refreshes():
    """Schedules jobs for all enabled M3U sources."""
    try:
        sources = M3uSource.query.filter_by(enabled=True).all()
        for source in sources:
            schedule_source_refresh_job(source.id, source.url, source.refresh_interval_hours)
        current_app.logger.info(f"Scheduled refresh jobs for {len(sources)} M3U sources.")
    except Exception as e:
        current_app.logger.error(f"Error scheduling M3U source jobs: {e}", exc_info=True)

def schedule_all_epg_refreshes():
    """Schedules jobs for all enabled EPG sources."""
    try:
        epg_sources = EpgSource.query.filter_by(enabled=True).all()
        for epg_source in epg_sources:
            schedule_epg_refresh_job(epg_source.id, epg_source.url, epg_source.refresh_interval_hours)
        current_app.logger.info(f"Scheduled refresh jobs for {len(epg_sources)} EPG sources.")
    except Exception as e:
        current_app.logger.error(f"Error scheduling EPG source jobs: {e}", exc_info=True)

def schedule_source_refresh_job(source_id, source_url, interval_hours):
    job_id = f'm3u_refresh_{source_id}'
    scheduler.add_job(
        func=refresh_single_m3u_source,
        trigger='interval', hours=max(1, interval_hours),
        args=[source_id, source_url], id=job_id,
        name=f'Refresh M3U {source_id}', replace_existing=True
    )

def schedule_epg_refresh_job(epg_id, epg_url, interval_hours):
    job_id = f'epg_refresh_{epg_id}'
    scheduler.add_job(
        func=refresh_single_epg_source,
        trigger='interval', hours=max(1, interval_hours),
        args=[epg_id, epg_url], id=job_id,
        name=f'Refresh EPG {epg_id}', replace_existing=True
    )

# --- Core Logic and Jobs ---

def _synchronize_channel_states_logic():
    """
    The master logic to synchronize the enabled state of all channels based on all active rules.
    This function expects to be run within an active Flask application context.
    A channel is ENABLED if and only if:
    1. It does NOT match any active regex filter.
    2. If the "no EPG" rule is active, it MUST have EPG data.
    """
    current_app.logger.info("[Sync-States] Starting logic to synchronize channel states with all active rules.")
    
    try:
        # Rule 1: Get all active regex filters
        active_regex_filters = Filter.query.filter_by(enabled=True).all()
        compiled_filters = [re.compile(f.pattern, re.IGNORECASE) for f in active_regex_filters]
        current_app.logger.info(f"[Sync-States] Found {len(compiled_filters)} active regex filters.")

        # Rule 2: Check if the "no EPG" rule is active and get relevant data
        no_epg_rule_active = current_app.config.get('DISABLE_CHANNELS_WITHOUT_EPG', False)
        channels_with_epg = set()
        if no_epg_rule_active:
            channels_with_epg = {row[0] for row in db.session.query(EpgData.channel_tvg_id).distinct().all() if row[0]}
            current_app.logger.info(f"[Sync-States] 'No EPG' rule is active. Found {len(channels_with_epg)} channels with EPG data.")

        all_channels = Channel.query.all()
        channels_to_disable = []
        channels_to_enable = []
        log_counter = 0
        
        for channel in all_channels:
            # Check if channel should be blocked by a regex filter
            is_blocked_by_regex = any(p.search(text) for p in compiled_filters for text in [channel.name, channel.category] if text)
            
            # Check if channel should be blocked by the "no EPG" rule
            is_blocked_by_no_epg = False
            if no_epg_rule_active:
                # A channel is blocked if it has no tvg_id OR its tvg_id is not in the set of channels with EPG data.
                if not channel.tvg_id or channel.tvg_id not in channels_with_epg:
                    is_blocked_by_no_epg = True
            
            # Determine the final state
            should_be_enabled = not is_blocked_by_regex and not is_blocked_by_no_epg

            # For debugging, log the decision for the first few channels
            if log_counter < 5:
                current_app.logger.info(f"[Sync-Debug] Chan: '{channel.name}' (ID:{channel.id}, Enabled:{channel.enabled}) | RegexBlock:{is_blocked_by_regex}, NoEpgBlock:{is_blocked_by_no_epg} -> ShouldBeEnabled:{should_be_enabled}")
                log_counter += 1

            if channel.enabled != should_be_enabled:
                if should_be_enabled:
                    channels_to_enable.append(channel.id)
                else:
                    channels_to_disable.append(channel.id)
        
        if channels_to_disable:
            current_app.logger.info(f"[Sync-States] Disabling {len(channels_to_disable)} channels.")
            db.session.query(Channel).filter(Channel.id.in_(channels_to_disable)).update({'enabled': False}, synchronize_session=False)

        if channels_to_enable:
            current_app.logger.info(f"[Sync-States] Enabling {len(channels_to_enable)} channels.")
            db.session.query(Channel).filter(Channel.id.in_(channels_to_enable)).update({'enabled': True}, synchronize_session=False)

        if channels_to_disable or channels_to_enable:
            db.session.commit()
            current_app.logger.info(f"[Sync-States] Commit complete. Disabled: {len(channels_to_disable)}, Enabled: {len(channels_to_enable)}.")
        else:
            current_app.logger.info("[Sync-States] All channel states are already synchronized with all rules. No changes needed.")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"[Sync-States] An error occurred: {e}", exc_info=True)

def synchronize_channel_states():
    """The main job function that creates an app context and runs the sync logic."""
    app = create_app()
    with app.app_context():
        _synchronize_channel_states_logic()

def refresh_single_m3u_source(source_id, source_url):
    """Fetches and processes a single M3U source URL efficiently."""
    app = create_app()
    with app.app_context():
        current_app.logger.info(f"[M3U-Refresh:{source_id}] Starting process for: {source_url}")
        start_time = datetime.now(timezone.utc)

        try:
            headers = {'User-Agent': 'M3U-Server/1.0'}
            response = requests.get(source_url, timeout=60, headers=headers)
            response.raise_for_status()
            m3u_content = response.text
        except requests.RequestException as e:
            current_app.logger.error(f"[M3U-Refresh:{source_id}] Download failed: {e}")
            return

        lines = m3u_content.splitlines()
        if not lines or not lines[0].strip().startswith('#EXTM3U'):
            current_app.logger.warning(f"[M3U-Refresh:{source_id}] Invalid M3U header.")
            return

        parsed_channels = {}
        current_extinf_data = None
        for line in lines:
            line = line.strip()
            if not line: continue

            if line.startswith('#EXTINF:'):
                match = re.match(r'#EXTINF:-?\d+\s*(.*),(.*)', line)
                if match:
                    attributes_str, display_name = match.groups()
                    attrs = dict(re.findall(r'([a-zA-Z0-9_-]+)="([^"]*)"', attributes_str))
                    attrs = {k.lower().replace('-', '_'): v for k, v in attrs.items()}
                    attrs['display_name'] = display_name.strip()
                    current_extinf_data = attrs
                else:
                    current_extinf_data = None
                continue

            if current_extinf_data and not line.startswith('#'):
                stream_url = line
                tvg_id = current_extinf_data.get('tvg_id')
                channel_name = current_extinf_data.get('tvg_name') or current_extinf_data.get('display_name')
                
                if not channel_name:
                    current_extinf_data = None
                    continue

                channel_key = tvg_id if tvg_id else channel_name
                
                if channel_key not in parsed_channels:
                    parsed_channels[channel_key] = {'attrs': current_extinf_data, 'urls': set()}
                
                parsed_channels[channel_key]['urls'].add(stream_url)
                current_extinf_data = None
        
        current_app.logger.info(f"[M3U-Refresh:{source_id}] Parsed {len(parsed_channels)} unique channels.")

        all_channel_keys = list(parsed_channels.keys())
        batch_size = 500
        for i in range(0, len(all_channel_keys), batch_size):
            batch_keys = all_channel_keys[i:i + batch_size]
            
            possible_matches = db.session.query(Channel).filter(
                or_(Channel.tvg_id.in_(batch_keys), Channel.name.in_(batch_keys))
            ).options(selectinload(Channel.urls)).all()

            channels_by_tvg_id = {ch.tvg_id: ch for ch in possible_matches if ch.tvg_id}
            channels_by_name = {ch.name: ch for ch in possible_matches}

            for channel_key, m3u_item in parsed_channels.items():
                if channel_key not in batch_keys: continue

                attrs = m3u_item['attrs']
                urls = m3u_item['urls']
                
                tvg_id = attrs.get('tvg_id')
                channel_name = attrs.get('tvg_name') or attrs['display_name']
                if not channel_name: continue

                channel_num_str = attrs.get('tvg_chno')
                channel_num = int(channel_num_str) if channel_num_str and channel_num_str.isdigit() else None
                logo_url = attrs.get('tvg_logo')
                category = attrs.get('group_title')

                channel = None
                if tvg_id: channel = channels_by_tvg_id.get(tvg_id)
                if not channel: channel = channels_by_name.get(channel_name)

                if not channel:
                    channel = Channel(
                        tvg_id=tvg_id,
                        name=channel_name,
                        tvg_name=attrs.get('tvg_name', channel_name),
                        tvg_logo=logo_url,
                        category=category,
                        channel_num=channel_num
                    )
                    db.session.add(channel)
                    if tvg_id: channels_by_tvg_id[tvg_id] = channel
                    channels_by_name[channel_name] = channel
                    db.session.flush()
                else:
                    channel.name = channel_name
                    if logo_url: channel.tvg_logo = logo_url
                    if category: channel.category = category
                    if channel_num is not None: channel.channel_num = channel_num
                    if tvg_id and not channel.tvg_id: channel.tvg_id = tvg_id

                channel.last_seen = start_time
                existing_urls = {u.url for u in channel.urls}
                for url_str in urls:
                    if url_str not in existing_urls:
                        db.session.add(Url(url=url_str, channel_id=channel.id, last_seen=start_time))
                    else:
                        for u in channel.urls:
                            if u.url == url_str: u.last_seen = start_time
            
            db.session.commit()
            
        source = M3uSource.query.get(source_id)
        if source:
            source.last_checked = start_time
            db.session.commit()

        current_app.logger.info(f"[M3U-Refresh:{source_id}] Process finished. Now running channel state synchronization.")
        _synchronize_channel_states_logic()


def refresh_single_epg_source(epg_id, epg_url):
    """Fetches and processes a single XMLTV EPG source, mapping EPG data and updating channel info."""
    app = create_app()
    with app.app_context():
        current_app.logger.info(f"[EPG-Refresh:{epg_id}] Starting process for: {epg_url}")
        start_time = datetime.now(timezone.utc)

        try:
            headers = {'User-Agent': 'M3U-Server/1.0'}
            response = requests.get(epg_url, timeout=300, headers=headers)
            response.raise_for_status()
            xml_content = response.content
        except requests.RequestException as e:
            current_app.logger.error(f"[EPG-Refresh:{epg_id}] Download failed: {e}")
            return

        try:
            root = ET.fromstring(xml_content)
            
            all_db_channels = Channel.query.all()
            db_channels_by_tvg_id = {c.tvg_id.lower(): c for c in all_db_channels if c.tvg_id}
            db_channels_by_norm_name = {normalize_name(c.name): c for c in all_db_channels}
            
            epg_to_db_channel_map = {}
            channels_to_update = []

            for epg_channel_node in root.findall('channel'):
                epg_id_attr = epg_channel_node.attrib.get('id')
                if not epg_id_attr: continue

                display_name_node = epg_channel_node.find('display-name')
                epg_display_name = display_name_node.text if display_name_node is not None else ''
                
                icon_node = epg_channel_node.find('icon')
                epg_logo_url = icon_node.attrib.get('src') if icon_node is not None else None

                matched_channel = db_channels_by_tvg_id.get(epg_id_attr.lower())
                if not matched_channel and epg_display_name:
                    matched_channel = db_channels_by_norm_name.get(normalize_name(epg_display_name))

                if matched_channel:
                    epg_to_db_channel_map[epg_id_attr] = matched_channel
                    updated = False
                    if not matched_channel.tvg_id:
                        matched_channel.tvg_id = epg_id_attr
                        updated = True
                    if epg_logo_url and not matched_channel.tvg_logo:
                        matched_channel.tvg_logo = epg_logo_url
                        updated = True
                    if updated:
                        channels_to_update.append(matched_channel)

            if channels_to_update:
                db.session.commit()
                current_app.logger.info(f"[EPG-Refresh:{epg_id}] Updated {len(channels_to_update)} channels with info from EPG.")

            if not epg_to_db_channel_map:
                current_app.logger.warning(f"[EPG-Refresh:{epg_id}] No channels were mapped. Aborting programme data update.")
                return

            mapped_db_tvg_ids = {ch.tvg_id for ch in epg_to_db_channel_map.values() if ch.tvg_id}
            db.session.query(EpgData).filter(EpgData.channel_tvg_id.in_(mapped_db_tvg_ids)).delete(synchronize_session=False)

            new_programs = []
            for prog_node in root.findall('programme'):
                prog_channel_id = prog_node.attrib.get('channel')
                db_channel = epg_to_db_channel_map.get(prog_channel_id)
                if not db_channel or not db_channel.tvg_id: continue

                start = parse_xmltv_datetime(prog_node.attrib.get('start'))
                stop = parse_xmltv_datetime(prog_node.attrib.get('stop'))
                if not start or not stop or stop < start_time: continue

                title = (prog_node.find('title').text if prog_node.find('title') is not None else 'No Title')
                description = (prog_node.find('desc').text if prog_node.find('desc') is not None else None)
                
                new_programs.append(EpgData(
                    channel_tvg_id=db_channel.tvg_id, title=title,
                    start_time=start, end_time=stop, description=description
                ))
            
            if new_programs:
                db.session.bulk_save_objects(new_programs)
                db.session.commit()
                current_app.logger.info(f"[EPG-Refresh:{epg_id}] Ingested {len(new_programs)} new EPG entries.")

            epg_source = EpgSource.query.get(epg_id)
            if epg_source:
                epg_source.last_checked = start_time
                db.session.commit()
            
            current_app.logger.info(f"[EPG-Refresh:{epg_id}] Process finished. Now running channel state synchronization.")
            _synchronize_channel_states_logic()

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"[EPG-Refresh:{epg_id}] An unexpected error occurred: {e}", exc_info=True)


def scheduled_cleanup_job():
    """Scheduled task to remove old channels, URLs, and EPG data."""
    app = create_app()
    with app.app_context():
        current_app.logger.info("[Cleanup-Job] Starting daily cleanup...")
        
        channel_cutoff = datetime.now(timezone.utc) - timedelta(days=current_app.config.get('CHANNEL_DATA_RETENTION_DAYS', 3))
        Url.query.filter(Url.last_seen < channel_cutoff).delete()
        
        orphan_channels_query = db.session.query(Channel.id).outerjoin(Url).filter(Url.id == None, Channel.last_seen < channel_cutoff)
        Channel.query.filter(Channel.id.in_(orphan_channels_query)).delete(synchronize_session=False)
        
        epg_cutoff = datetime.now(timezone.utc) - timedelta(hours=current_app.config.get('EPG_DATA_RETENTION_HOURS', 72))
        EpgData.query.filter(EpgData.end_time < epg_cutoff).delete()
        
        db.session.commit()
        current_app.logger.info("[Cleanup-Job] Finished.")

# --- Wrapper jobs for UI buttons and scheduled tasks ---

def apply_all_filters_job():
    """Wrapper job that triggers the main synchronization task."""
    synchronize_channel_states()

def disable_channels_without_epg():
    """Wrapper job that triggers the main synchronization task."""
    synchronize_channel_states()
