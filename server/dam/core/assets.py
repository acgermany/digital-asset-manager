"""Asset helpers: search text, tier changes."""
import os
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import PROTECTED_TIERS, TIERS, Asset, Ingest
from . import events
from .metadata import summarize_mediainfo


def build_search_text(asset: Asset, session: Session) -> str:
    """Flattened description used for keyword search and as embedding input."""
    parts: list = []
    names = {f.filename for f in asset.files}
    ingests = session.query(Ingest).filter(Ingest.asset_id == asset.id).all()
    names.update(i.original_filename for i in ingests)
    parts.extend(sorted(names))
    provenances = sorted({i.provenance for i in ingests if i.provenance} |
                         {f.provenance for f in asset.files if f.provenance})
    if provenances:
        parts.append("provenance: " + ", ".join(provenances))
    folders: set = set()
    for f in asset.files:
        folders.update(p for p in os.path.dirname(f.path).split("/")[-4:] if p)
    if folders:
        parts.append("folders: " + " ".join(sorted(folders)))
    if asset.tags:
        parts.append("tags: " + ", ".join(asset.tags))
    parts.append(f"type: {asset.kind}")
    parts.append(f"tier: {asset.value_tier}")
    if asset.captured_at:
        parts.append(f"captured: {asset.captured_at:%Y-%m-%d %B %Y}")
    exif = asset.exif or {}
    camera = " ".join(str(exif[k]) for k in ("Make", "Model") if exif.get(k))
    if camera:
        parts.append(f"camera: {camera}")
    for key in ("Title", "Description", "Keywords"):
        if exif.get(key):
            parts.append(f"{key.lower()}: {exif[key]}")
    media = summarize_mediainfo(asset.mediainfo)
    if media.get("general_duration"):
        try:
            parts.append(f"duration: {float(media['general_duration']):.0f}s")
        except (TypeError, ValueError):
            pass
    if media.get("width") and media.get("height"):
        parts.append(f"resolution: {media['width']}x{media['height']}")
    doc = asset.doc_features or {}
    if doc.get("tax_forms"):
        parts.append("forms: " + ", ".join(doc["tax_forms"]))
    if doc.get("years"):
        parts.append("years: " + ", ".join(doc["years"]))
    if doc.get("strong_terms") or doc.get("weak_terms"):
        parts.append("terms: " + ", ".join((doc.get("strong_terms") or []) + (doc.get("weak_terms") or [])))
    return re.sub(r"\s+", " ", " | ".join(parts)).strip()


def normalize_tags(tags: list) -> list:
    """Trimmed, de-duplicated (case-insensitively), max 50 chars each, order kept."""
    out, seen = [], set()
    for t in tags or []:
        t = re.sub(r"\s+", " ", str(t)).strip()[:50]
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out[:100]


def set_tags(session: Session, asset: Asset, tags: list, actor: str = "user") -> None:
    old = list(asset.tags or [])
    asset.tags = normalize_tags(tags)
    asset.search_text = build_search_text(asset, session)
    events.log(session, "tags_changed", actor=actor, asset_id=asset.id, old=old, new=asset.tags)


def set_tier(session: Session, asset: Asset, tier: str, actor: str = "user",
             confidence: Optional[float] = None, reasons: Optional[list] = None) -> None:
    if tier not in TIERS:
        raise ValueError(f"Unknown tier {tier}")
    old = asset.value_tier
    asset.value_tier = tier
    asset.tier_source = "user" if actor == "user" else asset.tier_source
    if confidence is not None:
        asset.tier_confidence = confidence
    if reasons is not None:
        asset.tier_reasons = reasons
    asset.protected = tier in PROTECTED_TIERS
    asset.needs_backup = tier in PROTECTED_TIERS
    asset.search_text = build_search_text(asset, session)
    events.log(session, "tier_changed", actor=actor, asset_id=asset.id, old=old, new=tier)
