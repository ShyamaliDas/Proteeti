import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # psycopg3 uses the same connection string format
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL',
        'postgresql+psycopg://postgres:PASSWORD@localhost:5432/proteeti_db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "proteeti_secret_key_2025")
