from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Read once per process via ``get_settings``."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = Field(min_length=16)
    db_path: Path = Path("var/tickets.db")

    admin_username: str = Field(min_length=3, max_length=32)
    admin_password: str = Field(min_length=8)

    session_max_age: int = 60 * 60 * 12
    cookie_secure: bool = False

    login_max_attempts: int = 5
    login_window_seconds: int = 300
    trust_proxy: bool = False

    event_name: str = "School Party"
    event_date: str = "TBA"
    event_datetime: datetime = datetime.fromisoformat("2026-12-12T19:00:00+05:30")
    event_venue: str = "TBA"
    event_timezone: str = "Asia/Colombo"
    currency: str = "LKR"
    base_url: str = "http://localhost:8000"

    mail_mode: Literal["console", "smtp"] = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_use_starttls: bool = True
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "Party Tickets"
    smtp_from_email: str = ""

    @field_validator("db_path")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()
