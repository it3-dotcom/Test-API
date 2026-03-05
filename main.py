from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import pdfplumber
import hashlib
import os
import io
import json
import tempfile
from datetime import datetime

app = FastAPI(title="PDF Extractor API", version="3.0.0")


@app.get("/")
def health():
    return {"status": "ok", "service": "pdf-extractor-api", "version": "2.0.0"}


# ─────────────────────────────────────────────
# ENDPOINT 1: Extract PDF → Master JSON
# ─────────────────────────────────────────────
@app.post("/extract/pdf")
async def extract_pdf(
    file: UploadFile = File(...),
    job_id: str = Form(...),
    file_id: str = Form(...),
    source_language: str = Form(default="zh"),
    target_language: str = Form(default="vi"),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        document_units = []
        segments = []
        seg_counter = 1
        block_counter = 1
        checksum = hashlib.sha256(content).hexdigest()
        file_size = len(content)

        with pdfplumber.open(tmp_path) as pdf:
            page_count = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                document_units.append({
                    "unit_id": f"page_{str(page_num).zfill(3)}",
                    "unit_type": "page",
                    "page_number": page_num,
                    "width": float(page.width),
                    "height": float(page.height)
                })

                words = page.extract_words(
                    extra_attrs=["fontname", "size"],
                    use_text_flow=True
                )
                if not words:
                    continue

                lines_map = {}
                for word in words:
                    y_key = round(float(word["top"]) / 5) * 5
                    if y_key not in lines_map:
                        lines_map[y_key] = []
                    lines_map[y_key].append(word)

                for y_key in sorted(lines_map.keys()):
                    line_words = lines_map[y_key]
                    line_text = " ".join(w["text"] for w in line_words).strip()
                    if not line_text:
                        continue

                    x0 = min(float(w["x0"]) for w in line_words)
                    y0 = min(float(w["top"]) for w in line_words)
                    x1 = max(float(w["x1"]) for w in line_words)
                    y1 = max(float(w["bottom"]) for w in line_words)

                    block_id = f"block_{str(block_counter).zfill(3)}"
                    seg_id = f"seg_pdf_{str(seg_counter).zfill(3)}"
                    block_path = f"page_{page_num}.{block_id}"

                    first = line_words[0]
                    default_font = first.get("fontname", "Unknown")
                    default_size = round(float(first.get("size", 12)), 1)

                    spans = [
                        {
                            "span_index": i,
                            "text": w["text"],
                            "font_name": w.get("fontname", default_font),
                            "font_size": round(float(w.get("size", default_size)), 1),
                            "color": "#000000"
                        }
                        for i, w in enumerate(line_words)
                    ]

                    bbox = {
                        "x": round(x0, 2),
                        "y": round(y0, 2),
                        "w": round(x1 - x0, 2),
                        "h": round(y1 - y0, 2)
                    }

                    document_units.append({
                        "unit_id": block_id,
                        "unit_type": "text_block",
                        "path": block_path,
                        "page_number": page_num,
                        "bbox": bbox,
                        "lines": [{"line_index": 0, "spans": spans}]
                    })

                    segments.append({
                        "segment_id": seg_id,
                        "unit_id": block_id,
                        "container_type": "text_block",
                        "container_ref": f"{block_path}.line_0.span_0",
                        "location_ref": {"page": page_num, "bbox": bbox},
                        "source_text": line_text,
                        "translated_text": None,
                        "style_ref": f"pdf_style_{str(block_counter).zfill(3)}",
                        "translation_status": "pending"
                    })

                    block_counter += 1
                    seg_counter += 1

        master = {
            "job_id": job_id,
            "file_id": file_id,
            "file_name": file.filename,
            "file_type": "pdf",
            "pdf_mode": "text_based",
            "source_language": source_language,
            "target_language": target_language,
            "status": "extracted",
            "created_at": datetime.now().isoformat(),
            "file_meta": {
                "original_path": f"/input/{file.filename}",
                "size_bytes": file_size,
                "page_count": page_count,
                "sheet_count": None,
                "slide_count": None,
                "checksum": f"sha256_{checksum[:16]}",
                "extractor_version": "v2.0.0",
                "rebuild_strategy": "replace_text_in_original_structure"
            },
            "structure": {"document_units": document_units},
            "segments": segments,
            "translation_meta": {
                "chunk_strategy": "by_segment_group",
                "max_chars_per_chunk": 3000,
                "llm_model": None,
                "translation_status": "pending"
            },
            "rebuild_meta": {
                "can_rebuild": True,
                "fidelity_goal": "high",
                "warnings": []
            }
        }

        return JSONResponse(content=master)

    finally:
        os.unlink(tmp_path)



# ─────────────────────────────────────────────
# ENDPOINT 2: Rebuild PDF
# ─────────────────────────────────────────────
@app.post("/rebuild/pdf")
async def rebuild_pdf_endpoint(
    file: UploadFile = File(...),
    rebuild_payload: str = Form(...),
    translation_map: str = Form(...),
    target_language: str = Form(default="vi"),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        payload = json.loads(rebuild_payload)
        trans_map = json.loads(translation_map)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    content = await file.read()

    from rebuild_pdf import rebuild_pdf
    pdf_bytes = rebuild_pdf(content, payload, trans_map)

    original_name = file.filename.replace(".pdf", "")
    output_filename = f"{original_name}_translated_{target_language}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{output_filename}"',
            "Content-Length": str(len(pdf_bytes))
        }
    )
