# m3u_server/forms.py
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SubmitField, URLField, IntegerField, SelectField, 
    TextAreaField, BooleanField, HiddenField
)
from wtforms.validators import DataRequired, Optional, URL, Length

class SourceM3uForm(FlaskForm):
    m3u_url = URLField('M3U Source URL', validators=[DataRequired(), URL()])
    submit = SubmitField('Add M3U Source')

class EpgSourceForm(FlaskForm):
    epg_url = URLField('EPG XMLTV URL', validators=[DataRequired(), URL()])
    submit = SubmitField('Add EPG Source')

class FilterForm(FlaskForm):
    pattern = StringField('RegEx Pattern', validators=[DataRequired(), Length(min=1, max=255)])
    description = StringField('Description', validators=[Optional(), Length(max=255)])
    enabled = BooleanField('Enabled', default=True)
    submit = SubmitField('Save Filter')

class UpdateIntervalForm(FlaskForm):
    interval_choices = [
        (1, '1h'), (3, '3h'), (6, '6h'), (12, '12h'),
        (24, '24h (Daily)'), (48, '48h'), (168, '168h (Weekly)')
    ]
    interval = SelectField('Refresh Interval', choices=interval_choices, coerce=int, validators=[DataRequired()])
    submit = SubmitField('Set')

class EditChannelForm(FlaskForm):
    """Form used in the channel edit modal."""
    channel_id = HiddenField()
    name = StringField('Channel Name', validators=[DataRequired()])
    category = StringField('Category', validators=[Optional()])
    tvg_id = StringField('EPG ID (tvg-id)', validators=[Optional()])
    tvg_logo = URLField('Logo URL', validators=[Optional(), URL()])
    enabled = BooleanField('Enabled', default=True)
    submit = SubmitField('Save Changes')

class AddChannelForm(FlaskForm):
    """Form for manually adding a new channel."""
    name = StringField('Channel Name', validators=[DataRequired()])
    category = StringField('Category', validators=[Optional()])
    tvg_id = StringField('EPG ID (tvg-id)', validators=[Optional()])
    tvg_logo = URLField('Logo URL', validators=[Optional(), URL()])
    channel_num = IntegerField('Channel Number', validators=[Optional()])
    url = URLField('Stream URL', validators=[DataRequired(), URL(message="A valid stream URL is required.")])
    submit = SubmitField('Add Channel')
