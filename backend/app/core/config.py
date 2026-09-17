from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ArtesaNFC API"
    app_env: str = "local"
    debug: bool = False

    database_url: str = "postgresql://artesanfc:artesanfc@localhost:5432/artesanfc"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
