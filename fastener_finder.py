#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fastener Company Finder
=======================
Tool tổng hợp WEBSITE + EMAIL các công ty sản xuất / nhập khẩu / phân phối:
fasteners, screws, threaded rods, studs, washers... ở Mỹ và Châu Âu.

Kiến trúc: 1 file core duy nhất, chia 5 module:
  [1] utils        — chuẩn hoá domain/URL
  [2] qualification— chấm điểm & phân loại từng kết quả tìm kiếm
  [3] search       — sinh truy vấn + gọi DuckDuckGo
  [2b] geo verify  — xác minh QUỐC GIA (schema.org + heuristic nội dung)
  [2c] registry    — xác minh qua VAT/VIES, GLEIF, UK Companies House
  [3b] directories — thu hoạch danh bạ hội ngành (nguồn sạch nhất)
  [4] email        — quét email trên website (có provenance & retry)
  [4b] profile     — vai trò (sản xuất/phân phối/nhập khẩu), sản phẩm, SĐT
  [5] io/state     — ghi file atomic, checkpoint, báo cáo
CLI và Google Colab đều chỉ là adapter mỏng gọi vào file này.

Chạy:
  python fastener_finder.py                    # quét 1 lần
  python fastener_finder.py --loop 30          # chạy liên tục, nghỉ 30'
  python fastener_finder.py --emails FILE.csv  # quét email + XÁC MINH nước
  python fastener_finder.py --directories          # lấy từ danh bạ hội ngành
  python fastener_finder.py --requalify FILE.csv   # chấm điểm lại file cũ

Cài đặt:
  pip install -r requirements.txt   (ddgs, pandas, openpyxl, requests)
"""

import os
import re
import sys
import time
import random
import argparse
from datetime import datetime
from urllib.parse import urlparse, urljoin

import pandas as pd

try:
    from ddgs import DDGS          # package mới (pip install ddgs)
except ImportError:
    try:
        from duckduckgo_search import DDGS  # fallback tên cũ
    except ImportError:
        DDGS = None  # cho phép import module khi chỉ cần quét email/test

# ---------------------------------------------------------------------------
# CẤU HÌNH
# ---------------------------------------------------------------------------

PRODUCTS = [
    "fasteners", "screws", "bolts", "threaded rod", "threaded studs",
    "washers",
]

# Từ khoá mở rộng cho chế độ chạy liên tục — mỗi vòng bốc ngẫu nhiên
EXTRA_PRODUCTS = [
    "hex bolts", "anchor bolts", "stud bolts", "nuts and bolts",
    "machine screws", "self-tapping screws", "wood screws",
    "socket head cap screws", "rivets", "spring washers", "flat washers",
    "DIN 975 threaded rod", "B7 stud bolts", "stainless steel fasteners",
    "industrial fasteners", "construction fasteners", "automotive fasteners",
    "aerospace fasteners", "cold heading fasteners", "custom fasteners",
]

ROLES = ["manufacturer", "importer", "distributor", "supplier", "wholesaler"]

EXTRA_MODIFIERS = ["", "company", "factory", "stockist", "ISO 9001", "OEM"]

# Từ khoá đặc thù thị trường Mỹ (tiêu chuẩn ASTM/SAE/UNC, hệ inch)
US_PRODUCTS = [
    "grade 8 bolts", "grade 5 bolts", "ASTM A193 B7 studs",
    "ASTM A307 bolts", "SAE fasteners", "UNC threaded rod",
    "structural bolts A325", "mil-spec fasteners", "inch series fasteners",
    "domestic fasteners made in USA",
]

# Các bang công nghiệp Mỹ — biến thể địa lý để quét chi tiết theo bang
US_STATES = [
    "Texas", "California", "Ohio", "Illinois", "Michigan", "Pennsylvania",
    "Indiana", "Wisconsin", "New York", "North Carolina", "Georgia",
    "Tennessee", "Alabama", "South Carolina", "Kentucky", "Missouri",
    "Minnesota", "New Jersey", "Florida", "Connecticut", "Massachusetts",
    "Washington state", "Oregon", "Arizona", "Colorado", "Iowa", "Kansas",
    "Oklahoma", "Louisiana", "Virginia",
]

REGIONS = {
    # region_label : (danh sách từ khoá địa lý, mã vùng ddgs)
    # Chế độ chạy liên tục bốc NGẪU NHIÊN 1 từ khoá địa lý trong danh sách
    # -> với USA sẽ quét chi tiết theo từng bang.
    "USA": (["USA", "United States"] + US_STATES, "us-en"),
    # # --- Tây Âu ---
    # "Germany": (["Germany"], "de-de"),
    # "UK": (["UK", "United Kingdom"], "uk-en"),
    # "France": (["France"], "fr-fr"),
    # "Italy": (["Italy"], "it-it"),
    # "Spain": (["Spain"], "es-es"),
    # "Netherlands": (["Netherlands"], "nl-nl"),
    # "Belgium": (["Belgium"], "be-nl"),
    # "Austria": (["Austria"], "at-de"),
    # "Switzerland": (["Switzerland"], "ch-de"),
    # "Ireland": (["Ireland"], "ie-en"),
    # "Portugal": (["Portugal"], "pt-pt"),
    # "Luxembourg": (["Luxembourg"], "wt-wt"),
    # # --- Bắc Âu ---
    # "Sweden": (["Sweden"], "se-sv"),
    # "Denmark": (["Denmark"], "dk-da"),
    # "Finland": (["Finland"], "fi-fi"),
    # "Norway": (["Norway"], "no-no"),
    # # --- Trung & Đông Âu ---
    # "Poland": (["Poland"], "pl-pl"),
    # "Czech": (["Czech Republic"], "cz-cs"),
    # "Slovakia": (["Slovakia"], "sk-sk"),
    # "Hungary": (["Hungary"], "hu-hu"),
    # "Romania": (["Romania"], "ro-ro"),
    # "Bulgaria": (["Bulgaria"], "bg-bg"),
    # "Slovenia": (["Slovenia"], "sl-sl"),
    # "Croatia": (["Croatia"], "hr-hr"),
    # "Estonia": (["Estonia"], "ee-et"),
    # "Latvia": (["Latvia"], "lv-lv"),
    # "Lithuania": (["Lithuania"], "lt-lt"),
    # # --- Nam Âu khác ---
    # "Greece": (["Greece"], "gr-el"),
    # "Turkey": (["Turkey"], "tr-tr"),
}

# Từ khoá sản phẩm riêng theo vùng (bổ sung vào pool khi bốc truy vấn)
REGION_PRODUCTS = {"USA": US_PRODUCTS}

MASTER_CSV = "fastener_companies_master.csv"
RESULTS_PER_QUERY = 15
SLEEP_RANGE = (1.5, 3.5)

# ---------------------------------------------------------------------------
# [1] UTILS — chuẩn hoá domain
# ---------------------------------------------------------------------------


def _clean_domain(url):
    """https://www.abc.com/x -> abc.com"""
    netloc = urlparse(url).netloc.lower().split(":")[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def _base_domain(domain):
    """sub.abc.com -> abc.com ; abc.co.uk -> abc.co.uk (xấp xỉ, đủ dùng)."""
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac",
                                         "gov"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


# ---------------------------------------------------------------------------
# [2] QUALIFICATION — xác minh tích cực từng kết quả
# ---------------------------------------------------------------------------

DOMAIN_BLOCKLIST = {
    # marketplaces / directories
    "alibaba.com", "made-in-china.com", "amazon.com", "ebay.com",
    "thomasnet.com", "europages.com", "europages.co.uk", "kompass.com",
    "globalsources.com", "indiamart.com", "tradeindia.com", "yellowpages.com",
    "yelp.com", "dnb.com", "zoominfo.com", "kellysearch.com", "wlw.de",
    "industrystock.com", "directindustry.com", "ensun.io", "bizvibe.com",
    "mfg.com", "macraesbluebook.com", "manta.com",
    "ec21.com", "turkishexporter.net", "tradekey.com", "ecplaza.net",
    "exportersindia.com", "go4worldbusiness.com",
    # mạng xã hội / bách khoa / tin tức
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "reddit.com", "quora.com",
    "pinterest.com", "medium.com", "glassdoor.com", "indeed.com",
    "crunchbase.com",
    # trang tin / so sánh ngành
    "accio.com", "chemeurope.com", "owler.com", "expometals.net",
    "fobshanghai.com", "fasteners.eu", "fastenerandfixing.com",
    "assemblymag.com", "volza.com", "importyeti.com", "panjiva.com",
    "google.com", "bing.com", "duckduckgo.com",
}

EXCLUDE_TLDS = {".in", ".cn", ".pk", ".tw", ".id", ".vn", ".my", ".th",
                ".ae"}

DOMAIN_JUNK = re.compile(
    r"(steelcentre|specialmetals|inconel|hastelloy|monel|metalalloys|"
    r"pipefitting|tubefitting|flanges?manufactur)", re.I)

TITLE_JUNK = re.compile(
    r"\b(top \d+|best \d+|list of|review|news|blog|wiki|forum|"
    r"china|chinese|india|indian|taiwan|vietnam)\b", re.I)

# Từ khoá đúng ngành (đa ngôn ngữ Âu-Mỹ) — dấu hiệu TÍCH CỰC
RELEVANT_TERMS = re.compile(
    r"(fasten|screw|bolt|washer|thread|stud|rivet|nut\b|nuts\b|fixing|"
    r"vis\b|visserie|boulon|tornillo|tuerca|viti\b|viteria|bullone|"
    r"gewinde|schraub|mutter|befestigung|bevestig|srub|śrub|wkręt|"
    r"vida\b|civata|bult|skruv|fästelement)", re.I)

# Từ khoá vai trò (manufacturer/distributor... đa ngôn ngữ)
ROLE_TERMS = re.compile(
    r"(manufactur|distribut|supplier|wholesal|import|producer|factory|"
    r"fabrication|hersteller|fabricant|produttore|fabricante|producent|"
    r"tillverkare|üretici|toptan|grossist|groothandel)", re.I)

# ccTLD -> quốc gia (xác minh địa lý). Thứ tự: đuôi dài xét trước.
CCTLD_COUNTRY = [
    (".co.uk", "UK"), (".org.uk", "UK"), (".uk", "UK"),
    (".com.tr", "Turkey"), (".tr", "Turkey"),
    (".de", "Germany"), (".fr", "France"), (".it", "Italy"),
    (".es", "Spain"), (".nl", "Netherlands"), (".pl", "Poland"),
    (".se", "Sweden"), (".us", "USA"),
    (".at", "Austria"), (".ch", "Switzerland"), (".be", "Belgium"),
    (".cz", "Czech"), (".dk", "Denmark"), (".fi", "Finland"),
    (".no", "Norway"), (".pt", "Portugal"), (".ie", "Ireland"),
    (".lu", "Luxembourg"), (".sk", "Slovakia"), (".hu", "Hungary"),
    (".ro", "Romania"), (".bg", "Bulgaria"), (".si", "Slovenia"),
    (".hr", "Croatia"), (".ee", "Estonia"), (".lv", "Latvia"),
    (".lt", "Lithuania"), (".gr", "Greece"),
    (".eu", "EU"),
]

# Ngưỡng phân loại theo confidence_score
QUALIFIED_MIN = 0.50
REVIEW_MIN = 0.25


def _cctld_country(domain):
    for tld, country in CCTLD_COUNTRY:
        if domain.endswith(tld):
            return country
    return ""


def qualify_result(title, url, region):
    """Chấm điểm 1 kết quả tìm kiếm. Trả về dict:

    status:  qualified | review | rejected
    confidence: 0..1
    verified_country: quốc gia suy ra từ ccTLD ('' nếu domain .com/.net...)
    reasons: danh sách reason-code (vì sao bị trừ/loại)
    """
    domain = _base_domain(_clean_domain(url))
    reasons = []

    # --- luật loại cứng (reason-coded) ---
    if not domain:
        return _q("rejected", 0.0, "", ["empty_domain"])
    if domain in DOMAIN_BLOCKLIST:
        return _q("rejected", 0.0, "", ["blocklisted_domain"])
    if any(domain.endswith(t) for t in EXCLUDE_TLDS):
        return _q("rejected", 0.0, "", ["excluded_tld"])
    if DOMAIN_JUNK.search(domain):
        return _q("rejected", 0.0, "", ["junk_domain_pattern"])
    if TITLE_JUNK.search(title or ""):
        return _q("rejected", 0.0, "", ["junk_title"])

    # --- xác minh tích cực ---
    score = 0.0
    if RELEVANT_TERMS.search(domain):
        score += 0.40
    else:
        reasons.append("domain_not_relevant")
    if RELEVANT_TERMS.search(title or ""):
        score += 0.30
    else:
        reasons.append("title_not_relevant")
    if ROLE_TERMS.search(title or ""):
        score += 0.10

    verified_country = _cctld_country(domain)
    if verified_country:
        if verified_country == region or verified_country == "EU":
            score += 0.20
        else:
            # ccTLD nước khác trong khối Âu-Mỹ: vẫn hợp lệ, ghi chú lại
            score += 0.10
            reasons.append("cctld_region_mismatch")
    else:
        score += 0.05  # .com/.net... không xác minh được nước

    score = round(min(score, 1.0), 2)
    if score >= QUALIFIED_MIN:
        status = "qualified"
    elif score >= REVIEW_MIN:
        status = "review"
    else:
        status = "rejected"
        reasons.append("low_relevance")

    # QUAN TRỌNG: domain .com/.net không mang bằng chứng quốc gia nào, nên
    # công ty Trung Quốc/Ấn Độ nhắm từ khoá "USA fastener supplier" trông
    # giống hệt công ty Mỹ thật. Vì vậy KHÔNG cho 'qualified' khi chưa xác
    # minh địa lý — phải chạy bước verify (đọc nội dung website) trước.
    if status == "qualified" and not verified_country:
        status = "review"
        reasons.append("geo_unverified")
    return _q(status, score, verified_country, reasons)


def _q(status, confidence, verified_country, reasons):
    return {"status": status, "confidence": confidence,
            "verified_country": verified_country, "reasons": reasons}


def qualify_dataframe(df, region_col="region"):
    """Bổ sung 4 cột qualification cho DataFrame có sẵn (làm sạch master cũ)."""
    out = df.copy()
    results = [
        qualify_result(str(r.get("company_name", "")), str(r["website"]),
                       str(r.get(region_col, "")))
        for _, r in out.iterrows()
    ]
    out["qualification_status"] = [r["status"] for r in results]
    out["confidence_score"] = [r["confidence"] for r in results]
    out["verified_country"] = [r["verified_country"] for r in results]
    out["rejection_reasons"] = ["; ".join(r["reasons"]) for r in results]
    return out


# ---------------------------------------------------------------------------
# [2b] GEO VERIFICATION — xác minh quốc gia bằng NỘI DUNG website
#      (search engine không cho biết công ty ở đâu; domain .com cũng không.
#       Cách duy nhất đáng tin: đọc trang web và tìm bằng chứng địa lý.)
# ---------------------------------------------------------------------------

# Quốc gia MỤC TIÊU (Mỹ + Châu Âu). Ngoài danh sách này -> loại.
TARGET_COUNTRIES = {
    "USA", "UK", "Germany", "France", "Italy", "Spain", "Netherlands",
    "Belgium", "Austria", "Switzerland", "Ireland", "Portugal", "Luxembourg",
    "Sweden", "Denmark", "Finland", "Norway", "Poland", "Czech", "Slovakia",
    "Hungary", "Romania", "Bulgaria", "Slovenia", "Croatia", "Estonia",
    "Latvia", "Lithuania", "Greece", "Turkey", "EU",
}

# Quốc gia LOẠI THẲNG khi phát hiện (nguồn SEO spam chính)
BANNED_COUNTRIES = {
    "China", "India", "Pakistan", "Taiwan", "Vietnam", "Thailand",
    "Indonesia", "Malaysia", "UAE",
}

US_STATE_NAMES = (
    r"Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|"
    r"Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
    r"Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
    r"Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|"
    r"New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
    r"Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|"
    r"Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming"
)
US_STATE_ABBR = (
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|"
    r"MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
    r"VT|VA|WA|WV|WI|WY"
)

# --- Dữ liệu CÓ CẤU TRÚC: quốc gia do chính công ty khai báo -----------------
# Chính xác hơn mọi heuristic: schema.org PostalAddress.addressCountry,
# og:locale, đơn vị tiền tệ. Phải đọc TRƯỚC khi _strip_code() bỏ <script>.

ISO2_COUNTRY = {
    "US": "USA", "GB": "UK", "UK": "UK", "DE": "Germany", "FR": "France",
    "IT": "Italy", "ES": "Spain", "NL": "Netherlands", "BE": "Belgium",
    "AT": "Austria", "CH": "Switzerland", "IE": "Ireland", "PT": "Portugal",
    "LU": "Luxembourg", "SE": "Sweden", "DK": "Denmark", "FI": "Finland",
    "NO": "Norway", "PL": "Poland", "CZ": "Czech", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "SI": "Slovenia",
    "HR": "Croatia", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "GR": "Greece", "TR": "Turkey",
    "CN": "China", "IN": "India", "TW": "Taiwan", "PK": "Pakistan",
    "VN": "Vietnam", "TH": "Thailand", "MY": "Malaysia", "ID": "Indonesia",
    "AE": "UAE",
}

NAME_COUNTRY = {
    "united states": "USA", "usa": "USA", "u.s.a.": "USA",
    "united kingdom": "UK", "great britain": "UK", "england": "UK",
    "deutschland": "Germany", "germany": "Germany",
    "france": "France", "italia": "Italy", "italy": "Italy",
    "españa": "Spain", "espana": "Spain", "spain": "Spain",
    "nederland": "Netherlands", "netherlands": "Netherlands",
    "holland": "Netherlands", "belgië": "Belgium", "belgique": "Belgium",
    "belgium": "Belgium", "österreich": "Austria", "austria": "Austria",
    "schweiz": "Switzerland", "suisse": "Switzerland",
    "switzerland": "Switzerland", "ireland": "Ireland",
    "portugal": "Portugal", "luxembourg": "Luxembourg",
    "sverige": "Sweden", "sweden": "Sweden", "danmark": "Denmark",
    "denmark": "Denmark", "suomi": "Finland", "finland": "Finland",
    "norge": "Norway", "norway": "Norway", "polska": "Poland",
    "poland": "Poland", "česko": "Czech", "czechia": "Czech",
    "czech republic": "Czech", "slovensko": "Slovakia",
    "slovakia": "Slovakia", "magyarország": "Hungary",
    "hungary": "Hungary", "românia": "Romania", "romania": "Romania",
    "bulgaria": "Bulgaria", "slovenija": "Slovenia", "slovenia": "Slovenia",
    "hrvatska": "Croatia", "croatia": "Croatia", "eesti": "Estonia",
    "estonia": "Estonia", "latvija": "Latvia", "latvia": "Latvia",
    "lietuva": "Lithuania", "lithuania": "Lithuania", "ελλάδα": "Greece",
    "greece": "Greece", "türkiye": "Turkey", "turkey": "Turkey",
    "china": "China", "中国": "China", "p.r.c": "China",
    "india": "India", "भारत": "India", "taiwan": "Taiwan",
    "pakistan": "Pakistan", "vietnam": "Vietnam", "viet nam": "Vietnam",
    "thailand": "Thailand", "malaysia": "Malaysia",
    "indonesia": "Indonesia", "united arab emirates": "UAE",
}

CURRENCY_COUNTRY = {"USD": "USA", "GBP": "UK", "CNY": "China",
                    "RMB": "China", "INR": "India", "TRY": "Turkey",
                    "PLN": "Poland", "SEK": "Sweden", "DKK": "Denmark",
                    "NOK": "Norway", "CHF": "Switzerland", "CZK": "Czech",
                    "HUF": "Hungary", "RON": "Romania"}


def _norm_country(value):
    """'US' / 'united states' / 'Deutschland' -> nhãn quốc gia của tool."""
    if not value or not isinstance(value, str):
        return ""
    v = value.strip()
    if len(v) == 2 and v.upper() in ISO2_COUNTRY:
        return ISO2_COUNTRY[v.upper()]
    return NAME_COUNTRY.get(v.lower().strip(". "), "")


def _walk_json(node):
    """Duyệt cây JSON, sinh ra từng (key, value) kể cả trong list lồng nhau."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk_json(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


# Trọng số: địa chỉ khai báo là bằng chứng MẠNH; còn og:locale chỉ là
# NGÔN NGỮ chứ không phải quốc gia (vd stauff.fr dùng en_GB -> không phải UK),
# nên chỉ được coi là gợi ý yếu, một mình không đủ xác minh.
STRUCT_STRONG, STRUCT_WEAK = 4.0, 1.0


def structured_country(html):
    """Đọc quốc gia KHAI BÁO trong dữ liệu có cấu trúc.

    Trả về (country, evidence_label, weight) — ('', '', 0.0) nếu không có.
    """
    import json as _json

    # 1) JSON-LD schema.org
    for m in re.finditer(
            r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
            html):
        raw = m.group(1).strip()
        try:
            data = _json.loads(raw)
        except Exception:
            continue
        for key, value in _walk_json(data):
            if key.lower() != "addresscountry":
                continue
            if isinstance(value, dict):
                value = value.get("name") or value.get("identifier") or ""
            country = _norm_country(value)
            if country:
                return country, "JSON-LD addressCountry", STRUCT_STRONG

    # 2) microdata: itemprop="addressCountry"
    m = re.search(
        r'itemprop=["\']addressCountry["\'][^>]*content=["\']([^"\']+)',
        html, re.I)
    if m:
        country = _norm_country(m.group(1))
        if country:
            return country, "microdata addressCountry", STRUCT_STRONG

    # 3) og:locale (vd de_DE, en_US)
    m = re.search(
        r'property=["\']og:locale["\'][^>]*content=["\']\w{2}[_-](\w{2})',
        html, re.I)
    if m:
        country = _norm_country(m.group(1))
        if country:
            return country, "og:locale (ngôn ngữ, gợi ý yếu)", STRUCT_WEAK

    # 4) đơn vị tiền tệ trong dữ liệu sản phẩm
    m = re.search(r'priceCurrency["\':\s]+([A-Z]{3})', html)
    if m:
        country = CURRENCY_COUNTRY.get(m.group(1).upper(), "")
        if country:
            return (country, f"priceCurrency {m.group(1).upper()}",
                    STRUCT_WEAK)
    return "", "", 0.0


def _strip_code(html):
    """Bỏ <script>/<style>/comment trước khi dò tín hiệu địa lý.

    JavaScript là nguồn dương tính giả lớn nhất: `e.timestamp+604800` từng
    bị nhận là số điện thoại Malaysia (+60...).
    """
    html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    return html


def _ph(cc):
    """Regex số điện thoại quốc tế cho mã vùng cc, chống dương tính giả.

    - Không khớp khi trước dấu '+' là chữ/số (loại `timestamp+604800`)
    - Phải có dấu phân cách sau mã vùng, HOẶC là dãy 8-12 số liền
    """
    return (rf"(?<![\w.>])\+\s?{cc}[\s\-./)]\s?\d[\d\s\-./()]{{5,}}"
            rf"|(?<![\w.>])\+{cc}\d{{8,12}}(?!\d)")


# (regex, trọng số, nhãn bằng chứng) — trọng số cao = bằng chứng mạnh
GEO_SIGNALS = {
    "China": [
        (r"ICP\s*备|ICP备|[京沪粤浙苏鲁冀津]ICP", 4.0, "giấy phép ICP"),
        (_ph("86"), 3.5, "điện thoại +86"),
        (r"\b(Ningbo|Wenzhou|Handan|Yongnian|Haiyan|Dongguan|Shenzhen|"
         r"Jiaxing|Hebei|Zhejiang|Jiangsu|Guangdong|Shandong|Xingtai|"
         r"Qingdao|Suzhou|Tianjin|Shanghai|Guangzhou)\b", 2.5, "địa danh TQ"),
        (r"Made in China|China factory|Chinese manufactur", 2.0, "'China'"),
        (r"[一-鿿]{4,}", 2.0, "chữ Hán"),
    ],
    "India": [
        (_ph("91"), 3.5, "điện thoại +91"),
        (r"\bGSTIN?\b|\bIS\s?1367\b|\bIEC\s?\d{10}\b", 3.0, "GST/IS 1367"),
        (r"\b(Mumbai|Ludhiana|Rajkot|Jamnagar|Ahmedabad|Pune|Chennai|"
         r"Kolkata|Maharashtra|Gujarat|Punjab|Tamil Nadu|Haryana|"
         r"Navi Mumbai|Thane)\b", 2.5, "địa danh Ấn Độ"),
        (r"₹|\bRs\.?\s?\d", 2.0, "giá rupee"),
    ],
    "Taiwan": [
        (_ph("886"), 3.5, "điện thoại +886"),
        (r"\b(Taichung|Kaohsiung|Tainan|Changhua|Taipei)\b", 2.5,
         "địa danh Đài Loan"),
    ],
    "Pakistan": [(_ph("92"), 3.5, "điện thoại +92"),
                 (r"\b(Karachi|Lahore|Sialkot)\b", 2.5, "địa danh Pakistan")],
    "Vietnam": [(_ph("84"), 3.5, "điện thoại +84")],
    "Thailand": [(_ph("66"), 3.5, "điện thoại +66")],
    "Malaysia": [(_ph("60"), 3.5, "điện thoại +60")],
    "Indonesia": [(_ph("62"), 3.5, "điện thoại +62")],
    "UAE": [(_ph("971"), 3.5, "điện thoại +971"),
            (r"\b(Dubai|Sharjah|Abu Dhabi)\b", 2.5, "địa danh UAE")],

    # --- Mỹ & Châu Âu ---
    "USA": [
        (rf"\b(?:{US_STATE_ABBR})[\s,]+\d{{5}}(?:-\d{{4}})?\b", 3.5,
         "địa chỉ bang + ZIP"),
        (r"\b(?:800|888|877|866|855)[\s\-.]\d{3}[\s\-.]\d{4}\b", 2.5,
         "hotline toll-free"),
        (rf"\b(?:{US_STATE_NAMES})\b", 2.0, "tên bang"),
        (r"\bUnited States\b|\bU\.S\.A\.|\bUSA\b", 1.5, "'USA'"),
        (r"\bASTM\s?A\d{2,3}\b|\bSAE\s?J\d{3}\b|\bANSI\b|\bUNC\b|\bUNF\b",
         1.5, "tiêu chuẩn Mỹ"),
        (r"\bInc\.|\bLLC\b|\bCorp\.", 1.0, "loại hình Inc/LLC"),
    ],
    "UK": [
        (_ph("44"), 3.0, "điện thoại +44"),
        (r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", 2.5, "mã bưu chính UK"),
        (r"\bVAT\s?(?:No\.?|number)?\s?GB\s?\d", 2.5, "VAT GB"),
        (r"\bLtd\b|\bLimited\b|\bPLC\b", 1.0, "loại hình Ltd"),
    ],
    "Germany": [
        (_ph("49"), 3.0, "điện thoại +49"),
        (r"\bUSt-IdNr\.?\s?DE\s?\d|\bDE\d{9}\b", 2.5, "VAT DE"),
        (r"\bGmbH\b|\bKG\b|\bAG\b", 1.5, "loại hình GmbH"),
        (r"\bImpressum\b|Stra(?:ß|ss)e\b|\bD-\d{5}\b", 1.5, "Impressum/địa chỉ"),
    ],
    "France": [(_ph("33"), 3.0, "điện thoại +33"),
               (r"\bSIRET\b|\bTVA\s?FR\d", 2.5, "SIRET/TVA FR"),
               (r"\bSARL\b|\bSAS\b|\bS\.A\.S\b", 1.5, "loại hình SARL/SAS")],
    "Italy": [(_ph("39"), 3.0, "điện thoại +39"),
              (r"\bP\.?\s?IVA\b|\bpartita IVA\b", 2.5, "P.IVA"),
              (r"\bS\.?r\.?l\.?\b|\bS\.?p\.?A\.?\b", 1.5, "loại hình Srl/SpA")],
    "Spain": [(_ph("34"), 3.0, "điện thoại +34"),
              (r"\bC\.?I\.?F\.?\s?[A-Z]\d{8}\b|\bNIF\b", 2.5, "CIF/NIF"),
              (r"\bS\.?L\.?U?\.?\b|\bS\.?A\.?\b", 1.0, "loại hình SL/SA")],
    "Netherlands": [(_ph("31"), 3.0, "điện thoại +31"),
                    (r"\bKvK\b|\bBTW\s?NL\d", 2.5, "KvK/BTW"),
                    (r"\bB\.?V\.?\b", 1.5, "loại hình BV")],
    "Belgium": [(_ph("32"), 3.0, "điện thoại +32"),
                (r"\bBTW\s?BE\d|\bTVA\s?BE\d", 2.5, "VAT BE")],
    "Austria": [(_ph("43"), 3.0, "điện thoại +43"),
                (r"\bATU\d{8}\b", 2.5, "VAT ATU")],
    "Switzerland": [(_ph("41"), 3.0, "điện thoại +41"),
                    (r"\bCHE-\d{3}\.\d{3}\.\d{3}\b", 2.5, "UID CHE")],
    "Ireland": [(_ph("353"), 3.0, "điện thoại +353")],
    "Portugal": [(_ph("351"), 3.0, "điện thoại +351"),
                 (r"\bLda\b|\bNIPC\b", 1.5, "loại hình Lda")],
    "Luxembourg": [(_ph("352"), 3.0, "điện thoại +352")],
    "Sweden": [(_ph("46"), 3.0, "điện thoại +46"),
               (r"\borganisationsnummer\b|\bSE\d{12}\b", 2.5, "org.nr"),
               (r"\bAB\b(?!\s?C)", 1.0, "loại hình AB")],
    "Denmark": [(_ph("45"), 3.0, "điện thoại +45"),
                (r"\bCVR\b|\bA/S\b|\bApS\b", 2.0, "CVR/A-S")],
    "Finland": [(_ph("358"), 3.0, "điện thoại +358"),
                (r"\bY-tunnus\b|\bOy\b(?:\s|$)", 2.0, "Y-tunnus/Oy")],
    "Norway": [(_ph("47"), 3.0, "điện thoại +47"),
               (r"\borganisasjonsnummer\b|\bAS\b(?=\s|,|\.)", 1.5, "org.nr")],
    "Poland": [(_ph("48"), 3.0, "điện thoại +48"),
               (r"\bNIP\b|\bREGON\b", 2.5, "NIP/REGON"),
               (r"Sp\.?\s?z\s?o\.?o\.?", 1.5, "loại hình Sp. z o.o.")],
    "Czech": [(_ph("420"), 3.0, "điện thoại +420"),
              (r"\bIČO?\b|\bDIČ\b|\bs\.r\.o\.", 2.0, "IČO/s.r.o.")],
    "Slovakia": [(_ph("421"), 3.0, "điện thoại +421")],
    "Hungary": [(_ph("36"), 3.0, "điện thoại +36"),
                (r"\bKft\.?\b|\badószám\b", 2.0, "Kft/adószám")],
    "Romania": [(_ph("40"), 3.0, "điện thoại +40"),
                (r"\bS\.?R\.?L\.?\b|\bCUI\b", 1.5, "SRL/CUI")],
    "Bulgaria": [(_ph("359"), 3.0, "điện thoại +359")],
    "Slovenia": [(_ph("386"), 3.0, "điện thoại +386")],
    "Croatia": [(_ph("385"), 3.0, "điện thoại +385")],
    "Estonia": [(_ph("372"), 3.0, "điện thoại +372")],
    "Latvia": [(_ph("371"), 3.0, "điện thoại +371")],
    "Lithuania": [(_ph("370"), 3.0, "điện thoại +370")],
    "Greece": [(_ph("30"), 3.0, "điện thoại +30")],
    "Turkey": [(_ph("90"), 3.0, "điện thoại +90"),
               (r"\bA\.?Ş\.?\b|Ltd\.?\s?Şti|\bSanayi\b|\bOSB\b", 2.0,
                "A.Ş./Sanayi"),
               (r"\b(İstanbul|Istanbul|İzmir|Bursa|Ankara|Konya|Gebze)\b",
                2.0, "địa danh TNK")],
}

GEO_SIGNALS_COMPILED = {
    country: [(re.compile(p), w, label) for p, w, label in pats]
    for country, pats in GEO_SIGNALS.items()
}

GEO_MIN_SCORE = 2.5   # điểm tối thiểu để coi là đã xác minh được nước


def detect_country_from_html(html):
    """Suy ra quốc gia từ nội dung trang. Trả về (country, score, evidence).

    country = '' nếu không đủ bằng chứng.
    """
    # Bước 1: dữ liệu có cấu trúc (công ty tự khai) — đáng tin nhất.
    declared, declared_label, declared_weight = structured_country(html)

    html = _strip_code(html)
    scores, evidence = {}, {}
    for country, pats in GEO_SIGNALS_COMPILED.items():
        total, found = 0.0, []
        for pat, weight, label in pats:
            if pat.search(html):
                total += weight
                found.append(label)
        if total:
            scores[country] = total
            evidence[country] = found
    # Khai báo có cấu trúc được cộng điểm rất cao (bằng chứng trực tiếp)
    if declared:
        scores[declared] = scores.get(declared, 0.0) + declared_weight
        evidence.setdefault(declared, []).insert(0, declared_label)

    if not scores:
        return "", 0.0, []

    # ƯU TIÊN TÍN HIỆU "LOẠI": công ty TQ/Ấn Độ bán hàng sang Mỹ thường
    # đăng cả địa chỉ bang + ZIP của Mỹ và tiêu chuẩn ASTM, nên điểm USA có
    # thể cao hơn. Nhưng công ty Mỹ thật gần như không bao giờ có số +86,
    # +91, mã GST hay chữ Hán. Vì vậy hễ nước bị loại đủ điểm là nó thắng.
    banned = {c: s for c, s in scores.items() if c in BANNED_COUNTRIES}
    if banned:
        worst = max(banned, key=banned.get)
        if banned[worst] >= GEO_MIN_SCORE:
            return worst, round(banned[worst], 1), evidence[worst]

    best = max(scores, key=scores.get)
    if scores[best] < GEO_MIN_SCORE:
        return "", round(scores[best], 1), evidence.get(best, [])
    return best, round(scores[best], 1), evidence[best]


def restatus_by_geo(current_status, detected_country, geo_score):
    """Quyết định lại status sau khi có bằng chứng địa lý.

    Trả về (status, reason_thêm).
    """
    if detected_country in BANNED_COUNTRIES:
        return "rejected", f"geo_outside_target:{detected_country}"
    if detected_country in TARGET_COUNTRIES:
        if current_status == "rejected":
            return "rejected", ""          # đã bị loại vì lý do khác
        return "qualified", ""
    return current_status, "geo_unverified"


# ---------------------------------------------------------------------------
# [2c] REGISTRY VERIFICATION — xác minh bằng ĐĂNG KÝ CHÍNH THỨC
#      Mạnh hơn mọi heuristic: mã số thuế VAT do nhà nước cấp, tra qua
#      VIES (EU, miễn phí, không cần key) trả về quốc gia đăng ký + tên
#      pháp lý. GLEIF (mã LEI) và UK Companies House bổ sung.
# ---------------------------------------------------------------------------

# Định dạng mã số thuế VAT của EU (có tiền tố quốc gia).
# UK không còn trong VIES sau Brexit -> dùng Companies House thay thế.
VAT_PATTERNS = {
    "DE": r"DE\s?(\d{9})",
    "NL": r"NL\s?(\d{9}\s?B\s?\d{2})",
    "FR": r"FR\s?([A-Z0-9]{2}\s?\d{9})",
    "IT": r"IT\s?(\d{11})",
    "ES": r"ES\s?([A-Z]\d{7}[A-Z0-9])",
    "BE": r"BE\s?(0?\d{9})",
    "AT": r"ATU\s?(\d{8})",
    "PL": r"PL\s?(\d{10})",
    "SE": r"SE\s?(\d{12})",
    "DK": r"DK\s?(\d{8})",
    "FI": r"FI\s?(\d{8})",
    "IE": r"IE\s?(\d{7}[A-Z]{1,2})",
    "PT": r"PT\s?(\d{9})",
    "CZ": r"CZ\s?(\d{8,10})",
    "SK": r"SK\s?(\d{10})",
    "HU": r"HU\s?(\d{8})",
    "RO": r"RO\s?(\d{6,10})",
    "BG": r"BG\s?(\d{9,10})",
    "SI": r"SI\s?(\d{8})",
    "HR": r"HR\s?(\d{11})",
    "EE": r"EE\s?(\d{9})",
    "LV": r"LV\s?(\d{11})",
    "LT": r"LT\s?(\d{9}|\d{12})",
    "LU": r"LU\s?(\d{8})",
    "EL": r"(?:EL|GR)\s?(\d{9})",
}
VAT_COMPILED = {cc: re.compile(p, re.I) for cc, p in VAT_PATTERNS.items()}

VIES_URL = ("https://ec.europa.eu/taxation_customs/vies/rest-api/ms/"
            "{cc}/vat/{num}")
GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
REGISTRY_MIN_INTERVAL = 0.6      # giây giữa 2 lần gọi (tôn trọng rate limit)
_registry_cache = {}
_registry_last_call = [0.0]


def extract_vat_ids(html):
    """Trích mã số thuế VAT (EU) từ HTML. Trả về [(country_code, number)]."""
    text = _strip_code(html)
    found = []
    for cc, pat in VAT_COMPILED.items():
        for m in pat.finditer(text):
            num = re.sub(r"\s+", "", m.group(1)).upper()
            if (cc, num) not in found:
                found.append((cc, num))
    return found[:5]


def _throttle():
    import time as _t
    wait = REGISTRY_MIN_INTERVAL - (_t.monotonic() - _registry_last_call[0])
    if wait > 0:
        _t.sleep(wait)
    _registry_last_call[0] = _t.monotonic()


def verify_vat_vies(country_code, number, timeout=25):
    """Tra mã số thuế qua VIES. Trả về dict:

    valid / country / name / status ('ok' | 'invalid' | 'unavailable')
    """
    import requests

    key = ("vies", country_code, number)
    if key in _registry_cache:
        return _registry_cache[key]

    result = {"valid": False, "country": "", "name": "",
              "status": "unavailable"}
    try:
        _throttle()
        r = requests.get(
            VIES_URL.format(cc=country_code, num=number),
            headers={"Accept": "application/json",
                     "User-Agent": HTTP_HEADERS["User-Agent"]},
            timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if data.get("isValid"):
                name = (data.get("name") or "").strip()
                result = {
                    "valid": True,
                    "country": ISO2_COUNTRY.get(country_code.upper(), ""),
                    "name": "" if name in ("---", "") else name,
                    "status": "ok"}
            else:
                result["status"] = "invalid"
    except Exception:
        pass
    _registry_cache[key] = result
    return result


def _norm_name(name):
    """Chuẩn hoá tên để so khớp: bỏ loại hình DN, dấu câu, khoảng trắng."""
    n = re.sub(r"[^\w\s]", " ", (name or "").lower())
    n = re.sub(r"\b(gmbh|ag|kg|ohg|bv|nv|sa|sas|sarl|srl|spa|ltd|limited|"
               r"plc|inc|llc|corp|corporation|company|co|ab|as|oy|aps|"
               r"sp|z|o|zoo|kft|sro|doo|lda|group|holding|international)\b",
               " ", n)
    return re.sub(r"\s+", " ", n).strip()


def verify_name_gleif(company_name, timeout=25):
    """Tra tên công ty trong GLEIF (mã LEI). Trả về (country, legal_name).

    Chỉ nhận khi tên khớp đủ chặt để tránh nhận nhầm công ty khác.
    """
    import requests

    key = ("gleif", (company_name or "").lower())
    if key in _registry_cache:
        return _registry_cache[key]

    out = ("", "")
    target = _norm_name(company_name)
    if len(target) >= 4:
        try:
            _throttle()
            r = requests.get(
                GLEIF_URL,
                params={"filter[entity.legalName]": company_name,
                        "page[size]": 5},
                headers={"Accept": "application/vnd.api+json",
                         "User-Agent": HTTP_HEADERS["User-Agent"]},
                timeout=timeout)
            if r.status_code == 200:
                for rec in r.json().get("data", []):
                    entity = rec.get("attributes", {}).get("entity", {})
                    legal = (entity.get("legalName") or {}).get("name", "")
                    country = ISO2_COUNTRY.get(
                        (entity.get("legalAddress") or {}).get("country", ""),
                        "")
                    if not country:
                        continue
                    got = _norm_name(legal)
                    if got == target or got.startswith(target) or \
                            target.startswith(got):
                        out = (country, legal)
                        break
        except Exception:
            pass
    _registry_cache[key] = out
    return out


def registry_country_for_site(vat_ids, company_name="", use_gleif=True):
    """Xác minh quốc gia bằng đăng ký chính thức.

    Trả về (country, source_label, official_name).
    """
    for cc, num in vat_ids:
        res = verify_vat_vies(cc, num)
        if res["valid"] and res["country"]:
            return (res["country"], f"VIES VAT {cc}{num}", res["name"])
    if use_gleif and company_name:
        country, legal = verify_name_gleif(company_name)
        if country:
            return country, "GLEIF LEI", legal
    return "", "", ""


# --- UK Companies House: KHÁM PHÁ công ty mới theo mã ngành SIC -------------
# SIC 25940 = "Manufacture of fasteners and screw machine products"
# Cần API key miễn phí: https://developer.company-information.service.gov.uk
# -> đặt biến môi trường COMPANIES_HOUSE_KEY
CH_API = "https://api.company-information.service.gov.uk/advanced-search/companies"
CH_SIC_FASTENERS = ["25940", "25930", "46740"]  # fasteners, wire, hardware WS


def discover_uk_companies(sic_codes=None, size=100, max_pages=5,
                          api_key=None, verbose=True):
    """Lấy công ty Anh theo mã ngành SIC từ Companies House (cần API key).

    Đây là DỮ LIỆU ĐĂNG KÝ CHÍNH THỨC: mọi công ty đều thật và ở UK.
    """
    import requests

    api_key = api_key or os.environ.get("COMPANIES_HOUSE_KEY", "")
    if not api_key:
        print("  (!) Chưa có COMPANIES_HOUSE_KEY -> bỏ qua Companies House.\n"
              "      Lấy key miễn phí tại "
              "https://developer.company-information.service.gov.uk\n"
              "      rồi: export COMPANIES_HOUSE_KEY=xxx")
        return pd.DataFrame()

    sic_codes = sic_codes or CH_SIC_FASTENERS
    rows = {}
    for sic in sic_codes:
        for page in range(max_pages):
            try:
                _throttle()
                r = requests.get(
                    CH_API, auth=(api_key, ""),
                    params={"sic_codes": sic, "size": size,
                            "start_index": page * size,
                            "company_status": "active"},
                    headers={"Accept": "application/json"}, timeout=30)
            except Exception as e:
                print(f"  !! Companies House lỗi: {e}")
                break
            if r.status_code == 401:
                print("  !! API key Companies House không hợp lệ")
                return pd.DataFrame()
            if r.status_code != 200:
                break
            items = r.json().get("items", [])
            if not items:
                break
            for it in items:
                name = (it.get("company_name") or "").strip()
                number = it.get("company_number") or ""
                if not name or number in rows:
                    continue
                addr = it.get("registered_office_address") or {}
                rows[number] = {
                    "company_name": name.title(),
                    "website": "",
                    "region": "UK",
                    "qualification_status": "review",
                    "confidence_score": 0.90,
                    "verified_country": "UK",
                    "registry_country": "UK",
                    "registry_source": f"Companies House SIC {sic}",
                    "registry_name": name,
                    "rejection_reasons": "no_website_yet",
                    "found_by_query": f"registry:companies-house:{sic}",
                    "source": "registry:companies-house",
                    "company_number": number,
                    "registered_address": ", ".join(
                        str(addr.get(k, "")) for k in
                        ("address_line_1", "locality", "postal_code")
                        if addr.get(k)),
                }
            if verbose:
                print(f"  SIC {sic}: trang {page + 1} -> tổng {len(rows)}")
            if len(items) < size:
                break
    df = pd.DataFrame(rows.values())
    if verbose:
        print(f"  Companies House -> {len(df)} công ty Anh (đăng ký chính "
              f"thức)")
    return df


# ---------------------------------------------------------------------------
# [3] SEARCH — sinh truy vấn + gọi DuckDuckGo
# ---------------------------------------------------------------------------


def build_queries(products=None, roles=None, regions=None, deep_geo=False):
    """Bộ truy vấn đầy đủ (tích Descartes) cho chế độ quét 1 lần.

    deep_geo=False: mỗi vùng chỉ dùng từ khoá địa lý đầu tiên ("USA").
    deep_geo=True : dùng MỌI từ khoá địa lý của vùng (với USA là từng bang)
                    và cộng thêm REGION_PRODUCTS của vùng đó -> quét sâu.
    """
    base_products = products or PRODUCTS
    roles = roles or ROLES
    regions = regions or REGIONS
    out = []
    for label, (geo_terms, code) in regions.items():
        geos = geo_terms if deep_geo else geo_terms[:1]
        prods = base_products
        if deep_geo and products is None:
            prods = base_products + REGION_PRODUCTS.get(label, [])
        for geo in geos:
            for p in prods:
                for r in roles:
                    out.append((f"{p} {r} {geo} company", label, code))
    # khử trùng lặp theo nội dung truy vấn, giữ thứ tự
    return list({q[0]: q for q in out}.values())


def build_cycle_queries(n_queries=60, seed=None, regions=None):
    """Bộ truy vấn NGẪU NHIÊN cho 1 vòng của chế độ chạy liên tục.

    Bốc ngẫu nhiên cả từ khoá địa lý trong vùng (với USA = từng bang) và
    sản phẩm đặc thù vùng (REGION_PRODUCTS) -> tự động quét sâu dần.
    """
    rng = random.Random(seed)
    regions = regions or REGIONS
    region_items = list(regions.items())
    queries = {}
    attempts = 0
    # sinh cho đủ n_queries truy vấn DUY NHẤT (tối đa 20*n lần thử)
    while len(queries) < n_queries and attempts < n_queries * 20:
        attempts += 1
        label, (geo_terms, code) = rng.choice(region_items)
        pool = PRODUCTS + EXTRA_PRODUCTS + REGION_PRODUCTS.get(label, [])
        q = " ".join(x for x in [rng.choice(pool), rng.choice(ROLES),
                                 rng.choice(geo_terms),
                                 rng.choice(EXTRA_MODIFIERS)] if x)
        queries.setdefault(q, (q, label, code))
    return list(queries.values())


def search_companies(queries, results_per_query=RESULTS_PER_QUERY,
                     sleep_range=SLEEP_RANGE, verbose=True,
                     known_domains=None, keep_review=True):
    """Chạy tìm kiếm + qualification. Trả về (DataFrame, report dict).

    known_domains: domain đã có -> bỏ qua (chế độ tích luỹ).
    keep_review:   giữ cả hàng 'review' (mặc định) hay chỉ 'qualified'.
    """
    if DDGS is None:
        raise RuntimeError("Thiếu thư viện ddgs: pip install ddgs")
    known_domains = set(known_domains or set())
    rows = {}
    report = {"queries_ok": 0, "queries_failed": 0, "candidates": 0,
              "rejected": 0, "review": 0, "qualified": 0, "duplicates": 0}
    ddgs = DDGS()

    for i, (query, region_label, ddgs_region) in enumerate(queries, 1):
        if verbose:
            print(f"[{i}/{len(queries)}] {query}")
        try:
            results = ddgs.text(query, region=ddgs_region,
                                max_results=results_per_query)
            report["queries_ok"] += 1
        except Exception as e:
            report["queries_failed"] += 1
            print(f"    !! lỗi, bỏ qua truy vấn này: {e}")
            time.sleep(10)
            continue

        for r in results or []:
            url = r.get("href") or r.get("url") or ""
            title = (r.get("title") or "").strip()
            if not url:
                continue
            report["candidates"] += 1
            domain = _base_domain(_clean_domain(url))
            if domain in known_domains or domain in rows:
                report["duplicates"] += 1
                continue

            q = qualify_result(title, url, region_label)
            if q["status"] == "rejected":
                report["rejected"] += 1
                continue
            if q["status"] == "review" and not keep_review:
                report["review"] += 1
                continue
            report[q["status"]] += 1

            rows[domain] = {
                "company_name": re.split(r"[|\-–—:]", title)[0].strip(),
                "website": f"https://{domain}",
                "region": region_label,
                "qualification_status": q["status"],
                "confidence_score": q["confidence"],
                "verified_country": q["verified_country"],
                "rejection_reasons": "; ".join(q["reasons"]),
                "found_by_query": query,
            }
        time.sleep(random.uniform(*sleep_range))

    df = pd.DataFrame(rows.values())
    if not df.empty:
        df = df.sort_values(["region", "confidence_score"],
                            ascending=[True, False]).reset_index(drop=True)
    return df, report


def _print_report(report):
    print(f"   truy vấn: {report['queries_ok']} ok, "
          f"{report['queries_failed']} lỗi | "
          f"ứng viên: {report['candidates']} "
          f"(qualified {report['qualified']}, review {report['review']}, "
          f"loại {report['rejected']}, trùng {report['duplicates']})")


# ---------------------------------------------------------------------------
# [3b] DIRECTORY HARVEST — lấy công ty từ danh bạ HỘI NGÀNH
#      Đây là nguồn sạch nhất: hội viên đã được hội kiểm duyệt là doanh
#      nghiệp thật trong ngành, và QUỐC GIA là thuộc tính của chính hội
#      (NFDA/PacWest = hội Mỹ) chứ không phải thứ phải suy đoán như khi
#      quét search engine.
# ---------------------------------------------------------------------------

DIRECTORIES = [
    {"name": "NFDA",
     "url": "https://www.nfda-fastener.org/member-list1",
     "country": "USA",
     "note": "National Fastener Distributors Association (Mỹ)"},
    {"name": "PacWest",
     "url": "https://www.pac-west.org/member-list",
     "country": "USA",
     "note": "Pacific-West Fastener Association (Mỹ)"},
    {"name": "EFDA",
     "url": "https://www.efda-fastenerdistributors.org/de/members",
     "country": "",   # hội châu Âu: lấy nước theo ccTLD của từng hội viên
     "note": "European Fastener Distributor Association (Châu Âu)"},
]

# Nền tảng quản lý hội viên / CDN — khớp theo CHUỖI CON (tên rất đặc thù,
# không trùng với tên công ty thật)
DIRECTORY_SKIP = re.compile(
    r"(memberclicks|growthzone|azureedge|cloudflare|fontawesome|gstatic|"
    r"jquery|bootstrapcdn|eventbrite|mailchimp|constantcontact|gravatar|"
    r"wordpress|googletagmanager|doubleclick)", re.I)

# Các domain phải khớp CHÍNH XÁC. Trước đây dùng chuỗi con nên `x\.com`
# (chặn Twitter/X) khớp luôn metfix.com.pl / fixdex.com / phoenix.com, và
# `bing` khớp binghamfasteners.com -> loại oan công ty thật.
DIRECTORY_SKIP_EXACT = {
    "x.com", "vimeo.com", "flickr.com", "xing.com", "paypal.com",
    "google.com", "bing.com", "apple.com", "microsoft.com", "adobe.com",
    "wp.com", "gmpg.org", "w3.org", "schema.org", "gstatic.com",
}


# Danh bạ có lẫn HỘI NGÀNH, TRIỂN LÃM, tạp chí — không phải nhà cung cấp
NON_COMPANY = re.compile(
    r"(association|assoc\.|federation|verband|institute|föreningen|"
    r"\bexpo\b|exhibition|trade ?show|fastenershows|conference|congress|"
    r"magazine|journal|\bmedia\b|university|college|chamber of commerce)",
    re.I)

# Hội viên là hội quốc gia thường có tên viết tắt toàn chữ hoa (FDS, NEVIB,
# BIAFD...) -> không loại hẳn, chỉ hạ xuống 'review' để người dùng tự duyệt
ACRONYM_ONLY = re.compile(r"^[A-Z][A-Z&.\- ]{1,7}$")


GENERIC_ANCHOR = re.compile(
    r"(?i)^(website|web ?site|web|visit(\s+(us|website))?|click here|more|"
    r"link|home ?page|url|www\.?|info|details|read more)\.?$")


def _name_country_before_anchor(html, pos):
    """Nhiều danh bạ đặt tên công ty TRƯỚC link, anchor chỉ ghi 'Website'.

    Vd EFDA: <strong>Bendkopp Group</strong> (Romania) <a ...>Website</a>
    -> lấy tên trong <strong>/<b> gần nhất và quốc gia trong ngoặc.
    """
    import html as _html

    ctx = html[max(0, pos - 400):pos]
    ctx_country_zone = ctx[-150:]   # nước phải ở NGAY trước link
    names = re.findall(r"(?is)<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>", ctx)
    name = ""
    if names:
        name = _html.unescape(re.sub(r"<[^>]+>", " ", names[-1]))
        name = re.sub(r"\s+", " ", name).strip(" -–—|,")
    country = ""
    for candidate in reversed(re.findall(r"\(([^)]{3,30})\)",
                                        ctx_country_zone)):
        country = _norm_country(candidate.strip())
        if country:
            break
    return name, country


def _name_from_domain(domain):
    """metfix.com.pl -> Metfix (phương án cuối khi không có tên)."""
    label = domain.split(".")[0].replace("-", " ").replace("_", " ")
    return label.title()


def parse_directory_html(html, base_url, source_name, country=""):
    """Trích (tên công ty, website) từ HTML một trang danh bạ hội viên.

    Dùng anchor text làm tên công ty — sạch hơn nhiều so với cắt tiêu đề
    trang từ kết quả tìm kiếm.
    """
    import html as _html

    own_domain = _base_domain(_clean_domain(base_url))
    rows = {}
    for m in re.finditer(
            r"<a[^>]+href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>",
            html, re.I | re.S):
        href, inner = m.group(1), m.group(2)
        domain = _base_domain(_clean_domain(href))
        if (not domain or domain == own_domain
                or domain in DOMAIN_BLOCKLIST
                or domain in DIRECTORY_SKIP_EXACT
                or DIRECTORY_SKIP.search(domain)):
            continue

        name = _html.unescape(re.sub(r"<[^>]+>", " ", inner))
        name = re.sub(r"\s+", " ", name).strip(" -–—|,")
        ctx_country = ""
        # anchor rỗng / chung chung ("Website") -> lấy tên đứng trước link,
        # hoặc alt của logo, cuối cùng mới suy từ tên miền
        if not name or len(name) < 3 or GENERIC_ANCHOR.match(name):
            ctx_name, ctx_country = _name_country_before_anchor(html,
                                                                m.start())
            alt = re.search(r'alt=["\']([^"\']{3,80})["\']', inner, re.I)
            name = ctx_name or (alt.group(1).strip() if alt else "")
            if not name:
                name = _name_from_domain(domain)
        if name.lower().startswith("http"):
            name = _name_from_domain(domain)
        if NON_COMPANY.search(name) or NON_COMPANY.search(domain):
            continue
        if domain in rows and len(rows[domain]["company_name"]) >= len(name):
            continue

        verified = country or ctx_country or _cctld_country(domain)
        is_acronym = bool(ACRONYM_ONLY.match(name))
        rows[domain] = {
            "company_name": name[:120],
            "website": f"https://{domain}",
            "region": verified or "Europe",
            # hội viên hội ngành => đúng ngành, đúng vùng (theo hội)
            "qualification_status": (
                "qualified" if verified in TARGET_COUNTRIES and not is_acronym
                else "review"),
            "confidence_score": (0.95 if verified and not is_acronym
                                 else 0.60),
            "verified_country": verified if verified in TARGET_COUNTRIES
                                else "",
            "rejection_reasons": "; ".join(
                x for x in ["" if verified else "geo_unverified",
                            "possible_association" if is_acronym else ""]
                if x),
            "found_by_query": f"directory:{source_name}",
            "source": f"directory:{source_name}",
        }
    return list(rows.values())


# --- Seed files: danh bạ chỉ xem được sau khi render JS -------------------
# Một số danh bạ (MWFA...) là SPA nên `requests` không đọc được. Ta trích một
# lần bằng trình duyệt rồi lưu thành CSV trong repo `seeds/` — nhờ vậy tool
# vẫn chạy được ở mọi nơi (CLI, Colab, CI) mà không cần trình duyệt.
SEEDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds")


def harvest_seeds(seeds_dir=None, verbose=True):
    """Nạp mọi file CSV trong seeds/ thành DataFrame công ty."""
    seeds_dir = seeds_dir or SEEDS_DIR
    if not os.path.isdir(seeds_dir):
        return pd.DataFrame()
    frames = []
    for fname in sorted(os.listdir(seeds_dir)):
        if not fname.endswith(".csv"):
            continue
        path = os.path.join(seeds_dir, fname)
        try:
            seed = pd.read_csv(path).fillna("")
        except Exception as e:
            print(f"  !! seed {fname}: {e}")
            continue
        rows = []
        for _, r in seed.iterrows():
            website = str(r.get("website", "")).strip()
            domain = _base_domain(_clean_domain(website))
            if not domain:
                continue
            country = str(r.get("country", "")).strip() or \
                _cctld_country(domain)
            source = str(r.get("source", "")).strip() or f"seed:{fname}"
            rows.append({
                "company_name": str(r.get("company_name", "")).strip(),
                "website": f"https://{domain}",
                "region": country or "Europe",
                "qualification_status": ("qualified" if country in
                                         TARGET_COUNTRIES else "review"),
                "confidence_score": 0.95 if country else 0.60,
                "verified_country": country if country in TARGET_COUNTRIES
                                    else "",
                "rejection_reasons": "" if country else "geo_unverified",
                "found_by_query": source,
                "source": source,
            })
        if verbose:
            print(f"  seed {fname:24} -> {len(rows):4} công ty")
        frames.append(pd.DataFrame(rows))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def harvest_directory(cfg, verbose=True):
    """Tải 1 danh bạ và trả về DataFrame công ty."""
    page, reason = _fetch(cfg["url"])
    if page is None:
        if verbose:
            print(f"  !! {cfg['name']}: không tải được ({reason})")
        return pd.DataFrame()
    rows = parse_directory_html(page[0], page[1], cfg["name"],
                               cfg.get("country", ""))
    if verbose:
        print(f"  {cfg['name']:10} -> {len(rows):4} công ty "
              f"({cfg.get('note', '')})")
    return pd.DataFrame(rows)


def harvest_directories(configs=None, out_csv="fastener_companies_directory.csv",
                        merge_into=None, verbose=True):
    """Thu hoạch TẤT CẢ danh bạ; tuỳ chọn gộp vào file tổng (khử trùng lặp).

    Trả về DataFrame các công ty lấy từ danh bạ.
    """
    configs = configs or DIRECTORIES
    if verbose:
        print(f"Thu hoạch {len(configs)} danh bạ hội ngành:")
    frames = [harvest_directory(c, verbose=verbose) for c in configs]
    seeds = harvest_seeds(verbose=verbose)
    if not seeds.empty:
        frames.append(seeds)
    frames = [f for f in frames if not f.empty]
    if not frames:
        print("Không lấy được công ty nào từ danh bạ.")
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["added_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    # khử trùng lặp: giữ dòng có điểm cao nhất cho mỗi domain
    df = (df.sort_values("confidence_score", ascending=False)
            .drop_duplicates("website", keep="first")
            .reset_index(drop=True))
    if verbose:
        print(f"\n>> Tổng {len(df)} công ty duy nhất từ danh bạ")
        print(df["region"].value_counts().to_string())

    if out_csv:
        save_tables(df, out_csv)

    if merge_into:
        master, known = _load_master(merge_into)
        fresh = df[~df["website"].map(
            lambda u: _base_domain(_clean_domain(u))).isin(known)]
        merged = pd.concat([master, fresh], ignore_index=True)
        save_tables(merged, merge_into)
        print(f">> Gộp vào {merge_into}: thêm {len(fresh)} công ty mới "
              f"(tổng {len(merged)})")
        return merged
    return df


# ---------------------------------------------------------------------------
# [4] EMAIL — quét email trên website, có provenance + retry
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
EMAIL_JUNK = re.compile(
    r"(\.png|\.jpe?g|\.gif|\.svg|\.webp|\.css|\.js|"
    r"example\.|sentry\.|wixpress|sitename|yourdomain|domain\.com|"
    r"@\d+\.\d+|no-?reply|@(schema|w3)\.org)", re.I)
CONTACT_HINTS = (
    "contact", "kontakt", "impressum", "about", "imprint", "legal",
    "contacto", "contatti", "contact-us", "contactus", "kontakty",
    "uber-uns", "chi-siamo", "quienes-somos", "mentions-legales",
)
HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en;q=0.9,de;q=0.8,fr;q=0.7",
}
FETCH_TIMEOUT = 12
FETCH_RETRIES = 2
MAX_RESPONSE_BYTES = 2_000_000   # 2MB — chặn file khổng lồ
MAX_CONTACT_PAGES = 3
MAX_EMAILS_PER_SITE = 5
EMAIL_WORKERS = 10
CHECKPOINT_EVERY = 50            # lưu tạm sau mỗi N website


def _decode_cfemail(hex_str):
    """Giải mã email bị Cloudflare che (data-cfemail="...")."""
    try:
        data = bytes.fromhex(hex_str)
        key = data[0]
        return "".join(chr(b ^ key) for b in data[1:])
    except Exception:
        return None


def _extract_emails_from_html(html):
    emails = set()
    for m in EMAIL_RE.findall(html.replace("mailto:", " ")):
        emails.add(m.lower().strip("."))
    for hexstr in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html):
        decoded = _decode_cfemail(hexstr)
        if decoded and EMAIL_RE.fullmatch(decoded):
            emails.add(decoded.lower())
    for m in re.findall(
            r"([a-zA-Z0-9._%+\-]+)\s*[\[(]\s*at\s*[\])]\s*"
            r"([a-zA-Z0-9.\-]+)\s*[\[(]\s*dot\s*[\])]\s*([a-zA-Z]{2,})",
            html, re.I):
        emails.add(f"{m[0]}@{m[1]}.{m[2]}".lower())
    return {e for e in emails if not EMAIL_JUNK.search(e)}


def _find_contact_links(html, base_url):
    links, seen, out = [], set(), []
    for href in re.findall(r'href=["\']([^"\'#]+)["\']', html, re.I):
        low = href.lower()
        if any(h in low for h in CONTACT_HINTS) and not low.startswith(
                ("mailto:", "tel:", "javascript:")):
            full = urljoin(base_url, href)
            if urlparse(full).netloc == urlparse(base_url).netloc:
                links.append(full)
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out[:MAX_CONTACT_PAGES]


def _fetch(url):
    """GET có retry + kiểm tra status/content-type + giới hạn dung lượng.

    Trả về ((html, final_url), 'ok') hoặc (None, reason):
    reason ∈ blocked | http_4xx/5xx | not_html | timeout | error
    """
    import requests

    reason = "error"
    for attempt in range(FETCH_RETRIES):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS,
                                timeout=FETCH_TIMEOUT, allow_redirects=True,
                                stream=True)
            if resp.status_code in (403, 429):
                return None, "blocked"
            if resp.status_code >= 400:
                return None, f"http_{resp.status_code}"
            ctype = resp.headers.get("content-type", "")
            if ctype and "html" not in ctype and "text" not in ctype:
                return None, "not_html"
            raw = resp.raw.read(MAX_RESPONSE_BYTES, decode_content=True)
            html = raw.decode(resp.encoding or "utf-8", errors="ignore")
            return (html, str(resp.url)), "ok"
        except requests.Timeout:
            reason = "timeout"
        except Exception:
            reason = "error"
        time.sleep(1)
    return None, reason


def _classify_email(email, site_domain):
    """same_domain | related_domain | external"""
    edomain = email.split("@")[-1]
    if edomain == site_domain or edomain.endswith("." + site_domain):
        return "same_domain"
    site_name = site_domain.split(".")[0]
    if len(site_name) >= 4 and site_name in edomain:
        return "related_domain"
    return "external"


def scrape_site(website):
    """Quét 1 website MỘT LẦT cho cả email và bằng chứng địa lý.

    Trả về dict:
      emails: [(email, class, source_url)] — same/related trước, external sau
      status: found | not_found | timeout | blocked | error | http_*
      country / geo_score / geo_evidence: bằng chứng quốc gia từ nội dung
    """
    site_domain = _base_domain(_clean_domain(website))
    found = {}          # email -> (class, source_url)
    html_all = []       # gom HTML để dò tín hiệu địa lý

    page, reason = _fetch(website)
    if page is None:
        return {"emails": [], "status": reason, "country": "",
                "geo_score": 0.0, "geo_evidence": [], "vat_ids": [],
                "site_name": "", "roles": [], "products": [], "phones": []}
    html, final_url = page
    html_all.append(html)

    for e in _extract_emails_from_html(html):
        found.setdefault(e, (_classify_email(e, site_domain), final_url))

    # Trang contact/impressum vừa nhiều email vừa nhiều bằng chứng địa chỉ
    for link in _find_contact_links(html, final_url):
        sub, _ = _fetch(link)
        if sub is None:
            continue
        html_all.append(sub[0])
        if len(found) < MAX_EMAILS_PER_SITE:
            for e in _extract_emails_from_html(sub[0]):
                found.setdefault(e, (_classify_email(e, site_domain), link))

    order = {"same_domain": 0, "related_domain": 1, "external": 2}
    ranked = sorted(((e, c, s) for e, (c, s) in found.items()),
                    key=lambda x: (order[x[1]], x[0]))[:MAX_EMAILS_PER_SITE]

    all_html = "\n".join(html_all)
    country, geo_score, evidence = detect_country_from_html(all_html)
    # Email .cn/.in cũng là bằng chứng địa lý mạnh
    if not country:
        for e, _c, _s in ranked:
            tld = "." + e.split(".")[-1]
            for banned_tld, banned_country in ((".cn", "China"),
                                               (".in", "India"),
                                               (".tw", "Taiwan")):
                if tld == banned_tld:
                    country, geo_score = banned_country, 3.0
                    evidence = [f"email {banned_tld}"]

    return {"emails": ranked, "status": "found" if ranked else "not_found",
            "country": country, "geo_score": geo_score,
            "geo_evidence": evidence,
            # mã số thuế để tra ĐĂNG KÝ CHÍNH THỨC (không gọi mạng ở đây)
            "vat_ids": extract_vat_ids(all_html),
            # công ty LÀ GÌ / BÁN GÌ / tên tự khai / điện thoại
            "site_name": structured_company_name(html),
            "roles": detect_roles(all_html),
            "products": detect_products(all_html),
            "phones": extract_phones(all_html, country)}


def scrape_emails_for_site(website):
    """Tương thích ngược: chỉ trả phần email của scrape_site()."""
    r = scrape_site(website)
    return {"emails": r["emails"], "status": r["status"]}


# Tên cắt từ tiêu đề trang thường lẫn từ khoá SEO -> coi là "rác"
JUNKY_NAME = re.compile(
    r"(manufactur|supplier|distributor|wholesal|exporter|,|\bfor sale\b|"
    r"\bbest\b|\bcheap\b|\bin (usa|india|china)\b)", re.I)


def _best_company_name(current, site_name, registry_name=""):
    """Chọn tên đáng tin nhất: đăng ký > website tự khai > tên hiện có.

    Chỉ thay tên hiện có khi nó trông như cắt từ tiêu đề SEO (quá dài hoặc
    chứa từ khoá sản phẩm) — tên từ danh bạ hội ngành vốn đã sạch.
    """
    current = str(current or "").strip()
    registry_name = str(registry_name or "").strip()
    site_name = str(site_name or "").strip()
    if registry_name and registry_name.lower() not in ("nan",):
        return registry_name.title() if registry_name.isupper() else registry_name
    junky = (not current or len(current) > 55 or JUNKY_NAME.search(current))
    if junky and site_name:
        return site_name
    return current or site_name


def apply_geo_status(df):
    """Áp dụng lại status theo bằng chứng địa lý cho MỌI dòng đã có geo.

    Phải chạy cho cả dòng được resume bỏ qua — nếu không, dòng đã biết là
    China vẫn giữ 'qualified' từ lần chấm điểm search trước đó.
    """
    if "detected_country" not in df.columns:
        return df
    has_registry = "registry_country" in df.columns
    changed = 0
    for idx in df.index:
        # ĐĂNG KÝ CHÍNH THỨC thắng heuristic nội dung trang
        country = ""
        if has_registry:
            country = str(df.at[idx, "registry_country"] or "")
        if not country:
            country = str(df.at[idx, "detected_country"] or "")
        if not country or country == "nan":
            continue
        cur = str(df.at[idx, "qualification_status"] or "review")
        new_status, reason = restatus_by_geo(cur, country, 0.0)
        if new_status != cur:
            changed += 1
        df.at[idx, "qualification_status"] = new_status
        if country in TARGET_COUNTRIES:
            df.at[idx, "verified_country"] = country
        if reason:
            old_r = str(df.at[idx, "rejection_reasons"] or "")
            if reason not in old_r:
                df.at[idx, "rejection_reasons"] = "; ".join(
                    x for x in [old_r, reason] if x)
    if changed:
        print(f"   (cập nhật lại status cho {changed} dòng theo bằng chứng "
              f"địa lý đã có)")
    return df


# Tăng số này mỗi khi bước enrich lấy thêm dữ liệu mới (vd thêm vat_ids):
# dòng nào có enrich_version cũ hơn sẽ tự được quét lại.
ENRICH_VERSION = 3

ENRICH_COLS = ("emails", "emails_external", "email_status",
               "email_found_on", "detected_country", "geo_confidence",
               "geo_evidence", "vat_ids", "registry_country",
               "registry_source", "registry_name", "company_name_clean",
               "company_role", "products", "phones", "enrich_version")


def enrich_csv(csv_path, out_path=None, workers=EMAIL_WORKERS, resume=True,
               verify_registry=True):
    """Đọc CSV (cột `website`), với MỖI website quét 1 lượt để lấy:
      - email (kèm provenance)
      - bằng chứng QUỐC GIA từ nội dung trang -> cập nhật lại
        qualification_status (loại công ty ngoài Mỹ/Châu Âu)

    resume=True: chạy lại chỉ quét website chưa xong (checkpoint mỗi
    CHECKPOINT_EVERY website, ghi atomic nên an toàn khi dừng giữa chừng).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    df = pd.read_csv(csv_path)
    if "website" not in df.columns:
        raise SystemExit(f"File {csv_path} không có cột 'website'")
    out_path = out_path or csv_path.replace(".csv", "_with_emails.csv")

    # geo_confidence là cột SỐ (NaN = chưa quét); còn lại là chuỗi
    text_cols = [c for c in ENRICH_COLS
                 if c not in ("geo_confidence", "enrich_version")] + [
        "qualification_status", "rejection_reasons", "verified_country",
        "company_name"]
    for col in text_cols:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype(object)
    for numcol in ("geo_confidence", "enrich_version"):
        if numcol not in df.columns:
            df[numcol] = pd.NA
    df["geo_confidence"] = pd.to_numeric(df["geo_confidence"],
                                         errors="coerce")
    df = df.fillna({c: "" for c in text_cols})

    # resume: nạp kết quả cũ nếu có
    if resume and os.path.exists(out_path):
        old = pd.read_csv(out_path).fillna("")
        if "website" in old.columns and "email_status" in old.columns:
            keep = [c for c in ENRICH_COLS if c in old.columns]
            done_map = old.set_index("website")[keep].to_dict("index")
            for col in keep:
                if col == "geo_confidence":
                    df[col] = df.apply(
                        lambda r: pd.to_numeric(
                            done_map.get(r["website"], {}).get(col),
                            errors="coerce")
                        if r["website"] in done_map else r[col], axis=1)
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                else:
                    df[col] = df.apply(
                        lambda r: done_map.get(r["website"], {}).get(col, "")
                        or r[col], axis=1)

    # "Đã xong" = có kết quả email VÀ đã qua bước xác minh địa lý.
    # (file từ bản cũ chỉ có email -> vẫn được quét lại để xác minh nước)
    df["enrich_version"] = pd.to_numeric(df["enrich_version"],
                                         errors="coerce").fillna(0)
    done_mask = (df["email_status"].astype(str) != "") & \
                df["geo_confidence"].notna() & \
                (df["enrich_version"] >= ENRICH_VERSION)
    todo = df.loc[~done_mask, "website"].tolist()
    print(f"Quét email + xác minh quốc gia: {len(todo)} website "
          f"({len(df) - len(todo)} đã có từ lần trước), "
          f"{workers} luồng song song...")

    def _apply(website, result):
        same = [e for e, c, _ in result["emails"] if c != "external"]
        ext = [e for e, c, _ in result["emails"] if c == "external"]
        pages = list(dict.fromkeys(s for _, _, s in result["emails"]))
        mask = df["website"] == website
        df.loc[mask, "emails"] = "; ".join(same)
        df.loc[mask, "emails_external"] = "; ".join(ext)
        df.loc[mask, "email_status"] = result["status"]
        df.loc[mask, "email_found_on"] = "; ".join(pages)

        country = result.get("country", "")
        df.loc[mask, "detected_country"] = country
        df.loc[mask, "geo_confidence"] = result.get("geo_score", 0.0)
        df.loc[mask, "geo_evidence"] = "; ".join(result.get("geo_evidence",
                                                            []))
        df.loc[mask, "vat_ids"] = "; ".join(
            f"{cc}{num}" for cc, num in result.get("vat_ids", []))
        df.loc[mask, "company_role"] = "; ".join(result.get("roles", []))
        df.loc[mask, "products"] = "; ".join(result.get("products", []))
        df.loc[mask, "phones"] = "; ".join(result.get("phones", []))
        site_name = result.get("site_name", "")
        for idx in df.index[mask]:
            df.at[idx, "company_name_clean"] = _best_company_name(
                df.at[idx, "company_name"], site_name,
                df.at[idx, "registry_name"])
        df.loc[mask, "enrich_version"] = ENRICH_VERSION
        # cập nhật lại status theo bằng chứng địa lý
        for idx in df.index[mask]:
            cur = df.at[idx, "qualification_status"] or "review"
            new_status, reason = restatus_by_geo(
                cur, country, result.get("geo_score", 0.0))
            df.at[idx, "qualification_status"] = new_status
            if country in TARGET_COUNTRIES:
                df.at[idx, "verified_country"] = country
            if reason:
                old_r = str(df.at[idx, "rejection_reasons"] or "")
                if reason not in old_r:
                    df.at[idx, "rejection_reasons"] = "; ".join(
                        x for x in [old_r, reason] if x)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scrape_site, w): w for w in todo}
        for fut in as_completed(futures):
            w = futures[fut]
            done += 1
            try:
                result = fut.result()
            except Exception:
                result = {"emails": [], "status": "error", "country": "",
                          "geo_score": 0.0, "geo_evidence": []}
            _apply(w, result)
            n = len(result["emails"])
            geo = result.get("country") or "?"
            print(f"  [{done}/{len(todo)}] {w} -> "
                  f"{n if n else result['status']} | {geo}")
            if done % CHECKPOINT_EVERY == 0:
                save_tables(df, out_path, xlsx=False, quiet=True)

    # --- Bước tra ĐĂNG KÝ CHÍNH THỨC (tuần tự, tôn trọng rate limit) ---
    if verify_registry:
        todo_reg = df[(df["registry_country"].astype(str).isin(["", "nan"]))
                      & (df["vat_ids"].astype(str) != "")]
        if len(todo_reg):
            print(f"\nTra đăng ký chính thức (VIES) cho {len(todo_reg)} "
                  f"công ty có mã số thuế...")
        for n, idx in enumerate(todo_reg.index, 1):
            vat_ids = [(v[:2], v[2:]) for v in
                       str(df.at[idx, "vat_ids"]).split("; ") if len(v) > 2]
            country, source, official = registry_country_for_site(
                vat_ids, str(df.at[idx, "company_name"] or ""),
                use_gleif=False)
            if country:
                df.at[idx, "registry_country"] = country
                df.at[idx, "registry_source"] = source
                df.at[idx, "registry_name"] = official
                print(f"  [{n}/{len(todo_reg)}] "
                      f"{df.at[idx, 'website']} -> {country} ({source})")
            if n % CHECKPOINT_EVERY == 0:
                save_tables(df, out_path, xlsx=False, quiet=True)

    df = apply_geo_status(df)
    save_tables(df, out_path)
    n_found = int((df["email_status"] == "found").sum())
    print(f"\n>> Có email: {n_found}/{len(df)} | "
          f"email_status: {df['email_status'].value_counts().to_dict()}")
    print(f">> Quốc gia phát hiện: "
          f"{df['detected_country'].replace('', '(không rõ)').value_counts().to_dict()}")
    roles = df["company_role"].astype(str).replace("nan", "")
    from collections import Counter
    role_count = Counter(r for row in roles for r in row.split("; ") if r)
    print(f">> Vai trò: {dict(role_count)}")
    prods = df["products"].astype(str).replace("nan", "")
    prod_count = Counter(p for row in prods for p in row.split("; ") if p)
    print(f">> Sản phẩm: {dict(prod_count)}")
    print(f">> Có số điện thoại: "
          f"{(df['phones'].astype(str).replace('nan', '') != '').sum()}")
    reg = df["registry_country"].astype(str).replace("nan", "")
    print(f">> Xác minh qua đăng ký chính thức: {(reg != '').sum()} công ty "
          f"{reg[reg != ''].value_counts().to_dict()}")
    print(f">> Sau xác minh: "
          f"{df['qualification_status'].value_counts().to_dict()}")
    return df


# tương thích ngược với notebook/script cũ
add_emails_to_csv = enrich_csv


# ---------------------------------------------------------------------------
# [4b] PROFILE — công ty này LÀ GÌ và BÁN GÌ (đọc từ HTML đã tải, 0 request)
# ---------------------------------------------------------------------------

# Vai trò: một công ty có thể vừa sản xuất vừa phân phối -> đa nhãn
ROLE_SIGNALS = {
    "manufacturer": [
        r"we manufactur", r"our (own )?(factory|manufacturing|plant|mill)",
        r"manufacturer of", r"manufacturers of", r"we produce",
        r"in[- ]house manufactur", r"cold form(ing|ed)", r"cold head(ing|ed)",
        r"we are a manufactur", r"custom manufactur", r"thread rolling",
        r"hersteller", r"herstellung", r"fabricant", r"fabrication de",
        r"produttore", r"produzione", r"fabricante", r"producent",
        r"tillverkare", r"üretici", r"produsent",
    ],
    "distributor": [
        r"distributor of", r"distributors of", r"authori[sz]ed distributor",
        r"we distribute", r"master distributor", r"stocking distributor",
        r"wholesal", r"grossist", r"groothandel", r"distributeur",
        r"distributore", r"distribuidor", r"dystrybutor", r"händler",
        r"vertrieb", r"toptan",
    ],
    "importer": [
        r"importer of", r"importers of", r"we import", r"import(eur|ador|atore)",
        r"importeur", r"ithalat",
    ],
    "stockist": [
        r"stockist", r"from stock", r"ex[- ]stock", r"large stock",
        r"stock(ed|ing) (items|products|range)", r"lagerhaltung",
        r"ab lager", r"en stock",
    ],
}
ROLE_COMPILED = {role: [re.compile(p, re.I) for p in pats]
                 for role, pats in ROLE_SIGNALS.items()}

# Nhóm sản phẩm — đa ngôn ngữ, bám đúng nhu cầu: threaded rod, studs, washers
PRODUCT_SIGNALS = {
    "threaded rod": [
        r"threaded rod", r"threaded bar", r"all[- ]?thread", r"thread(ed)? stud",
        r"gewindestange", r"tige filet", r"barra filettat", r"barra roscad",
        r"pr[ęe]t gwintowany", r"g[äa]ngst[åa]ng", r"DIN\s?975", r"DIN\s?976",
    ],
    "studs": [r"stud bolt", r"\bstuds\b", r"double[- ]end stud",
              r"stiftschraube", r"\bgoujon", r"\btirante", r"B7 stud",
              r"ASTM\s?A193"],
    "washers": [r"\bwasher", r"\bscheibe", r"\brondelle", r"\brondell",
                r"arandela", r"podk[łl]adk", r"\bbricka"],
    "screws": [r"\bscrew", r"schraube", r"\bvis\b", r"visserie", r"\bvit[ei]\b",
               r"tornillo", r"[śs]rub", r"\bskruv", r"\bvida\b"],
    "bolts": [r"\bbolt", r"\bbolzen", r"\bboulon", r"\bbullone", r"\bperno",
              r"\bcivata"],
    "nuts": [r"\bnuts?\b", r"\bmutter", r"\b[ée]crou", r"\bdad[oi]\b",
             r"tuerca", r"nakr[ęe]tk"],
    "rivets": [r"\brivet", r"\bniete", r"rivetto", r"remache", r"\bnit\b"],
    "anchors": [r"anchor bolt", r"\bd[üu]bel", r"cheville", r"anchor(ing)? "
                r"(system|product)"],
}
PRODUCT_COMPILED = {name: [re.compile(p, re.I) for p in pats]
                    for name, pats in PRODUCT_SIGNALS.items()}

PHONE_RE = re.compile(
    r"(?<![\w.>])(\+\d{1,3}[\s\-./()]{0,2}(?:\d[\s\-./()]{0,2}){6,14}\d"
    r"|\(?\b\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}\b)")


def _match_labels(text, compiled, min_hits=1):
    """Trả về các nhãn khớp, sắp theo số lần khớp giảm dần."""
    scored = []
    for label, pats in compiled.items():
        hits = sum(len(p.findall(text)) for p in pats)
        if hits >= min_hits:
            scored.append((hits, label))
    scored.sort(reverse=True)
    return [label for _, label in scored]


def detect_roles(html):
    """Công ty LÀ GÌ: manufacturer / distributor / importer / stockist."""
    return _match_labels(_strip_code(html), ROLE_COMPILED)


def detect_products(html):
    """Công ty BÁN GÌ: threaded rod / studs / washers / screws / bolts..."""
    # cần nhắc >=2 lần để tránh nhắc qua trong menu/footer
    return _match_labels(_strip_code(html), PRODUCT_COMPILED, min_hits=2)


def structured_company_name(html):
    """Tên công ty do chính website khai báo (sạch hơn cắt tiêu đề trang)."""
    import json as _json

    for m in re.finditer(
            r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
            html):
        try:
            data = _json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                types = node.get("@type", "")
                types = types if isinstance(types, list) else [types]
                if any(str(t) in ("Organization", "LocalBusiness",
                                  "Corporation", "Store", "Manufacturer")
                       for t in types):
                    name = node.get("name")
                    if isinstance(name, str) and 2 < len(name.strip()) < 100:
                        return name.strip()
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)

    m = re.search(
        r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']{2,90})',
        html, re.I)
    if m:
        import html as _html
        return _html.unescape(m.group(1)).strip()
    return ""


def extract_phones(html, country=""):
    """Số điện thoại trên trang; ưu tiên số quốc tế của đúng nước."""
    text = _strip_code(html)
    seen, out = set(), []
    for m in PHONE_RE.finditer(text):
        raw = re.sub(r"\s+", " ", m.group(1)).strip(" -.")
        digits = re.sub(r"\D", "", raw)
        if not (7 <= len(digits) <= 15):
            continue
        if len(set(digits)) <= 2:           # 000-000-0000 / 111 111 1111
            continue
        if digits in seen:
            continue
        seen.add(digits)
        out.append(raw)
    # số bắt đầu bằng + (có mã quốc gia) lên trước
    out.sort(key=lambda s: (not s.startswith("+"),))
    return out[:3]


# ---------------------------------------------------------------------------
# [5] IO / STATE — ghi file atomic, master, báo cáo
# ---------------------------------------------------------------------------


def atomic_write_csv(df, path):
    """Ghi CSV an toàn: ghi file tạm rồi đổi tên (không hỏng file nếu chết
    giữa chừng)."""
    tmp = f"{path}.tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def save_tables(df, csv_path, xlsx=True, quiet=False):
    """Ghi CSV (atomic) + XLSX. Báo trung thực nếu XLSX thất bại."""
    atomic_write_csv(df, csv_path)
    xlsx_path, xlsx_ok = csv_path.replace(".csv", ".xlsx"), False
    if xlsx:
        try:
            df.to_excel(xlsx_path, index=False)
            xlsx_ok = True
        except Exception as e:
            if not quiet:
                print(f"   (!) Không ghi được XLSX ({e}) — chỉ có CSV")
    if not quiet:
        saved = csv_path + (f" và {xlsx_path}" if xlsx_ok else "")
        print(f"Đã lưu: {saved}")
    return xlsx_ok


def _load_master(path=MASTER_CSV):
    """Đọc file tổng tích luỹ; trả về (DataFrame, tập domain đã có)."""
    if os.path.exists(path):
        df = pd.read_csv(path)
        domains = {_base_domain(_clean_domain(u)) for u in df["website"]}
        return df, domains
    return pd.DataFrame(), set()


# ---------------------------------------------------------------------------
# ĐIỂM VÀO — dùng chung cho CLI và Colab
# ---------------------------------------------------------------------------


def run(products=None, roles=None, regions=None,
        results_per_query=RESULTS_PER_QUERY,
        out_csv="fastener_companies.csv", deep_geo=False):
    """Quét 1 lần toàn bộ tổ hợp truy vấn (deep_geo=True: quét theo bang)."""
    queries = build_queries(products, roles, regions, deep_geo=deep_geo)
    print(f"Tổng số truy vấn: {len(queries)}")
    df, report = search_companies(queries,
                                  results_per_query=results_per_query)
    print(f"\nTìm được {len(df)} công ty (domain duy nhất).")
    _print_report(report)
    if not df.empty:
        save_tables(df, out_csv)
    return df


def run_forever(interval_minutes=30, queries_per_cycle=60, max_cycles=None,
                master_csv=MASTER_CSV, regions=None):
    """Chạy LIÊN TỤC: mỗi vòng quét truy vấn ngẫu nhiên, chỉ thêm công ty
    MỚI vào master_csv. Dừng bằng Ctrl+C (dữ liệu đã lưu sau mỗi vòng)."""
    cycle = 0
    master_df = pd.DataFrame()
    while True:
        cycle += 1
        master_df, known = _load_master(master_csv)
        print(f"\n{'=' * 60}")
        print(f"VÒNG {cycle} — {datetime.now():%Y-%m-%d %H:%M} "
              f"— đã có {len(known)} công ty trong file tổng")
        print('=' * 60)

        queries = build_cycle_queries(queries_per_cycle, regions=regions)
        new_df, report = search_companies(queries, known_domains=known)

        if not new_df.empty:
            new_df["added_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            master_df = pd.concat([master_df, new_df], ignore_index=True)
            save_tables(master_df, master_csv, quiet=True)
        print(f"\n>> Vòng {cycle}: thêm {len(new_df)} công ty mới "
              f"(tổng: {len(master_df)}) -> {master_csv}")
        _print_report(report)

        if max_cycles and cycle >= max_cycles:
            print("Đã đủ số vòng yêu cầu, dừng.")
            return master_df
        print(f"Nghỉ {interval_minutes} phút... (Ctrl+C để dừng)")
        try:
            time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            print("\nDừng theo yêu cầu. Dữ liệu đã lưu đầy đủ.")
            return master_df


def _positive(value, name):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"{name} phải > 0")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fastener Company Finder")
    parser.add_argument("--loop", type=lambda v: _positive(v, "--loop"),
                        metavar="PHUT", default=None,
                        help="Chạy liên tục, nghỉ PHUT phút giữa các vòng")
    parser.add_argument("--queries",
                        type=lambda v: _positive(v, "--queries"), default=60,
                        help="Số truy vấn mỗi vòng ở chế độ liên tục")
    parser.add_argument("--cycles", type=lambda v: _positive(v, "--cycles"),
                        default=None, help="Giới hạn số vòng rồi dừng")
    parser.add_argument("--emails", metavar="FILE.CSV", default=None,
                        help="Với mỗi website: quét email VÀ xác minh quốc "
                             "gia từ nội dung trang (loại công ty ngoài "
                             "Mỹ/Châu Âu); tự resume nếu chạy lại")
    parser.add_argument("--directories", action="store_true",
                        help="Lấy công ty từ danh bạ HỘI NGÀNH (NFDA, "
                             "PacWest, EFDA...) — nguồn sạch nhất, quốc gia "
                             "là dữ liệu sẵn có, không phải suy đoán")
    parser.add_argument("--merge-into", metavar="FILE.CSV", default=None,
                        help="Gộp kết quả --directories vào file tổng này")
    parser.add_argument("--registry-uk", action="store_true",
                        help="Lấy công ty Anh từ ĐĂNG KÝ CHÍNH THỨC "
                             "Companies House theo mã ngành SIC 25940 "
                             "(cần COMPANIES_HOUSE_KEY)")
    parser.add_argument("--requalify", metavar="FILE.CSV", default=None,
                        help="Chấm điểm qualification lại cho file CSV cũ")
    parser.add_argument("--deep", action="store_true",
                        help="Quét SÂU: dùng mọi từ khoá địa lý của vùng "
                             "(với USA là từng bang) + từ khoá sản phẩm "
                             "đặc thù vùng (ASTM/SAE/UNC...)")
    args = parser.parse_args(argv)

    if args.registry_uk:
        df = discover_uk_companies()
        if not df.empty:
            save_tables(df, "fastener_companies_uk_registry.csv")
            if args.merge_into:
                master, known = _load_master(args.merge_into)
                merged = pd.concat([master, df], ignore_index=True)
                save_tables(merged, args.merge_into)
                print(f">> Gộp vào {args.merge_into}: tổng {len(merged)}")
    elif args.directories:
        harvest_directories(merge_into=args.merge_into)
    elif args.emails:
        if not os.path.exists(args.emails):
            raise SystemExit(f"Không thấy file: {args.emails}")
        enrich_csv(args.emails)
    elif args.requalify:
        if not os.path.exists(args.requalify):
            raise SystemExit(f"Không thấy file: {args.requalify}")
        df = apply_geo_status(qualify_dataframe(pd.read_csv(args.requalify)))
        save_tables(df, args.requalify)
        print(df["qualification_status"].value_counts().to_string())
    elif args.loop is not None:
        run_forever(interval_minutes=args.loop,
                    queries_per_cycle=args.queries, max_cycles=args.cycles)
    else:
        run(deep_geo=args.deep)


if __name__ == "__main__":
    main()
