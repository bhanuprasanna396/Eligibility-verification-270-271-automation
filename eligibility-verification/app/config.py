from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    postgres_user: str = "eligibility_user"
    postgres_password: str = "eligibility_pass"
    postgres_db: str = "eligibility_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Clearinghouse
    clearinghouse_base_url: str = "https://sandbox.availity.com"
    clearinghouse_client_id: str = ""
    clearinghouse_client_secret: str = ""

    # Clinic EDI identifiers
    clinic_npi: str = "1234567890"
    clinic_tax_id: str = "123456789"
    clinic_edi_id: str = "YOURCLINIC"
    clearinghouse_edi_id: str = "CLEARINGHOUSE"

    # Business logic
    eligibility_check_days_before: int = 3

    # Security — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    phi_encryption_key: str = ""

    # App
    app_env: str = "development"
    secret_key: str = "change-this-in-production"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
