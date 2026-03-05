"""
PDF Rebuild: 
- Dùng reportlab tạo trang mới hoàn toàn với text đã dịch
- Dùng pdfrw để giữ lại graphics/images từ trang gốc
- Merge graphics layer (gốc) + text layer (dịch)
"""
from pypdf import PdfReader, PdfWriter
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
        font_path = "/tmp/NotoSans.ttf"
        if not os.path.exists(font_path):
            logger.info("Downloading NotoSans...")
            urllib.request.urlretrieve(
                "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
                font_path
            )
        pdfmetrics.registerFont(TTFont("UniFont", font_path))
        FONT_NAME = "UniFont"
        logger.info("Using NotoSans")
        return
    except Exception as e:
        logger.warning(f"Font download failed: {e}")

    logger.warning("Using Helvetica fallback")

register_font()


def draw_text_page(page_width, page_height, page_blocks, font_name, font_size_default=10):
    """Tạo 1 trang PDF chỉ chứa text đã dịch trên nền trắng"""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    # White background để che hoàn toàn text gốc
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, page_width, page_height, fill=1, stroke=0)

    for info in page_blocks:
        bbox = info["bbox"]
        font_size = info["font_size"]
        text = (info["translated_text"] or "").strip()
        if not text:
            continue

        x = float(bbox["x"])
        w = float(bbox["w"])
        h = float(bbox["h"])
        # pdfplumber: y từ top, reportlab: y từ bottom
        y_rl = page_height - float(bbox["y"]) - h

        # Tiếng Việt dài hơn Trung ~40% → mở rộng max width
        max_w = page_width - x - 5

        c.setFillColorRGB(0, 0, 0)
        c.setFont(font_name, font_size)

        # Word wrap
        text_y = y_rl + h - font_size
        words = text.split()
        line = ""
        for word in words:
            test = (line + " " + word).strip()
            if c.stringWidth(test, font_name, font_size) <= max_w:
                line = test
            else:
                if line:
                    c.drawString(x, text_y, line)
                    text_y -= (font_size + 2)
                line = word
        if line:
            c.drawString(x, text_y, line)

    c.save()
    buf.seek(0)
    return buf


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

    # Get page dimensions from original
    orig_reader = PdfReader(io.BytesIO(original_pdf_bytes))
    writer = PdfWriter()

    for page_idx, page in enumerate(orig_reader.pages):
        page_num = page_idx + 1
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        if page_num in pages_data:
            # Build translated text page (white bg + translated text)
            text_buf = draw_text_page(
                page_width, page_height,
                pages_data[page_num],
                FONT_NAME
            )

            # Strategy: 
            # 1. text_page has white background → covers original text
            # 2. merge original page UNDER text page → keeps images/graphics
            text_reader = PdfReader(text_buf)
            text_page = text_reader.pages[0]

            # Merge original ON text_page (original graphics show through)
            # But text_page white bg covers original text
            # 
            # Actually: merge original FIRST, then text on top
            # White bg on text_page will cover Chinese text
            text_page.merge_page(page)  # original graphics merge INTO text page
            writer.add_page(text_page)
        else:
            writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    logger.info(f"Output: {out.tell()} bytes")
    return out.getvalue()
