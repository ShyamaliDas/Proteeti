import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # psycopg3 uses the same connection string format
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL',
        'postgresql://neondb_owner:npg_6woGaTPdJ9NR@ep-gentle-mountain-ahafls0b.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "proteeti_secret_key_2025")
