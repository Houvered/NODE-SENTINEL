import os
from pathlib import Path


class Settings:
    PROJECT_NAME = "NODE SENTINEL"
    API_V1_STR = "/api"
    BASE_DIR = str(Path(__file__).resolve().parent.parent)

    # Security & Auth Settings
    SECRET_KEY = os.environ.get("SECRET_KEY", "nodesentinel_insecure_dev_key_change_in_prod_99f81a")
    AUTH_TOKEN_EXPIRE_MINUTES = int(os.environ.get("AUTH_TOKEN_EXPIRE_MINUTES", "480"))  # 8 hours
    USERS_FILE = os.environ.get("USERS_FILE", str(Path(BASE_DIR) / "sample_data" / "users.json"))
    AUDIT_FILE = os.environ.get("AUDIT_FILE", str(Path(BASE_DIR) / "sample_data" / "audit_log.json"))
    CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]


settings = Settings()

