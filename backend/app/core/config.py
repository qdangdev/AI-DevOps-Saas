"""Application settings loaded from environment.

All config goes through here — no module reads os.environ directly.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    # Comma-separated origins for CORS, e.g. "http://localhost:5173,https://app.example.com"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- URLs ---
    backend_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"

    # --- Database / cache ---
    database_url: PostgresDsn
    redis_url: RedisDsn

    # --- Auth / crypto ---
    jwt_secret: str  # rotate via env, sign HS256
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30
    # Fernet key (urlsafe base64 32 bytes). Generate via:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str

    # --- GitHub OAuth ---
    github_client_id: str
    github_client_secret: str
    github_oauth_scopes: str = "read:user user:email repo"

    # --- LLM ---
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"

    @property
    def github_redirect_uri(self) -> str:
        return f"{self.backend_base_url}{self.api_v1_prefix}/auth/github/callback"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
