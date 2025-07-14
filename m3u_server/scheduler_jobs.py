# m3u_server/scheduler_jobs.py
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from flask import current_app
from sqlalchemy.orm import selectinload

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

# --- Core Background Jobs ---

def refresh_single_m3u_source(source_id, source_url):
    """Fetches and processes a single M3U source URL efficiently."""
    app = create_app()
    with app.app_context():
        current_app.logger.info(f"[M3U-Refresh:{source_id}] Starting process for: {source_url}")
        start_time = datetime.now(timezone.utc)
        
        active_filters = Filter.query.filter_by(enabled=True).all()
        compiled_filters = [re.compile(f.pattern, re.IGNORECASE) for f in active_filters]
        
        def should_disable_channel(channel_name):
            return any(pattern.search(channel_name) for pattern in compiled_filters)

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

        # --- New Parsing Logic ---
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
                continue

            if current_extinf_data and not line.startswith('#'):
                stream_url = line
                tvg_id = current_extinf_data.get('tvg_id')
                
                # Use tvg_id as the primary key. If it's missing, we can't reliably track the channel.
                if not tvg_id:
                    current_extinf_data = None
                    continue
                
                if tvg_id not in parsed_channels:
                    parsed_channels[tvg_id] = {'attrs': current_extinf_data, 'urls': set()}
                
                parsed_channels[tvg_id]['urls'].add(stream_url)
                current_extinf_data = None
        
        current_app.logger.info(f"[M3U-Refresh:{source_id}] Parsed {len(parsed_channels)} unique channels with tvg-id.")

        # --- New DB Update Logic ---
        all_tvg_ids = list(parsed_channels.keys())
        batch_size = 500
        for i in range(0, len(all_tvg_ids), batch_size):
            batch_ids = all_tvg_ids[i:i + batch_size]
            existing_channels = db.session.query(Channel).filter(Channel.tvg_id.in_(batch_ids)).options(selectinload(Channel.urls)).all()
            channel_map = {ch.tvg_id: ch for ch in existing_channels}

            for tvg_id, m3u_item in parsed_channels.items():
                if tvg_id not in batch_ids: continue

                attrs = m3u_item['attrs']
                urls = m3u_item['urls']
                
                channel_name = attrs.get('tvg_name') or attrs['display_name']
                channel_num_str = attrs.get('tvg_chno')
                channel_num = int(channel_num_str) if channel_num_str and channel_num_str.isdigit() else None
                logo_url = attrs.get('tvg_logo')
                category = attrs.get('group_title')

                channel = channel_map.get(tvg_id)
                
                is_disabled_by_filter = should_disable_channel(channel_name)

                if not channel:
                    current_app.logger.info(f"Adding new channel '{channel_name}' (ID: {tvg_id}, Ch: {channel_num})")
                    channel = Channel(
                        tvg_id=tvg_id,
                        name=channel_name,
                        tvg_name=attrs.get('tvg_name', channel_name),
                        tvg_logo=logo_url,
                        category=category,
                        channel_num=channel_num,
                        enabled=not is_disabled_by_filter # Apply filter on creation
                    )
                    db.session.add(channel)
                    db.session.flush() # Needed to get the channel.id for new URLs
                
                # Update existing channel attributes
                channel.name = channel_name
                channel.tvg_logo = logo_url
                channel.category = category
                channel.channel_num = channel_num
                channel.last_seen = start_time
                
                # Re-apply filter on every refresh
                if is_disabled_by_filter:
                    channel.enabled = False

                # Update URLs
                existing_urls = {u.url for u in channel.urls}
                for url_str in urls:
                    if url_str not in existing_urls:
                        new_url = Url(url=url_str, channel_id=channel.id, last_seen=start_time)
                        db.session.add(new_url)
                    else:
                        # Touch the last_seen timestamp for existing URLs
                        for u in channel.urls:
                            if u.url == url_str:
                                u.last_seen = start_time
                                break
            
            db.session.commit()
            
        source = M3uSource.query.get(source_id)
        if source:
            source.last_checked = start_time
            db.session.commit()
        current_app.logger.info(f"[M3U-Refresh:{source_id}] Process finished.")


def refresh_single_epg_source(epg_id, epg_url):
    """Fetches and processes a single XMLTV EPG source efficiently using a diffing method."""
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
            
            db_channels = Channel.query.all()
            db_channels_by_id = {c.tvg_id.lower(): c for c in db_channels if c.tvg_id}
            db_channels_by_num = {str(c.channel_num): c for c in db_channels if c.channel_num is not None}
            db_channels_by_norm_name = {normalize_name(c.name): c for c in db_channels}

            final_epg_id_map = {}
            epg_xml_channels = root.findall('channel')

            for epg_channel in epg_xml_channels:
                original_epg_id = epg_channel.attrib.get('id')
                display_name_elem = epg_channel.find('display-name')
                epg_display_name = display_name_elem.text if display_name_elem is not None else ''
                
                if not original_epg_id: continue

                if original_epg_id.lower() in db_channels_by_id:
                    final_epg_id_map[original_epg_id] = db_channels_by_id[original_epg_id.lower()].tvg_id
                    continue

                if epg_display_name:
                    match = re.match(r'^\s*(\d+)\s*', epg_display_name)
                    if match:
                        epg_channel_num = match.group(1)
                        if epg_channel_num in db_channels_by_num:
                            final_epg_id_map[original_epg_id] = db_channels_by_num[epg_channel_num].tvg_id
                            continue
                
                if epg_display_name:
                    norm_name = normalize_name(epg_display_name)
                    if norm_name in db_channels_by_norm_name:
                        final_epg_id_map[original_epg_id] = db_channels_by_norm_name[norm_name].tvg_id
                        continue
            
            current_app.logger.info(f"[EPG-Refresh:{epg_id}] Successfully mapped {len(final_epg_id_map)} EPG channels to DB channels.")

            mapped_db_tvg_ids = set(final_epg_id_map.values())
            if not mapped_db_tvg_ids:
                current_app.logger.warning(f"[EPG-Refresh:{epg_id}] No channels were mapped. Aborting EPG update.")
                return

            existing_epg_data = EpgData.query.filter(EpgData.channel_tvg_id.in_(mapped_db_tvg_ids)).all()
            existing_programs = {(d.channel_tvg_id, d.start_time, d.title): d for d in existing_epg_data}
            
            new_programs_from_xml = {}
            
            for prog in root.findall('programme'):
                original_prog_channel_id = prog.attrib.get('channel')
                db_tvg_id = final_epg_id_map.get(original_prog_channel_id)
                if not db_tvg_id: continue

                start = parse_xmltv_datetime(prog.attrib.get('start'))
                stop = parse_xmltv_datetime(prog.attrib.get('stop'))
                if not start or not stop or stop < start_time: continue

                title_elem = prog.find('title')
                title = title_elem.text if title_elem is not None else 'No Title'
                
                program_key = (db_tvg_id, start, title)
                
                desc_elem = prog.find('desc')
                description = desc_elem.text if desc_elem is not None else None
                
                new_programs_from_xml[program_key] = {
                    'end_time': stop,
                    'description': description
                }

            to_add, to_update = [], []
            existing_keys, new_keys = set(existing_programs.keys()), set(new_programs_from_xml.keys())

            for key in new_keys - existing_keys:
                prog_data = new_programs_from_xml[key]
                to_add.append(EpgData(
                    channel_tvg_id=key[0], start_time=key[1], title=key[2],
                    end_time=prog_data['end_time'], description=prog_data['description']
                ))

            for key in new_keys.intersection(existing_keys):
                existing_prog, new_prog_data = existing_programs[key], new_programs_from_xml[key]
                if existing_prog.end_time != new_prog_data['end_time'] or existing_prog.description != new_prog_data['description']:
                    existing_prog.end_time = new_prog_data['end_time']
                    existing_prog.description = new_prog_data['description']
                    to_update.append(existing_prog)

            ids_to_delete = [existing_programs[key].id for key in existing_keys - new_keys]

            if to_add:
                db.session.add_all(to_add)
                current_app.logger.info(f"[EPG-Refresh:{epg_id}] Adding {len(to_add)} new EPG entries.")
            if to_update:
                current_app.logger.info(f"[EPG-Refresh:{epg_id}] Updating {len(to_update)} existing EPG entries.")
            if ids_to_delete:
                db.session.query(EpgData).filter(EpgData.id.in_(ids_to_delete)).delete(synchronize_session=False)
                current_app.logger.info(f"[EPG-Refresh:{epg_id}] Deleting {len(ids_to_delete)} stale EPG entries.")

            db.session.commit()

            epg_source = EpgSource.query.get(epg_id)
            if epg_source:
                epg_source.last_checked = start_time
                db.session.commit()
            current_app.logger.info(f"[EPG-Refresh:{epg_id}] Process finished.")

        except ET.ParseError as e:
            current_app.logger.error(f"[EPG-Refresh:{epg_id}] XML parsing failed: {e}")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"[EPG-Refresh:{epg_id}] An unexpected error occurred: {e}", exc_info=True)

def scheduled_cleanup_job():
    """Scheduled task to remove old channels, URLs, and EPG data."""
    app = create_app()
    with app.app_context():
        current_app.logger.info("[Cleanup-Job] Starting daily cleanup...")
        
        channel_cutoff = datetime.now(timezone.utc) - timedelta(days=current_app.config.get('CHANNEL_DATA_RETENTION_DAYS', 3))
        urls_deleted = Url.query.filter(Url.last_seen < channel_cutoff).delete()
        
        orphan_channels_query = db.session.query(Channel.id).outerjoin(Url).filter(Url.id == None, Channel.last_seen < channel_cutoff)
        channels_deleted = Channel.query.filter(Channel.id.in_(orphan_channels_query)).delete(synchronize_session=False)
        db.session.commit()
        current_app.logger.info(f"[Cleanup-Job] Deleted {urls_deleted} old URLs and {channels_deleted} old/orphan channels.")
        
        epg_cutoff = datetime.now(timezone.utc) - timedelta(hours=current_app.config.get('EPG_DATA_RETENTION_HOURS', 72))
        epg_deleted = EpgData.query.filter(EpgData.end_time < epg_cutoff).delete()
        db.session.commit()
        current_app.logger.info(f"[Cleanup-Job] Deleted {epg_deleted} old EPG entries.")
        
        current_app.logger.info("[Cleanup-Job] Finished.")

def disable_channels_without_epg():
    """Disables channels that do not have any EPG data if the feature is enabled."""
    app = create_app()
    with app.app_context():
        if not current_app.config.get('DISABLE_CHANNELS_WITHOUT_EPG'):
            current_app.logger.info("[Disable-No-EPG] Job skipped: feature is disabled in config.")
            return

        current_app.logger.info("[Disable-No-EPG] Starting job to disable channels without EPG data.")
        
        try:
            subquery = db.session.query(EpgData.channel_tvg_id).distinct()
            channels_with_epg = {row[0] for row in subquery.all()}

            channels_to_disable = Channel.query.filter(
                Channel.enabled == True,
                Channel.tvg_id.isnot(None),
                Channel.tvg_id.notin_(channels_with_epg)
            )

            disabled_count = channels_to_disable.update({'enabled': False}, synchronize_session=False)
            db.session.commit()

            if disabled_count > 0:
                current_app.logger.info(f"[Disable-No-EPG] Successfully disabled {disabled_count} channels without EPG data.")
            else:
                current_app.logger.info("[Disable-No-EPG] No channels found to disable.")

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"[Disable-No-EPG] An error occurred: {e}", exc_info=True)
