from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
import bcrypt
import json

db = SQLAlchemy()

# Helper function for Bangladesh time (UTC+6)
def bd_now():
    return (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(16), default=bd_now, index=True)
    profile = db.Column(db.JSON, default=dict)
    trusted_contacts = db.Column(db.JSON, default=list)
    notification_prefs = db.Column(db.JSON, default=dict)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    def set_password(self, password):
        if password:
            self.password_hash = bcrypt.hashpw(
                password.encode('utf-8'),
                bcrypt.gensalt()
            ).decode('utf-8')
    def check_password(self, password):
        if not self.password_hash:
            return False
        return bcrypt.checkpw(
            password.encode('utf-8'),
            self.password_hash.encode('utf-8')
        )
    def to_dict(self):
        return {
            'username': self.username,
            'email': self.email,
            'verified': self.verified,
            'created_at': self.created_at,
            'profile': self.profile or {},
            'trusted_contacts': self.trusted_contacts or [],
            'notification_prefs': self.notification_prefs or {}
        }

class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey('users.username'), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    timestamp = db.Column(db.String(16), default=bd_now, index=True)
    user = db.relationship('User', backref='reports')
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'lat': self.lat,
            'lng': self.lng,
            'category': self.category,
            'description': self.description,
            'timestamp': self.timestamp
        }

class SOSAlert(db.Model):
    __tablename__ = 'sos_alerts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    username = db.Column(db.String(80), nullable=False, index=True)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.String(16), default=bd_now, index=True)
    user = db.relationship('User', backref='sos_alerts')
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'lat': self.lat,
            'lng': self.lng,
            'accuracy': self.accuracy,
            'status': self.status,
            'created_at': self.created_at
        }

class Admin(db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.String(16), default=bd_now, index=True)
    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

class StarRating(db.Model):
    __tablename__ = "star_ratings"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey('users.username'), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    rated_at = db.Column(db.String(16), default=bd_now, index=True)
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'rating': self.rating,
            'rated_at': self.rated_at
        }
