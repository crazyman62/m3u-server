# m3u_server/routes/sources.py
import os
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from .. import db
from ..models import M3uSource
from ..forms import SourceM3uForm, UpdateIntervalForm
from ..scheduler_jobs import schedule_source_refresh_job, refresh_single_m3u_source
from .. import scheduler

sources_bp = Blueprint('sources', __name__)

@sources_bp.route('/')
def manage_sources():
    """Displays M3U source URLs and allows management."""

    # --- DEBUGGING STEP ---
    # Log the exact path Flask is checking for the template.
    template_path = os.path.join(current_app.root_path, 'templates', 'manage_sources.html')
    current_app.logger.info(f"Attempting to render template. Checking for file at: {template_path}")

    if not os.path.exists(template_path):
        current_app.logger.error(f"*** TEMPLATE NOT FOUND AT PATH: {template_path} ***")
        
        # Also, let's log the contents of the templates directory to see what's there.
        templates_dir = os.path.join(current_app.root_path, 'templates')
        try:
            dir_contents = os.listdir(templates_dir)
            current_app.logger.info(f"Actual contents of templates directory ('{templates_dir}'): {dir_contents}")
        except FileNotFoundError:
            current_app.logger.error(f"The 'templates' directory itself was not found at: {templates_dir}")
    else:
        current_app.logger.info("Success! Template file was found at the expected path.")
    # --- END DEBUGGING STEP ---

    sources = M3uSource.query.order_by(M3uSource.id).all()
    interval_form = UpdateIntervalForm()
    return render_template('manage_sources.html', sources=sources, interval_form=interval_form, title="Manage M3U Sources")

@sources_bp.route('/add', methods=['GET', 'POST'])
def add_source():
    """Adds a new M3U source URL and schedules it."""
    form = SourceM3uForm()
    if form.validate_on_submit():
        url = form.m3u_url.data
        if M3uSource.query.filter_by(url=url).first():
            flash('Source URL already exists.', 'warning')
        else:
            new_source = M3uSource(url=url)
            db.session.add(new_source)
            db.session.commit()
            schedule_source_refresh_job(new_source.id, new_source.url, new_source.refresh_interval_hours)
            flash(f'Source "{url[:50]}..." added and scheduled for daily refresh.', 'success')
        return redirect(url_for('sources.manage_sources'))
    return render_template('source_form.html', form=form, title="Add M3U Source")

@sources_bp.route('/delete/<int:source_id>', methods=['POST'])
def delete_source(source_id):
    source = M3uSource.query.get_or_404(source_id)
    job_id = f'm3u_refresh_{source_id}'
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    db.session.delete(source)
    db.session.commit()
    flash('Source URL deleted and job unscheduled.', 'success')
    return redirect(url_for('sources.manage_sources'))

@sources_bp.route('/toggle/<int:source_id>', methods=['POST'])
def toggle_source(source_id):
    source = M3uSource.query.get_or_404(source_id)
    source.enabled = not source.enabled
    db.session.commit()
    job_id = f'm3u_refresh_{source_id}'
    if source.enabled:
        schedule_source_refresh_job(source.id, source.url, source.refresh_interval_hours)
        flash('Source enabled and job scheduled.', 'success')
    else:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        flash('Source disabled and job unscheduled.', 'warning')
    return redirect(url_for('sources.manage_sources'))

@sources_bp.route('/refresh/<int:source_id>', methods=['POST'])
def force_refresh_source(source_id):
    source = M3uSource.query.get_or_404(source_id)
    if not source.enabled:
        flash('Cannot refresh a disabled source. Please enable it first.', 'warning')
    else:
        scheduler.add_job(
            func=refresh_single_m3u_source,
            args=[source.id, source.url],
            id=f'manual_m3u_refresh_{source.id}',
            name=f'Manual M3U Refresh {source.id}',
            replace_existing=True,
            misfire_grace_time=None,
            trigger='date' # Run immediately
        )
        flash(f'Manual refresh for source {source.id} has been triggered.', 'info')
    return redirect(url_for('sources.manage_sources'))

@sources_bp.route('/update_interval/<int:source_id>', methods=['POST'])
def update_source_interval(source_id):
    source = M3uSource.query.get_or_404(source_id)
    form = UpdateIntervalForm(request.form)
    if form.validate():
        source.refresh_interval_hours = form.interval.data
        db.session.commit()
        if source.enabled:
            schedule_source_refresh_job(source.id, source.url, source.refresh_interval_hours)
            flash(f'Interval updated to {source.refresh_interval_hours}h. Job rescheduled.', 'success')
        else:
            flash(f'Interval updated to {source.refresh_interval_hours}h. Source remains disabled.', 'info')
    else:
        flash('Invalid interval selected.', 'danger')
    return redirect(url_for('sources.manage_sources'))
