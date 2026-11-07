from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://pedidos:pedidos@localhost:5432/pedidos"
    )
    api_token: SecretStr = Field(min_length=32)
    search_mode: Literal["lexical", "hybrid"] = "hybrid"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: str = "/tmp/pedidos-models"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver", "backend"]
    allowed_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
