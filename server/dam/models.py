"""Database schema.

An Asset is one unique piece of content (one sha256). A File is one physical
copy of it on disk: either filed into the library by ingest, or found in place
by a catalog scan. An Ingest records one arrival through the hot folder.
"""
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings

TIERS = ("critical", "precious", "standard", "low", "junk")
PROTECTED_TIERS = ("critical", "precious")


class Base(DeclarativeBase):
    pass


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filesize: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16), default="other")  # image|video|audio|document|other
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    is_media: Mapped[bool] = mapped_column(Boolean, default=False)
    mediainfo: Mapped[Optional[dict]] = mapped_column(JSONB)
    exif: Mapped[Optional[dict]] = mapped_column(JSONB)
    doc_features: Mapped[Optional[dict]] = mapped_column(JSONB)
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    value_tier: Mapped[str] = mapped_column(String(16), default="standard")
    tier_source: Mapped[str] = mapped_column(String(16), default="rule")  # rule|model|user
    tier_confidence: Mapped[Optional[float]] = mapped_column(Float)
    tier_reasons: Mapped[Optional[list]] = mapped_column(JSONB)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_backup: Mapped[bool] = mapped_column(Boolean, default=False)

    tags: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    # Vision-model description (images: caption; videos: summary of sampled frames)
    description: Mapped[Optional[str]] = mapped_column(Text)
    description_tags: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    description_model: Mapped[Optional[str]] = mapped_column(String(128))
    described_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    description_error: Mapped[Optional[str]] = mapped_column(Text)

    # Flattened text used for keyword search and as the embedding source
    search_text: Mapped[Optional[str]] = mapped_column(Text)
    metadata_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|failed

    first_seen_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list["File"]] = relationship(back_populates="asset", order_by="File.id")

    __table_args__ = (
        Index("idx_assets_tier", "value_tier"),
        Index("idx_assets_kind", "kind"),
        Index("idx_assets_tags", "tags", postgresql_using="gin"),
        Index("idx_assets_search_trgm", "search_text", postgresql_using="gin",
              postgresql_ops={"search_text": "gin_trgm_ops"}),
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    path: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(Text)
    filesize: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))  # library|catalog
    provenance: Mapped[Optional[str]] = mapped_column(Text)
    marked_for_deletion: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()
    scanned_at: Mapped[datetime] = _now()

    asset: Mapped[Optional[Asset]] = relationship(back_populates="files")

    __table_args__ = (
        Index("idx_files_sha256", "sha256"),
        Index("idx_files_asset", "asset_id"),
        Index("idx_files_filename_lower", func.lower(filename)),
    )


class Ingest(Base):
    __tablename__ = "ingests"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    original_filename: Mapped[str] = mapped_column(Text)
    original_path: Mapped[str] = mapped_column(Text)
    original_mtime: Mapped[Optional[float]] = mapped_column(Float)
    provenance: Mapped[str] = mapped_column(Text, default="unspecified")
    provenance_meta: Mapped[Optional[dict]] = mapped_column(JSONB)
    ingest_date: Mapped[datetime] = _now()
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    filesize: Mapped[int] = mapped_column(BigInteger)
    # queued|processing|accepted|duplicate_deleted|duplicate_review|failed
    status: Mapped[str] = mapped_column(String(24), default="queued")
    step: Mapped[Optional[str]] = mapped_column(String(24))
    decision: Mapped[Optional[str]] = mapped_column(String(24))  # new|duplicate_exact|duplicate_review
    duplicate_of_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    current_path: Mapped[Optional[str]] = mapped_column(Text)  # where the file is now (library / _review)
    error: Mapped[Optional[str]] = mapped_column(Text)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    asset: Mapped[Optional[Asset]] = relationship(foreign_keys=[asset_id])

    __table_args__ = (
        Index("idx_ingests_status", "status"),
        Index("idx_ingests_path", "original_path"),
        Index("idx_ingests_sha256", "sha256"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # ingest|catalog_scan|embed_batch
    queue: Mapped[str] = mapped_column(String(16), default="default")  # default|heavy|ai
    # queued|running|done|failed|cancelling|cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    ingest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ingests.id", ondelete="CASCADE"))
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = _now()
    locked_by: Mapped[Optional[str]] = mapped_column(String(64))
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    waiting_reason: Mapped[Optional[str]] = mapped_column(Text)  # e.g. "waiting for AI model"
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("idx_jobs_claim", "status", "queue", "run_after"),)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # exact_name_differs|classification|near_duplicate
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved
    priority: Mapped[int] = mapped_column(Integer, default=0)
    ingest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ingests.id", ondelete="CASCADE"))
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    suggestion: Mapped[Optional[dict]] = mapped_column(JSONB)
    resolution: Mapped[Optional[str]] = mapped_column(String(32))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(32))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()

    ingest: Mapped[Optional[Ingest]] = relationship()
    asset: Mapped[Optional[Asset]] = relationship()

    __table_args__ = (Index("idx_reviews_open", "status", "kind"),)


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(128))
    source_hash: Mapped[str] = mapped_column(String(64))
    embedding = mapped_column(Vector(settings.embed_dims))
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("asset_id", "model", name="uq_embedding_asset_model"),
        Index("idx_embeddings_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    in_point: Mapped[float] = mapped_column(Float)
    out_point: Mapped[float] = mapped_column(Float)
    label: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column("metadata", Text, default="{}")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = _now()
    actor: Mapped[str] = mapped_column(String(16))  # system|user|agent|model
    action: Mapped[str] = mapped_column(String(48))
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), index=True)
    ingest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ingests.id", ondelete="SET NULL"), index=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class AIState(Base):
    """Single row (id=1) describing the Ollama embedding model on the Mac."""
    __tablename__ = "ai_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    # offline|unloaded|loading|ready
    state: Mapped[str] = mapped_column(String(16), default="offline")
    reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    model_installed: Mapped[bool] = mapped_column(Boolean, default=False)
    model_loaded: Mapped[bool] = mapped_column(Boolean, default=False)
    semantic_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    loading_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    loaded_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)


class AppSetting(Base):
    """Editable settings (Configure page). Missing keys fall back to env defaults."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)  # {"v": <value>}
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NetworkMount(Base):
    """SMB/NFS share the app mounts itself under /mnt/dam/<name>."""
    __tablename__ = "network_mounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    protocol: Mapped[str] = mapped_column(String(8))  # smb|nfs
    server: Mapped[str] = mapped_column(String(255))
    share: Mapped[str] = mapped_column(String(512))   # SMB share name / NFS export path
    username: Mapped[Optional[str]] = mapped_column(String(255))
    has_password: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[Optional[str]] = mapped_column(String(512))
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_mount: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
