"""Configuração validada exclusivamente a partir do ambiente."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    def provider_credentials(self) -> "Settings":
        if self.ai_provider == "gemini" and not self.gemini_api_key.get_secret_value():
            raise ValueError("GEMINI_API_KEY é obrigatória para AI_PROVIDER=gemini")
        if self.ai_provider == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY é obrigatória para AI_PROVIDER=openai")
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
