# m3u_server/routes/main.py
from flask import Blueprint, redirect, url_for, render_template, abort, current_app, request, Response, stream_with_context
from sqlalchemy.orm import joinedload
from datetime import datetime, timezone
from xml.sax.saxutils import escape
from .. import db
from ..models import Channel, Url, EpgData

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    """Redirects the root URL to the manage sources page."""
    return redirect(url_for('sources.manage_sources'))

@main_bp.route('/playlist.m3u')
def get_m3u_playlist():
    """Generates and serves the final M3U playlist file with an EPG link."""
    try:
        results = db.session.query(Channel).options(joinedload(Channel.urls)).filter(Channel.enabled == True).order_by(Channel.category, Channel.name).all()
    except Exception as e:
        current_app.logger.error(f"Database error generating playlist: {e}", exc_info=True)
        abort(500, description="Database error occurred while generating the playlist.")

    # Generate the absolute URL for the EPG file
    epg_url = url_for('main.get_epg_xml', _external=True)
    
    # Start the M3U content with the header including the EPG URL
    m3u_content = [f'#EXTM3U url-tvg="{epg_url}"']

    for channel in results:
        for url_obj in channel.urls:
            extinf_parts = ['#EXTINF:-1']
            
            if channel.tvg_id: extinf_parts.append(f'tvg-id="{channel.tvg_id}"')
            if channel.tvg_name: extinf_parts.append(f'tvg-name="{channel.tvg_name}"')
            else: extinf_parts.append(f'tvg-name="{channel.name}"')
            if channel.tvg_logo: extinf_parts.append(f'tvg-logo="{channel.tvg_logo}"')
            if channel.category: extinf_parts.append(f'group-title="{channel.category}"')
            if channel.channel_num is not None: extinf_parts.append(f'tvg-chno="{channel.channel_num}"')

            extinf_line = " ".join(extinf_parts) + f",{channel.name}"
            m3u_content.append(extinf_line)
            m3u_content.append(url_obj.url)

    response_text = "\n".join(m3u_content)
    return response_text.encode('utf-8'), 200, {
        'Content-Type': 'application/vnd.apple.mpegurl; charset=utf-8',
        'Content-Disposition': 'attachment; filename="playlist.m3u"'
    }

@main_bp.route('/epg.xml')
def get_epg_xml():
    """Generates and serves the final EPG XMLTV file via streaming."""
    def generate():
        yield '<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n'

        # 1. Output Channels
        channels_query = Channel.query.filter(Channel.enabled == True, Channel.tvg_id != None)

        # We also need to get the set of valid tvg_ids to filter the EPG data.
        # Since we are streaming, we can't efficiently iterate the channel list twice without querying twice
        # or storing in memory. The list of channels is usually small enough to store IDs in memory.
        # But for robustness, we use a subquery for the EPG data filtering as planned.

        for channel in channels_query:
             channel_id_esc = escape(channel.tvg_id)
             display_name_esc = escape(channel.name)

             yield f'  <channel id="{channel_id_esc}">\n'
             yield f'    <display-name>{display_name_esc}</display-name>\n'
             if channel.tvg_logo:
                 icon_src_esc = escape(channel.tvg_logo)
                 yield f'    <icon src="{icon_src_esc}" />\n'
             yield '  </channel>\n'

        # 2. Output Programmes
        # Optimization: Query only EPG data for the enabled channels using a subquery
        valid_tvg_ids_subquery = db.session.query(Channel.tvg_id).filter(
             Channel.enabled == True,
             Channel.tvg_id != None
        )

        epg_query = EpgData.query.filter(
            EpgData.channel_tvg_id.in_(valid_tvg_ids_subquery),
            EpgData.end_time > datetime.now(timezone.utc)
        ).order_by(EpgData.start_time)

        # Use yield_per to fetch in chunks to avoid memory overload
        for prog in epg_query.yield_per(500):
            channel_id_esc = escape(prog.channel_tvg_id)
            title_esc = escape(prog.title)
            
            # Helper to format time
            start_str = prog.start_time.strftime('%Y%m%d%H%M%S %z')
            end_str = prog.end_time.strftime('%Y%m%d%H%M%S %z')

            yield f'  <programme start="{start_str}" stop="{end_str}" channel="{channel_id_esc}">\n'
            yield f'    <title lang="en">{title_esc}</title>\n'
            if prog.description:
                desc_esc = escape(prog.description)
                yield f'    <desc lang="en">{desc_esc}</desc>\n'
            yield '  </programme>\n'
        
        yield '</tv>'

    return Response(stream_with_context(generate()), mimetype='application/xml')
