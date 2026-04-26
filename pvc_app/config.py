import os
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Fail loudly in production if SECRET_KEY is not set
    _secret = os.environ.get("SECRET_KEY", "change-me-in-prod")
    SECRET_KEY = _secret if _secret else "dev-only-insecure-key"

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://root:@localhost/pvc_db2"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Registration: set to False in production to disable public sign-up
    ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "false").lower() == "true"

    # GST rate used in freight calculations
    GST_RATE = float(os.environ.get("GST_RATE", "0.18"))


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    ALLOW_REGISTRATION = True   # allow in dev by default


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False

    def __init__(self):
        # Hard-crash if secrets are missing in production
        if not os.environ.get("SECRET_KEY"):
            sys.exit(
                "[FATAL] SECRET_KEY environment variable must be set in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )


config_by_name = {
    "dev": DevelopmentConfig,
    "prod": ProductionConfig,
}
