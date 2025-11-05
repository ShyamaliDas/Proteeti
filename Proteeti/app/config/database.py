# import os
# from dotenv import load_dotenv

# load_dotenv()

# class Config:
#     # psycopg3 uses the same connection string format
#     SQLALCHEMY_DATABASE_URI = os.getenv(
#         'DATABASE_URL',
#         'postgresql://neondb_owner:npg_6woGaTPdJ9NR@ep-gentle-mountain-ahafls0b.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require'
#     )
#     SQLALCHEMY_TRACK_MODIFICATIONS = False
#     SECRET_KEY = os.getenv("SECRET_KEY", "proteeti_secret_key_2025")



import os
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()

def init_db(app):
    db.init_app(app)
    migrate.init_app(app, db)

load_dotenv()

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql://neondb_owner:npg6woGaTPdJ9NRep-gentle-mountain-ahafls0b@c-3.us-east-1.aws.neon.tech/neondb?sslmode=require"
    )
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "pool_recycle": 3600, 
        "pool_pre_ping": True, 
        "connect_args": {
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    }
    
    SECRET_KEY = os.getenv("SECRET_KEY", "proteeti_secret_key_2025")
