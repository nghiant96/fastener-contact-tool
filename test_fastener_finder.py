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


def test_geo_js_timestamp_not_a_phone():
    """`e.timestamp+604800` trong JS từng bị nhận là điện thoại +60."""
    assert ff.detect_country_from_html("if(e.timestamp+604800){}")[0] == ""


def test_geo_ignores_script_and_style():
    html = ("<script>var t=x+8612345678;</script>"
            "<style>a{content:'+91 22 1234 5678'}</style>"
            "<p>Ohio 44101, call 800-555-1234</p>")
    assert ff.detect_country_from_html(html)[0] == "USA"


def test_geo_real_phones_still_detected():
    for text, expected in (
            ("Tel: +60 12-345 6789 Kuala Lumpur", "Malaysia"),
            ("Tel: +86 574 8888 8888 Ningbo", "China"),
            ("Tel. +49 (0)7132 99-0 GmbH Impressum", "Germany"),
            ("Tél: +33 1 42 68 53 00 SARL TVA FR12345678901", "France")):
        assert ff.detect_country_from_html(text)[0] == expected, text


def test_geo_no_false_positive_on_french_site_text():
    # trang Pháp có JS lẫn số: không được nhận thành nước châu Á
    html = ("<script>d=n+60123;</script><p>AHG France, Tél. +33 4 74 00 00 00,"
            " TVA FR40123456789</p>")
    assert ff.detect_country_from_html(html)[0] == "France"


# --------------------------------------------- structured data (mục 3)

def test_structured_jsonld_addresscountry_iso2():
    html = ('<script type="application/ld+json">{"@type":"Organization",'
            '"address":{"@type":"PostalAddress","addressCountry":"DE"}}'
            '</script>')
    assert ff.structured_country(html) == ("Germany", "JSON-LD addressCountry",
                                           ff.STRUCT_STRONG)


def test_structured_jsonld_nested_object_and_fullname():
    html = ('<script type="application/ld+json">'
            '{"address":{"addressCountry":{"name":"United States"}}}</script>')
    assert ff.structured_country(html)[0] == "USA"


def test_structured_jsonld_in_list():
    html = ('<script type="application/ld+json">[{"@type":"WebSite"},'
            '{"@type":"LocalBusiness","address":{"addressCountry":"IT"}}]'
            '</script>')
    assert ff.structured_country(html)[0] == "Italy"


def test_structured_ignores_broken_json():
    html = '<script type="application/ld+json">{oops,</script>'
    assert ff.structured_country(html) == ("", "", 0.0)


def test_structured_og_locale_and_currency():
    assert ff.structured_country(
        '<meta property="og:locale" content="fr_FR">')[0] == "France"
    assert ff.structured_country('"priceCurrency": "GBP"')[0] == "UK"


def test_structured_beats_weak_heuristics():
    # trang khai báo CN nhưng nhắc 'USA' trong nội dung -> phải là China
    html = ('<script type="application/ld+json">'
            '{"address":{"addressCountry":"CN"}}</script>'
            '<p>Supplier for USA market, ASTM A193</p>')
    assert ff.detect_country_from_html(html)[0] == "China"


def test_norm_country_variants():
    assert ff._norm_country("US") == "USA"
    assert ff._norm_country("deutschland") == "Germany"
    assert ff._norm_country("Türkiye") == "Turkey"
    assert ff._norm_country("Nowhere") == ""
    assert ff._norm_country(None) == ""


def test_og_locale_is_language_not_country():
    """stauff.fr (công ty Pháp) dùng og:locale en_GB -> KHÔNG được thành UK."""
    html = ('<meta property="og:locale" content="en_GB">'
            '<p>STAUFF France SARL, Tél. +33 1 23 45 67 89, TVA FR12345678901</p>')
    assert ff.detect_country_from_html(html)[0] == "France"
    # một mình og:locale không đủ điểm để xác minh
    weak = ff.detect_country_from_html('<meta property="og:locale" content="en_GB">')
    assert weak[0] == ""


# ------------------------------------------- directory harvest (mục 1)

NFDA_LIKE = '''
<ul>
  <li><a href="https://www.brightonbest.com" target="_blank">Brighton-Best
      International</a></li>
  <li><a href="https://afcind.com">AFC Industries, Inc.</a></li>
  <li><a href="https://fastenershows.com">International Fastener Expo</a></li>
  <li><a href="https://www.facebook.com/nfda">Facebook</a></li>
  <li><a href="https://nfda-fastener.org/about">About us</a></li>
</ul>
'''

EFDA_LIKE = '''
<p><strong>Bendkopp Group </strong>(Romania)
   <a class="more" href="http://www.bendkopp.ro" target="_blank">Website</a>
   <br /><strong>Etra Oy</strong> (Finland)
   <a class="more" href="http://www.etra.fi">Website</a>
   <br /><strong>FDS</strong> (Germany)
   <a class="more" href="http://www.fds-online.de">Website</a></p>
'''


def test_directory_parse_anchor_text_names():
    rows = ff.parse_directory_html(NFDA_LIKE, "https://nfda-fastener.org/x",
                                   "NFDA", country="USA")
    by_site = {r["website"]: r for r in rows}
    assert by_site["https://brightonbest.com"]["company_name"] == (
        "Brighton-Best International")
    assert by_site["https://afcind.com"]["company_name"] == (
        "AFC Industries, Inc.")
    # hội viên hội Mỹ -> qualified, nước lấy từ chính hội
    assert by_site["https://afcind.com"]["qualification_status"] == "qualified"
    assert by_site["https://afcind.com"]["verified_country"] == "USA"
    assert by_site["https://afcind.com"]["source"] == "directory:NFDA"


def test_directory_skips_expo_social_and_own_domain():
    sites = {r["website"] for r in ff.parse_directory_html(
        NFDA_LIKE, "https://nfda-fastener.org/x", "NFDA", country="USA")}
    assert "https://fastenershows.com" not in sites   # triển lãm
    assert "https://facebook.com" not in sites        # mạng xã hội
    assert "https://nfda-fastener.org" not in sites   # chính trang danh bạ


def test_directory_name_and_country_before_generic_anchor():
    """Kiểu EFDA: tên trong <strong>, nước trong (), anchor chỉ là 'Website'."""
    rows = ff.parse_directory_html(
        EFDA_LIKE, "https://efda-fastenerdistributors.org/de/members",
        "EFDA", country="")
    by_site = {r["website"]: r for r in rows}
    assert by_site["https://bendkopp.ro"]["company_name"] == "Bendkopp Group"
    assert by_site["https://bendkopp.ro"]["region"] == "Romania"
    assert by_site["https://etra.fi"]["company_name"] == "Etra Oy"
    assert by_site["https://etra.fi"]["region"] == "Finland"


def test_directory_acronym_member_downgraded_to_review():
    """FDS là hội quốc gia, không phải nhà cung cấp -> chỉ 'review'."""
    rows = ff.parse_directory_html(
        EFDA_LIKE, "https://efda-fastenerdistributors.org/de/members",
        "EFDA", country="")
    fds = next(r for r in rows if r["website"] == "https://fds-online.de")
    assert fds["qualification_status"] == "review"
    assert "possible_association" in fds["rejection_reasons"]


def test_directory_name_from_domain_fallback():
    html = '<a href="https://metfix.com.pl"><img src="logo.png"></a>'
    rows = ff.parse_directory_html(html, "https://efda.org", "EFDA")
    assert rows[0]["company_name"] == "Metfix"


def test_name_country_window_is_tight():
    """Nước của dòng TRƯỚC không được gán sai cho công ty dòng sau."""
    html = ("<strong>Alpha</strong> (Spain) " + "&nbsp; " * 40 +
            "<strong>Beta</strong> <a href='https://beta-bolts.de'>Website</a>")
    rows = ff.parse_directory_html(html, "https://efda.org", "EFDA")
    beta = rows[0]
    assert beta["company_name"] == "Beta"
    assert beta["region"] != "Spain"


def test_directory_skip_does_not_eat_lookalike_domains():
    """`x.com` / `bing` / `apple` từng khớp chuỗi con -> loại oan công ty."""
    html = ('<a href="https://metfix.com.pl">Metfix</a>'
            '<a href="https://binghamfasteners.com">Bingham Fasteners</a>'
            '<a href="https://appletonbolt.com">Appleton Bolt</a>'
            '<a href="https://fixdex.com">Fixdex</a>'
            '<a href="https://x.com/nfda">X</a>'
            '<a href="https://bing.com">Bing</a>')
    sites = {r["website"] for r in ff.parse_directory_html(
        html, "https://efda.org", "EFDA")}
    assert "https://metfix.com.pl" in sites
    assert "https://binghamfasteners.com" in sites
    assert "https://appletonbolt.com" in sites
    assert "https://fixdex.com" in sites
    assert "https://x.com" not in sites and "https://bing.com" not in sites
