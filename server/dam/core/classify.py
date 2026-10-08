"""Rule-based value tiering.

critical = tax / financial documents, precious = family photos, videos, recordings.
When a file *might* be critical or precious but the rules are not confident, it is
still given the higher tier (so it is protected) and flagged for human review.
Wrongly protecting a file costs one review click; wrongly not protecting a tax
return could cost the file.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from .metadata import EIN_RE, SSN_RE

CONSUMER_MAKES = (
    "apple", "samsung", "google", "motorola", "oneplus", "xiaomi", "huawei", "sony", "lg",
    "canon", "nikon", "fujifilm", "olympus", "om digital", "panasonic", "gopro", "dji",
    "kodak", "leica", "pentax", "ricoh", "hasselblad", "insta360",
)
FAMILY_HINTS = re.compile(
    r"\b(family|kids?|baby|babies|wedding|birthday|christmas|holiday|vacation|"
    r"grandm\w*|grandp\w*|mom|dad|home ?movies?|home ?videos?|anniversary|graduation)\b", re.I)
VOICE_HINTS = re.compile(r"(voice ?memo|new recording|recording|dictaphone|voicemail)", re.I)
SCREENSHOT_HINTS = re.compile(r"(screen ?shot|screenshot|screen recording|scr_\d)", re.I)
JUNK_HINTS = re.compile(r"(\bcache\b|thumbnails?|\.thumb|~\$|\btmp\b|\btemp\b)", re.I)

TAX_FORM_RE = re.compile(
    r"\bform\s+(1040(?:-?(?:sr|nr|x|es))?|w-?2|w-?4|w-?9|1099(?:-[a-z]{1,4})?|1098(?:-[a-z])?|"
    r"k-?1|8949|1065|1120s?|941|5498|1095-[abc])\b", re.I)
STRONG_FINANCIAL = re.compile(
    r"(internal revenue service|department of the treasury|tax return|"
    r"wage and tax statement|employer identification number|adjusted gross income|"
    r"taxable income|franchise tax board|department of revenue)", re.I)
WEAK_FINANCIAL = re.compile(
    r"(statement period|account summary|beginning balance|ending balance|routing number|"
    r"account number|available balance|mortgage|escrow|brokerage|dividends?|"
    r"capital gains?|\bira\b|401\(k\)|roth|premium|policy number|amount due|"
    r"payment due|interest paid|year-end summary|cost basis|pay ?stub|net pay|gross pay)", re.I)
FINANCIAL_PATH_HINTS = re.compile(
    r"\b(tax(es)?|irs|w-?2|1099|1040|bank|statements?|mortgage|brokerage|401k|ira|"
    r"insurance|pay ?stubs?|receipts?|financ\w*|invoices?)\b", re.I)
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")


@dataclass
class TierResult:
    tier: str = "standard"
    confidence: float = 0.5
    reasons: list = field(default_factory=list)
    needs_review: bool = False
    doc_features: Optional[dict] = None


def _norm_path(rel_path: str) -> str:
    return re.sub(r"[_/\\.\-]+", " ", rel_path)


def document_features(text: Optional[str]) -> dict:
    """Features of extracted document text. Never stores identifying numbers."""
    if not text:
        return {"has_text": False}
    forms = sorted({m.group(1).upper().replace("W2", "W-2") for m in TAX_FORM_RE.finditer(text)})
    strong = sorted({m.group(1).lower() for m in STRONG_FINANCIAL.finditer(text)})
    weak = sorted({m.group(1).lower() for m in WEAK_FINANCIAL.finditer(text)})
    years = YEAR_RE.findall(text)
    year_counts: dict = {}
    for y in years:
        year_counts[y] = year_counts.get(y, 0) + 1
    top_years = sorted(year_counts, key=lambda y: -year_counts[y])[:3]
    return {
        "has_text": True,
        "chars": len(text),
        "tax_forms": forms,
        "strong_terms": strong,
        "weak_terms": weak[:12],
        "years": top_years,
        "contains_ssn_pattern": bool(SSN_RE.search(text)),
        "contains_ein_pattern": bool(EIN_RE.search(text)),
    }


def classify(kind: str, filename: str, rel_path: str, exif: Optional[dict] = None,
             media_summary: Optional[dict] = None, doc_text: Optional[str] = None,
             threshold: float = 0.7) -> TierResult:
    exif = exif or {}
    media_summary = media_summary or {}
    path_words = _norm_path(rel_path)
    r = TierResult()

    if JUNK_HINTS.search(rel_path):
        r.tier, r.confidence = "junk", 0.6
        r.reasons.append("path looks like a cache/temp location")

    if kind in ("image", "video"):
        make = str(exif.get("Make") or media_summary.get("com_apple_quicktime_make") or "").lower()
        model = exif.get("Model") or media_summary.get("com_apple_quicktime_model")
        has_capture_date = any(exif.get(k) for k in ("DateTimeOriginal", "ContentCreateDate", "MediaCreateDate"))
        if make and any(m in make for m in CONSUMER_MAKES):
            r.tier, r.confidence = "precious", 0.85 if has_capture_date else 0.75
            r.reasons.append(f"taken on a camera/phone ({make.title()}{' ' + str(model) if model else ''})")
        elif SCREENSHOT_HINTS.search(filename):
            r.tier, r.confidence = "low", 0.75
            r.reasons.append("filename looks like a screenshot or screen recording")
        elif has_capture_date or exif.get("GPSLatitude") is not None:
            r.tier, r.confidence = "precious", 0.6
            r.reasons.append("has original capture date/location but no camera make")
        if FAMILY_HINTS.search(path_words):
            if r.tier != "precious":
                r.tier, r.confidence = "precious", 0.6
            else:
                r.confidence = min(0.95, r.confidence + 0.1)
            r.reasons.append("folder/filename suggests family content")

    elif kind == "audio":
        encoder = str(media_summary.get("general_encoded_application") or "").lower()
        if "voicememos" in encoder.replace(" ", "") or VOICE_HINTS.search(filename) or VOICE_HINTS.search(path_words):
            r.tier, r.confidence = "precious", 0.75
            r.reasons.append("looks like a voice memo / personal recording")
        elif FAMILY_HINTS.search(path_words):
            r.tier, r.confidence = "precious", 0.6
            r.reasons.append("folder/filename suggests family content")

    elif kind == "document" or doc_text:
        features = document_features(doc_text)
        r.doc_features = features
        path_hint = bool(FINANCIAL_PATH_HINTS.search(path_words))
        if features.get("tax_forms") or features.get("strong_terms"):
            r.tier, r.confidence = "critical", 0.9
            r.reasons.append("tax form / IRS language found: " +
                             ", ".join((features.get("tax_forms") or []) + (features.get("strong_terms") or []))[:200])
        elif len(features.get("weak_terms") or []) >= 2:
            r.tier, r.confidence = "critical", 0.75 if path_hint else 0.65
            r.reasons.append("financial statement language: " + ", ".join(features["weak_terms"][:5]))
        elif path_hint:
            r.tier, r.confidence = "critical", 0.55
            r.reasons.append("folder/filename suggests financial records")
        if r.tier == "critical" and features.get("contains_ssn_pattern"):
            r.confidence = min(0.97, r.confidence + 0.1)
            r.reasons.append("contains an SSN-shaped number (redacted)")
        if not features.get("has_text") and kind == "document" and filename.lower().endswith(".pdf"):
            r.reasons.append("no extractable text (scanned PDF?) — OCR not yet enabled")
            if r.tier == "standard":
                r.needs_review = True

    if r.tier in ("critical", "precious") and r.confidence < threshold:
        r.needs_review = True
    if not r.reasons:
        r.reasons.append("no rule matched")
    return r
