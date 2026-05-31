"""Application configuration loaded from environment variables."""

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field, model_validator

DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string"
MIN_JWT_SECRET_LEN = 32


class TierLimits:
    TIERS = {
        "free": {
            "requests_per_minute": 100,
            "documents_per_month": 50,
            "storage_gb": 1,
            "max_collections": 5,
            "concurrent_queries": 2,
            "models": ["gpt-4o-mini"],
        },
        "pro": {
            "requests_per_minute": 60,
            "documents_per_month": 1000,
            "storage_gb": 50,
            "max_collections": 50,
            "concurrent_queries": 10,
            "models": ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet-20241022"],
        },
        "enterprise": {
            "requests_per_minute": 300,
            "documents_per_month": -1,  # unlimited
            "storage_gb": 500,
            "max_collections": -1,
            "concurrent_queries": 50,
            "models": ["*"],
        },
    }

    @classmethod
    def get(cls, tier: str) -> dict:
        return cls.TIERS.get(tier, cls.TIERS["free"])


class Settings(BaseSettings):
    # App
    app_name: str = "AgentRAG"
    app_env: str = "development"
    app_debug: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:5173"
    max_upload_size_mb: int = 100
    log_level: str = "INFO"

    # Brute-force protection: stricter, IP-keyed limit for unauthenticated auth
    # endpoints (login/register/refresh), independent of tenant tier.
    auth_rate_limit_per_minute: int = 10
    # Only trust X-Forwarded-For / X-Real-IP when running behind a proxy you control
    # (e.g. an ingress/LB). If false, the IP comes from the direct socket so clients
    # cannot spoof their identity to evade IP rate limits.
    trust_proxy_headers: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://agentrag:agentrag@localhost:5432/agentrag"
    database_sync_url: str = "postgresql://agentrag:agentrag@localhost:5432/agentrag"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "agentrag"
    minio_use_ssl: bool = False

    # JWT
    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # OAuth2
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    github_client_id: Optional[str] = None
    github_client_secret: Optional[str] = None
    oauth_redirect_base: str = "http://localhost:3000/auth/callback"

    # LLM
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None

    # Model defaults
    default_embedding_model: str = "text-embedding-3-small"
    default_embedding_dimensions: int = 1536
    default_llm_model: str = "gpt-4o"
    default_reranker_model: str = "rerank-english-v3.0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _reject_insecure_defaults_outside_dev(self):
        """Fail fast at startup if running outside development with default secrets.

        A weak/default JWT secret lets anyone forge tokens; default object-store
        credentials expose every stored document. Better to crash loudly on boot than
        to run silently insecure. Development keeps the convenient defaults.
        """
        if self.app_env == "development":
            return self

        problems = []
        if self.jwt_secret == DEFAULT_JWT_SECRET:
            problems.append("JWT_SECRET is still the default placeholder")
        elif len(self.jwt_secret) < MIN_JWT_SECRET_LEN:
            problems.append(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LEN} characters")
        if self.minio_access_key == "minioadmin" or self.minio_secret_key == "minioadmin":
            problems.append("MinIO credentials are still the default 'minioadmin'")

        if problems:
            raise ValueError(
                f"Insecure configuration for APP_ENV={self.app_env!r}: "
                + "; ".join(problems)
                + ". Set strong values in the environment, or use APP_ENV=development locally."
            )
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
