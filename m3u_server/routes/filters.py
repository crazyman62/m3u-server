# m3u_server/routes/filters.py
import re
from flask import Blueprint, render_template, request, flash, redirect, url_for
from .. import db, scheduler
from ..models import Filter
from ..forms import FilterForm
from ..scheduler_jobs import disable_channels_without_epg, apply_all_filters_job

filters_bp = Blueprint('filters', __name__)

def trigger_apply_filters_job():
    """Helper function to schedule the apply_all_filters job to run immediately."""
    scheduler.add_job(
        func=apply_all_filters_job,
        id='manual_apply_all_filters_job',
        name='Manual run of Apply All Filters',
        replace_existing=True,
        trigger='date' # The 'date' trigger runs the job immediately
    )
    flash('Task to apply all filters has been triggered. Changes will be reflected shortly.', 'info')

@filters_bp.route('/', methods=['GET', 'POST'])
def manage_filters():
    """Displays, adds, and edits filters."""
    form = FilterForm()
    if form.validate_on_submit():
        try:
            # Validate regex pattern before saving
            re.compile(form.pattern.data, re.IGNORECASE)
            
            new_filter = Filter(
                pattern=form.pattern.data,
                description=form.description.data,
                enabled=form.enabled.data
            )
            db.session.add(new_filter)
            db.session.commit()
            flash('New filter added successfully.', 'success')

            # If the new filter is enabled, trigger the job to apply it immediately
            if new_filter.enabled:
                trigger_apply_filters_job()
            
            return redirect(url_for('filters.manage_filters'))
        except re.error as e:
            flash(f'Invalid Regex Pattern: {e}', 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding filter: {e}', 'danger')

    filters = Filter.query.order_by(Filter.id).all()
    return render_template('manage_filters.html', filters=filters, form=form, title="Manage Disable Filters")

@filters_bp.route('/delete/<int:filter_id>', methods=['POST'])
def delete_filter(filter_id):
    filter_to_delete = Filter.query.get_or_404(filter_id)
    db.session.delete(filter_to_delete)
    db.session.commit()
    # Important: Deleting a filter does not automatically re-enable channels.
    flash('Filter deleted. Note: This does not re-enable channels. Please run a manual M3U source refresh to update channel status.', 'success')
    return redirect(url_for('filters.manage_filters'))

@filters_bp.route('/toggle/<int:filter_id>', methods=['POST'])
def toggle_filter(filter_id):
    filter_to_toggle = Filter.query.get_or_404(filter_id)
    filter_to_toggle.enabled = not filter_to_toggle.enabled
    db.session.commit()
    flash(f'Filter status updated.', 'success')

    # If the filter was just enabled, trigger the job to apply it
    if filter_to_toggle.enabled:
        trigger_apply_filters_job()
    else:
        # If disabled, inform the user about re-enabling channels
        flash('Note: Disabling a filter does not automatically re-enable channels. Please run a manual M3U source refresh.', 'info')

    return redirect(url_for('filters.manage_filters'))

@filters_bp.route('/apply_all', methods=['POST'])
def apply_all_filters():
    """Triggers the job to apply all enabled filters to all channels."""
    trigger_apply_filters_job()
    return redirect(url_for('filters.manage_filters'))

@filters_bp.route('/apply_no_epg_disable', methods=['POST'])
def apply_no_epg_disable():
    """Triggers the job to disable channels without EPG data immediately."""
    scheduler.add_job(
        func=disable_channels_without_epg,
        id='manual_disable_no_epg_job',
        name='Manual run of Disable Channels without EPG',
        replace_existing=True,
        trigger='date'
    )
    flash('Task to disable channels without EPG data has been triggered. The changes will be reflected shortly.', 'info')
    return redirect(url_for('filters.manage_filters'))
