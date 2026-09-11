from pathlib import Path


class Settings:
    PROJECT_NAME = "NODE SENTINEL"
    API_V1_STR = "/api"
    BASE_DIR = str(Path(__file__).resolve().parent.parent)


settings = Settings()
