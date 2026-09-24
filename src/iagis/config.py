"""Configuração validada exclusivamente a partir do ambiente."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=True)

    glpi_url: str = Field(alias="GLPI_URL")
    glpi_app_token: SecretStr = Field(alias="GLPI_APP_TOKEN")
    glpi_user_token: SecretStr = Field(alias="GLPI_USER_TOKEN")
    ai_provider: str = Field(default="ollama", alias="AI_PROVIDER")
    operation_mode: str = Field(default="suggestion", alias="IAGIS_MODE")
    ai_model: str = Field(
        default="qwen3:4b-instruct-2507-q4_K_M",
        validation_alias=AliasChoices("AI_MODEL", "OPENAI_MODEL"),
    )
    gemini_api_key: SecretStr = Field(default=SecretStr(""), alias="GEMINI_API_KEY")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    ollama_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_URL")
    ollama_timeout: float = Field(default=120, gt=0, le=600, alias="OLLAMA_TIMEOUT")
    max_attempts: int = Field(default=3, ge=1, le=10, alias="IAGIS_MAX_ATTEMPTS")
    retry_delay: int = Field(default=300, ge=5, le=86400, alias="IAGIS_RETRY_DELAY")
    admin_user: str = Field(default="iagis", alias="IAGIS_ADMIN_USER")
    admin_password: SecretStr = Field(default=SecretStr(""), alias="IAGIS_ADMIN_PASSWORD")
    admin_host: str = Field(default="127.0.0.1", alias="IAGIS_ADMIN_HOST")
    admin_port: int = Field(default=8090, ge=1024, le=65535, alias="IAGIS_ADMIN_PORT")
    mention: str = Field(default="@IAgis", alias="IAGIS_MENTION")
    dry_run: bool = Field(default=True, alias="IAGIS_DRY_RUN")
    poll_interval: int = Field(default=60, ge=5, alias="IAGIS_POLL_INTERVAL")
    database_path: Path = Field(default=Path("data/iagis.db"), alias="IAGIS_DATABASE_PATH")
    allowed_entity_ids: str = Field(default="", alias="IAGIS_ALLOWED_ENTITY_IDS")
    auto_publish_verdicts: str = Field(
        default="HOMOLOGADO,HOMOLOGADO_COM_RESTRICOES",
        alias="IAGIS_AUTO_PUBLISH_VERDICTS",
    )
    glpi_user_id: int | None = Field(default=None, alias="IAGIS_GLPI_USER_ID")
    entity_id: int | None = Field(default=None, alias="IAGIS_ENTITY_ID")
    glpi_initial_lookback_hours: int = Field(
        default=24, ge=1, le=24 * 30, alias="IAGIS_GLPI_INITIAL_LOOKBACK_HOURS"
    )
    glpi_poll_overlap_seconds: int = Field(
        default=120, ge=0, le=3600, alias="IAGIS_GLPI_POLL_OVERLAP_SECONDS"
    )
    vpn_enabled: bool = Field(default=False, alias="IAGIS_VPN_ENABLED")
    vpn_broker_url: str = Field(
        default="http://127.0.0.1:8091", alias="IAGIS_VPN_BROKER_URL"
    )
    vpn_broker_token: SecretStr = Field(default=SecretStr(""), alias="IAGIS_VPN_BROKER_TOKEN")
    vpn_action_min_confidence: float = Field(
        default=0.80, ge=0.5, le=1, alias="IAGIS_VPN_ACTION_MIN_CONFIDENCE"
    )
    vpn_max_event_age_minutes: int = Field(
        default=30, ge=5, le=24 * 60, alias="IAGIS_VPN_MAX_EVENT_AGE_MINUTES"
    )

    @field_validator("glpi_url")
    @classmethod
    def secure_glpi_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith("https://"):
            raise ValueError("GLPI_URL deve usar HTTPS")
        return value

    @field_validator("mention", "ai_model")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("valor não pode ser vazio")
        return value.strip()

    @field_validator("ai_provider")
    @classmethod
    def supported_provider_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"gemini", "openai", "anthropic", "ollama"}:
            raise ValueError("AI_PROVIDER deve ser gemini, openai, anthropic ou ollama")
        return normalized

    @field_validator("operation_mode")
    @classmethod
    def supported_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"suggestion", "governance"}:
            raise ValueError("IAGIS_MODE deve ser suggestion ou governance")
        return normalized

    @model_validator(mode="after")
    def provider_credentials(self) -> Settings:
        if self.ai_provider == "gemini" and not self.gemini_api_key.get_secret_value():
            raise ValueError("GEMINI_API_KEY é obrigatória para AI_PROVIDER=gemini")
        if self.ai_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY é obrigatória para AI_PROVIDER=openai")
        if not self.dry_run and self.glpi_user_id is None:
            raise ValueError("IAGIS_GLPI_USER_ID é obrigatório quando IAGIS_DRY_RUN=false")
        if not self.dry_run and self.operation_mode != "suggestion":
            raise ValueError("produção de atendimento exige IAGIS_MODE=suggestion")
        if not self.dry_run and not self.authorized_entities:
            raise ValueError("IAGIS_ALLOWED_ENTITY_IDS é obrigatória em produção")
        if not self.dry_run and (self.entity_id is None or self.entity_id not in self.authorized_entities):
            raise ValueError("IAGIS_ENTITY_ID deve pertencer a IAGIS_ALLOWED_ENTITY_IDS em produção")
        if self.vpn_enabled and not self.vpn_broker_token.get_secret_value():
            raise ValueError("IAGIS_VPN_BROKER_TOKEN é obrigatório quando IAGIS_VPN_ENABLED=true")
        if self.vpn_enabled:
            parsed = urlparse(self.vpn_broker_url)
            loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
                raise ValueError("IAGIS_VPN_BROKER_URL deve usar HTTPS ou HTTP no loopback")
        return self

    @property
    def authorized_entities(self) -> frozenset[int]:
        return frozenset(int(item.strip()) for item in self.allowed_entity_ids.split(",") if item.strip())

    @property
    def authorized_auto_verdicts(self) -> frozenset[str]:
        return frozenset(item.strip() for item in self.auto_publish_verdicts.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
