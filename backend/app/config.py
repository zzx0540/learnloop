from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://learnloop:learnloop@pg:5432/learnloop"
    static_dir: str = "/srv/static"
    files_dir: str = "/data/files"

    cookie_secure: bool = False
    session_ttl_days: int = 30

    admin_username: str = "admin"
    admin_password: str = ""
    initial_invite_code: str = ""

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    high_base_url: str = ""
    high_api_key: str = ""
    mineru_api_key: str = ""
    mineru_base_url: str = "https://mineru.net"
    model_low: str = "deepseek-v4-flash"
    model_medium: str = "deepseek-v4-pro"
    model_high: str = "gpt-5.6"
    model_vision: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
