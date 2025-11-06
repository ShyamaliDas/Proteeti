"""One-time script to migrate users.json and reports.json to PostgreSQL"""
import json
from app import app, db
from models.user import User, Report

def migrate_users():
    """Migrate users from JSON to database"""
    print("Migrating users...")
    with open('data/users.json', 'r') as f:
        users_data = json.load(f)
    
    for username, data in users_data.items():
        existing = User.query.filter_by(username=username).first()
        if existing:
            print(f"Skipping {username} (already exists)")
            continue
        
        user = User(
            username=username,
            email=data['email'],
            verified=data.get('verified', True),
            created_at=data.get('created_at'),
            profile=data.get('profile', {}),
            trusted_contacts=data.get('trusted_contacts', []),
            notification_prefs=data.get('notification_prefs', {})
        )
        
        # Hash the plaintext password from JSON
        if data.get('password'):
            user.set_password(data['password'])
        
        db.session.add(user)
        print(f"Migrated user: {username}")
    
    db.session.commit()
    print("Users migrated successfully!")

def migrate_reports():
    """Migrate reports from JSON to database"""
    print("Migrating reports...")
    with open('data/reports.json', 'r') as f:
        reports_data = json.load(f)
    
    for report_data in reports_data:
        report = Report(
            username=report_data['username'],
            lat=report_data['lat'],
            lng=report_data['lng'],
            category=report_data['category'],
            description=report_data.get('description', ''),
            timestamp=report_data.get('timestamp')
        )
        db.session.add(report)
    
    db.session.commit()
    print(f"Migrated {len(reports_data)} reports successfully!")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("Database tables created!")
        
        # Migrate data
        migrate_users()
        migrate_reports()
        
        print("\n✅ Migration complete!")
