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
python fastener_finder.py                  # quét 1 lần (~870 truy vấn, 29 nước)
python fastener_finder.py --loop 30        # chạy LIÊN TỤC, nghỉ 30'/vòng
python fastener_finder.py --emails FILE.csv    # quét email (tự resume)
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
| `emails` | Email **cùng domain / liên quan** với website — đáng tin để liên hệ |
| `emails_external` | Email khác domain (cẩn thận khi dùng) |
| `email_status` | found / not_found / timeout / blocked / http_4xx / error |
| `email_found_on` | Các trang đã tìm thấy email |

## Chế độ chạy liên tục

Mỗi vòng bốc ~60 truy vấn ngẫu nhiên (từ khoá xoay vòng với 20 biến thể sản
phẩm), **chỉ thêm công ty MỚI** vào `fastener_companies_master.csv` (ghi
atomic — chết giữa chừng không hỏng file). Chạy càng lâu danh sách càng dài;
Ctrl+C dừng an toàn, chạy lại là tích luỹ tiếp. Cuối mỗi vòng in báo cáo:
truy vấn ok/lỗi, số ứng viên, số qualified/review/loại/trùng.

## Quét email

Đọc trang chủ + tối đa 3 trang liên hệ (contact / kontakt / impressum /
mentions-legales...), tối đa 5 email/công ty. Bắt được email trong `mailto:`,
email bị Cloudflare che, dạng `name (at) domain (dot) com`. Có retry, kiểm
tra HTTP status / content-type, giới hạn 2MB/trang. **Tự resume**: chạy lại
lệnh là chỉ quét những website chưa xong (checkpoint mỗi 50 website).

## Test

```bash
pip install pytest
pytest -q          # 18 test offline, không gọi mạng
```

## Lưu ý

- Ưu tiên dùng dòng `qualified`; nhóm `review` nên lướt duyệt tay; đừng
  gửi email hàng loạt khi chưa kiểm tra.
- Bị ratelimit: tăng `SLEEP_RANGE = (3, 6)` trong core.
- `region` là quốc gia *của truy vấn* — công ty đa quốc gia có thể xuất hiện
  ở nước khác trụ sở; đối chiếu thêm `verified_country`.
