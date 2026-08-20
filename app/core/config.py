"""
Central configuration. Every environment variable the app needs is declared
here, typed, with sane local-dev defaults. Nothing else in the codebase
should call os.environ directly — always go through `get_settings()`.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "sqlite+aiosqlite:///./plant_companion.db"

    # Session JWT
    jwt_secret_key: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 43200

    # Billing — Razorpay Subscriptions, sold on the VANYA website (NOT
    # Google Play Billing / not RevenueCat — deliberately removed; see
    # billing_service.py's module docstring for the Google Play "reader
    # app" / consumption-only compliance reasoning behind this). key_id is
    # public (also used client-side by the website's Razorpay Checkout.js);
    # key_secret is server-side only, used for Basic Auth on Razorpay's
    # REST API (creating subscriptions) — never send it to any client.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    # Set the SAME value in Razorpay Dashboard > Account & Settings >
    # Webhooks > Secret. Razorpay signs every webhook payload with this,
    # verified here via HMAC-SHA256 (see razorpay_client.verify_webhook_signature).
    razorpay_webhook_secret: str = "change-me-to-a-long-random-string"

    # Firebase Authentication — unifies Google + Apple (+ any other
    # provider configured in the Firebase console) sign-in verification.
    # firebase_service_account_json (raw JSON contents) wins when set —
    # see firebase_auth.py's module docstring; the _path variant is the
    # local-dev fallback.
    firebase_service_account_json_path: str = "./secrets/firebase_service_account.json"
    firebase_service_account_json: str = ""

    # AI Vision provider
    gemini_api_key: str = ""
    ai_model: str = "gemini-3.5-flash-lite"

    # Photo storage — "local" (dev only, see storage.py's module docstring
    # on why it's unsafe on most production hosts) or "s3" (any
    # S3-compatible provider: Cloudflare R2, AWS S3, Backblaze B2, DO
    # Spaces — same API, just a different endpoint_url).
    storage_backend: str = "local"
    local_storage_dir: str = "./uploads"
    public_base_url: str = "http://localhost:8000"

    # S3-compatible storage — only used when storage_backend="s3".
    s3_endpoint_url: str = ""  # e.g. https://<account_id>.r2.cloudflarestorage.com for R2; leave blank for real AWS S3
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"  # R2 uses "auto"; AWS S3 needs a real region like "ap-south-1"
    # The URL prefix photos are actually served from — a public R2/S3
    # bucket URL, or (better, for a real launch) a CDN/custom domain
    # sitting in front of the bucket. Photo URLs become f"{s3_public_url_base}/{key}".
    s3_public_url_base: str = ""

    # Daily AI-provider call cap — an infra cost safety net, NOT a
    # subscription-tier value. Distinct from the per-tier weekly/monthly
    # identification/calculator/diagnose allowances, which all live in
    # app/core/plans.py (the single source of truth for pricing/limits —
    # see that file's docstring). This just protects the Gemini bill from
    # a runaway client regardless of which tier is calling.
    ai_daily_call_limit: int = 10

    # CORS
    cors_origins: str = "http://localhost:5173,capacitor://localhost,http://localhost"

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached so .env is only parsed once per process."""
    return Settings()
