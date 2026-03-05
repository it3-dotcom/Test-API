# PDF Extractor API

FastAPI service để extract text từ PDF với đầy đủ thông tin bbox, font, style.
Deploy lên Railway và gọi từ n8n Cloud qua HTTP Request.

## Endpoints

| Method | Path | Mô tả |
|--------|------|--------|
| GET | `/` | Health check |
| POST | `/extract/pdf` | Extract PDF → Master JSON |

## Deploy lên Railway

### Bước 1: Push lên GitHub
```bash
git init
git add .
git commit -m "init pdf extractor api"
git remote add origin https://github.com/YOUR_USERNAME/pdf-extractor-api.git
git push -u origin main
```

### Bước 2: Deploy Railway
1. Vào https://railway.app
2. New Project → Deploy from GitHub repo
3. Chọn repo `pdf-extractor-api`
4. Railway tự detect và deploy
5. Vào Settings → Networking → Generate Domain
6. Copy URL dạng: `https://pdf-extractor-api-xxx.railway.app`

## Gọi từ n8n (HTTP Request node)

- **Method:** POST
- **URL:** `https://YOUR_RAILWAY_URL/extract/pdf`
- **Body Type:** Form Data (multipart)
- **Fields:**
  - `file` → Binary (PDF file)
  - `job_id` → `={{ $json.job_id }}`
  - `file_id` → `={{ $json.file_id }}`
  - `source_language` → `zh`
  - `target_language` → `vi`

## Test local

```bash
pip install -r requirements.txt
uvicorn main:app --reload

# Test với curl
curl -X POST http://localhost:8000/extract/pdf \
  -F "file=@test.pdf" \
  -F "job_id=job_001" \
  -F "file_id=file_001" \
  -F "source_language=zh" \
  -F "target_language=vi"
```
