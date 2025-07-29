from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET")

    SECRET_KEY: str = os.getenv("SECRET_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    CALLBACK_BASE_URI: str = os.getenv("CALLBACK_BASE_URI")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    LOGOUT_REDIRECT: str = os.getenv("LOGOUT_REDIRECT")
    AUTH_SUCCESS_REDIRECT: str = os.getenv("AUTH_SUCCESS_REDIRECT")

settings = Settings()