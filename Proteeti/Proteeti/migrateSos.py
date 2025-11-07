from sqlalchemy import text
from models.user import db, SOSAlert
from app import app

if __name__ == "__main__":
    with app.app_context():
        print("[*] Dropping old sos_alerts table...")
        db.session.execute(text('DROP TABLE IF EXISTS sos_alerts CASCADE;'))
        db.session.commit()
        
        print("[*] Creating new sos_alerts table...")
        db.create_all()
        
        print("✓ sos_alerts table recreated with columns:")
        print("  - id (Primary Key)")
        print("  - user_id (Foreign Key)")
        print("  - username (String)")
        print("  - lat (Float)")
        print("  - lng (Float)")
        print("  - accuracy (Float)")
        print("  - status (String)")
        print("  - created_at (DateTime)")
