# m3u_server/routes/main.py
from flask import Blueprint, redirect, url_for, render_template, abort, current_app, request
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
    """Generates and serves the final EPG XMLTV file."""
    try:
        channels = Channel.query.filter(Channel.enabled == True, Channel.tvg_id != None).all()
        epg_data = EpgData.query.filter(EpgData.end_time > datetime.now(timezone.utc)).order_by(EpgData.start_time).all()

        epg_map = {}
        for item in epg_data:
            if item.channel_tvg_id not in epg_map:
                epg_map[item.channel_tvg_id] = []
            epg_map[item.channel_tvg_id].append(item)

        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv>']

        for channel in channels:
            channel_id_esc = escape(channel.tvg_id)
            display_name_esc = escape(channel.name)
            
            xml_lines.append(f'  <channel id="{channel_id_esc}">')
            xml_lines.append(f'    <display-name>{display_name_esc}</display-name>')
            if channel.tvg_logo:
                icon_src_esc = escape(channel.tvg_logo)
                xml_lines.append(f'    <icon src="{icon_src_esc}" />')
            xml_lines.append('  </channel>')

        for channel in channels:
            if channel.tvg_id in epg_map:
                for prog in epg_map[channel.tvg_id]:
                    channel_id_esc = escape(prog.channel_tvg_id)
                    title_esc = escape(prog.title)
                    
                    start_str = prog.start_time.strftime('%Y%m%d%H%M%S %z')
                    end_str = prog.end_time.strftime('%Y%m%d%H%M%S %z')
                    
                    xml_lines.append(f'  <programme start="{start_str}" stop="{end_str}" channel="{channel_id_esc}">')
                    xml_lines.append(f'    <title lang="en">{title_esc}</title>')
                    if prog.description:
                        desc_esc = escape(prog.description)
                        xml_lines.append(f'    <desc lang="en">{desc_esc}</desc>')
                    xml_lines.append('  </programme>')

        xml_lines.append('</tv>')
        
        xml_content = "\n".join(xml_lines)
        return xml_content.encode('utf-8'), 200, {'Content-Type': 'application/xml; charset=utf-8'}

    except Exception as e:
        current_app.logger.error(f"Error generating EPG XML: {e}", exc_info=True)
        abort(500, "Error generating EPG XML.")
