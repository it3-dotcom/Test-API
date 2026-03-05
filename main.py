from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import pdfplumber
import hashlib
import os
import tempfile
from datetime import datetime

app = FastAPI(title="PDF Extractor API", version="1.0.0")


@app.get("/")
def health():
    return {"status": "ok", "service": "pdf-extractor-api", "version": "1.0.0"}


@app.post("/extract/pdf")
async def extract_pdf(
    file: UploadFile = File(...),
    job_id: str = Form(...),
    file_id: str = Form(...),
    source_language: str = Form(default="zh"),
    target_language: str = Form(default="vi"),
):
    # Validate file type
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Save uploaded file to temp
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        document_units = []
        segments = []
        seg_counter = 1
        block_counter = 1

        # Checksum
        checksum = hashlib.sha256(content).hexdigest()
        file_size = len(content)

        with pdfplumber.open(tmp_path) as pdf:
            page_count = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):

                # --- Page unit ---
                document_units.append({
                    "unit_id": f"page_{str(page_num).zfill(3)}",
                    "unit_type": "page",
                    "page_number": page_num,
                    "width": float(page.width),
                    "height": float(page.height)
                })

                # --- Extract words with font info ---
                words = page.extract_words(
                    extra_attrs=["fontname", "size"],
                    use_text_flow=True
                )

                if not words:
                    continue

                # --- Group words into lines by y-position (tolerance 3pt) ---
                lines_map = {}
                for word in words:
                    y_key = round(float(word["top"]) / 3) * 3
                    if y_key not in lines_map:
                        lines_map[y_key] = []
                    lines_map[y_key].append(word)

                # --- Build block + segment per line ---
                for y_key in sorted(lines_map.keys()):
                    line_words = lines_map[y_key]
                    line_text = " ".join(w["text"] for w in line_words).strip()

                    if not line_text:
                        continue

                    # Bounding box
                    x0 = min(float(w["x0"]) for w in line_words)
                    y0 = min(float(w["top"]) for w in line_words)
                    x1 = max(float(w["x1"]) for w in line_words)
                    y1 = max(float(w["bottom"]) for w in line_words)

                    block_id = f"block_{str(block_counter).zfill(3)}"
                    seg_id = f"seg_pdf_{str(seg_counter).zfill(3)}"
                    block_path = f"page_{page_num}.{block_id}"

                    # Font from first word
                    first = line_words[0]
                    default_font = first.get("fontname", "Unknown")
                    default_size = round(float(first.get("size", 12)), 1)

                    # Spans (one per word)
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

                    # document_unit
                    document_units.append({
                        "unit_id": block_id,
                        "unit_type": "text_block",
                        "path": block_path,
                        "page_number": page_num,
                        "bbox": bbox,
                        "lines": [{
                            "line_index": 0,
                            "spans": spans
                        }]
                    })

                    # segment
                    segments.append({
                        "segment_id": seg_id,
                        "unit_id": block_id,
                        "container_type": "text_block",
                        "container_ref": f"{block_path}.line_0.span_0",
                        "location_ref": {
                            "page": page_num,
                            "bbox": bbox
                        },
                        "source_text": line_text,
                        "translated_text": None,
                        "style_ref": f"pdf_style_{str(block_counter).zfill(3)}",
                        "translation_status": "pending"
                    })

                    block_counter += 1
                    seg_counter += 1

        # Build master JSON
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
                "extractor_version": "v1.0.0",
                "rebuild_strategy": "replace_text_in_original_structure"
            },
            "structure": {
                "document_units": document_units
            },
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
