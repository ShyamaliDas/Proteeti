from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
import bcrypt
import json


db = SQLAlchemy()


def bd_now():
    return (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(50), default=bd_now, index=True)
    profile = db.Column(db.JSON, default=dict)
    notification_prefs = db.Column(db.JSON, default=dict)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    legacy_contacts = db.Column(db.JSON, default=list)  # For non-app trusted contacts from onboarding
    
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
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'verified': self.verified,
            'created_at': self.created_at,
            'profile': self.profile or {},
            'notification_prefs': self.notification_prefs or {}
        }
    @property
    def trusted_contacts(self):
        """Get all accepted connections with user details and connection ID"""
        contacts = []
        
        # Sent & accepted
        sent = NetworkConnection.query.filter_by(requester_id=self.id, status='accepted').all()
        for conn in sent:
            contact = User.query.get(conn.recipient_id)
            if contact:
                contacts.append({
                    'user': contact,
                    'connection_id': conn.id
                })
        
        # Received & accepted
        received = NetworkConnection.query.filter_by(recipient_id=self.id, status='accepted').all()
        for conn in received:
            contact = User.query.get(conn.requester_id)
            if contact:
                contacts.append({
                    'user': contact,
                    'connection_id': conn.id
                })
        
        return contacts

    @property
    def trusted_contact_ids(self):
        return [c['user'].id for c in self.trusted_contacts]

class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey('users.username'), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    timestamp = db.Column(db.String(50), default=bd_now, index=True)
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
    status = db.Column(db.String(20), default='active')  # active, resolved, historical
    is_live = db.Column(db.Boolean, default=True)
    last_updated = db.Column(db.String(50), default=bd_now)
    created_at = db.Column(db.String(50), default=bd_now, index=True)
    resolved_at = db.Column(db.String(50), nullable=True)  # NEW: When it was resolved
    resolved_by = db.Column(db.String(80), nullable=True)  # NEW: Who resolved it
    
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
            'is_live': self.is_live,
            'last_updated': self.last_updated,
            'created_at': self.created_at,
            'resolved_at': self.resolved_at,  # NEW
            'resolved_by': self.resolved_by   # NEW
        }

class Admin(db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.String(50), default=bd_now, index=True)
    
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
    rated_at = db.Column(db.String(50), default=bd_now, index=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'rating': self.rating,
            'rated_at': self.rated_at
        }

class NetworkConnection(db.Model):
    __tablename__ = 'network_connections'
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending, accepted, declined
    created_at = db.Column(db.String(50), default=bd_now, index=True)
    accepted_at = db.Column(db.String(50), nullable=True)   # ← THIS WAS MISSING BEFORE

    requester = db.relationship('User', foreign_keys=[requester_id], backref='sent_requests')
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='received_requests')

    def to_dict(self):
        return {
            'id': self.id,
            'requester_id': self.requester_id,
            'recipient_id': self.recipient_id,
            'status': self.status,
            'created_at': self.created_at,
            'accepted_at': self.accepted_at
        }
    
class Notification(db.Model):
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # 'sos_alert', 'connection_request', etc.
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(500))  # Where to go when clicked
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=bd_now, index=True)
    
    # Extra data (JSON)
    data = db.Column(db.JSON, default=dict)
    
    user = db.relationship('User', backref='notifications')
    
    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'title': self.title,
            'message': self.message,
            'link': self.link,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat(),
            'data': self.data
        }
    
SafetyConnection = NetworkConnection