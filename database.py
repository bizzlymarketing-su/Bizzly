from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import os
from datetime import datetime

db = SQLAlchemy()

class Entrepreneur(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    business_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(500), nullable=False)
    accent_color = db.Column(db.String(7), default='#7fff00')

    businesses = db.relationship('Business', backref='owner', lazy=True)

    def __repr__(self):
        return f"<Entrepreneur {self.business_name}>"


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entrepreneur_id = db.Column(db.Integer, db.ForeignKey('entrepreneur.id'), nullable=False)

    title = db.Column(db.String(100), nullable=False)  # bijv. "Nieuwe review"
    message = db.Column(db.Text, nullable=False)  # de tekst
    type = db.Column(db.String(50))  # "review", "wishlist", "view"
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Notification {self.title}>"

class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    used_by = db.Column(db.Integer, db.ForeignKey('entrepreneur.id'), nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entrepreneur_id = db.Column(db.Integer, db.ForeignKey('entrepreneur.id'), unique=True, nullable=False)
    activated_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)   # activated_at + 30 dagen
    status = db.Column(db.String(20), default='active')
    # mogelijke statussen: 'active', 'expiring_soon', 'expired', 'deactivated'


