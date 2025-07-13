import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server Configuration
    BACKEND_HOST: str = Field(default="0.0.0.0", description="Backend host address")
    BACKEND_PORT: int = Field(default=8000, description="Backend port")
    FRONTEND_PORT: int = Field(default=5173, description="Frontend port")
    DEBUG: bool = Field(default=True, description="Debug mode")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # Security
    SECRET_KEY: str = Field(
        default="dev-secret-key", description="Secret key for security"
    )
    CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Allowed CORS origins",
    )

    # Camera Configuration
    OAK_CAMERA_IP: str = Field(
        default="192.168.1.100", description="Default Oak camera IP"
    )
    DEFAULT_FPS: int = Field(default=30, description="Default camera FPS")
    MAX_RECORDING_SIZE: int = Field(
        default=10485760, description="Max recording size in KB"
    )

    # Storage Configuration
    RECORDINGS_PATH: str = Field(
        default="./recordings", description="Path to store recordings"
    )
    MAX_FILE_SIZE: int = Field(default=2147483648, description="Max file size in bytes")
    AUTO_CLEANUP_DAYS: int = Field(
        default=7, description="Auto cleanup recordings after days"
    )

    # Streaming Configuration
    STREAM_BUFFER_SIZE: int = Field(
        default=1024 * 1024, description="Stream buffer size"
    )
    MAX_CONCURRENT_STREAMS: int = Field(default=5, description="Max concurrent streams")

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Allow extra fields from .env


# Create global settings instance
settings = Settings()
