# create_admin.py
from app import app, db
from models.user import Admin

with app.app_context():
    # Create admins table
    db.create_all()
    
    # Create your first admin
    admin = Admin(username="proteeti_admins")  # Change this username
    admin.set_password("proteeti_shy_tas")  # CHANGE THIS!
    
    db.session.add(admin)
    db.session.commit()
    
    print(f"Admin created: {admin.username}")
    print("  Access at: http://localhost:5000/admin/login")