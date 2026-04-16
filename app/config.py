from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    openai_api_key: str
    groq_api_key: str
    redis_url: str = "redis://localhost:6379"
    upload_dir: str = "./uploads"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    llm_model: str = "llama-3.3-70b-versatile"


settings = Settings()