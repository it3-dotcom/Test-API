from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global font config
FONT_NAME = "Helvetica"  # default fallback

def register_font():
    global FONT_NAME

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/DejaVuSans.ttf",
    ]

    for path in font_paths:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("DejaVuSans", path))
                FONT_NAME = "DejaVuSans"
                logger.info(f"Font registered: {path}")
                return
            except Exception as e:
                logger.warning(f"Font register failed: {e}")

    logger.warning("DejaVu not found, using Helvetica (Vietnamese may show as boxes)")


# Register once at module load
register_font()


def rebuild_pdf(original_pdf_bytes: bytes, rebuild_payload: dict, translation_map: dict) -> bytes:
    logger.info(f"Rebuild start. Segments: {len(translation_map)}, Font: {FONT_NAME}")

    # Build block_map
    block_map = {}
    for unit in rebuild_payload.get("structure", {}).get("document_units", []):
        if unit.get("unit_type") == "text_block":
            spans = unit.get("lines", [{}])[0].get("spans", [{}])
            font_size = spans[0].get("font_size", 10) if spans else 10
            block_map[unit["unit_id"]] = {
                "page_number": unit["page_number"],
                "bbox": unit["bbox"],
                "font_size": max(6, min(float(font_size), 12))
            }

    # Map seg → block
    seg_to_block = {}
    for seg_id, translated_text in translation_map.items():
        num = seg_id.replace("seg_pdf_", "")
        block_id = f"block_{num}"
        if block_id in block_map:
            seg_to_block[seg_id] = {
                **block_map[block_id],
                "translated_text": translated_text
            }

    logger.info(f"Matched: {len(seg_to_block)}/{len(translation_map)}")

    # Group by page
    pages_data = {}
    for info in seg_to_block.values():
        p = info["page_number"]
        if p not in pages_data:
            pages_data[p] = []
        pages_data[p].append(info)

    # Process PDF
    reader = PdfReader(io.BytesIO(original_pdf_bytes))
    writer = PdfWriter()

    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        if page_num in pages_data:
            overlay_buffer = io.BytesIO()
            c = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

            for info in pages_data[page_num]:
                bbox = info["bbox"]
                font_size = info["font_size"]
                text = (info["translated_text"] or "").strip()
                if not text:
                    continue

                x = float(bbox["x"])
                w = float(bbox["w"])
                h = float(bbox["h"])
                y_rl = page_height - float(bbox["y"]) - h

                # White overlay
                c.setFillColorRGB(1, 1, 1)
                c.rect(x - 1, y_rl - 2, w + 2, h + 4, fill=1, stroke=0)

                # Draw text
                c.setFillColorRGB(0, 0, 0)
                c.setFont(FONT_NAME, font_size)

                text_y = y_rl + h - font_size - 1
                words = text.split()
                line = ""
                for word in words:
                    test = (line + " " + word).strip()
                    if c.stringWidth(test, FONT_NAME, font_size) <= w:
                        line = test
                    else:
                        if line:
                            c.drawString(x, text_y, line)
                            text_y -= (font_size + 2)
                        line = word
                        if text_y < y_rl:
                            break
                if line and text_y >= y_rl:
                    c.drawString(x, text_y, line)

            c.save()
            overlay_buffer.seek(0)
            overlay_page = PdfReader(overlay_buffer).pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    logger.info(f"Output size: {out.tell()} bytes")
    return out.getvalue()
