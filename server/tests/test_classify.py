from dam.core.classify import classify


def test_phone_photo_is_precious():
    r = classify("image", "IMG_1234.HEIC", "family/IMG_1234.HEIC",
                 exif={"Make": "Apple", "Model": "iPhone 15", "DateTimeOriginal": "2024:07:04 12:00:00"})
    assert r.tier == "precious" and r.confidence >= 0.85 and not r.needs_review


def test_screenshot_is_low():
    r = classify("image", "Screenshot 2024-01-01 at 10.00.00.png", "Screenshot 2024-01-01 at 10.00.00.png")
    assert r.tier == "low"


def test_tax_form_text_is_critical_and_redacted_features():
    text = "Form W-2 Wage and Tax Statement 2023 Employee SSN 123-45-6789"
    r = classify("document", "scan0003.pdf", "scan0003.pdf", doc_text=text)
    assert r.tier == "critical" and r.confidence >= 0.9
    assert "W-2" in r.doc_features["tax_forms"]
    assert r.doc_features["contains_ssn_pattern"] is True
    assert "123-45-6789" not in str(r.doc_features)


def test_financial_folder_hint_only_needs_review_but_is_protected():
    r = classify("document", "doc.pdf", "taxes/2022/doc.pdf", doc_text="hello")
    assert r.tier == "critical" and r.needs_review


def test_voice_memo_is_precious():
    r = classify("audio", "New Recording 12.m4a", "New Recording 12.m4a")
    assert r.tier == "precious"


def test_plain_file_is_standard():
    r = classify("other", "notes.bin", "notes.bin")
    assert r.tier == "standard" and not r.needs_review
