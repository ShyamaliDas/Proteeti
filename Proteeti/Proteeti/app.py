from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_dance.contrib.google import make_google_blueprint, google
from flask_migrate import Migrate
from flask_login import login_required, current_user, LoginManager
from flask import current_app
from flask_sqlalchemy import SQLAlchemy
import json, os, re, random, requests, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from config.database import Config
from models.user import db, User, Report, SOSAlert, Admin, StarRating
import base64
import hashlib
import hmac

load_dotenv()

DEV_MODE = os.getenv("DEV_MODE", "False").lower() in ("1", "true", "yes")
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__)
app.config.from_object(Config)



# Initialize database
db.init_app(app)

# Create tables on first run
with app.app_context():
    db.create_all()

migrate = Migrate(app,db)

# ======= Google OAuth =======
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    google_bp = make_google_blueprint(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scope=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
        redirect_to="index"
    )
    app.register_blueprint(google_bp, url_prefix="/login")
else:
    print("WARNING: Google OAuth credentials not found.")

user_subscriptions = {}

# ======= Email validation =======
def is_valid_email(email):
    return re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email) is not None

MAILBOXLAYER_KEY = os.getenv("MAILBOXLAYER_KEY")

def mailboxlayer_check(email):
    if DEV_MODE or not MAILBOXLAYER_KEY:
        return True, "Email validation skipped in dev mode"
    url = f"http://apilayer.net/api/check?access_key={MAILBOXLAYER_KEY}&email={email}&smtp=1&format=1"
    try:
        data = requests.get(url, timeout=8).json()  
        if not data.get("success", True) and "error" in data:
            return True, "Email validation service unavailable, skipping"
        if not data.get("format_valid", False): return False, "Email format is invalid."
        if not data.get("mx_found", False): return False, "Email domain has no MX records."
        if not data.get("smtp_check", False): return False, "Email address does not exist or cannot receive mail."
        if data.get("disposable", False): return False, "Disposable/temporary email addresses are not allowed."
        return True, "Email is valid."
    except Exception:
        return True, "Email validation skipped"


# ======= 6-digit code =======
def generate_verification_code():
    return str(random.randint(100000, 999999))



GMAIL_SENDER = os.getenv("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

def send_verification_code(email, code):

    if DEV_MODE:
        return True
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        return True
    try:
        msg = MIMEMultipart()
        msg['From'] = f"Proteeti <{GMAIL_SENDER}>"
        msg['To'] = email
        msg['Subject'] = "Proteeti Verification Code"
        msg.attach(MIMEText(f"Your verification code is: {code}\n\nThis code will expire in 10 minutes.\n", 'plain'))

        # hard timeout prevents long stall
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=8)
        server.starttls()
        server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        try:
            app.logger.warning(f"Email send failed: {e}")
        except Exception:
            pass
        return False



def send_sos_email_with_location(user, latitude, longitude):
    """Send immediate SOS email with live location (no attachment)"""
    if not user.trusted_contacts:
        print(f"[DEBUG] No trusted contacts found for {user.username}")
        return False
    
    GMAIL_SENDER = os.getenv("GMAIL_SENDER")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
    
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("[DEBUG] Gmail credentials not configured!")
        return False
    
    maps_link = f"https://www.google.com/maps?q={latitude},{longitude}"
    
    for contact in user.trusted_contacts:
        try:
            recipient_email = contact.get('email')
            print(f"[DEBUG] Sending location SOS to {recipient_email}")
            
            msg = MIMEMultipart()
            msg['From'] = f"Proteeti <{GMAIL_SENDER}>"
            msg['To'] = recipient_email
            msg['Subject'] = "🚨 EMERGENCY SOS ALERT - LOCATION"
            
            body = f"""EMERGENCY SOS ALERT

{user.username} needs immediate help!

📍 LIVE LOCATION: {maps_link}
Coordinates: {latitude}, {longitude}

IMMEDIATE ACTION REQUIRED!
This is an automated SOS alert from Proteeti.
An audio recording will follow shortly."""
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.send_message(msg)
            server.quit()
            
            print(f"[DEBUG] Location SOS sent to {recipient_email}")
        except Exception as e:
            print(f"[DEBUG] Failed to send location SOS: {e}")
            continue
    
    return True


def send_sos_email_with_audio(user, audio_blob):
    """Send SOS email with 2-minute audio recording"""
    if not user.trusted_contacts:
        print(f"[DEBUG] No trusted contacts found for {user.username}")
        return False
    
    GMAIL_SENDER = os.getenv("GMAIL_SENDER")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
    
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("[DEBUG] Gmail credentials not configured!")
        return False
    
    audio_size_mb = len(audio_blob) / 1024 / 1024
    print(f"[DEBUG] Audio file size: {audio_size_mb:.2f} MB")
    
    for contact in user.trusted_contacts:
        try:
            recipient_email = contact.get('email')
            print(f"[DEBUG] Sending audio SOS to {recipient_email}")
            
            msg = MIMEMultipart()
            msg['From'] = f"Proteeti <{GMAIL_SENDER}>"
            msg['To'] = recipient_email
            msg['Subject'] = "🚨 EMERGENCY SOS ALERT - AUDIO RECORDING"
            
            body = f"""EMERGENCY SOS ALERT - AUDIO EVIDENCE

{user.username} emergency audio recording (2 minutes) is attached.

Please review and take immediate action if needed.

This is an automated SOS alert from Proteeti."""
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Attach audio
            from email.mime.application import MIMEApplication
            part = MIMEApplication(audio_blob)
            part.add_header('Content-Disposition', 'attachment', filename='emergency_audio.webm')
            msg.attach(part)
            
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.send_message(msg)
            server.quit()
            
            print(f"[DEBUG] Audio SOS sent successfully to {recipient_email}")
        except Exception as e:
            print(f"[DEBUG] Failed to send audio SOS: {e}")
            continue
    
    return True



# ======= Routes =======
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/")
def home():
    return render_template('base.html', config=current_app.config)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username_or_email = request.form["username_or_email"].strip()
        password = request.form["password"]
        
        if "@" in username_or_email and not is_valid_email(username_or_email):
            error = "Please enter a valid email address."
            return render_template("login.html", error=error)
        
        # Query database instead of JSON
        user = User.query.filter(
            (User.username == username_or_email) | (User.email == username_or_email)
        ).first()
        
        if user and user.check_password(password):
            session.clear()
            session["loggedin"] = True
            session["username"] = user.username
            session.modified = True
            print(f"Login successful for {user.username}")
            return redirect(url_for("index"))
        else:
            error = "Invalid username/email or password."
    
    return render_template("login.html", error=error)

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        confirmpassword = request.form.get("confirmpassword", "")
        
        if not username or not email or not password:
            error = "Username, email and password are required."
            return render_template("register.html", error=error)
        
        if not is_valid_email(email):
            error = "Please enter a valid email address."
            return render_template("register.html", error=error)
        
        if password != confirmpassword:
            error = "Passwords do not match."
            return render_template("register.html", error=error)
        
        if User.query.filter_by(username=username).first():
            error = "Username already exists."
            return render_template("register.html", error=error)
        
        if User.query.filter_by(email=email).first():
            error = "Email already in use."
            return render_template("register.html", error=error)
        
        code = generate_verification_code()
        send_verification_code(email, code)
        
        session["pending_user"] = {
            "flow": "manual",
            "username": username,
            "email": email,
            "password": password,
            "code": code,
            "created_at": datetime.now().isoformat(),
        }
        
        return redirect(url_for("verify_email"))
    
    return render_template("register.html", error=error)

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    error = None
    pending = session.get("pending_user")
    
    if not pending:
        return redirect(url_for("register"))
    
    show_code = DEV_MODE
    
    if request.method == "POST":
        entered_code = request.form["code"].strip()
        
        if entered_code == pending["code"]:
            # Create user in database
            user = User(
                username=pending["username"],
                email=pending["email"],
                verified=True
            )
            user.set_password(pending["password"])
            
            db.session.add(user)
            db.session.commit()
            
            session.clear()
            session["loggedin"] = True
            session["username"] = user.username
            session.modified = True
            
            return redirect(url_for("onboarding"))
        else:
            error = "Verification code incorrect."
    
    return render_template("verify_email.html", error=error, show_code=show_code, code=(pending["code"] if show_code else None))

@app.route("/account")
def account():
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    if not user:
        return redirect(url_for("logout"))
    
    # Convert to dict format like JSON
    info = user.to_dict()
    info.setdefault("notification_prefs", {
        "channels": {"email": True, "sms": True, "push": False},
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "hazard_categories": []
    })
    
    return render_template("account.html", user=info, username=username)

@app.route("/update_account", methods=["POST"])
def update_account():
    if not session.get("loggedin"):
        return jsonify({"error": "Please login first"}), 401
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    try:
        data = request.get_json(force=True, silent=True) or {}
        
        # Update profile
        if not user.profile:
            user.profile = {}
        
        core = data.get("core") or {}
        for k in ("full_name", "phone", "country", "city", "language", "timezone"):
            if k in core:
                user.profile[k] = core[k]
        
        # Update consents
        cons = data.get("consents")
        if cons is not None:
            user.profile["consents"] = cons
        
        # Update notification preferences
        notif = data.get("notification_prefs")
        if notif is not None:
            user.notification_prefs = notif
        
        # Update optional fields
        optional = data.get("optional") or {}
        for k, v in optional.items():
            user.profile[k] = v
        
        db.session.commit()
        return jsonify({"message": "Account updated"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Unable to update account"}), 400

@app.route("/add_trusted_contact", methods=["POST"])
def add_trusted_contact():
    if not session.get("loggedin"):
        return jsonify({"error": "Please login first"}), 401
    
    try:
        data = request.get_json()
        username = session.get("username")
        user = User.query.filter_by(username=username).first()
        
        if not user.trusted_contacts:
            user.trusted_contacts = []
        
        new_contact = {
            "id": len(user.trusted_contacts) + 1,
            "name": data.get("name"),
            "email": data.get("email"),
            "phone": data.get("phone")
        }
        
        user.trusted_contacts.append(new_contact)
        db.session.commit()
        
        return jsonify({"message": "Trusted contact added successfully", "contact": new_contact}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    

@app.route("/remove_trusted_contact", methods=["POST"])
def remove_trusted_contact():
    if not session.get("loggedin"):
        return jsonify({"error": "Please login first"}), 401
    
    try:
        data = request.get_json()
        contact_id = data.get("contact_id")
        
        if contact_id is None:
            return jsonify({"error": "Contact ID required"}), 400
        
        contact_id = int(contact_id)
        username = session.get("username")
        user = User.query.filter_by(username=username).first()
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Remove contact from database
        if user.trusted_contacts:
            user.trusted_contacts = [
                c for c in user.trusted_contacts
                if c.get("id") != contact_id
            ]
        
        db.session.commit()
        
        return jsonify({
            "message": "Trusted contact removed successfully",
            "status": "ok"
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/profile', methods=['GET', 'POST'])  
def profile():
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    if request.method == "POST":
        core = request.form or request.get_json(silent=True) or {}
        for k in ("full_name", "phone", "country", "city", "language", "timezone"):
            if k in core:
                if not user.profile:
                    user.profile = {}
                user.profile[k] = core[k]
        db.session.commit()
        return jsonify({"message": "Profile updated"}), 200
    
    return render_template("profile.html", user=user.to_dict(), username=username)


    
@app.route('/onboarding', methods=['GET', 'POST'])
def onboarding():
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    if not user:
        return redirect(url_for("logout"))

    if request.method == "POST":
        data = request.form
        
        # Create profile dict if needed
        if not user.profile:
            user.profile = {}
            
        # Handle all form fields including dropdowns
        profile_fields = ["full_name", "phone", "city", "language", "timezone"]
        for field in profile_fields:
            value = data.get(field)
            if value:
                user.profile[field] = value
        
            # Store both country code and country name
            country_code = data.get("country")  # This is the code (BD, US, IN)
            country_name = data.get("country_name")  # This comes from the hidden field

            if country_code:
                user.profile["country"] = country_code
            if country_name:
                user.profile["country_name"] = country_name
        
            trusted_contact = {
                "id": len(user.trusted_contacts) + 1 if user.trusted_contacts else 1,
                "name": data.get("tc_name"),
                "relation": data.get("tc_relation"), 
                "email": data.get("tc_email"),
                "phone": data.get("tc_phone"),
                "channel": data.get("tc_channel")
            }
                    
        if not user.trusted_contacts:
            user.trusted_contacts = []
        user.trusted_contacts.append(trusted_contact)
        
        db.session.commit()  
        return redirect(url_for("index"))  
        
    return render_template("onboarding.html", username=username)


@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    if not user:
        return redirect(url_for("logout"))

    profile_fields = [
        "full_name", "phone", "country", "city", "language", "timezone",
        "location_permission", "comms_consent", "secondary_phone",
        "home_area", "medical_notes", "emergency_instructions",
        "avatar", "gender", "dob", "push_token"
    ]

    if request.method == "POST":
        form = request.form
        profile = dict(user.profile) if user.profile else {}
        for key in profile_fields:
            if key == "location_permission":
                # Checkbox: only present if checked
                profile[key] = form.get(key) == "on"
            else:
                value = form.get(key)
                if value is not None:
                    profile[key] = value
        user.profile = profile
        db.session.commit()
        return redirect(url_for("account"))
    return render_template("edit_profile.html", user=user)



@app.route("/send_sos", methods=["POST"])
def send_sos():
    """Send immediate location-based SOS alert"""
    
    try:
        if not session.get("loggedin"):
            return jsonify({"error": "Please login first"}), 401

        data = request.get_json()
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy = data.get("accuracy")

        # Validate received coordinates
        if latitude is None or longitude is None:
            return jsonify({"error": "Location required"}), 400
        try:
            lat_f = float(latitude)
            lng_f = float(longitude)
        except Exception:
            return jsonify({"error": "Invalid coordinates"}), 400
        if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
            return jsonify({"error": "Invalid location coordinates"}), 400
        

        username = session.get("username")
        user = User.query.filter_by(username=username).first()

        if not user:
            return jsonify({"error": "User not found"}), 404

        sos_alert = SOSAlert(
            user_id=user.id,
            username=username,
            lat=lat_f,
            lng=lng_f,
            accuracy=accuracy or 0,
            status='active'
        )
        db.session.add(sos_alert)
        db.session.commit()

        send_sos_email_with_location(user, lat_f, lng_f)

        return jsonify({
            "message": "Location SOS sent. Recording audio...",
            "status": "success",
            "alert_id": sos_alert.id
        }), 200

    except Exception as e:
        print(f"[DEBUG] SOS error: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/send_sos_audio", methods=["POST"])
def send_sos_audio():
    """Send 2-minute audio recording after SOS location"""
    try:
        if not session.get("loggedin"):
            return jsonify({"error": "Please login first"}), 401

        username = session.get("username")
        user = User.query.filter_by(username=username).first()

        if not user:
            return jsonify({"error": "User not found"}), 404

        audio_file = request.files.get('audio')

        if not audio_file:
            return jsonify({"error": "Audio file required"}), 400

        audio_blob = audio_file.read()

        if not audio_blob or len(audio_blob) < 100:
            return jsonify({"error": "Invalid audio data"}), 400

        print(f"[DEBUG] SOS audio from {username}")
        print(f"[DEBUG] Audio size: {len(audio_blob) / 1024 / 1024:.2f} MB")

        send_sos_email_with_audio(user, audio_blob)

        return jsonify({
            "message": "Audio SOS sent",
            "status": "success"
        }), 200

    except Exception as e:
        print(f"[DEBUG] Audio SOS error: {e}")
        return jsonify({"error": str(e)}), 500



@app.route('/sos')
def sos():
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    return render_template("sos.html")


@app.route("/api/sos-alerts")
def get_sos_alerts():
    sos_alerts = SOSAlert.query.all()
    return jsonify([alert.to_dict() for alert in sos_alerts])





@app.route("/map")
def show_map():
    # Default to Dhaka if not logged-in or no city
    center_lat = 23.8103
    center_lng = 90.4125

    if session.get('loggedin'):
        user = User.query.filter_by(username=session.get('username')).first()
        profile = user.profile if user and user.profile else {}
        city = profile.get('city')

        CITY_COORDS = {
            "Dhaka North": (23.8341, 90.3841), 
            "Dhaka South": (23.7104, 90.4074), 
            "Chattogram": (22.3569, 91.7832),
            "Khulna": (22.8200, 89.5500),
            "Rajshahi": (24.3745, 88.6042),
            "Sylhet": (24.8949, 91.8687),
            "Barisal": (22.7010, 90.3535),
            "Rangpur": (25.7558, 89.2440),
            "Comilla": (23.4607, 91.1800),
            "Narayanganj": (23.6200, 90.5000),
            "Gazipur": (23.9999, 90.4203),
            "Mymensingh": (24.7539, 90.4031),
        }

        if city and city in CITY_COORDS:
            center_lat, center_lng = CITY_COORDS[city]
        elif profile.get('center_lat') and profile.get('center_lng'):
            center_lat = profile['center_lat']
            center_lng = profile['center_lng']

    reports = Report.query.all() 
    return render_template("map.html", reports=reports, center_lat=center_lat, center_lng=center_lng)


@app.route("/resources")
def resources():
    # Default to Dhaka center
    center_lat = 23.8103
    center_lng = 90.4125

    if session.get("loggedin"):
        user = User.query.filter_by(username=session.get("username")).first()
        profile = user.profile if user and user.profile else {}
        city = profile.get('city')

        CITY_COORDS = {
            "Dhaka North": (23.8341, 90.3841),
            "Dhaka South": (23.7104, 90.4074),
            "Chattogram": (22.3569, 91.7832),
            "Khulna": (22.8200, 89.5500),
            "Rajshahi": (24.3745, 88.6042),
            "Sylhet": (24.8949, 91.8687),
            "Barisal": (22.7010, 90.3535),
            "Rangpur": (25.7558, 89.2440),
            "Comilla": (23.4607, 91.1800),
            "Narayanganj": (23.6200, 90.5000),
            "Gazipur": (23.9999, 90.4203),
            "Mymensingh": (24.7539, 90.4031),
        }
        if city and city in CITY_COORDS:
            center_lat, center_lng = CITY_COORDS[city]

    return render_template("resources.html", center_lat=center_lat, center_lng=center_lng)




@app.route("/submit_report", methods=["POST"])
def submit_report():
    if not session.get("loggedin"):
        return jsonify({"error": "Please login first"}), 401
    
    try:
        report_data = request.get_json()
        
        report = Report(
            username=session.get("username"),
            lat=report_data.get("lat"),
            lng=report_data.get("lng"),
            category=report_data.get("category"),
            description=report_data.get("description", "")
        )
        
        db.session.add(report)
        db.session.commit()
        
        return jsonify({"message": "Report submitted successfully", "status": "ok", "report_id": report.id}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/reports")
def get_reports_api():
    reports = Report.query.all()
    return jsonify([r.to_dict() for r in reports])

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin/setup", methods=["GET", "POST"])
def admin_setup():
    """One-time admin setup page"""
    from models.user import Admin
    
    # Check if any admin already exists
    if Admin.query.first():
        return "Admin already exists! Go to <a href='/admin/login'>/admin/login</a>"
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        
        if not username or not password:
            return render_template("admin_setup.html", error="All fields required")
        
        if password != confirm:
            return render_template("admin_setup.html", error="Passwords don't match")
        
        if len(password) < 8:
            return render_template("admin_setup.html", error="Password must be at least 8 characters")
        
        # Create admin
        admin = Admin(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        
        return redirect(url_for("admin_login"))
    
    return render_template("admin_setup.html")

# Admin login page
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        admin_username = request.form.get("admin_username", "").strip()
        admin_password = request.form.get("admin_password", "")
        
        from models.user import Admin
        admin = Admin.query.filter_by(username=admin_username).first()
        
        if admin and admin.check_password(admin_password):
            session.clear()
            session["admin_loggedin"] = True
            session["admin_username"] = admin.username
            session.modified = True
            print(f"[ADMIN] Login successful: {admin.username}")
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Invalid admin credentials"
            print(f"[ADMIN] Failed login attempt: {admin_username}")
    
    return render_template("admin_login.html", error=error)

# Admin dashboard (protected)
@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_loggedin"):
        return redirect(url_for("admin_login"))
    
    return render_template("admin.html", admin_username=session.get("admin_username"))

# Admin logout
@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_loggedin", None)
    session.pop("admin_username", None)
    return redirect(url_for("admin_login"))

# Protect all admin API routes
def require_admin_api():
    if not session.get("admin_loggedin"):
        return jsonify({"error": "Unauthorized - Admin access required"}), 401
    return None

@app.route("/api/admin/sos-alerts")
def get_admin_sos_alerts():
    check = require_admin_api()
    if check: return check
    
    alerts = SOSAlert.query.order_by(SOSAlert.created_at.desc()).all()
    return jsonify([alert.to_dict() for alert in alerts])

@app.route("/api/admin/sos-alerts/<int:alert_id>/resolve", methods=["POST"])
def resolve_sos_alert(alert_id):
    check = require_admin_api()
    if check: return check
    
    alert = SOSAlert.query.get(alert_id)
    if alert:
        alert.status = 'resolved'
        db.session.commit()
        return jsonify({"message": "Alert resolved"}), 200
    return jsonify({"error": "Alert not found"}), 404

@app.route("/api/admin/users")
def get_admin_users():
    check = require_admin_api()
    if check: return check
    
    users = User.query.all()
    return jsonify([{
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "verified": u.verified,
        "trusted_contacts_count": len(u.trusted_contacts) if u.trusted_contacts else 0,
        "created_at": u.created_at.isoformat() if hasattr(u, 'created_at') else None
    } for u in users])

@app.route("/api/admin/reports/<int:report_id>", methods=["DELETE"])
def delete_admin_report(report_id):
    check = require_admin_api()
    if check: return check
    
    report = Report.query.get(report_id)
    if report:
        db.session.delete(report)
        db.session.commit()
        return jsonify({"message": "Report deleted"}), 200
    return jsonify({"error": "Report not found"}), 404

# Add these routes to your app.py file

@app.route("/admin/settings")
def admin_settings():
    """Admin settings page"""
    if not session.get("admin_loggedin"):
        return redirect(url_for("admin_login"))
    return render_template("admin_management.html", admin_username=session.get("admin_username"))

@app.route("/api/admin/change-password", methods=["POST"])
def admin_change_password():
    """Change admin password"""
    if not session.get("admin_loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        
        if not current_password or not new_password:
            return jsonify({"error": "All fields required"}), 400
        
        if len(new_password) < 8:
            return jsonify({"error": "Password must be at least 8 characters"}), 400
        
        from models.user import Admin
        admin_username = session.get("admin_username")
        admin = Admin.query.filter_by(username=admin_username).first()
        
        if not admin or not admin.check_password(current_password):
            return jsonify({"error": "Current password is incorrect"}), 400
        
        admin.set_password(new_password)
        db.session.commit()
        
        return jsonify({"message": "Password updated successfully"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/list-admins")
def list_admins():
    """List all admin accounts"""
    if not session.get("admin_loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        from models.user import Admin
        admins = Admin.query.all()
        current_admin = session.get("admin_username")
        
        admin_list = [{
            "username": admin.username,
            "created_at": admin.created_at.isoformat() if hasattr(admin, 'created_at') else None,
            "is_current": admin.username == current_admin
        } for admin in admins]
        
        return jsonify({"admins": admin_list}), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/add-admin", methods=["POST"])
def add_admin():
    """Add new admin account"""
    if not session.get("admin_loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        username = data.get("username", "").strip()
        password = data.get("password", "")
        
        if not username or not password:
            return jsonify({"error": "Username and password required"}), 400
        
        if len(password) < 8:
            return jsonify({"error": "Password must be at least 8 characters"}), 400
        
        from models.user import Admin
        
        # Check if admin already exists
        if Admin.query.filter_by(username=username).first():
            return jsonify({"error": "Admin username already exists"}), 400
        
        # Create new admin
        new_admin = Admin(username=username)
        new_admin.set_password(password)
        db.session.add(new_admin)
        db.session.commit()
        
        return jsonify({"message": f"Admin '{username}' created successfully"}), 201
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/delete-admin", methods=["POST"])
def delete_admin():
    """Delete another admin account (cannot delete yourself)"""
    if not session.get("admin_loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        username = data.get("username", "").strip()
        
        if not username:
            return jsonify({"error": "Username required"}), 400
        
        current_admin = session.get("admin_username")
        
        # Cannot delete yourself
        if username == current_admin:
            return jsonify({"error": "Cannot delete your own account"}), 400
        
        from models.user import Admin
        admin = Admin.query.filter_by(username=username).first()
        
        if not admin:
            return jsonify({"error": "Admin not found"}), 404
        
        # Check if this is the last admin
        total_admins = Admin.query.count()
        if total_admins <= 1:
            return jsonify({"error": "Cannot delete the last admin"}), 400
        
        db.session.delete(admin)
        db.session.commit()
        
        return jsonify({"message": f"Admin '{username}' deleted successfully"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/delete-own-account", methods=["POST"])
def delete_own_admin_account():
    """Delete your own admin account"""
    if not session.get("admin_loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        from models.user import Admin
        
        # Check if this is the last admin
        total_admins = Admin.query.count()
        if total_admins <= 1:
            return jsonify({"error": "Cannot delete the last admin account"}), 400
        
        current_admin = session.get("admin_username")
        admin = Admin.query.filter_by(username=current_admin).first()
        
        if not admin:
            return jsonify({"error": "Admin not found"}), 404
        
        db.session.delete(admin)
        db.session.commit()
        
        # Clear session
        session.clear()
        
        return jsonify({"message": "Account deleted successfully"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/admin/analytics/overview")
def get_analytics_overview():
    """Get overview analytics for admin dashboard"""
    check = require_admin_api()
    if check: return check
    
    try:
        from datetime import timedelta
        from sqlalchemy import func
        
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        
        # Count stats
        total_users = User.query.count()
        verified_users = User.query.filter_by(verified=True).count()
        total_reports = Report.query.count()
        active_sos = SOSAlert.query.filter_by(status='active').count()
        
        # Recent activity (last 7 days)
        recent_reports = Report.query.filter(Report.timestamp >= week_ago).count()
        recent_users = User.query.filter(User.created_at >= week_ago).count()
        
        # Reports by category
        category_counts = db.session.query(
            Report.category, 
            func.count(Report.id)
        ).group_by(Report.category).all()
        
        categories = [{"category": cat, "count": count} for cat, count in category_counts]
        
        return jsonify({
            "total_users": total_users,
            "verified_users": verified_users,
            "total_reports": total_reports,
            "active_sos": active_sos,
            "recent_reports": recent_reports,
            "recent_users": recent_users,
            "categories": categories
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/admin/analytics/trends")
def get_trends():
    """Get 7-day trends for reports and SOS"""
    check = require_admin_api()
    if check: return check
    
    try:
        from datetime import timedelta
        from sqlalchemy import func, cast, Date
        
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        
        # Reports by day (last 7 days)
        report_trends = db.session.query(
            cast(Report.timestamp, Date).label('date'),
            func.count(Report.id).label('count')
        ).filter(Report.timestamp >= week_ago).group_by('date').all()
        
        # SOS by day (last 7 days)
        sos_trends = db.session.query(
            cast(SOSAlert.created_at, Date).label('date'),
            func.count(SOSAlert.id).label('count')
        ).filter(SOSAlert.created_at >= week_ago).group_by('date').all()
        
        # Format data
        report_data = [{"date": str(date), "count": count} for date, count in report_trends]
        sos_data = [{"date": str(date), "count": count} for date, count in sos_trends]
        
        return jsonify({
            "reports": report_data,
            "sos": sos_data
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/analytics/heatmap-data")
def get_heatmap_data():
    """Get location data for heatmap"""
    check = require_admin_api()
    if check: return check
    
    try:
        # Get all reports with coordinates
        reports = Report.query.with_entities(Report.lat, Report.lng, Report.category).all()
        sos_alerts = SOSAlert.query.with_entities(SOSAlert.lat, SOSAlert.lng).all()
        
        heatmap_points = []
        
        # Add reports (intensity 1)
        for lat, lng, category in reports:
            heatmap_points.append({
                "lat": float(lat),
                "lng": float(lng),
                "intensity": 1,
                "type": "report"
            })
        
        # Add SOS alerts (intensity 3 - more critical)
        for lat, lng in sos_alerts:
            heatmap_points.append({
                "lat": float(lat),
                "lng": float(lng),
                "intensity": 3,
                "type": "sos"
            })
        
        return jsonify({"points": heatmap_points}), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    



@app.route('/rate', methods=['POST'])
def rate():
    if not session.get('loggedin'):
        return jsonify({'message': 'You must be logged in.'}), 401
    data = request.get_json(force=True)
    rating = int(data.get('rating', 0))
    username = session.get('username')
    if rating < 1 or rating > 5:
        return jsonify({'message': 'Invalid rating.'}), 400
    existing = StarRating.query.filter_by(username=username).first()
    if existing:
        existing.rating = rating
        existing.rated_at = datetime.now()
    else:
        sr = StarRating(username=username, rating=rating)
        db.session.add(sr)
    db.session.commit()
    return jsonify({'message': f'Your rating ({rating} stars) has been saved.'}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)