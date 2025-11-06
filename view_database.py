from config.database import Config
from models.user import db, User, Report
from flask import Flask

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    print("=" * 60)
    print("USERS IN DATABASE")
    print("=" * 60)
    
    users = User.query.all()
    for user in users:
        print(f"\nUsername: {user.username}")
        print(f"Email: {user.email}")
        print(f"Verified: {user.verified}")
        print(f"Created: {user.created_at}")
        print(f"Trusted Contacts: {user.trusted_contacts}")
        print("-" * 60)
    
    print(f"\n✅ Total Users: {len(users)}\n")
    
    print("=" * 60)
    print("REPORTS IN DATABASE")
    print("=" * 60)
    
    reports = Report.query.all()
    for report in reports:
        print(f"\nReport ID: {report.id}")
        print(f"Username: {report.username}")
        print(f"Location: ({report.lat}, {report.lng})")
        print(f"Category: {report.category}")
        print(f"Description: {report.description}")
        print(f"Timestamp: {report.timestamp}")
        print("-" * 60)
    
    print(f"\n✅ Total Reports: {len(reports)}\n")
