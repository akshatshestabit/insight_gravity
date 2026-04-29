from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://insightforge:insightforge@localhost:5432/insightforge"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "insightforge"

    # Uploads
    UPLOAD_DIR: str = "/tmp/insightforge_uploads"

    # Day 2 — Vector store
    QDRANT_URL: str = "http://localhost:6333"
    EMBEDDING_MODEL: str = "gemini-embedding-001"   # 768-dim, task_type support

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
