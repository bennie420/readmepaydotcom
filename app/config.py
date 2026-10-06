"""Application configuration settings using Pydantic Settings."""


from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PORT: int = 8080
    HOST: str = "0.0.0.0"
    DB_URL: str = "sqlite:///./badge_platform.db"
    GITHUB_TOKEN: str | None = None
    SALT: str = "os-badge-platform-secret-salt-2026"
    BASE_URL: str = "http://localhost:8080"
    ADMIN_SECRET_KEY: str = "os-admin-secret-key-prod-2026"
    ENABLE_DOCS: bool = False

    # GitHub OAuth Integration
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None

    # PayPal Integration
    PAYPAL_CLIENT_ID: str | None = None
    PAYPAL_CLIENT_SECRET: str | None = None
    PAYPAL_MODE: str = "sandbox"  # "sandbox" or "live"
    PAYPAL_WEBHOOK_ID: str | None = None


    # Crypto Integration (NOWPayments / Coinbase Commerce / Web3)
    CRYPTO_GATEWAY_API_KEY: str | None = None
    CRYPTO_WEBHOOK_SECRET: str | None = None
    CRYPTO_PAYOUT_WALLET: str | None = None


settings = Settings()

