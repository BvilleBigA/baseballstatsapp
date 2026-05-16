import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key-change-in-production")
    # Bump or set STATENTRY_ASSET_VERSION in production if CDN/browser caches GWT assets aggressively.
    STATENTRY_ASSET_VERSION = os.environ.get("STATENTRY_ASSET_VERSION", "").strip() or None
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(basedir, 'instance', 'baseball.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(basedir, "uploads")
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB for GWT saveboxscore (large game blobs)
