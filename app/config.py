import os
from dataclasses import dataclass, field


def _env(name, default):
    return os.getenv(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./support.db"))
    payment_url: str = field(default_factory=lambda: _env("PAYMENT_URL", "http://127.0.0.1:8001"))
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-secret-change-me-dev-secret-change-me"))
    dev_mode: bool = field(default_factory=lambda: _env("DEV_MODE", "1") == "1")
    approval_ttl_hours: int = 24
    shipping_down: bool = False  # flip to simulate a shipping API outage
