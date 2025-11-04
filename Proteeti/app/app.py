from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_dance.contrib.google import make_google_blueprint, google
import json, os, re, random, requests, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import base64
import json as _json
load_dotenv()

# DEV_MODE toggles development behavior (skip sending real emails, show verification codes)
DEV_MODE = os.getenv("DEV_MODE", "False").lower() in ("1", "true", "yes")

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "proteeti_secret_key_2025")

# ======= Config / Paths =======
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
REPORTS_FILE = os.path.join(DATA_DIR, "reports.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ======= Google OAuth =======
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = GOOGLE_CLIENT_ID
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = GOOGLE_CLIENT_SECRET
    
    google_bp = make_google_blueprint(
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scope=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
        redirect_to="google_authorized"
    )
    app.register_blueprint(google_bp, url_prefix="/login")
else:
    print("WARNING: Google OAuth credentials not found. Google login will not work.")

# ======= Storage utils =======
def init_json_files():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump({}, f)
    if not os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "w") as f:
            json.dump([], f)

def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_user_record(username, email, password, verified=True):
    print("Saving users to:", os.path.abspath(USERS_FILE))
    users = load_users()
    
    existing_contacts = []
    if username in users:
        existing_contacts = users[username].get("trusted_contacts", [])
    
    users[username] = {
        "password": password,
        "email": email,
        "created_at": users.get(username, {}).get("created_at", datetime.now().isoformat()),
        "verified": verified,
        "trusted_contacts": existing_contacts
    }
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)
    print("Saved user:", username)

def email_in_use(email):
    return any(u.get("email") == email for u in load_users().values())

def load_reports():
    try:
        if os.path.exists(REPORTS_FILE):
            with open(REPORTS_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return []

def save_reports(reports):
    with open(REPORTS_FILE, "w") as f:
        json.dump(reports, f, indent=4)

# ======= Email validation =======
def is_valid_email(email):
    return re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email) is not None

MAILBOXLAYER_KEY = os.getenv("MAILBOXLAYER_KEY")

def mailboxlayer_check(email):
    """Skip remote email validation in DEV to avoid slow requests."""
    if DEV_MODE or not MAILBOXLAYER_KEY:
        return True, "Email validation skipped in dev mode"
    url = f"http://apilayer.net/api/check?access_key={MAILBOXLAYER_KEY}&email={email}&smtp=1&format=1"
    try:
        data = requests.get(url, timeout=8).json()  # 8s timeout to avoid page hang
        if not data.get("success", True) and "error" in data:
            return True, "Email validation service unavailable, skipping"
        if not data.get("format_valid", False): return False, "Email format is invalid."
        if not data.get("mx_found", False): return False, "Email domain has no MX records."
        if not data.get("smtp_check", False): return False, "Email address does not exist or cannot receive mail."
        if data.get("disposable", False): return False, "Disposable/temporary email addresses are not allowed."
        return True, "Email is valid."
    except Exception:
        # don't block registration if the API is slow/unreachable
        return True, "Email validation skipped"

# ======= 6-digit code =======
def generate_verification_code():
    return str(random.randint(100000, 999999))

GMAIL_SENDER = os.getenv("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

def send_verification_code(email, code):
    """
    Non-blocking-ish email sender:
    - DEV_MODE: do not send, return immediately.
    - No creds: return immediately.
    - SMTP connection timeout enforced (8s) so UI won't hang.
    - Failures are swallowed; OTP is still displayed in DEV via 'show_code'.
    """
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
        # don't block the flow if email fails; log and continue
        try:
            app.logger.warning(f"Email send failed: {e}")
        except Exception:
            pass
        return False

# ======= Routes =======
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username_or_email = request.form["username_or_email"].strip()
        password = request.form["password"]

        if "@" in username_or_email and not is_valid_email(username_or_email):
            error = "Please enter a valid email address."
            return render_template("login.html", error=error)

        users = load_users()
        found = None
        for uname, info in users.items():
            if username_or_email == uname or username_or_email == info.get("email"):
                found = {"username": uname, "password": info.get("password")}
                break

        if found and password == found["password"]:
            session.clear()
            session["loggedin"] = True
            session["username"] = found["username"]
            session.modified = True
            print(f"Login successful for {found['username']}")
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

        ok, msg = mailboxlayer_check(email)
        if not ok:
            error = msg
            return render_template("register.html", error=error)

        if password != confirmpassword:
            error = "Passwords do not match."
            return render_template("register.html", error=error)

        users = load_users()
        if username in users or email_in_use(email):
            error = "Username or email already exists."
            return render_template("register.html", error=error)

        code = generate_verification_code()
        # Do not block UI on email sending; failures are fine in DEV/test.
        _ = send_verification_code(email, code)

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

@app.route("/login/google/authorized")
def google_authorized():
    print("=== Google OAuth Callback ===")
    print("Request URL:", request.url)
    
    if not google.authorized:
        print("Not authorized, redirecting to login")
        return redirect(url_for("login"))

    try:
        resp = google.get("/oauth2/v2/userinfo")
        if not resp.ok:
            print(f"Failed to get user info: {resp.text}")
            return redirect(url_for("login"))
        
        userinfo = resp.json()
        print("User info:", userinfo)
        
        google_sub = userinfo.get("sub")
        email = userinfo.get("email")
        name = userinfo.get("name", email.split("@")[0])

        # Check if user already exists
        users = load_users()
        existing_user = None
        for uname, info in users.items():
            if info.get("email") == email:
                existing_user = uname
                break

        if existing_user:
            # User exists, log them in directly
            session.clear()
            session["loggedin"] = True
            session["username"] = existing_user
            session.modified = True
            print(f"Existing user logged in: {existing_user}")
            return redirect(url_for("account"))
        
        # New user - send verification code
        code = generate_verification_code()
        try:
            send_verification_code(email, code)
        except Exception as e:
            print(f"Email send error: {e}")
            # Continue anyway in dev mode
            if not DEV_MODE:
                return render_template("login.html", error=f"Could not initiate verification: {e}")

        session["pending_user"] = {
            "flow": "google",
            "username": f"google_{google_sub}",
            "email": email,
            "password": None,
            "name": name,
            "code": code,
            "created_at": datetime.now().isoformat(),
        }
        return redirect(url_for("verify_email"))
    
    except Exception as e:
        print(f"Error in google_authorized: {e}")
        return redirect(url_for("login"))

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    error = None
    pending = session.get("pending_user")
    print("Pending user in session:", pending)
    
    if not pending:
        return redirect(url_for("register"))

    show_code = DEV_MODE

    if request.method == "POST":
        entered_code = request.form["code"].strip()
        print("Entered:", entered_code, "Expected:", pending["code"])
        
        if entered_code == pending["code"]:
            save_user_record(
                pending["username"],
                pending["email"],
                pending["password"],
                verified=True
            )
            
            session.clear()
            session["loggedin"] = True
            session["username"] = pending["username"]
            session.modified = True
            
            print("Session set - loggedin:", session.get("loggedin"), "username:", session.get("username"))
            
            return redirect(url_for("onboarding"))
        else:
            error = "Verification code incorrect."
    
    return render_template("verify_email.html", error=error, show_code=show_code, code=(pending["code"] if show_code else None))

# ---------- /account: render dashboard with full user info ----------
@app.route("/account")
def account():
    if not session.get("loggedin"):
        return redirect(url_for("login"))

    username = session.get("username")
    users = load_users()
    info = users.get(username)

    if not info:
        # user record missing → logout for safety
        return redirect(url_for("logout"))

    # Ensure sane defaults so templates don't crash
    info.setdefault("profile", {})
    info.setdefault("trusted_contacts", [])
    info.setdefault("notification_prefs", {
        "channels": {"email": True, "sms": True, "push": False},
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "hazard_categories": []
    })

    return render_template("account.html", user=info, username=username)


# ---------- /update_account: accept JSON and persist ----------
@app.route("/update_account", methods=["POST"])
def update_account():
    if not session.get("loggedin"):
        return jsonify({"error": "Please login first"}), 401

    username = session.get("username")
    users = load_users()
    info = users.get(username, {})

    # Always have containers
    info.setdefault("profile", {})
    info.setdefault("notification_prefs", {})

    try:
        data = request.get_json(force=True, silent=True) or {}

        # A) Core
        core = data.get("core") or {}
        for k in ("full_name", "phone", "country", "city", "language", "timezone"):
            if k in core:
                v = core.get(k) or ""
                info["profile"][k] = v.strip() if isinstance(v, str) else v

        # C) Consents & preferences
        cons = data.get("consents")
        if cons is not None:
            # expected: {'location': bool, 'privacy': bool, 'communication': {'transactional': bool, 'promotional': bool}}
            info["profile"]["consents"] = {
                "location": bool(cons.get("location", False)),
                "privacy": bool(cons.get("privacy", True)),
                "communication": {
                    "transactional": bool((cons.get("communication") or {}).get("transactional", True)),
                    "promotional": bool((cons.get("communication") or {}).get("promotional", False)),
                }
            }

        notif = data.get("notification_prefs")
        if notif is not None:
            # expected keys exist or we default them
            channels = (notif.get("channels") or {})
            quiet = (notif.get("quiet_hours") or {})
            info["notification_prefs"] = {
                "channels": {
                    "email": bool(channels.get("email", True)),
                    "sms": bool(channels.get("sms", True)),
                    "push": bool(channels.get("push", False)),
                },
                "quiet_hours": {
                    "start": str(quiet.get("start", "22:00")),
                    "end": str(quiet.get("end", "07:00")),
                },
                "hazard_categories": list(notif.get("hazard_categories") or [])
            }

        # D/E) Optional
        optional = data.get("optional") or {}
        if optional:
            for k in ("secondary_phone", "medical_notes", "emergency_instructions",
                      "avatar_url", "gender", "dob", "device_push_token"):
                if k in optional:
                    info["profile"][k] = optional.get(k)
            if "home_area" in optional:
                # expected: {'lat': float, 'lng': float, 'radius_m': int}
                ha = optional.get("home_area") or {}
                lat = ha.get("lat"); lng = ha.get("lng"); r = ha.get("radius_m")
                # store only if at least lat/lng present
                if lat is not None and lng is not None:
                    info["profile"]["home_area"] = {
                        "lat": float(lat),
                        "lng": float(lng),
                        "radius_m": int(r) if r is not None else 1500
                    }

        # Persist
        users[username] = info
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, indent=4)

        return jsonify({"message": "Account updated"}), 200

    except Exception as e:
        try:
            app.logger.error(f"/update_account error: {e}")
        except Exception:
            pass
        return jsonify({"error": "Unable to update account"}), 400

@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    if not session.get("loggedin"):
        return redirect(url_for("login"))

    username = session.get("username")
    users = load_users()
    info = users.get(username, {}) or {}

    # existing values (for prefill)
    profile = info.get("profile", {})
    full_name  = profile.get("full_name", "")
    phone      = profile.get("phone", "")
    country    = profile.get("country", "")
    city       = profile.get("city", "")
    language   = profile.get("language", "en")
    timezone   = profile.get("timezone", "UTC")

    error = None

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone     = (request.form.get("phone") or "").strip()
        country   = (request.form.get("country") or "").strip().upper()
        city      = (request.form.get("city") or "").strip()
        language  = (request.form.get("language") or "en").strip()
        timezone  = (request.form.get("timezone") or "UTC").strip()

        tc_name     = (request.form.get("tc_name") or "").strip()
        tc_relation = (request.form.get("tc_relation") or "").strip()
        tc_reach    = (request.form.get("tc_reach") or "").strip()
        tc_channel  = (request.form.get("tc_channel") or "sms").strip()

        # minimal validation (why: ensure alerts can reach someone)
        required = [full_name, phone, country, city, language, timezone, tc_name, tc_relation, tc_reach]
        if not all(required):
            error = "Please complete all required fields."
        else:
            # merge profile
            info.setdefault("profile", {})
            info["profile"].update({
                "full_name": full_name,
                "phone": phone,
                "country": country,
                "city": city,
                "language": language,
                "timezone": timezone,
                "consents": {"privacy": True, "communication": {"transactional": True, "promotional": False}}
            })
            # trusted contact list ensure + append one
            info.setdefault("trusted_contacts", [])
            next_id = (max([c.get("id", 0) for c in info["trusted_contacts"]], default=0) + 1)
            info["trusted_contacts"].append({
                "id": next_id,
                "name": tc_name,
                "relation": tc_relation,
                "email": (tc_reach if "@" in tc_reach else None),
                "phone": (tc_reach if "@" not in tc_reach else None),
                "preferred_channel": tc_channel,
                "notify_scope": "sos_only",
                "confirmed": False
            })

            # persist
            users[username] = info
            with open(USERS_FILE, "w") as f:
                json.dump(users, f, indent=4)

            # end onboarding: sign-out to force clean login
            session.clear()
            return redirect(url_for("login"))

    return render_template(
        "onboarding.html",
        error=error,
        full_name=full_name, phone=phone, country=country, city=city,
        language=language, timezone=timezone
    )

@app.route("/add_trusted_contact", methods=["POST"])
def add_trusted_contact():
    if not session.get("loggedin"):
        return jsonify({"error": "Please login first"}), 401
    
    try:
        data = request.get_json()
        contact_name = data.get("name", "").strip()
        contact_email = data.get("email", "").strip()
        contact_phone = data.get("phone", "").strip()
        
        if not contact_name or not contact_email:
            return jsonify({"error": "Name and email are required"}), 400
        
        if not is_valid_email(contact_email):
            return jsonify({"error": "Please enter a valid email address"}), 400
        
        username = session.get("username")
        users = load_users()
        
        if "trusted_contacts" not in users[username]:
            users[username]["trusted_contacts"] = []
        
        new_contact = {
            "id": len(users[username]["trusted_contacts"]) + 1,
            "name": contact_name,
            "email": contact_email,
            "phone": contact_phone
        }
        users[username]["trusted_contacts"].append(new_contact)
        
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, indent=4)
        
        return jsonify({
            "message": "Trusted contact added successfully",
            "contact": new_contact
        }), 200
        
    except Exception as e:
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
        users = load_users()
        
        if "trusted_contacts" in users[username]:
            users[username]["trusted_contacts"] = [
                c for c in users[username]["trusted_contacts"] 
                if c.get("id") != contact_id
            ]
        
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, indent=4)
        
        return jsonify({"message": "Trusted contact removed successfully"}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/sos")
def sos():
    return render_template("sos.html")

@app.route("/map")
def show_map():
    reports = load_reports()
    return render_template("map.html", reports=reports)

@app.route("/resources")
def resources():
    return render_template("resources.html")

# ======= APIs =======
@app.route("/send_sos", methods=["POST"])
def send_sos():
    try:
        if not session.get("loggedin"):
            return jsonify({"error": "Please login first"}), 401
        
        sos_data = request.get_json()
        print("Received SOS from", session.get("username"), sos_data)
        
        # Here you would send emails/SMS to trusted contacts
        # For now, just log it
        
        return jsonify({"message": "SOS alert sent successfully", "status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/submit_report", methods=["POST"])
def submit_report():
    try:
        if not session.get("loggedin"):
            return jsonify({"error": "Please login first"}), 401
        
        report_data = request.get_json()
        reports = load_reports()
        
        new_report = {
            "id": len(reports) + 1,
            "username": session.get("username"),
            "lat": report_data.get("lat"),
            "lng": report_data.get("lng"),
            "category": report_data.get("category"),
            "description": report_data.get("description", ""),
            "timestamp": datetime.now().isoformat(),
        }
        reports.append(new_report)
        save_reports(reports)
        
        return jsonify({
            "message": "Report submitted successfully", 
            "status": "ok", 
            "report_id": new_report["id"]
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reports")
def get_reports_api():
    reports = load_reports()
    return jsonify(reports)

# Initialize data files
init_json_files()

# ======= Boot =======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)