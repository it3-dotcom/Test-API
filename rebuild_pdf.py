from pypdf import PdfReader, PdfWriter, PageObject
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
import os
import urllib.request
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FONT_NAME = "Helvetica"
FONT_PATH = "/tmp/NotoSans.ttf"

def register_font():
    global FONT_NAME

    dejavu_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for path in dejavu_paths:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("UniFont", path))
            FONT_NAME = "UniFont"
            logger.info(f"Using DejaVu: {path}")
            return

    try:
        if not os.path.exists(FONT_PATH):
            logger.info("Downloading NotoSans...")
            urllib.request.urlretrieve(
                "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
                FONT_PATH
            )
        pdfmetrics.registerFont(TTFont("UniFont", FONT_PATH))
        FONT_NAME = "UniFont"
        logger.info("Using NotoSans")
        return
    except Exception as e:
        logger.warning(f"Font download failed: {e}")

    logger.warning("Using Helvetica - Vietnamese may show as boxes")

register_font()


def rebuild_pdf(original_pdf_bytes: bytes, rebuild_payload: dict, translation_map: dict) -> bytes:
    logger.info(f"Rebuild. Segments: {len(translation_map)}, Font: {FONT_NAME}")

    # Build block_map
    block_map = {}
    for unit in rebuild_payload.get("structure", {}).get("document_units", []):
        if unit.get("unit_type") == "text_block":
            spans = unit.get("lines", [{}])[0].get("spans", [{}])
            font_size = spans[0].get("font_size", 10) if spans else 10
            block_map[unit["unit_id"]] = {
                "page_number": unit["page_number"],
                "bbox": unit["bbox"],
                "font_size": max(7, min(float(font_size), 11))
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

    reader = PdfReader(io.BytesIO(original_pdf_bytes))
    writer = PdfWriter()

    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        if page_num in pages_data:
            # Create a NEW blank page with translated text only
            # This completely replaces original text layer
            overlay_buffer = io.BytesIO()
            c = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

            # First pass: draw all white rectangles to blank out original text areas
            for info in pages_data[page_num]:
                bbox = info["bbox"]
                x = float(bbox["x"])
                w = float(bbox["w"])
                h = float(bbox["h"])
                y_rl = page_height - float(bbox["y"]) - h

                # Large white rect to cover original Chinese text
                c.setFillColorRGB(1, 1, 1)
                c.setStrokeColorRGB(1, 1, 1)
                c.rect(x - 2, y_rl - 4, w + 60, h + 8, fill=1, stroke=1)

            # Second pass: draw translated text
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

                # Vietnamese text is ~40% longer than Chinese
                # Use page_width as effective max width
                max_w = min(page_width - x - 10, w * 2.5)

                c.setFillColorRGB(0, 0, 0)
                c.setFont(FONT_NAME, font_size)

                text_y = y_rl + h - font_size
                words = text.split()
                line = ""
                for word in words:
                    test = (line + " " + word).strip()
                    if c.stringWidth(test, FONT_NAME, font_size) <= max_w:
                        line = test
                    else:
                        if line:
                            c.drawString(x, text_y, line)
                            text_y -= (font_size + 2)
                        line = word
                if line:
                    c.drawString(x, text_y, line)

            c.save()
            overlay_buffer.seek(0)

            # Merge: overlay ON TOP of original (white rects hide Chinese, new text shows)
            overlay_reader = PdfReader(overlay_buffer)
            overlay_page = overlay_reader.pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    logger.info(f"Output: {out.tell()} bytes")
    return out.getvalue()
