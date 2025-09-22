from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'proteeti_secret_key_2025'  # Change this for production!

# File paths for data storage
USERS_FILE = 'data/users.json'
REPORTS_FILE = 'data/reports.json'

# Ensure data directory exists
os.makedirs('data', exist_ok=True)

def load_users():
    """Load users from JSON file"""
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    """Save users to JSON file"""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

def load_reports():
    """Load reports from JSON file"""
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_reports(reports):
    """Save reports to JSON file"""
    with open(REPORTS_FILE, 'w') as f:
        json.dump(reports, f, indent=4)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        users = load_users()
        
        # Check if user exists and password matches
        if username in users and users[username]['password'] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid username or password")
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        if not username or not password:
            return render_template('register.html', error="Username and password are required")
        
        if password != confirm_password:
            return render_template('register.html', error="Passwords do not match")
        
        if len(password) < 3:
            return render_template('register.html', error="Password must be at least 3 characters")
        
        users = load_users()
        
        if username in users:
            return render_template('register.html', error="Username already exists")
        
        # Save new user
        users[username] = {
            'password': password,
            'created_at': datetime.now().isoformat()
        }
        save_users(users)
        
        # Auto-login after registration
        session['logged_in'] = True
        session['username'] = username
        return redirect(url_for('index'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/sos')
def sos():
    return render_template('sos.html')

@app.route('/map')
def show_map():
    # Load existing reports to display on the map
    reports = load_reports()
    return render_template('map.html', reports=reports)

@app.route('/resources')
def resources():
    return render_template('resources.html')

# API Routes
@app.route('/send_sos', methods=['POST'])
def send_sos():
    try:
        if not session.get('logged_in'):
            return jsonify({"error": "Please login first"}), 401
            
        sos_data = request.get_json()
        
        # Log the SOS alert (you can save this to a file too)
        print("🔴 SOS ALERT RECEIVED:")
        print(f"User: {session.get('username')}")
        print(f"Location: {sos_data.get('lat')}, {sos_data.get('lng')}")
        print(f"Time: {datetime.now().isoformat()}")
        print("--- In a real app, this would send SMS/Email to trusted contacts ---")
        
        return jsonify({
            "message": "SOS alert sent successfully", 
            "status": "ok",
            "user": session.get('username')
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/submit_report', methods=['POST'])
def submit_report():
    try:
        if not session.get('logged_in'):
            return jsonify({"error": "Please login first"}), 401
            
        report_data = request.get_json()
        
        # Create new report
        new_report = {
            'id': len(load_reports()) + 1,
            'username': session.get('username'),
            'lat': report_data.get('lat'),
            'lng': report_data.get('lng'),
            'category': report_data.get('category'),
            'description': report_data.get('description', ''),
            'timestamp': datetime.now().isoformat()
        }
        
        # Save report
        reports = load_reports()
        reports.append(new_report)
        save_reports(reports)
        
        print("📍 NEW HAZARD REPORT SAVED:")
        print(f"User: {new_report['username']}")
        print(f"Location: {new_report['lat']}, {new_report['lng']}")
        print(f"Category: {new_report['category']}")
        
        return jsonify({
            "message": "Report submitted successfully", 
            "status": "ok",
            "report_id": new_report['id']
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# API route to get reports as JSON
@app.route('/api/reports')
def get_reports_api():
    reports = load_reports()
    return jsonify(reports)

if __name__ == '__main__':
    # Create initial data files if they don't exist
    if not os.path.exists(USERS_FILE):
        save_users({})
    if not os.path.exists(REPORTS_FILE):
        save_reports([])
    
    app.run(debug=True)