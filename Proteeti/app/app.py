from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_dance.contrib.google import make_google_blueprint, google
import json, os, re, random, requests, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "proteeti_secret_key_2025"  

# ======= Config / Paths =======
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
REPORTS_FILE = os.path.join(DATA_DIR, "reports.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ======= Google OAuth =======
app.config["GOOGLE_OAUTH_CLIENT_ID"] = "client_ID"
app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = "client_Secret"
google_bp = make_google_blueprint(
    client_id=app.config["GOOGLE_OAUTH_CLIENT_ID"],
    client_secret=app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email",
    ],
)
app.register_blueprint(google_bp, url_prefix="/login")

# ======= Storage utils =======
def init_json_files():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump({}, f)
    if not os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "w") as f:
            json.dump([], f)

def load_users():
    with open(USERS_FILE, "r") as f:
        return json.load(f)

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
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_reports(reports):
    with open(REPORTS_FILE, "w") as f:
        json.dump(reports, f, indent=4)

# ======= Email validation =======
def is_valid_email(email):
    return re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email)

MAILBOXLAYER_KEY = "mailboxlayer key"

def mailboxlayer_check(email):
    url = f"http://apilayer.net/api/check?access_key={MAILBOXLAYER_KEY}&email={email}&smtp=1&format=1"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        
        if not data.get("success", True) and "error" in data:
            print(f"Mailboxlayer API error: {data['error']}")
            return False, "Email verification service is unavailable"
        
        if not data.get("format_valid", False):
            return False, "Email format is invalid."
        if not data.get("mx_found", False):
            return False, "Email domain has no MX records."
        if not data.get("smtp_check", False):
            return False, "Email address does not exist or cannot receive mail."
        if data.get("disposable", False):
            return False, "Disposable/temporary email addresses are not allowed."
        return True, "Email is valid."
        
    except requests.Timeout:
        print("Mailboxlayer API timeout")
        return False, "Email verification service timed out"
    except Exception as e:
        print(f"Mailboxlayer error: {e}")
        return False, f"Email verification service error: {e}"


# ======= 6-digit code =======
def generate_verification_code():
    return str(random.randint(100000, 999999))

GMAIL_SENDER = "proteeti39@gmail.com"  
GMAIL_APP_PASSWORD = "gmail app password" 

def send_verification_code(email, code):
    if DEV_MODE:
        return True
    
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        raise Exception("Gmail SMTP not configured")
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = f"Proteeti <{GMAIL_SENDER}>"
        msg['To'] = email
        msg['Subject'] = "Proteeti"
        
        body = f"""Hello,

Your verification code is: {code}

This code will expire in 10 minutes.

Thank you,
Proteeti Team
"""
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))  
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        raise Exception(f"Gmail SMTP error: {str(e)}")

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
        try:
            send_verification_code(email, code)
        except Exception as e:
            error = f"Could not initiate verification: {e}"
            return render_template("register.html", error=error)

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
    from flask import request
    print("Callback received at:", request.url)

    if not google.authorized:
        return redirect(url_for("login"))

    userinfo = google.get("/oauth2/v2/userinfo").json()
    google_sub = userinfo.get("sub")
    email = userinfo.get("email")
    name = userinfo.get("name")

    code = generate_verification_code()
    try:
        send_verification_code(email, code)
    except Exception as e:
        return render_template("login.html", error=f"Could not initiate verification: {e}")

    session["pending_user"] = {
        "flow": "google",
        "username": google_sub,
        "email": email,
        "password": None,
        "name": name,
        "code": code,
        "created_at": datetime.now().isoformat(),
    }
    return redirect(url_for("verify_email"))

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
            save_user_record(pending["username"], pending["email"], pending["password"], verified=True)
            
            session.clear()
            
            session["loggedin"] = True
            session["username"] = pending.get("name") or pending["username"]
            
            session.modified = True
            
            print("Session set - loggedin:", session.get("loggedin"), "username:", session.get("username"))
            
            return redirect(url_for("index"))
        else:
            error = "Verification code incorrect."
    return render_template("verify_email.html", error=error, show_code=show_code, code=(pending["code"] if show_code else None))

@app.route("/account")
def account():
    if not session.get("loggedin"):
        return redirect(url_for("login"))
    
    username = session.get("username")
    users = load_users()
    
    # Find user data
    user_data = None
    for uname, info in users.items():
        if uname == username or info.get("email") == username:
            user_data = {
                "username": uname,
                "email": info.get("email"),
                "trusted_contacts": info.get("trusted_contacts", [])
            }
            break
    
    if not user_data:
        return redirect(url_for("logout"))
    
    return render_template("account.html", user=user_data)


@app.route("/add_trusted_contact", methods=["POST"])
def add_trusted_contact():
    
    try:
        data = request.get_json()
        contact_name = data.get("name", "").strip()
        contact_email = data.get("email", "").strip()
        contact_phone = data.get("phone", "").strip()
        
        if contact_email and not is_valid_email(contact_email):
            return jsonify({"error": "Please enter a valid email address"}), 400
    
        ok, msg = mailboxlayer_check(contact_email)
        if not ok:
            return jsonify({"error": "Please enter a valid email address"}), 400
        
       
        username = session.get("username")
        users = load_users()

        new_contact = {
            "id": len(users[username]["trusted_contacts"]) + 1,
            "name": contact_name,
            "email": contact_email,
            "phone": contact_phone
        }
        users[username]["trusted_contacts"].append(new_contact)
        
        # Save
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
        
        print(f"DEBUG: Trying to remove contact_id: {contact_id}, type: {type(contact_id)}")  # DEBUG
        
        if contact_id is None:
            return jsonify({"error": "Contact ID required"}), 400
        
        try:
            contact_id = int(contact_id)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid contact ID"}), 400
        
        username = session.get("username")
        users = load_users()
        
        
      
        if "trusted_contacts" in users[username]:
            original_count = len(users[username]["trusted_contacts"])
            users[username]["trusted_contacts"] = [
                c for c in users[username]["trusted_contacts"] 
                if c.get("id") != contact_id
            ]
            new_count = len(users[username]["trusted_contacts"])
            print(f"DEBUG: Removed {original_count - new_count} contacts")  # DEBUG
        
        print(f"DEBUG: After removal: {users[username].get('trusted_contacts', [])}")  # DEBUG
        
        # Save
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
        print("🔴 SOS from", session.get("username"), sos_data)
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
        return jsonify({"message": "Report submitted successfully", "status": "ok", "report_id": new_report["id"]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/api/reports")
def get_reports_api():
    reports = load_reports()
    return jsonify(reports)

# Initialize data files (but they won't persist on Vercel)
if not os.path.exists(USERS_FILE):
    save_user_record({})
if not os.path.exists(REPORTS_FILE):
    save_reports([])

# ======= Boot =======
if __name__ == "__main__":
    init_json_files()
    app.run(host="0.0.0.0", port=5000, debug=True)
    

