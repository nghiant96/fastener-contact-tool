# 🔩 Fastener Company Finder

Tool tổng hợp **website + email** các công ty **sản xuất / nhập khẩu / phân
phối** fasteners, screws, bolts, threaded rod, studs, washers... ở **Mỹ và
Châu Âu**. Không cần API key (tìm kiếm qua DuckDuckGo).

## Kiến trúc

- **`fastener_finder.py`** — core duy nhất, chia 5 module: utils /
  qualification / search / email / io-state. Có test offline
  (`test_fastener_finder.py`, chạy `pytest -q`).
- **`fastener_finder_colab.ipynb`** — adapter mỏng cho Colab: tự tải core
  mới nhất từ repo này (`raw.githubusercontent.com`), không copy code.
  Sửa core + push = Colab tự có bản mới.
- CLI cũng chỉ là adapter gọi vào core.

## 2 cách chạy

### Cách 1 — Google Colab (không cần cài gì)

Mở notebook thẳng từ repo:

```
https://colab.research.google.com/github/nghiant96/fastener-contact-tool/blob/main/fastener_finder_colab.ipynb
```

Chạy các cell từ trên xuống. File CSV + Excel tự tải về.

### Cách 2 — Chạy trên máy (standalone)

```bash
pip install -r requirements.txt
python fastener_finder.py                  # quét 1 lần (1 truy vấn/nước/sản phẩm)
python fastener_finder.py --deep           # quét SÂU: từng bang Mỹ + ASTM/SAE/UNC
python fastener_finder.py --loop 30        # chạy LIÊN TỤC, nghỉ 30'/vòng
python fastener_finder.py --directories        # lấy từ DANH BẠ HỘI NGÀNH
python fastener_finder.py --registry-uk        # công ty Anh từ Companies House
python fastener_finder.py --directories --merge-into fastener_companies_master.csv
python fastener_finder.py --emails FILE.csv    # quét email + xác minh nước
python fastener_finder.py --requalify FILE.csv # chấm điểm lại file cũ
```

## Các cột kết quả

| Cột | Ý nghĩa |
|-----|---------|
| `company_name` | Tên công ty (cắt từ tiêu đề trang) |
| `website` | Website (khử trùng lặp theo domain) |
| `region` | Quốc gia của truy vấn tìm ra |
| `qualification_status` | **qualified** (tin được) / **review** (nên duyệt tay) / **rejected** |
| `confidence_score` | Điểm tin cậy 0–1 (đúng ngành + đúng vai trò + đúng nước) |
| `verified_country` | Quốc gia suy từ đuôi tên miền (.de → Germany...); trống nếu .com |
| `rejection_reasons` | Reason-code vì sao bị trừ điểm/loại |
| `detected_country` | **Quốc gia thật, suy từ nội dung website** (điện thoại, địa chỉ/ZIP, mã số thuế, loại hình DN) |
| `geo_confidence` | Điểm bằng chứng địa lý (≥2.5 mới coi là xác minh được) |
| `geo_evidence` | Bằng chứng cụ thể (vd: "địa chỉ bang + ZIP; hotline toll-free") |
| `emails` | Email **cùng domain / liên quan** với website — đáng tin để liên hệ |
| `emails_external` | Email khác domain (cẩn thận khi dùng) |
| `email_status` | found / not_found / timeout / blocked / http_4xx / error |
| `email_found_on` | Các trang đã tìm thấy email |
| `registry_country` | **Quốc gia ĐĂNG KÝ chính thức** (VIES/GLEIF) — bằng chứng mạnh nhất |
| `registry_source` | Nguồn xác minh, vd `VIES VAT DE811907980` |
| `registry_name` | Tên pháp lý theo đăng ký |
| `vat_ids` | Mã số thuế tìm thấy trên website |
| `source` | Nguồn dữ liệu: `directory:NFDA`, `registry:companies-house`, trống = search engine |
| `enrich_version` | Phiên bản bước enrich (dòng cũ hơn sẽ tự quét lại) |

## Quét sâu theo địa phương (`--deep`)

Mỗi vùng trong `REGIONS` có **danh sách** từ khoá địa lý. Chế độ thường chỉ
dùng từ khoá đầu (`"USA"`); `--deep` dùng **tất cả** — với Mỹ là **30 bang
công nghiệp** (Texas, Ohio, Michigan, Illinois...) — và cộng thêm từ khoá
sản phẩm đặc thù vùng từ `REGION_PRODUCTS` (Mỹ: `grade 8 bolts`,
`ASTM A193 B7 studs`, `A325 structural bolts`, `UNC threaded rod`,
`mil-spec fasteners`, `domestic fasteners made in USA`...).

Chế độ **chạy liên tục tự bốc ngẫu nhiên cả bang và từ khoá đặc thù**, nên
không cần cờ `--deep` — cứ để chạy lâu là tự đào sâu dần.

Muốn thêm bang / từ khoá: sửa `US_STATES` và `US_PRODUCTS` trong core.
Muốn quét sâu nước khác, thêm từ khoá vùng vào `REGIONS`, ví dụ
`"Germany": (["Germany", "Bavaria", "NRW", "Baden-Württemberg"], "de-de")`.

## Chế độ chạy liên tục

Mỗi vòng bốc ~60 truy vấn ngẫu nhiên (từ khoá xoay vòng với 20 biến thể sản
phẩm), **chỉ thêm công ty MỚI** vào `fastener_companies_master.csv` (ghi
atomic — chết giữa chừng không hỏng file). Chạy càng lâu danh sách càng dài;
Ctrl+C dừng an toàn, chạy lại là tích luỹ tiếp. Cuối mỗi vòng in báo cáo:
truy vấn ok/lỗi, số ứng viên, số qualified/review/loại/trùng.

## 🏅 Nguồn tốt nhất: danh bạ hội ngành (`--directories`)

Thay vì "tìm bừa rồi đoán quốc gia", cách tối ưu là lấy từ nguồn mà **quốc
gia là một trường dữ liệu sẵn có**: danh bạ hội viên của hội ngành. Hội viên
đã được hội kiểm duyệt là doanh nghiệp thật trong ngành, và quốc gia là
thuộc tính của chính hội (NFDA/Pac-West = hội Mỹ).

| Danh bạ | Phạm vi | Số công ty |
|---------|---------|-----------|
| NFDA — National Fastener Distributors Association | Mỹ | ~156 |
| Pac-West Fastener Association | Mỹ | ~145 |
| EFDA — European Fastener Distributor Association | Châu Âu | ~22 |

Ưu điểm so với quét search engine:

- **Không cần đoán quốc gia** — hội Mỹ thì hội viên là công ty Mỹ.
- **Tên công ty sạch** — lấy từ anchor text, không phải cắt tiêu đề trang.
- **Không cần đoán "đúng ngành"** — tư cách hội viên đã bảo đảm; nhờ vậy
  giữ được cả công ty tên không chứa chữ "fastener" (vd `gexproservices.com`)
  mà bộ lọc từ khoá sẽ bỏ sót.
- Tự lọc hội ngành / triển lãm / tạp chí lẫn trong danh bạ; hội viên là hội
  quốc gia (tên viết tắt như `FDS`, `NEVIB`) hạ xuống `review` chứ không xoá.

Thêm danh bạ mới chỉ cần 1 dòng trong `DIRECTORIES` (core), gồm `name`,
`url`, `country`. Parser xử lý được cả 2 kiểu bố cục: tên nằm trong anchor
(kiểu NFDA) và tên nằm **trước** anchor kèm `(Quốc gia)` (kiểu EFDA).

## 🏛️ Xác minh bằng ĐĂNG KÝ CHÍNH THỨC (mạnh nhất)

Bằng chứng mạnh nhất không phải suy đoán mà là **đăng ký nhà nước**. Bước
`--emails` tự làm việc này, miễn phí và **không cần API key**:

| Nguồn | Vai trò | Cần key? |
|-------|---------|----------|
| **VIES** (hệ thống VAT của EU) | Trích mã số thuế trên website → tra ra **quốc gia đăng ký** + tên pháp lý | Không |
| **GLEIF** (mã LEI toàn cầu) | Tra tên công ty → quốc gia trụ sở pháp lý | Không |
| **UK Companies House** | **Khám phá** công ty Anh theo mã ngành SIC 25940 (sản xuất fasteners) | Có (miễn phí) |

Cách hoạt động: tool trích mã số thuế từ trang contact/impressum (nhận dạng
26 định dạng VAT của EU: `DE`, `NL...B01`, `ATU`, `P.IVA`, `NIP`...), rồi gọi
VIES. Một mã VAT `DE` hợp lệ nghĩa là công ty **đăng ký ở Đức** — không còn
gì để tranh luận. Kết quả vào 3 cột: `registry_country`, `registry_source`,
`registry_name`.

**Thứ tự ưu tiên khi xác định quốc gia:**

```
registry_country (VAT/VIES, GLEIF)   ← mạnh nhất, đăng ký nhà nước
  > detected_country (nội dung trang: điện thoại, địa chỉ, mã số thuế)
    > verified_country (đuôi tên miền .de/.co.uk)
```

Companies House còn dùng để **khám phá công ty mới** (không chỉ xác minh):
lọc theo mã ngành SIC ra toàn bộ công ty Anh đang hoạt động trong ngành
fasteners, kèm địa chỉ đăng ký. Cần key miễn phí:

```bash
# lấy key tại https://developer.company-information.service.gov.uk
export COMPANIES_HOUSE_KEY=xxx
python fastener_finder.py --registry-uk --merge-into fastener_companies_master.csv
```

Các công ty từ Companies House chưa có website (registry không lưu), nên để
`review` kèm lý do `no_website_yet` — dùng tên công ty tìm website sau.

## 🔎 Đọc dữ liệu công ty tự khai (schema.org)

Trước khi dùng heuristic, tool đọc **quốc gia do chính công ty khai báo** trong
dữ liệu có cấu trúc: `schema.org PostalAddress.addressCountry` (JSON-LD hoặc
microdata) — chính xác hơn mọi suy đoán, trọng số cao nhất.

`og:locale` và `priceCurrency` chỉ là **gợi ý yếu**: `og:locale` là *ngôn ngữ*
chứ không phải quốc gia — `stauff.fr` (công ty Pháp) dùng `en_GB`, nếu tin
tưởng nó thì sẽ gán sai thành UK.

## ⚠️ Vì sao phải chạy bước xác minh quốc gia

Search engine **không cho biết công ty ở đâu**, và domain `.com` cũng không.
Rất nhiều công ty Trung Quốc / Ấn Độ làm SEO nhắm đúng từ khoá
*"fasteners supplier USA"*, đăng cả địa chỉ bang + ZIP của Mỹ và tiêu chuẩn
ASTM — nhìn từ kết quả tìm kiếm thì **không thể phân biệt** với công ty Mỹ
thật.

Vì vậy:

- Ở bước tìm kiếm, domain `.com` **không bao giờ** được `qualified` — cao
  nhất là `review` kèm lý do `geo_unverified`. Chỉ ccTLD (`.de`, `.co.uk`...)
  mới được xác minh ngay.
- Bước `--emails` sẽ **đọc nội dung website** và tìm bằng chứng: điện thoại
  (+86 / +91 / +1 / +49...), địa chỉ + mã bưu chính, mã số thuế
  (GST, VAT, NIP, P.IVA...), loại hình doanh nghiệp (Inc/LLC, GmbH, S.r.l.),
  chữ Hán, giấy phép ICP. Công ty ngoài Mỹ/Châu Âu bị đổi thành `rejected`
  với lý do `geo_outside_target:China`.
- **Tín hiệu "loại" được ưu tiên**: công ty Ấn Độ có đăng địa chỉ Mỹ vẫn bị
  nhận ra nhờ số +91 — vì công ty Mỹ thật gần như không bao giờ có +91/+86.
- Website chặn bot (`email_status = blocked`) sẽ ở lại `review`, **không bị
  loại oan** — chỉ là chưa xác minh được.

## Quét email + xác minh quốc gia

Đọc trang chủ + tối đa 3 trang liên hệ (contact / kontakt / impressum /
mentions-legales...), tối đa 5 email/công ty. Bắt được email trong `mailto:`,
email bị Cloudflare che, dạng `name (at) domain (dot) com`. Có retry, kiểm
tra HTTP status / content-type, giới hạn 2MB/trang. **Tự resume**: chạy lại
lệnh là chỉ quét những website chưa xong (checkpoint mỗi 50 website).

## Test

```bash
pip install pytest
pytest -q          # 62 test offline, không gọi mạng
```

## Lưu ý

- Ưu tiên dùng dòng `qualified`; nhóm `review` nên lướt duyệt tay; đừng
  gửi email hàng loạt khi chưa kiểm tra.
- Bị ratelimit: tăng `SLEEP_RANGE = (3, 6)` trong core.
- `region` là quốc gia *của truy vấn* — công ty đa quốc gia có thể xuất hiện
  ở nước khác trụ sở; đối chiếu thêm `verified_country`.
