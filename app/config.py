import os
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


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
    ENV = os.environ.get("ENV", os.environ.get("APP_ENV", "development")).lower()

    def validate(self) -> None:
        insecure_default = self.SECRET_KEY.startswith("nodesentinel_insecure")
        if insecure_default and self.ENV in ("prod", "production"):
            raise RuntimeError(
                "Refusing to boot with insecure default SECRET_KEY in production. "
                "Set SECRET_KEY env var (e.g. python -c \"import secrets; print(secrets.token_hex(32))\")"
            )
        if insecure_default:
            logger.warning("Using insecure dev SECRET_KEY — set SECRET_KEY env var before any production deployment.")
        if self.CORS_ORIGINS == ["*"]:
            logger.warning("CORS_ORIGINS=* is for local dev only — pin to frontend origin in production.")


settings = Settings()
settings.validate()

