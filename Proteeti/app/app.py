# app.py (top area)
from flask import Flask
import os
from flask_cors import CORS

# If templates/static are NOT in the same folder as app.py, point to them safely:
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "..", "templates")   # adjust if needed
STATIC_DIR    = os.path.join(BASE_DIR, "..", "static")      # adjust if needed

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)
CORS(app)

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
from models.user import db, User, Report, SOSAlert, NotificationSubscription
import base64
import hashlib
import hmac

load_dotenv()

DEV_MODE = os.getenv("DEV_MODE", "False").lower() in ("1", "true", "yes")
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__, template_folder="templates", static_folder="static")
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


VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', 'YOUR_PUBLIC_KEY')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', 'YOUR_PRIVATE_KEY')
VAPID_EMAIL = os.getenv('VAPID_EMAIL', 'mailto:your-email@example.com')


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
        
        if not latitude or not longitude:
            return jsonify({"error": "Location required"}), 400
        
        username = session.get("username")
        user = User.query.filter_by(username=username).first()
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Save SOS alert to database
        sos_alert = SOSAlert(
            user_id=user.id,
            username=username,
            lat=latitude,
            lng=longitude,
            accuracy=accuracy or 0,
            status='active'
        )
        db.session.add(sos_alert)
        db.session.commit()
        
        # Send immediate location email
        send_sos_email_with_location(user, latitude, longitude)
        
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




@app.route("/map")
def show_map():
    reports = Report.query.all() 
    return render_template("map.html", reports=reports)

@app.route("/resources")
def resources():
    return render_template("resources.html")



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


@app.route('/api/notifications/subscribe', methods=['POST'])
@login_required
def subscribe_to_notifications():
    """Subscribe user to push notifications"""
    try:
        subscription_data = request.get_json()
        
        # Validate subscription data
        if not all(key in subscription_data for key in ['endpoint', 'keys']):
            return jsonify({'error': 'Invalid subscription data'}), 400
        
        # Store subscription in database
        auth_key = subscription_data['keys'].get('auth')
        p256dh_key = subscription_data['keys'].get('p256dh')
        
        # Check if subscription already exists
        existing = NotificationSubscription.query.filter_by(
            endpoint=subscription_data['endpoint']
        ).first()
        
        if existing:
            existing.is_active = True
            existing.user_id = current_user.id
        else:
            subscription = NotificationSubscription(
                user_id=current_user.id,
                endpoint=subscription_data['endpoint'],
                auth_key=auth_key,
                p256dh_key=p256dh_key
            )
            db.session.add(subscription)
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Subscribed successfully'}), 201
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/notifications/unsubscribe', methods=['POST'])
@login_required
def unsubscribe_from_notifications():
    """Unsubscribe user from push notifications"""
    try:
        data = request.get_json()
        endpoint = data.get('endpoint')
        
        subscription = NotificationSubscription.query.filter_by(
            endpoint=endpoint,
            user_id=current_user.id
        ).first()
        
        if subscription:
            subscription.is_active = False
            db.session.commit()
        
        return jsonify({'success': True, 'message': 'Unsubscribed successfully'}), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/notifications/send-hazard', methods=['POST'])
@login_required
def send_hazard_alert():
    """Send hazard alert to all active subscriptions (admin only)"""
    try:
        if not current_user.is_admin:
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        title = data.get('title', 'Hazard Alert')
        body = data.get('body', 'A new hazard has been detected')
        urgent = data.get('urgent', False)
        
        # Get all active subscriptions
        subscriptions = NotificationSubscription.query.filter_by(is_active=True).all()
        
        if not subscriptions:
            return jsonify({'success': True, 'sent': 0, 'message': 'No active subscriptions'}), 200
        
        # Send to all subscriptions
        sent_count = 0
        from pywebpush import webpush, WebPushException
        
        for sub in subscriptions:
            try:
                payload = {
                    'title': title,
                    'body': body,
                    'tag': 'hazard-alert',
                    'urgent': urgent,
                    'timestamp': datetime.utcnow().isoformat()
                }
                
                webpush(
                    subscription_info={
                        'endpoint': sub.endpoint,
                        'keys': {
                            'auth': sub.auth_key,
                            'p256dh': sub.p256dh_key
                        }
                    },
                    data=json.dumps(payload),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': VAPID_EMAIL}
                )
                sent_count += 1
            except WebPushException as e:
                # Mark subscription as inactive if it fails
                if e.response.status_code == 410:
                    sub.is_active = False
                    db.session.commit()
        
        return jsonify({
            'success': True,
            'sent': sent_count,
            'message': f'Hazard alert sent to {sent_count} users'
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notifications/send-community', methods=['POST'])
@login_required
def send_community_update():
    """Send community safety update to all active subscriptions"""
    try:
        if not current_user.is_admin:
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        title = data.get('title', 'Community Update')
        body = data.get('body', 'Community safety update')
        
        subscriptions = NotificationSubscription.query.filter_by(is_active=True).all()
        
        if not subscriptions:
            return jsonify({'success': True, 'sent': 0}), 200
        
        sent_count = 0
        from pywebpush import webpush, WebPushException
        
        for sub in subscriptions:
            try:
                payload = {
                    'title': title,
                    'body': body,
                    'tag': 'community-update',
                    'urgent': False,
                    'timestamp': datetime.utcnow().isoformat()
                }
                
                webpush(
                    subscription_info={
                        'endpoint': sub.endpoint,
                        'keys': {
                            'auth': sub.auth_key,
                            'p256dh': sub.p256dh_key
                        }
                    },
                    data=json.dumps(payload),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': VAPID_EMAIL}
                )
                sent_count += 1
            except WebPushException as e:
                if e.response.status_code == 410:
                    sub.is_active = False
                    db.session.commit()
        
        return jsonify({'success': True, 'sent': sent_count}), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notifications/status', methods=['GET'])
@login_required
def get_notification_status():
    """Get user's notification subscription status"""
    try:
        subscription = NotificationSubscription.query.filter_by(
            user_id=current_user.id,
            is_active=True
        ).first()
        
        return jsonify({
            'subscribed': subscription is not None,
            'permission': Notification.permission if 'Notification' in dir() else 'unknown'
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)