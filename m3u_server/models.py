# m3u_server/models.py
from datetime import datetime
from . import db

class M3uSource(db.Model):
    __tablename__ = 'm3u_sources'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String, nullable=False, unique=True)
    last_checked = db.Column(db.DateTime)
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    refresh_interval_hours = db.Column(db.Integer, nullable=False, default=24)

class Channel(db.Model):
    __tablename__ = 'channels'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, index=True)
    category = db.Column(db.String, index=True)
    tvg_id = db.Column(db.String, index=True) # EPG mapping ID
    tvg_name = db.Column(db.String) # Name for EPG matching
    tvg_logo = db.Column(db.String)
    channel_num = db.Column(db.Integer)
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    urls = db.relationship('Url', backref='channel', lazy=True, cascade="all, delete-orphan")
    epg_data = db.relationship('EpgData', backref='channel', lazy=True, cascade="all, delete-orphan")

class Url(db.Model):
    __tablename__ = 'urls'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String, nullable=False)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False, index=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class EpgSource(db.Model):
    __tablename__ = 'epg_sources'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String, nullable=False, unique=True)
    last_checked = db.Column(db.DateTime)
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    refresh_interval_hours = db.Column(db.Integer, nullable=False, default=12)

class EpgData(db.Model):
    __tablename__ = 'epg_data'
    id = db.Column(db.Integer, primary_key=True)
    channel_tvg_id = db.Column(db.String, db.ForeignKey('channels.tvg_id'), nullable=False, index=True)
    title = db.Column(db.String, nullable=False)
    start_time = db.Column(db.DateTime, nullable=False, index=True)
    end_time = db.Column(db.DateTime, nullable=False)
    description = db.Column(db.Text)

class Filter(db.Model):
    __tablename__ = 'filters'
    id = db.Column(db.Integer, primary_key=True)
    pattern = db.Column(db.String, nullable=False, unique=True)
    description = db.Column(db.String)
    enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
