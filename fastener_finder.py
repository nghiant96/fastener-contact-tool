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
  [4] email        — quét email trên website (có provenance & retry)
  [5] io/state     — ghi file atomic, checkpoint, báo cáo
CLI và Google Colab đều chỉ là adapter mỏng gọi vào file này.

Chạy:
  python fastener_finder.py                    # quét 1 lần
  python fastener_finder.py --loop 30          # chạy liên tục, nghỉ 30'
  python fastener_finder.py --emails FILE.csv  # quét email (resume được)

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

REGIONS = {
    # region_label : (danh sách từ khoá địa lý, mã vùng ddgs)
    "USA": (["USA", "United States"], "us-en"),
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
# [3] SEARCH — sinh truy vấn + gọi DuckDuckGo
# ---------------------------------------------------------------------------


def build_queries(products=None, roles=None, regions=None):
    """Bộ truy vấn đầy đủ (tích Descartes) cho chế độ quét 1 lần."""
    products = products or PRODUCTS
    roles = roles or ROLES
    regions = regions or REGIONS
    return [(f"{p} {r} {geo[0]} company", label, code)
            for label, (geo, code) in regions.items()
            for p in products for r in roles]


def build_cycle_queries(n_queries=60, seed=None, regions=None):
    """Bộ truy vấn NGẪU NHIÊN cho 1 vòng của chế độ chạy liên tục."""
    rng = random.Random(seed)
    regions = regions or REGIONS
    all_products = PRODUCTS + EXTRA_PRODUCTS
    region_items = list(regions.items())
    queries = {}
    attempts = 0
    # sinh cho đủ n_queries truy vấn DUY NHẤT (tối đa 20*n lần thử)
    while len(queries) < n_queries and attempts < n_queries * 20:
        attempts += 1
        label, (geo, code) = rng.choice(region_items)
        q = " ".join(x for x in [rng.choice(all_products), rng.choice(ROLES),
                                 geo[0], rng.choice(EXTRA_MODIFIERS)] if x)
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


def scrape_emails_for_site(website):
    """Quét 1 website. Trả về dict:

    emails:  [(email, class, source_url)] — same/related trước, external sau
    status:  found | not_found | timeout | blocked | error | http_*
    """
    site_domain = _base_domain(_clean_domain(website))
    found = {}  # email -> (class, source_url)

    page, reason = _fetch(website)
    if page is None:
        return {"emails": [], "status": reason}
    html, final_url = page

    for e in _extract_emails_from_html(html):
        found.setdefault(e, (_classify_email(e, site_domain), final_url))

    for link in _find_contact_links(html, final_url):
        if len(found) >= MAX_EMAILS_PER_SITE:
            break
        sub, _ = _fetch(link)
        if sub is None:
            continue
        for e in _extract_emails_from_html(sub[0]):
            found.setdefault(e, (_classify_email(e, site_domain), link))

    order = {"same_domain": 0, "related_domain": 1, "external": 2}
    ranked = sorted(((e, c, s) for e, (c, s) in found.items()),
                    key=lambda x: (order[x[1]], x[0]))[:MAX_EMAILS_PER_SITE]
    return {"emails": ranked, "status": "found" if ranked else "not_found"}


def add_emails_to_csv(csv_path, out_path=None, workers=EMAIL_WORKERS,
                      resume=True):
    """Đọc CSV (cột `website`), quét email song song, ghi CSV/XLSX mới.

    resume=True: nếu file output đã tồn tại, bỏ qua website đã quét xong
    (checkpoint tự lưu sau mỗi CHECKPOINT_EVERY website).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    df = pd.read_csv(csv_path)
    if "website" not in df.columns:
        raise SystemExit(f"File {csv_path} không có cột 'website'")
    out_path = out_path or csv_path.replace(".csv", "_with_emails.csv")

    for col in ("emails", "emails_external", "email_status",
                "email_found_on"):
        if col not in df.columns:
            df[col] = ""
    df = df.fillna({"emails": "", "emails_external": "", "email_status": "",
                    "email_found_on": ""})

    # resume: nạp kết quả cũ nếu có
    if resume and os.path.exists(out_path):
        old = pd.read_csv(out_path).fillna("")
        if "website" in old.columns and "email_status" in old.columns:
            done_map = old.set_index("website")[
                ["emails", "emails_external", "email_status",
                 "email_found_on"]].to_dict("index")
            for col in ("emails", "emails_external", "email_status",
                        "email_found_on"):
                df[col] = df.apply(
                    lambda r: done_map.get(r["website"], {}).get(col, "")
                    or r[col], axis=1)

    todo = df[df["email_status"] == ""]["website"].tolist()
    print(f"Quét email: {len(todo)} website cần quét "
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

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scrape_emails_for_site, w): w for w in todo}
        for fut in as_completed(futures):
            w = futures[fut]
            done += 1
            try:
                result = fut.result()
            except Exception:
                result = {"emails": [], "status": "error"}
            _apply(w, result)
            n = len(result["emails"])
            print(f"  [{done}/{len(todo)}] {w} -> "
                  f"{n if n else result['status']}")
            if done % CHECKPOINT_EVERY == 0:
                save_tables(df, out_path, xlsx=False, quiet=True)

    save_tables(df, out_path)
    n_found = int((df["email_status"] == "found").sum())
    breakdown = df["email_status"].value_counts().to_dict()
    print(f"\n>> Có email: {n_found}/{len(df)} công ty | "
          f"trạng thái: {breakdown}")
    return df


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
        out_csv="fastener_companies.csv"):
    """Quét 1 lần toàn bộ tổ hợp truy vấn."""
    queries = build_queries(products, roles, regions)
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
                        help="Quét email các website trong file CSV "
                             "(cột 'website'); tự resume nếu chạy lại")
    parser.add_argument("--requalify", metavar="FILE.CSV", default=None,
                        help="Chấm điểm qualification lại cho file CSV cũ")
    args = parser.parse_args(argv)

    if args.emails:
        if not os.path.exists(args.emails):
            raise SystemExit(f"Không thấy file: {args.emails}")
        add_emails_to_csv(args.emails)
    elif args.requalify:
        if not os.path.exists(args.requalify):
            raise SystemExit(f"Không thấy file: {args.requalify}")
        df = qualify_dataframe(pd.read_csv(args.requalify))
        save_tables(df, args.requalify)
        print(df["qualification_status"].value_counts().to_string())
    elif args.loop is not None:
        run_forever(interval_minutes=args.loop,
                    queries_per_cycle=args.queries, max_cycles=args.cycles)
    else:
        run()


if __name__ == "__main__":
    main()
