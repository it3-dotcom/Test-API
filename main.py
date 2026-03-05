from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import pdfplumber
import fitz  # PyMuPDF
import hashlib
import os
import io
import json
import tempfile
from datetime import datetime

app = FastAPI(title="PDF Extractor API", version="2.0.0")


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
# ENDPOINT 2: Rebuild PDF từ rebuild_payload + translation_map
# ─────────────────────────────────────────────
@app.post("/rebuild/pdf")
async def rebuild_pdf(
    file: UploadFile = File(...),           # file PDF gốc
    rebuild_payload: str = Form(...),       # JSON string: rebuild_payload từ Drive
    translation_map: str = Form(...),       # JSON string: {seg_id: translated_text}
    target_language: str = Form(default="vi"),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Parse JSON inputs
    try:
        payload = json.loads(rebuild_payload)
        trans_map = json.loads(translation_map)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # Build segment_id → {bbox, page, font_size} map từ structure
    block_map = {}
    for unit in payload.get("structure", {}).get("document_units", []):
        if unit.get("unit_type") == "text_block":
            spans = unit.get("lines", [{}])[0].get("spans", [{}])
            font_size = spans[0].get("font_size", 11) if spans else 11
            block_map[unit["unit_id"]] = {
                "page_number": unit["page_number"],
                "bbox": unit["bbox"],
                "font_size": font_size
            }

    # Build seg_id → block info map
    # (dùng segment list từ payload nếu có, hoặc map qua unit_id)
    seg_to_block = {}
    # payload structure không có segments → dùng naming convention
    # seg_pdf_001 → block_001
    for seg_id, translated_text in trans_map.items():
        # Extract block number từ seg_id
        # seg_pdf_001 → block_001
        num = seg_id.replace("seg_pdf_", "")
        block_id = f"block_{num}"
        if block_id in block_map:
            seg_to_block[seg_id] = {
                **block_map[block_id],
                "translated_text": translated_text
            }

    # Save original PDF to temp
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_in:
        content = await file.read()
        tmp_in.write(content)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path.replace(".pdf", "_translated.pdf")

    try:
        doc = fitz.open(tmp_in_path)

        for seg_id, info in seg_to_block.items():
            translated_text = info.get("translated_text", "")
            if not translated_text:
                continue

            page_num = info["page_number"] - 1  # fitz 0-indexed
            bbox = info["bbox"]
            font_size = info["font_size"]

            if page_num >= len(doc):
                continue

            page = doc[page_num]

            # Tọa độ fitz: (x0, y0, x1, y1)
            x0 = bbox["x"]
            y0 = bbox["y"]
            x1 = bbox["x"] + bbox["w"]
            y1 = bbox["y"] + bbox["h"]
            rect = fitz.Rect(x0, y0, x1, y1)

            # Xóa text gốc bằng cách vẽ rectangle trắng đè lên
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))

            # Chèn text đã dịch vào đúng vị trí
            page.insert_textbox(
                rect,
                translated_text,
                fontsize=font_size,
                fontname="helv",        # Helvetica - hỗ trợ Unicode tốt
                color=(0, 0, 0),
                align=0                 # left align
            )

        # Save output PDF
        doc.save(tmp_out_path, garbage=4, deflate=True)
        doc.close()

        # Read output and return as file download
        with open(tmp_out_path, "rb") as f:
            pdf_bytes = f.read()

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

    finally:
        if os.path.exists(tmp_in_path):
            os.unlink(tmp_in_path)
        if os.path.exists(tmp_out_path):
            os.unlink(tmp_out_path)
