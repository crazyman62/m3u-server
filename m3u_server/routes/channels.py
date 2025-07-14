# m3u_server/routes/channels.py
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from sqlalchemy import or_, func, desc, asc
from datetime import datetime, timedelta, timezone
from .. import db, csrf
from ..models import Channel, EpgData, Url
from ..forms import AddChannelForm, EditChannelForm

channels_bp = Blueprint('channels', __name__)

@channels_bp.route('/')
def manage_channels():
    """Renders the main page for managing channels."""
    edit_form = EditChannelForm() # For the modal
    return render_template('manage_channels.html', title="Manage Channels", edit_form=edit_form)

@channels_bp.route('/add', methods=['GET', 'POST'])
def add_channel():
    """Handles manual addition of a new channel and a single URL."""
    form = AddChannelForm()
    if form.validate_on_submit():
        try:
            # Check if a channel with the same name or tvg_id already exists
            existing_channel = Channel.query.filter(or_(Channel.name == form.name.data, Channel.tvg_id == form.tvg_id.data)).first()
            if existing_channel:
                flash(f'Channel with this name or TVG-ID already exists.', 'warning')
            else:
                new_channel = Channel(
                    name=form.name.data,
                    category=form.category.data,
                    tvg_id=form.tvg_id.data,
                    tvg_logo=form.tvg_logo.data,
                    channel_num=form.channel_num.data,
                    last_seen=datetime.utcnow()
                )
                db.session.add(new_channel)
                db.session.flush() # Get the ID for the new channel

                new_url = Url(url=form.url.data, channel_id=new_channel.id, last_seen=datetime.utcnow())
                db.session.add(new_url)
                
                db.session.commit()
                flash(f'Channel "{new_channel.name}" and its URL were added successfully.', 'success')
                return redirect(url_for('channels.manage_channels'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding manual channel: {e}", exc_info=True)
            flash(f'An error occurred: {e}', 'danger')
            
    return render_template('add_channel_form.html', form=form, title="Add Channel Manually")


@channels_bp.route('/data', methods=['POST'])
@csrf.exempt # Exempt this data-only endpoint from CSRF protection
def get_channels_data():
    """API endpoint for DataTables to fetch channel data."""
    try:
        draw = request.form.get('draw', type=int, default=1)
        start = request.form.get('start', type=int, default=0)
        length = request.form.get('length', type=int, default=50)
        search_value = request.form.get('search[value]', default='').strip().lower()
        
        # Base query
        query = db.session.query(Channel)
        records_total = db.session.query(func.count(Channel.id)).scalar()

        # Search filter
        if search_value:
            query = query.filter(or_(
                func.lower(Channel.name).like(f"%{search_value}%"),
                func.lower(Channel.category).like(f"%{search_value}%"),
                func.lower(Channel.tvg_id).like(f"%{search_value}%")
            ))
        
        records_filtered = query.order_by(None).count()

        # Order by enabled status first, then by name
        query = query.order_by(desc(Channel.enabled), asc(Channel.name))
        
        channels_page = query.offset(start).limit(length).all()

        # Get EPG data for the channels on the current page
        now = datetime.now(timezone.utc)
        two_hours_later = now + timedelta(hours=2)
        channel_ids_on_page = [ch.tvg_id for ch in channels_page if ch.tvg_id]
        
        epg_results = db.session.query(EpgData).filter(
            EpgData.channel_tvg_id.in_(channel_ids_on_page),
            EpgData.start_time < two_hours_later,
            EpgData.end_time > now
        ).order_by(EpgData.start_time).all()

        epg_map = {}
        for epg_item in epg_results:
            if epg_item.channel_tvg_id not in epg_map:
                epg_map[epg_item.channel_tvg_id] = []
            epg_map[epg_item.channel_tvg_id].append(f"{epg_item.start_time.strftime('%H:%M')}: {epg_item.title}")

        # Format data for DataTables
        data = []
        for ch in channels_page:
            epg_html = "<br>".join(epg_map.get(ch.tvg_id, ['<span class="text-muted">No EPG data</span>']))
            logo_html = f'<img src="{ch.tvg_logo}" alt="logo" class="channel-logo" onerror="this.style.display=\'none\'">' if ch.tvg_logo else ''
            
            data.append({
                "logo": logo_html,
                "name": ch.name or '',
                "epg": epg_html,
                "status": '<span class="badge bg-success">Enabled</span>' if ch.enabled else '<span class="badge bg-secondary">Disabled</span>',
                "actions": f"""
                    <button type="button" class="btn btn-outline-primary btn-sm edit-btn" data-id="{ch.id}" data-name="{ch.name}" data-category="{ch.category or ''}" data-tvg-id="{ch.tvg_id or ''}" data-tvg-logo="{ch.tvg_logo or ''}" data-enabled="{1 if ch.enabled else 0}">Edit</button>
                    <button type="button" class="btn btn-outline-secondary btn-sm toggle-btn" data-id="{ch.id}">{'Disable' if ch.enabled else 'Enable'}</button>
                """
            })

        return jsonify({
            "draw": draw,
            "recordsTotal": records_total,
            "recordsFiltered": records_filtered,
            "data": data
        })
    except Exception as e:
        current_app.logger.error(f"Error in /api/channels/data: {e}", exc_info=True)
        return jsonify({"error": "Server error."}), 500

@channels_bp.route('/toggle/<int:channel_id>', methods=['POST'])
def toggle_channel(channel_id):
    """Toggles the enabled status of a single channel."""
    channel = Channel.query.get_or_404(channel_id)
    channel.enabled = not channel.enabled
    db.session.commit()
    return jsonify({'status': 'success', 'new_state': channel.enabled})

@channels_bp.route('/edit/<int:channel_id>', methods=['POST'])
def edit_channel(channel_id):
    """Handles editing a channel's details from a modal form."""
    channel = Channel.query.get_or_404(channel_id)
    form = EditChannelForm()
    if form.validate_on_submit():
        channel.name = form.name.data
        channel.category = form.category.data
        channel.tvg_id = form.tvg_id.data
        channel.tvg_logo = form.tvg_logo.data
        channel.enabled = form.enabled.data
        db.session.commit()
        flash(f'Channel "{channel.name}" updated successfully.', 'success')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"Error in {getattr(form, field).label.text}: {error}", 'danger')
    return redirect(url_for('channels.manage_channels'))
