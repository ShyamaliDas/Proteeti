from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_dance.contrib.google import make_google_blueprint, google
from flask_migrate import Migrate
from flask_login import login_required, current_user, LoginManager
from flask import current_app
from flask_sqlalchemy import SQLAlchemy
import json, os, re, random, requests, smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from dotenv import load_dotenv
from config.database import Config
from models.user import db, User, Report, SOSAlert, Admin, StarRating, NetworkConnection
import base64
import hashlib
import hmac

load_dotenv()

DEV_MODE = os.getenv("DEV_MODE", "False").lower() in ("1", "true", "yes")
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__)
app.config.from_object(Config)

# Provide a consistent "now" helper used across the code (bd_now was referenced but not defined)
def bd_now():
    """Returns Bangladesh datetime OBJECT (UTC+6)"""
    return datetime.now(timezone.utc) + timedelta(hours=6)


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
    """Send immediate SOS email with live location to BOTH network + legacy contacts"""
    GMAIL_SENDER = os.getenv("GMAIL_SENDER")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
    
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("[ERROR] Gmail credentials not configured!")
        return False

    maps_link = f"https://www.google.com/maps?q={latitude},{longitude}"
    sent_to_anyone = False

    # === 1. Send to Network Connections (mutual app users) ===
    for contact in user.trusted_contacts:  # This is your property → returns accepted connections
        recipient_email = contact['user'].email
        if not recipient_email:
            continue
            
        try:
            msg = MIMEMultipart()
            msg['From'] = f"Proteeti SOS <{GMAIL_SENDER}>"
            msg['To'] = recipient_email
            msg['Subject'] = "EMERGENCY SOS - LIVE LOCATION"

            body = f"""URGENT: {user.username} HAS TRIGGERED AN SOS!

Live Location: {maps_link}
Coordinates: {latitude}, {longitude}

This person needs immediate help.
This is an automated alert from Proteeti Safety Network."""

            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=15)
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.send_message(msg)
            server.quit()

            print(f"[SOS] Location alert sent to network contact: {recipient_email}")
            sent_to_anyone = True

        except Exception as e:
            print(f"[SOS] Failed to send to {recipient_email}: {e}")

    # === 2. Send to Legacy Contacts (non-app users from onboarding) ===
    if user.legacy_contacts:
        for contact in user.legacy_contacts:
            recipient_email = contact.get('email')
            if not recipient_email:
                continue
                
            try:
                msg = MIMEMultipart()
                msg['From'] = f"Proteeti SOS <{GMAIL_SENDER}>"
                msg['To'] = recipient_email
                msg['Subject'] = "EMERGENCY SOS from " + user.username

                body = f"""EMERGENCY ALERT

{user.username} has triggered an SOS alert!

They added you as a trusted contact.

Live Location: {maps_link}
Coordinates: {latitude}, {longitude}

Please check on them immediately!
This is an automated message from Proteeti."""

                msg.attach(MIMEText(body, 'plain'))

                server = smtplib.SMTP('smtp.gmail.com', 587, timeout=15)
                server.starttls()
                server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
                server.send_message(msg)
                server.quit()

                print(f"[SOS] Location alert sent to legacy contact: {recipient_email}")
                sent_to_anyone = True

            except Exception as e:
                print(f"[SOS] Failed to send to legacy contact {recipient_email}: {e}")

    if not sent_to_anyone:
        print(f"[SOS] No contacts found for {user.username} — no emails sent")

    return sent_to_anyone


def send_sos_email_with_audio(user, audio_blob):
    """Send SOS email with 2-minute audio to BOTH network + legacy contacts"""
    GMAIL_SENDER = os.getenv("GMAIL_SENDER")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
    
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print("[ERROR] Gmail credentials not configured!")
        return False

    audio_size_mb = len(audio_blob) / (1024 * 1024)
    print(f"[SOS] Preparing to send audio ({audio_size_mb:.2f} MB)")
    sent_to_anyone = False

    # === 1. Network Connections ===
    for contact in user.trusted_contacts:
        recipient_email = contact['user'].email
        if not recipient_email:
            continue

        try:
            msg = MIMEMultipart()
            msg['From'] = f"Proteeti SOS <{GMAIL_SENDER}>"
            msg['To'] = recipient_email
            msg['Subject'] = "EMERGENCY SOS - AUDIO EVIDENCE"

            body = f"""CRITICAL: {user.username} HAS TRIGGERED AN SOS

An emergency audio recording (2 minutes) is attached below.

Please listen immediately and take action if needed.

This is an automated alert from Proteeti Safety Network."""

            msg.attach(MIMEText(body, 'plain'))

            # Attach audio
            part = MIMEApplication(audio_blob)
            part.add_header('Content-Disposition', 'attachment', filename='EMERGENCY_AUDIO.webm')
            msg.attach(part)

            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.send_message(msg)
            server.quit()

            print(f"[SOS] Audio sent to network contact: {recipient_email}")
            sent_to_anyone = True

        except Exception as e:
            print(f"[SOS] Failed sending audio to {recipient_email}: {e}")

    # === 2. Legacy Contacts ===
    if user.legacy_contacts:
        for contact in user.legacy_contacts:
            recipient_email = contact.get('email')
            if not recipient_email:
                continue

            try:
                msg = MIMEMultipart()
                msg['From'] = f"Proteeti SOS <{GMAIL_SENDER}>"
                msg['To'] = recipient_email
                msg['Subject'] = "EMERGENCY SOS AUDIO from " + user.username

                body = f"""{user.username} has triggered an emergency SOS!

They listed you as a trusted contact.

A 2-minute audio recording from the incident is attached.

Please listen and help immediately!

This is an automated message from Proteeti."""

                msg.attach(MIMEText(body, 'plain'))

                part = MIMEApplication(audio_blob)
                part.add_header('Content-Disposition', 'attachment', filename='EMERGENCY_AUDIO.webm')
                msg.attach(part)

                server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
                server.starttls()
                server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
                server.send_message(msg)
                server.quit()

                print(f"[SOS] Audio sent to legacy contact: {recipient_email}")
                sent_to_anyone = True

            except Exception as e:
                print(f"[SOS] Failed sending audio to legacy contact {recipient_email}: {e}")

    return sent_to_anyone



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
            print(f"Failed login attempt for {username_or_email}")
            print(f"User found: {user is not None}")
            print(f"Password correct: {user.check_password(password) if user else 'N/A'}")
            print(f"Stored password hash: {user.password_hash if user else 'N/A'}")
            print(f"Entered password: {password}")
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
    
    # ADD THIS: Fetch network connections
    network_connections = []
    
    # Get accepted connections where user is requester
    sent_connections = NetworkConnection.query.filter_by(
        requester_id=user.id, 
        status='accepted'
    ).all()
    
    # Get accepted connections where user is recipient
    received_connections = NetworkConnection.query.filter_by(
        recipient_id=user.id, 
        status='accepted'
    ).all()
    
    # Build network list
    for conn in sent_connections:
        contact = User.query.get(conn.recipient_id)
        if contact:
            network_connections.append({
                'connection_id': conn.id,
                'username': contact.username,
                'email': contact.email,
                'status': 'connected'
            })
    
    for conn in received_connections:
        contact = User.query.get(conn.requester_id)
        if contact:
            network_connections.append({
                'connection_id': conn.id,
                'username': contact.username,
                'email': contact.email,
                'status': 'connected'
            })
    
    # Legacy contacts from old JSON field (if any)
    legacy_contacts = user.trusted_contacts if user.trusted_contacts else []
    
    active_sos = SOSAlert.query.filter_by(
        user_id=user.id, 
        status='active'
    ).order_by(SOSAlert.created_at.desc()).all()
    
    resolved_sos = SOSAlert.query.filter_by(
        user_id=user.id,
        status='resolved'  # Only resolved, not 'historical'
    ).order_by(SOSAlert.created_at.desc()).limit(20).all()

    return render_template("account.html", 
                         user=info, 
                         username=username,
                         network_connections=network_connections,
                         legacy_contacts=legacy_contacts,
                         active_sos=active_sos,
                         resolved_sos=resolved_sos)

@app.route("/api/resolve_sos", methods=["POST"])
def resolve_sos():
    """Mark SOS as resolved"""
    try:
        if not session.get("loggedin"):
            return jsonify({"error": "Not logged in"}), 401
        
        data = request.get_json()
        sos_id = int(data.get("sos_id"))
        
        sos = SOSAlert.query.get(sos_id)
        
        if not sos:
            return jsonify({"error": "SOS not found"}), 404
        
        current_user = User.query.filter_by(username=session.get("username")).first()
        
        if not current_user or sos.user_id != current_user.id:
            return jsonify({"error": "Unauthorized"}), 403
        
        # Update SOS status
        sos.status = 'resolved'
        sos.is_live = False
        sos.resolved_by = current_user.username
        sos.resolved_at = bd_now()
        sos.last_updated = bd_now()
        
        db.session.commit()
        
        return jsonify({"message": "SOS marked as safe!"}), 200
        
    except Exception as e:
        print(f"[ERROR] Resolve SOS failed: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

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
        profile_fields = ["full_name", "phone", "country", "city", "language", "timezone"]
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
        
        # Handle trusted contact
        tc_email = data.get("tc_email")
        existing_user = User.query.filter_by(email=tc_email).first() if tc_email else None
        
        if existing_user:
            # Existing app user → send connection request
            existing_conn = NetworkConnection.query.filter(
                ((NetworkConnection.requester_id == user.id) & (NetworkConnection.recipient_id == existing_user.id)) |
                ((NetworkConnection.requester_id == existing_user.id) & (NetworkConnection.recipient_id == user.id))
            ).first()
            
            if not existing_conn:
                conn = NetworkConnection(
                    requester_id=user.id,
                    recipient_id=existing_user.id,
                    status='pending'
                )
                db.session.add(conn)
        else:
            # Non-app user → add to legacy_contacts
            trusted_contact = {
                "id": len(user.legacy_contacts) + 1 if user.legacy_contacts else 1,
                "name": data.get("tc_name"),
                "relation": data.get("tc_relation"), 
                "email": tc_email,
                "phone": data.get("tc_phone")
            }
            
            if not user.legacy_contacts:
                user.legacy_contacts = []
            user.legacy_contacts.append(trusted_contact)
        
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
        
        # Handle adding new legacy contact if fields are submitted (optional)
        tc_name = form.get("tc_name")
        if tc_name:  # If new contact form submitted
            tc_email = form.get("tc_email")
            existing_user = User.query.filter_by(email=tc_email).first() if tc_email else None
            
            if existing_user:
                # Send connection request (same as onboarding)
                existing_conn = NetworkConnection.query.filter(
                    ((NetworkConnection.requester_id == user.id) & (NetworkConnection.recipient_id == existing_user.id)) |
                    ((NetworkConnection.requester_id == existing_user.id) & (NetworkConnection.recipient_id == user.id))
                ).first()
                
                if not existing_conn:
                    conn = NetworkConnection(
                        requester_id=user.id,
                        recipient_id=existing_user.id,
                        status='pending'
                    )
                    db.session.add(conn)
            else:
                # Add to legacy
                new_contact = {
                    "id": len(user.legacy_contacts) + 1 if user.legacy_contacts else 1,
                    "name": tc_name,
                    "relation": form.get("tc_relation"),
                    "email": tc_email,
                    "phone": form.get("tc_phone")
                }
                if not user.legacy_contacts:
                    user.legacy_contacts = []
                user.legacy_contacts.append(new_contact)
        
        db.session.commit()
        return redirect(url_for("account"))
    return render_template("edit_profile.html", user=user)


@app.route("/send_sos", methods=["POST"])
def send_sos():
    """Send immediate location-based SOS alert with accuracy validation"""
    try:
        if not session.get("loggedin"):
            return jsonify({"error": "Please login first"}), 401

        data = request.get_json()
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy = data.get("accuracy")

        if latitude is None or longitude is None:
            return jsonify({"error": "Location required"}), 400
        
        try:
            lat_f = float(latitude)
            lng_f = float(longitude)
            accuracy_f = float(accuracy) if accuracy else 50.0
        except Exception:
            return jsonify({"error": "Invalid coordinates"}), 400
        
        # ✅ ACCURACY VALIDATION
        if accuracy_f > 1000:
            return jsonify({
                "error": "Location accuracy too low",
                "accuracy": accuracy_f,
                "message": "Please move outdoors and try again. GPS needs clear sky view."
            }), 400
        
        # Warn if accuracy is poor (but still accept)
        warning = None
        if accuracy_f > 200:
            warning = f"Location accuracy is {int(accuracy_f)}m - position is approximate"
        
        # Log accuracy for monitoring
        print(f"[SOS] Accuracy: {accuracy_f}m (acceptable: {accuracy_f <= 1000})")
        
        # Validate coordinates are reasonable (world bounds)
        if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
            return jsonify({"error": "Invalid location coordinates"}), 400

        username = session.get("username")
        user = User.query.filter_by(username=username).first()

        if not user:
            return jsonify({"error": "User not found"}), 404

        # Create SOS alert
        sos_alert = SOSAlert(
            user_id=user.id,
            username=username,
            lat=lat_f,
            lng=lng_f,
            accuracy=accuracy_f,
            status='active',
            is_live=True  # Enable live tracking
        )
        db.session.add(sos_alert)
        db.session.commit()

        print(f"[SOS] Created alert {sos_alert.id} - Location: {lat_f}, {lng_f} (±{accuracy_f}m)")

        # Send emails
        send_sos_email_with_location(user, lat_f, lng_f)

        # Notify network
        connections = NetworkConnection.query.filter(
            ((NetworkConnection.requester_id == user.id) | (NetworkConnection.recipient_id == user.id)),
            NetworkConnection.status == 'accepted'
        ).all()
        
        for conn in connections:
            other_user_id = conn.recipient_id if conn.requester_id == user.id else conn.requester_id
            create_notification(
                user_id=other_user_id,
                type='sos_alert',
                title=f'🚨 SOS ALERT from {username}',
                message=f'{username} needs immediate help! Click to track their location.',
                link=f'/track_sos/{sos_alert.id}',
                data={
                    'sos_id': sos_alert.id,
                    'latitude': lat_f,
                    'longitude': lng_f,
                    'accuracy': accuracy_f,
                    'username': username
                }
            )

        return jsonify({
            "message": "SOS alert sent successfully",
            "status": "success", 
            "alert_id": sos_alert.id,
            "location": {
                "lat": lat_f,
                "lng": lng_f,
                "accuracy": accuracy_f
            },
            "warning": warning  # Include warning if accuracy is poor
        }), 200

    except Exception as e:
        print(f"[SOS] Error: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



# ============= NETWORK CONNECTION ROUTES =============

@app.route("/network")
def network():
    """View your safety network"""
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    # Get accepted connections (mutual friends)
    accepted_sent = NetworkConnection.query.filter_by(
        requester_id=user.id, 
        status='accepted'
    ).all()
    
    accepted_received = NetworkConnection.query.filter_by(
        recipient_id=user.id, 
        status='accepted'
    ).all()
    
    # Get pending requests you sent
    pending_sent = NetworkConnection.query.filter_by(
        requester_id=user.id, 
        status='pending'
    ).all()
    
    # Get pending requests you received
    pending_received = NetworkConnection.query.filter_by(
        recipient_id=user.id, 
        status='pending'
    ).all()
    
    # Build network list
    network_users = []
    
    # Add accepted connections
    for conn in accepted_sent:
        contact = User.query.get(conn.recipient_id)
        network_users.append({
            'id': contact.id,
            'username': contact.username,
            'email': contact.email,
            'status': 'connected',
            'connection_id': conn.id
        })
    
    for conn in accepted_received:
        contact = User.query.get(conn.requester_id)
        network_users.append({
            'id': contact.id,
            'username': contact.username,
            'email': contact.email,
            'status': 'connected',
            'connection_id': conn.id
        })
    
    # Add pending sent
    pending_users = []
    for conn in pending_sent:
        contact = User.query.get(conn.recipient_id)
        pending_users.append({
            'id': contact.id,
            'username': contact.username,
            'email': contact.email,
            'status': 'pending_sent',
            'connection_id': conn.id
        })
    
    # Add pending received (requests you need to accept)
    requests = []
    for conn in pending_received:
        contact = User.query.get(conn.requester_id)
        requests.append({
            'id': contact.id,
            'username': contact.username,
            'email': contact.email,
            'status': 'pending_received',
            'connection_id': conn.id,
            'created_at': conn.created_at
        })
    
    return render_template("network.html", 
                         network_users=network_users,
                         pending_users=pending_users,
                         requests=requests,
                         user=user)


@app.route("/api/search_users", methods=["GET"])
def search_users():
    """Search for users by username or email"""
    if not session.get("loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    query = request.args.get("q", "").strip()
    
    if len(query) < 2:
        return jsonify([]), 200
    
    current_user = User.query.filter_by(username=session.get("username")).first()
    
    # Search users (exclude yourself)
    users = User.query.filter(
        (User.username.ilike(f"%{query}%")) | (User.email.ilike(f"%{query}%")),
        User.id != current_user.id
    ).limit(10).all()
    
    results = []
    for u in users:
        # Check if already connected
        existing = NetworkConnection.query.filter(
            ((NetworkConnection.requester_id == current_user.id) & (NetworkConnection.recipient_id == u.id)) |
            ((NetworkConnection.requester_id == u.id) & (NetworkConnection.recipient_id == current_user.id))
        ).first()
        
        status = 'none'
        if existing:
            if existing.status == 'accepted':
                status = 'connected'
            elif existing.requester_id == current_user.id:
                status = 'pending_sent'
            else:
                status = 'pending_received'
        
        results.append({
            'id': u.id,
            'username': u.username,
            'email': u.email,
            'status': status
        })
    
    return jsonify(results), 200


@app.route("/api/send_connection_request", methods=["POST"])
def send_connection_request():
    """Send a connection request to another user"""
    if not session.get("loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        recipient_id = data.get("user_id")
        
        current_user = User.query.filter_by(username=session.get("username")).first()
        
        if not recipient_id or recipient_id == current_user.id:
            return jsonify({"error": "Invalid user"}), 400
        
        # Check if connection already exists
        existing = NetworkConnection.query.filter(
            ((NetworkConnection.requester_id == current_user.id) & (NetworkConnection.recipient_id == recipient_id)) |
            ((NetworkConnection.requester_id == recipient_id) & (NetworkConnection.recipient_id == current_user.id))
        ).first()
        
        if existing:
            return jsonify({"error": "Connection already exists"}), 400
        
        # Create new connection request
        connection = NetworkConnection(
            requester_id=current_user.id,
            recipient_id=recipient_id,
            status='pending'
        )
        
        db.session.add(connection)
        db.session.commit()
        
        return jsonify({"message": "Connection request sent"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/accept_connection", methods=["POST"])
def accept_connection():
    """Accept a connection request"""
    if not session.get("loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        connection_id = data.get("connection_id")
        
        current_user = User.query.filter_by(username=session.get("username")).first()
        
        connection = NetworkConnection.query.get(connection_id)
        
        if not connection or connection.recipient_id != current_user.id:
            return jsonify({"error": "Invalid connection"}), 400
        
        connection.status = 'accepted'
        connection.accepted_at = bd_now()
        
        db.session.commit()
        
        return jsonify({"message": "Connection accepted"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/decline_connection", methods=["POST"])
def decline_connection():
    """Decline/remove a connection"""
    if not session.get("loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        connection_id = data.get("connection_id")
        
        current_user = User.query.filter_by(username=session.get("username")).first()
        
        connection = NetworkConnection.query.get(connection_id)
        
        if not connection:
            return jsonify({"error": "Connection not found"}), 404
        
        # Can only decline if you're the recipient or it's your request
        if connection.recipient_id != current_user.id and connection.requester_id != current_user.id:
            return jsonify({"error": "Unauthorized"}), 403
        
        db.session.delete(connection)
        db.session.commit()
        
        return jsonify({"message": "Connection removed"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/network_sos")
def get_network_sos():
    """Get active SOS alerts from your network (UPDATED)"""
    if not session.get("loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    # Get all accepted connections
    sent_connections = NetworkConnection.query.filter_by(
        requester_id=user.id, 
        status='accepted'
    ).all()
    
    received_connections = NetworkConnection.query.filter_by(
        recipient_id=user.id, 
        status='accepted'
    ).all()
    
    # Get user IDs of your network
    network_user_ids = []
    for conn in sent_connections:
        network_user_ids.append(conn.recipient_id)
    for conn in received_connections:
        network_user_ids.append(conn.requester_id)
    
    # Get active SOS alerts from network
    active_sos = []
    if network_user_ids:
        active_sos = SOSAlert.query.filter(
            SOSAlert.status == 'active',
            SOSAlert.user_id.in_(network_user_ids)
        ).all()
    
    return jsonify([sos.to_dict() for sos in active_sos])


@app.route("/api/map_sos_alerts")
def get_map_sos_alerts():
    """Get ALL SOS alerts for the safety map"""
    try:
        print("[DEBUG] map_sos_alerts endpoint called!")
        
        # Get real data from database
        all_sos = SOSAlert.query.order_by(SOSAlert.created_at.desc()).limit(50).all()
        print(f"[DEBUG] Found {len(all_sos)} SOS alerts in database")
        
        alerts_data = []
        for sos in all_sos:
            alerts_data.append({
                'id': sos.id,
                'username': sos.username,
                'lat': float(sos.lat) if sos.lat else 0,
                'lng': float(sos.lng) if sos.lng else 0,
                'accuracy': float(sos.accuracy) if sos.accuracy else 20,
                'status': str(sos.status) if sos.status else 'unknown',
                'created_at': str(sos.created_at) if sos.created_at else '',
                'last_updated': str(sos.last_updated) if sos.last_updated else '',
                'is_live': bool(sos.is_live) if sos.is_live is not None else False
            })
        
        active_count = len([a for a in alerts_data if a['status'] == 'active'])
        resolved_count = len([a for a in alerts_data if a['status'] == 'resolved'])
        
        print(f"[DEBUG] Returning {len(alerts_data)} alerts: {active_count} active, {resolved_count} resolved")
        return jsonify(alerts_data), 200
        
    except Exception as e:
        print(f"[DEBUG] ERROR in map_sos_alerts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Server error"}), 500

@app.route("/api/update_sos_location", methods=["POST"])
def update_sos_location():
    """Update live location for active SOS"""
    if not session.get("loggedin"):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        sos_id = data.get("sos_id")
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
        accuracy = data.get("accuracy", 0)
        
        current_user = User.query.filter_by(username=session.get("username")).first()
        
        sos = SOSAlert.query.get(sos_id)
        
        if not sos or sos.user_id != current_user.id:
            return jsonify({"error": "Unauthorized"}), 403
        
        # Update location
        sos.lat = lat
        sos.lng = lng
        sos.accuracy = accuracy
        sos.last_updated = bd_now()
        
        db.session.commit()
        
        return jsonify({"message": "Location updated"}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/track_sos/<int:sos_id>")
def track_sos(sos_id):
    """Page to track a specific SOS alert"""
    if not session.get("loggedin"):
        return redirect("/login")
    
    sos = SOSAlert.query.get(sos_id)
    if not sos:
        return "SOS alert not found", 404
    
    # Check if user has permission to view this SOS
    current_user = User.query.filter_by(username=session.get("username")).first()
    
    if sos.user_id == current_user.id:
        # User is the one who sent the SOS
        return render_template("track_sos.html", sos=sos)
    
    # Check if users are connected
    from models.user import NetworkConnection
    connection = NetworkConnection.query.filter(
        ((NetworkConnection.requester_id == current_user.id) & (NetworkConnection.recipient_id == sos.user_id)) |
        ((NetworkConnection.requester_id == sos.user_id) & (NetworkConnection.recipient_id == current_user.id)),
        NetworkConnection.status == 'accepted'
    ).first()
    
    if connection:
        return render_template("track_sos.html", sos=sos)
    else:
        return "You don't have permission to view this SOS", 403



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
    
    try:
        users = User.query.with_entities(
            User.id,
            User.username,
            User.email,
            User.verified,
            User.created_at,
            User.legacy_contacts
        ).order_by(User.created_at.desc()).all()  # Most recent first
        
        return jsonify([{
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "verified": u.verified,
            "trusted_contacts_count": len(u.legacy_contacts) if u.legacy_contacts else 0,
            "created_at": u.created_at  
        } for u in users])
    
    except Exception as e:
        print(f"[ERROR] get_admin_users: {e}")
        return jsonify({"error": str(e)}), 500

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
    """Get overview analytics for admin dashboard - OPTIMIZED"""
    check = require_admin_api()
    if check: return check
    
    try:
        from sqlalchemy import func
        
        # Single optimized query for counts
        total_users = db.session.query(func.count(User.id)).scalar() or 0
        verified_users = db.session.query(func.count(User.id)).filter(User.verified == True).scalar() or 0
        total_reports = db.session.query(func.count(Report.id)).scalar() or 0
        active_sos = db.session.query(func.count(SOSAlert.id)).filter(SOSAlert.status == 'active').scalar() or 0
        
        # Reports by category - optimized
        category_counts = db.session.query(
            Report.category, 
            func.count(Report.id)
        ).group_by(Report.category).all()
        
        categories = [{"category": cat or "Unknown", "count": count} for cat, count in category_counts]
        
        return jsonify({
            "total_users": total_users,
            "verified_users": verified_users,
            "total_reports": total_reports,
            "active_sos": active_sos,
            "recent_reports": 0,  # Optional: add if needed
            "recent_users": 0,    # Optional: add if needed
            "categories": categories
        }), 200
    
    except Exception as e:
        print(f"[ERROR] analytics/overview: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/admin/analytics/trends")
def get_trends():
    """Get 7-day trends - OPTIMIZED"""
    check = require_admin_api()
    if check: return check
    
    try:
        from sqlalchemy import func
        from datetime import datetime, timedelta
        
        # Get last 7 days of data
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        
        # Reports trend - optimized query
        report_trends = db.session.query(
            func.substr(Report.timestamp, 1, 10).label('date'),
            func.count(Report.id).label('count')
        ).filter(Report.timestamp >= seven_days_ago).group_by('date').all()
        
        # SOS trend - optimized query
        sos_trends = db.session.query(
            func.substr(SOSAlert.created_at, 1, 10).label('date'),
            func.count(SOSAlert.id).label('count')
        ).filter(SOSAlert.created_at >= seven_days_ago).group_by('date').all()
        
        # Format data
        report_data = [{"date": str(date), "count": count} for date, count in report_trends]
        sos_data = [{"date": str(date), "count": count} for date, count in sos_trends]
        
        return jsonify({
            "reports": report_data,
            "sos": sos_data
        }), 200
    
    except Exception as e:
        print(f"[ERROR] trends: {e}")
        import traceback
        traceback.print_exc()
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


@app.route("/api/notifications/unread")
def get_unread_notifications():
    """Get unread notifications for current user"""
    if not session.get("loggedin"):
        return jsonify({"error": "Not logged in"}), 401
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    from models.user import Notification
    notifications = Notification.query.filter_by(
        user_id=user.id,
        is_read=False
    ).order_by(Notification.created_at.desc()).limit(10).all()
    
    return jsonify({
        "count": len(notifications),
        "notifications": [n.to_dict() for n in notifications]
    }), 200

@app.route("/api/notifications/mark-read", methods=["POST"])
def mark_notification_read():
    """Mark notification as read"""
    if not session.get("loggedin"):
        return jsonify({"error": "Not logged in"}), 401
    
    data = request.get_json()
    notif_id = data.get("notification_id")
    
    from models.user import Notification
    notification = Notification.query.get(notif_id)
    
    if notification:
        notification.is_read = True
        db.session.commit()
        return jsonify({"message": "Marked as read"}), 200
    
    return jsonify({"error": "Notification not found"}), 404

@app.route("/api/notifications/mark-all-read", methods=["POST"])
def mark_all_read():
    """Mark all notifications as read"""
    if not session.get("loggedin"):
        return jsonify({"error": "Not logged in"}), 401
    
    username = session.get("username")
    user = User.query.filter_by(username=username).first()
    
    from models.user import Notification
    Notification.query.filter_by(
        user_id=user.id,
        is_read=False
    ).update({"is_read": True})
    
    db.session.commit()
    return jsonify({"message": "All marked as read"}), 200

# Helper function to create notifications
def create_notification(user_id, type, title, message, link=None, data=None):
    """Create a new notification for a user"""
    from models.user import Notification
    
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        link=link,
        data=data or {}
    )
    
    db.session.add(notification)
    db.session.commit()
    return notification


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)