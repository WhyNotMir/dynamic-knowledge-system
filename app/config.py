from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    database_url: str
    openai_api_key: str
    groq_api_key: str

    redis_url: str
    upload_dir: str

    cors_allowed_origins: list[str]

    embedding_model: str
    embedding_dimensions: int
    llm_model: str

    # ---- Agent framework (Phase 0 Slice B) ---------------------------
    # Tracing is opt-in. Leave `langsmith_tracing` = False in dev/test so
    # we never make an accidental outbound call. When True, every
    # LangChain chain automatically streams spans to LangSmith.
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "dks-dev"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # Temperature for the structure/title agent. Lower = more
    # deterministic. Overridable per-environment without a code change.
    agent_temperature: float = 0.3

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
