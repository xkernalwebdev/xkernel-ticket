import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-change-in-prod'
    MONGO_URI = os.environ.get('MONGO_URI')  # Your Atlas string
    EMAIL_USER = os.environ.get('EMAIL_USER')
    EMAIL_PASS = os.environ.get('EMAIL_PASS')
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
