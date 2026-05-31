"""Tests for fail-fast secret validation (F8).

Constructing Settings with init kwargs overrides env/.env, so these exercise the
validator directly. Development keeps defaults; any other env must supply strong
secrets or the process refuses to start.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, DEFAULT_JWT_SECRET

STRONG_JWT = "x" * 48
STRONG_MINIO = dict(minio_access_key="real-access", minio_secret_key="real-secret-value")


def test_development_allows_defaults():
    # The default config must boot in development without complaint.
    s = Settings(app_env="development", jwt_secret=DEFAULT_JWT_SECRET)
    assert s.app_env == "development"


def test_production_rejects_default_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret=DEFAULT_JWT_SECRET, **STRONG_MINIO)


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret="too-short", **STRONG_MINIO)


def test_production_rejects_default_minio_credentials():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            jwt_secret=STRONG_JWT,
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
        )


def test_production_boots_with_strong_secrets():
    s = Settings(app_env="production", jwt_secret=STRONG_JWT, **STRONG_MINIO)
    assert s.app_env == "production"
