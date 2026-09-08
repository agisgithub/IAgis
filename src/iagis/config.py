"""Configuração validada exclusivamente a partir do ambiente."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=True)

    glpi_url: str = Field(alias="GLPI_URL")
    glpi_app_token: SecretStr = Field(alias="GLPI_APP_TOKEN")
    glpi_user_token: SecretStr = Field(alias="GLPI_USER_TOKEN")
    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    openai_model: str = Field(alias="OPENAI_MODEL")
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

    @field_validator("mention", "openai_model")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("valor não pode ser vazio")
        return value.strip()

    @property
    def authorized_entities(self) -> frozenset[int]:
        return frozenset(int(item.strip()) for item in self.allowed_entity_ids.split(",") if item.strip())

    @property
    def authorized_auto_verdicts(self) -> frozenset[str]:
        return frozenset(item.strip() for item in self.auto_publish_verdicts.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
