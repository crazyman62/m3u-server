# m3u_server/routes/epg.py
from flask import Blueprint, render_template, request, flash, redirect, url_for
from .. import db, scheduler
from ..models import EpgSource
from ..forms import EpgSourceForm, UpdateIntervalForm
from ..scheduler_jobs import schedule_epg_refresh_job, refresh_single_epg_source

epg_bp = Blueprint('epg', __name__)

@epg_bp.route('/')
def manage_epg_sources():
    """Displays EPG source URLs and allows management."""
    sources = EpgSource.query.order_by(EpgSource.id).all()
    interval_form = UpdateIntervalForm()
    return render_template('manage_epg_sources.html', sources=sources, interval_form=interval_form, title="Manage EPG Sources")

@epg_bp.route('/add', methods=['GET', 'POST'])
def add_epg_source():
    form = EpgSourceForm()
    if form.validate_on_submit():
        url = form.epg_url.data
        if EpgSource.query.filter_by(url=url).first():
            flash('EPG Source URL already exists.', 'warning')
        else:
            new_source = EpgSource(url=url)
            db.session.add(new_source)
            db.session.commit()
            schedule_epg_refresh_job(new_source.id, new_source.url, new_source.refresh_interval_hours)
            flash(f'EPG Source added and scheduled for refresh.', 'success')
        return redirect(url_for('epg.manage_epg_sources'))
    return render_template('source_form.html', form=form, title="Add EPG Source")

@epg_bp.route('/delete/<int:source_id>', methods=['POST'])
def delete_epg_source(source_id):
    source = EpgSource.query.get_or_404(source_id)
    job_id = f'epg_refresh_{source_id}'
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    db.session.delete(source)
    db.session.commit()
    flash('EPG Source deleted and job unscheduled.', 'success')
    return redirect(url_for('epg.manage_epg_sources'))

@epg_bp.route('/toggle/<int:source_id>', methods=['POST'])
def toggle_epg_source(source_id):
    source = EpgSource.query.get_or_404(source_id)
    source.enabled = not source.enabled
    db.session.commit()
    job_id = f'epg_refresh_{source_id}'
    if source.enabled:
        schedule_epg_refresh_job(source.id, source.url, source.refresh_interval_hours)
        flash('EPG Source enabled and job scheduled.', 'success')
    else:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        flash('EPG Source disabled and job unscheduled.', 'warning')
    return redirect(url_for('epg.manage_epg_sources'))

@epg_bp.route('/refresh/<int:source_id>', methods=['POST'])
def force_refresh_epg_source(source_id):
    source = EpgSource.query.get_or_404(source_id)
    if not source.enabled:
        flash('Cannot refresh a disabled EPG source.', 'warning')
    else:
        scheduler.add_job(
            func=refresh_single_epg_source,
            args=[source.id, source.url],
            id=f'manual_epg_refresh_{source.id}',
            name=f'Manual EPG Refresh {source.id}',
            replace_existing=True,
            trigger='date'
        )
        flash(f'Manual refresh for EPG source {source.id} has been triggered.', 'info')
    return redirect(url_for('epg.manage_epg_sources'))

# --- NEW/FIXED ROUTE ---
@epg_bp.route('/update_interval/<int:source_id>', methods=['POST'])
def update_epg_interval(source_id):
    """Updates refresh interval and reschedules job for an EPG source."""
    source = EpgSource.query.get_or_404(source_id)
    form = UpdateIntervalForm(request.form)
    if form.validate():
        source.refresh_interval_hours = form.interval.data
        db.session.commit()
        if source.enabled:
            schedule_epg_refresh_job(source.id, source.url, source.refresh_interval_hours)
            flash(f'Interval updated to {source.refresh_interval_hours}h. Job rescheduled.', 'success')
        else:
            flash(f'Interval updated to {source.refresh_interval_hours}h. Source remains disabled.', 'info')
    else:
        flash('Invalid interval selected.', 'danger')
    return redirect(url_for('epg.manage_epg_sources'))
