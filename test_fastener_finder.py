# -*- coding: utf-8 -*-
"""Test offline cho core module (không gọi mạng). Chạy: pytest -q"""

import os
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


def test_cli_rejects_nonpositive():
    with pytest.raises(SystemExit):
        ff.main(["--loop", "0"])
    with pytest.raises(SystemExit):
        ff.main(["--queries", "-5", "--loop", "1"])
