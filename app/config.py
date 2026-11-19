"""Runtime configuration. Every value comes from an environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    corpus_dir: str
    manifest_path: str
    db_path: str
    llm_provider: str
    llm_model: str
    embeddings: str
    embedding_model: str
    model_cache_dir: str
    log_file: str
    log_level: str
    leave_year: int
    corpus_token_budget: int
    llm_timeout_s: float


DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-4.1-mini",
    "gemini": "gemini-2.5-flash",
    "fake": "fake",
}


def get_settings() -> Settings:
    provider = _env("LLM_PROVIDER", "anthropic").lower()
    return Settings(
        corpus_dir=_env("CORPUS_DIR", "./data/corpus"),
        manifest_path=_env("MANIFEST_PATH", "./data/manifest.json"),
        db_path=_env("DB_PATH", "./data/veltrix.db"),
        llm_provider=provider,
        llm_model=_env("LLM_MODEL", DEFAULT_MODELS.get(provider, "")),
        embeddings=_env("EMBEDDINGS", "fastembed").lower(),
        embedding_model=_env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        model_cache_dir=_env("MODEL_CACHE_DIR", "./.models"),
        log_file=_env("LOG_FILE", "./logs/app.log"),
        log_level=_env("LOG_LEVEL", "INFO"),
        leave_year=int(_env("LEAVE_YEAR", "2026")),
        corpus_token_budget=int(_env("CORPUS_TOKEN_BUDGET", "3000")),
        llm_timeout_s=float(_env("LLM_TIMEOUT_S", "12")),
    )


def today() -> date:
    """Today's date: APP_TODAY if set, otherwise the system date. Read on every call."""
    value = os.getenv("APP_TODAY")
    if value:
        return date.fromisoformat(value.strip())
    return date.today()
