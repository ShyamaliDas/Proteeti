from flask import Flask

def create_app():
    app = Flask(__name__)
    app.secret_key = "your_secret_key_here"

    # Import routes
    from .routes import main
    app.register_blueprint(main)

    return app
