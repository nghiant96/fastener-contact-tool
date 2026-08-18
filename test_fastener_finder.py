# -*- coding: utf-8 -*-
"""Test offline cho core module (không gọi mạng). Chạy: pytest -q"""

import os
import re
import tempfile

import pandas as pd
import pytest

import fastener_finder as ff


# ---------------------------------------------------------------- utils

def test_clean_domain():
    assert ff._clean_domain("https://www.abc.com/x?y=1") == "abc.com"
    assert ff._clean_domain("http://Sub.ABC.com:8080/") == "sub.abc.com"


def test_base_domain():
    assert ff._base_domain("sub.abc.com") == "abc.com"
    assert ff._base_domain("abc.co.uk") == "abc.co.uk"
    assert ff._base_domain("www2.abc.co.uk") == "abc.co.uk"
    assert ff._base_domain("abc.de") == "abc.de"


# -------------------------------------------------------- qualification

def test_qualify_blocklisted():
    q = ff.qualify_result("Alibaba fasteners", "https://alibaba.com/x", "USA")
    assert q["status"] == "rejected"
    assert "blocklisted_domain" in q["reasons"]


def test_qualify_excluded_tld():
    q = ff.qualify_result("Fastener co", "https://abcfast.in/", "USA")
    assert q["status"] == "rejected"
    assert "excluded_tld" in q["reasons"]


def test_qualify_junk_title():
    q = ff.qualify_result("Top 10 fastener manufacturers",
                          "https://boltco.com", "USA")
    assert q["status"] == "rejected"
    assert "junk_title" in q["reasons"]


def test_qualify_good_company_cctld_match():
    q = ff.qualify_result("Schrauben Müller GmbH - Hersteller",
                          "https://schrauben-mueller.de", "Germany")
    assert q["status"] == "qualified"
    assert q["verified_country"] == "Germany"
    assert q["confidence"] >= 0.5


def test_qualify_cctld_mismatch_noted_not_rejected():
    q = ff.qualify_result("Bolt Factory - screw manufacturer",
                          "https://boltfactory.de", "UK")
    assert q["status"] in ("qualified", "review")
    assert q["verified_country"] == "Germany"
    assert "cctld_region_mismatch" in q["reasons"]


def test_qualify_irrelevant_rejected():
    q = ff.qualify_result("Random travel agency",
                          "https://beachtours.com", "USA")
    assert q["status"] == "rejected"
    assert "low_relevance" in q["reasons"]


def test_qualify_dataframe_adds_columns():
    df = pd.DataFrame([
        {"company_name": "Bolt GmbH", "website": "https://boltgmbh.de",
         "region": "Germany"},
        {"company_name": "Travel", "website": "https://beachtours.com",
         "region": "USA"},
    ])
    out = ff.qualify_dataframe(df)
    for col in ("qualification_status", "confidence_score",
                "verified_country", "rejection_reasons"):
        assert col in out.columns
    assert out.loc[0, "qualification_status"] == "qualified"
    assert out.loc[1, "qualification_status"] == "rejected"


# ---------------------------------------------------------------- email

def test_extract_plain_and_mailto():
    html = '<a href="mailto:Sales@Bolt.com">contact</a> info@bolt.com'
    emails = ff._extract_emails_from_html(html)
    assert emails == {"sales@bolt.com", "info@bolt.com"}


def test_extract_cfemail():
    # "info@bolt.de" mã hoá XOR key 0x42
    key = 0x42
    encoded = bytes([key] + [ord(c) ^ key for c in "info@bolt.de"]).hex()
    html = f'<a data-cfemail="{encoded}">[protected]</a>'
    assert ff._extract_emails_from_html(html) == {"info@bolt.de"}


def test_extract_obfuscated_at_dot():
    html = "reach us: sales (at) boltco (dot) com"
    assert ff._extract_emails_from_html(html) == {"sales@boltco.com"}


def test_extract_filters_junk():
    html = "logo@2x.png x@example.com noreply@boltco.com a@sentry.io"
    assert ff._extract_emails_from_html(html) == set()


def test_classify_email():
    assert ff._classify_email("a@bolt.com", "bolt.com") == "same_domain"
    assert ff._classify_email("a@mail.bolt.com", "bolt.com") == "same_domain"
    assert ff._classify_email("a@bolt-polska.pl", "bolt.com") in (
        "related_domain", "external")  # 'bolt' chỉ 4 ký tự -> related
    assert ff._classify_email("a@gmail.com", "bolt.com") == "external"


def test_find_contact_links_same_host_only():
    html = ('<a href="/contact">c</a>'
            '<a href="https://other.com/contact">x</a>'
            '<a href="mailto:a@b.com">m</a>'
            '<a href="/about-us">a</a>')
    links = ff._find_contact_links(html, "https://bolt.com/")
    assert "https://bolt.com/contact" in links
    assert all("other.com" not in l for l in links)


# ------------------------------------------------------------- io/state

def test_atomic_write_and_save_tables():
    df = pd.DataFrame({"website": ["https://a.com"], "x": [1]})
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.csv")
        ok = ff.save_tables(df, path, quiet=True)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")
        back = pd.read_csv(path)
        assert list(back["website"]) == ["https://a.com"]
        assert ok  # openpyxl có sẵn trong môi trường test


def test_build_cycle_queries_unique_and_seeded():
    q1 = ff.build_cycle_queries(30, seed=1)
    q2 = ff.build_cycle_queries(30, seed=1)
    assert q1 == q2
    texts = [q[0] for q in q1]
    assert len(texts) == len(set(texts))


def test_regions_wellformed():
    for label, (geo_terms, ddgs_region) in ff.REGIONS.items():
        assert geo_terms and all(isinstance(g, str) for g in geo_terms)
        assert re.fullmatch(r"[a-z]{2}-[a-z]{2,3}", ddgs_region), (
            f"mã vùng ddgs sai định dạng: {label} -> {ddgs_region}")


def test_regions_covered_by_cctld_map():
    # mọi nước trong REGIONS (trừ Mỹ dùng .com phổ biến) đều nhận diện
    # được qua ít nhất 1 ccTLD trong bảng
    countries = {c for _, c in ff.CCTLD_COUNTRY}
    for label in ff.REGIONS:
        assert label in countries or label == "USA", (
            f"{label} chưa có trong CCTLD_COUNTRY")


def test_qualify_new_european_country():
    q = ff.qualify_result("Šroub s.r.o. - screw manufacturer",
                          "https://sroubcz.cz", "Czech")
    assert q["verified_country"] == "Czech"
    assert q["status"] in ("qualified", "review")


def test_build_queries_deep_geo_expands_us_states():
    regions = {"USA": (["USA", "Texas", "Ohio"], "us-en")}
    shallow = ff.build_queries(products=["bolts"], roles=["manufacturer"],
                               regions=regions)
    deep = ff.build_queries(regions=regions, deep_geo=True)
    assert len(shallow) == 1 and "USA" in shallow[0][0]
    geos = {"Texas", "Ohio", "USA"}
    assert all(any(g in q[0] for g in geos) for q in deep)
    assert any("Texas" in q[0] for q in deep)
    # deep_geo cộng thêm từ khoá đặc thù Mỹ (ASTM/SAE...)
    assert any("ASTM" in q[0] or "SAE" in q[0] or "grade 8" in q[0]
               for q in deep)


def test_build_queries_dedupes():
    regions = {"USA": (["USA", "USA"], "us-en")}
    out = ff.build_queries(products=["bolts"], roles=["manufacturer"],
                           regions=regions, deep_geo=True)
    assert len(out) == len({q[0] for q in out})


def test_cycle_queries_can_hit_us_states():
    regions = {"USA": ff.REGIONS["USA"]}
    qs = ff.build_cycle_queries(120, seed=7, regions=regions)
    text = " ".join(q[0] for q in qs)
    assert any(state in text for state in ("Texas", "Ohio", "Michigan"))


def test_cli_rejects_nonpositive():
    with pytest.raises(SystemExit):
        ff.main(["--loop", "0"])
    with pytest.raises(SystemExit):
        ff.main(["--queries", "-5", "--loop", "1"])


# ------------------------------------------------------- geo verification

def test_geo_detect_china():
    html = "Ningbo Zhejiang factory. Tel: +86 574 8888 8888. ICP备12345号"
    country, score, ev = ff.detect_country_from_html(html)
    assert country == "China"
    assert score >= ff.GEO_MIN_SCORE and ev


def test_geo_detect_usa():
    html = ("Portland, OR 97203 — Call 800-555-1234. ASTM A193 bolts. "
            "Acme Bolt Inc.")
    country, _, _ = ff.detect_country_from_html(html)
    assert country == "USA"


def test_geo_banned_signal_beats_higher_target_score():
    # công ty Ấn Độ đăng cả địa chỉ Mỹ + ASTM (điểm USA cao hơn) nhưng có
    # +91 và địa danh Ấn Độ -> phải nhận là India
    html = ("Houston, TX 77002 USA. ASTM A193 UNC. Mumbai, Maharashtra. "
            "Call +91 22 4567 8900")
    country, _, _ = ff.detect_country_from_html(html)
    assert country == "India"


def test_geo_no_signal_returns_empty():
    country, score, _ = ff.detect_country_from_html("just some bolts here")
    assert country == ""


def test_restatus_rejects_banned_country():
    status, reason = ff.restatus_by_geo("qualified", "China", 8.0)
    assert status == "rejected"
    assert reason.startswith("geo_outside_target")


def test_restatus_promotes_target_country():
    status, reason = ff.restatus_by_geo("review", "USA", 6.0)
    assert status == "qualified"
    assert reason == ""


def test_restatus_keeps_review_when_unverified():
    status, reason = ff.restatus_by_geo("review", "", 0.0)
    assert status == "review"
    assert reason == "geo_unverified"


def test_dotcom_cannot_be_qualified_without_geo():
    # đây là lỗi đã gây ra dữ liệu toàn công ty TQ: .com điểm ngành cao
    # nhưng KHÔNG có bằng chứng quốc gia -> chỉ được 'review'
    q = ff.qualify_result("Fastener manufacturer supplier USA",
                          "https://xyzfastener.com", "USA")
    assert q["status"] == "review"
    assert "geo_unverified" in q["reasons"]


def test_cctld_still_qualifies_directly():
    q = ff.qualify_result("Schrauben GmbH Hersteller",
                          "https://schrauben-x.de", "Germany")
    assert q["status"] == "qualified"


def test_resume_rescans_rows_missing_geo(monkeypatch):
    """File từ bản cũ (chỉ có email, chưa có geo) phải được quét lại."""
    calls = []

    def fake_scrape(website):
        calls.append(website)
        return {"emails": [("a@x.com", "same_domain", website)],
                "status": "found", "country": "USA", "geo_score": 6.0,
                "geo_evidence": ["tên bang"]}

    monkeypatch.setattr(ff, "scrape_site", fake_scrape)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.csv")
        out = os.path.join(d, "in_with_emails.csv")
        pd.DataFrame({"website": ["https://a.com"],
                      "qualification_status": ["review"]}).to_csv(
                          src, index=False)
        # file output kiểu CŨ: có email nhưng KHÔNG có cột geo
        pd.DataFrame({"website": ["https://a.com"], "emails": ["old@a.com"],
                      "emails_external": [""], "email_status": ["found"],
                      "email_found_on": ["https://a.com"]}).to_csv(
                          out, index=False)
        res = ff.enrich_csv(src, out_path=out, workers=1)
    assert calls == ["https://a.com"], "phải quét lại để lấy geo"
    assert res.loc[0, "detected_country"] == "USA"
    assert res.loc[0, "qualification_status"] == "qualified"
