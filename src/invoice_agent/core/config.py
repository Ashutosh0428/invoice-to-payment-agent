from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "INVOICE_AGENT_"


class _Base(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


class DatabaseConfig(_Base):
    model_config = SettingsConfigDict(
        env_prefix=f"{ENV_PREFIX}DB_", env_file=".env", extra="ignore"
    )

    host: str = "localhost"
    port: int = 5432
    user: str = "invoice"
    password: SecretStr = SecretStr("invoice")
    name: str = "invoice_agent"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False

    @property
    def async_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def checkpoint_dsn(self) -> str:
        """LangGraph's Postgres checkpointer speaks psycopg3, not SQLAlchemy URLs."""
        return (
            f"postgresql://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}?sslmode=disable"
        )


class LLMConfig(_Base):
    model_config = SettingsConfigDict(
        env_prefix=f"{ENV_PREFIX}LLM_", env_file=".env", extra="ignore"
    )

    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    temperature: float = 0.0
    request_timeout: float = 180.0
    num_ctx: int = 8192
    max_extraction_retries: int = 2


class MailboxConfig(_Base):
    model_config = SettingsConfigDict(
        env_prefix=f"{ENV_PREFIX}MAILBOX_", env_file=".env", extra="ignore"
    )

    provider: Literal["local", "graph", "gmail"] = "local"
    poll_interval_seconds: int = 60
    max_messages_per_poll: int = 25
    ap_folder: str = "Inbox"
    ar_folder: str = "Inbox"
    processed_label: str = "processed"

    local_root: Path = Path("samples/inbox")

    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: SecretStr = SecretStr("")
    graph_user_principal_name: str = ""

    gmail_credentials_file: Path = Path("credentials/gmail_credentials.json")
    gmail_token_file: Path = Path("credentials/gmail_token.json")
    gmail_query: str = "has:attachment newer_than:30d"


class ERPConfig(_Base):
    model_config = SettingsConfigDict(
        env_prefix=f"{ENV_PREFIX}ERP_", env_file=".env", extra="ignore"
    )

    base_url: str = "http://localhost:8081"
    api_key: SecretStr = SecretStr("mock-erp-key")
    timeout: float = 30.0
    max_retries: int = 3
    company_code: str = "1000"
    default_currency: str = "EUR"


class MatchingConfig(_Base):
    model_config = SettingsConfigDict(
        env_prefix=f"{ENV_PREFIX}MATCH_", env_file=".env", extra="ignore"
    )

    price_tolerance_pct: Decimal = Decimal("2.0")
    price_tolerance_abs: Decimal = Decimal("5.00")
    quantity_tolerance_pct: Decimal = Decimal("0.0")
    quantity_tolerance_abs: Decimal = Decimal("0")
    total_tolerance_pct: Decimal = Decimal("1.0")
    total_tolerance_abs: Decimal = Decimal("10.00")
    tax_tolerance_abs: Decimal = Decimal("1.00")

    auto_post_ceiling: Decimal = Decimal("25000.00")
    min_extraction_confidence: float = 0.75
    vendor_match_threshold: int = 88
    duplicate_amount_tolerance_abs: Decimal = Decimal("0.01")

    remittance_tolerance_abs: Decimal = Decimal("2.00")

    @field_validator("price_tolerance_pct", "quantity_tolerance_pct", "total_tolerance_pct")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("tolerance percentages must be non-negative")
        return v


class ObservabilityConfig(_Base):
    model_config = SettingsConfigDict(
        env_prefix=f"{ENV_PREFIX}OBS_", env_file=".env", extra="ignore"
    )

    enabled: bool = True
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"
    project_name: str = "invoice-to-payment-agent"
    log_level: str = "INFO"
    json_logs: bool = False


class AppConfig(_Base):
    environment: Literal["local", "dev", "prod"] = "local"
    api_prefix: str = "/api/v1"
    api_token: SecretStr = SecretStr("local-dev-token")
    auth_enabled: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    document_store: Path = Path("data/documents")

    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mailbox: MailboxConfig = Field(default_factory=MailboxConfig)
    erp: ERPConfig = Field(default_factory=ERPConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
