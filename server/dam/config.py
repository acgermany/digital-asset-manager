"""Runtime configuration, read once from environment variables."""
import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://dam:dam@localhost:5432/dam"
    )

    # Hot folder (local disk on the Linux box, shared to the Mac over SMB)
    hotfolder: str = os.environ.get("HOTFOLDER", "")
    poll_interval: float = float(os.environ.get("POLL_INTERVAL", "2"))
    stable_seconds: float = float(os.environ.get("STABLE_SECONDS", "5"))
    # "delete" removes exact duplicates from the hot folder; "trash" moves them to _trash/
    delete_mode: str = os.environ.get("DELETE_MODE", "delete")

    # Library on the NAS. Must contain LIBRARY_MARKER so an unmounted NAS is never
    # mistaken for an empty library (files would otherwise land on the local disk).
    library: str = os.environ.get("LIBRARY", "")
    library_marker: str = os.environ.get("LIBRARY_MARKER", ".dam_library")

    # Read-only NAS root for the catalog (in-place) scan
    catalog_root: str = os.environ.get("CATALOG_ROOT", "")

    # Ollama on the Mac
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    embed_model: str = os.environ.get("EMBED_MODEL", "nomic-embed-text")
    embed_dims: int = int(os.environ.get("EMBED_DIMS", "768"))
    embed_keep_alive: str = os.environ.get("EMBED_KEEP_ALIVE", "30m")
    # nomic-embed-text expects task prefixes; set both to "" for models that don't
    embed_doc_prefix: str = os.environ.get("EMBED_DOC_PREFIX", "search_document: ")
    embed_query_prefix: str = os.environ.get("EMBED_QUERY_PREFIX", "search_query: ")
    embed_batch_size: int = int(os.environ.get("EMBED_BATCH_SIZE", "32"))
    # Send (redacted) document text of critical/financial assets to the model
    embed_sensitive_text: bool = _bool("EMBED_SENSITIVE_TEXT", False)
    # Semantic hits below this cosine similarity are treated as noise
    semantic_min_score: float = float(os.environ.get("SEMANTIC_MIN_SCORE", "0.5"))
    model_load_timeout: float = float(os.environ.get("MODEL_LOAD_TIMEOUT", "300"))

    # Web UI auth. Empty password disables auth (local development only).
    app_password: str = os.environ.get("APP_PASSWORD", "")
    secret_key: str = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    session_hours: int = int(os.environ.get("SESSION_HOURS", "72"))
    critical_unlock_minutes: int = int(os.environ.get("CRITICAL_UNLOCK_MINUTES", "15"))

    # Classification
    tier_confidence_threshold: float = float(os.environ.get("TIER_CONFIDENCE_THRESHOLD", "0.7"))

    worker_queues: tuple = field(
        default_factory=lambda: tuple(
            q.strip() for q in os.environ.get("WORKER_QUEUES", "default,heavy").split(",") if q.strip()
        )
    )


settings = Settings()
