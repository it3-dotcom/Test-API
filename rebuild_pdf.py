"""
PDF Rebuild module using pypdf + reportlab
Strategy: overlay translated text on top of original PDF
"""
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
import io
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def rebuild_pdf(original_pdf_bytes: bytes, rebuild_payload: dict, translation_map: dict) -> bytes:
    logger.info(f"Starting rebuild. Segments to translate: {len(translation_map)}")
    logger.info(f"Document units: {len(rebuild_payload.get('structure', {}).get('document_units', []))}")

    # Build block_map: unit_id → {page, bbox, font_size}
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

    logger.info(f"Block map size: {len(block_map)}")

    # Build seg → block mapping
    seg_to_block = {}
    for seg_id, translated_text in translation_map.items():
        num = seg_id.replace("seg_pdf_", "")
        block_id = f"block_{num}"
        if block_id in block_map:
            seg_to_block[seg_id] = {
                **block_map[block_id],
                "translated_text": translated_text
            }

    logger.info(f"Matched segments: {len(seg_to_block)}")

    # Group by page
    pages_data = {}
    for seg_id, info in seg_to_block.items():
        page_num = info["page_number"]
        if page_num not in pages_data:
            pages_data[page_num] = []
        pages_data[page_num].append(info)

    # Read original PDF
    reader = PdfReader(io.BytesIO(original_pdf_bytes))
    writer = PdfWriter()

    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        logger.info(f"Processing page {page_num}: {page_width}x{page_height}")

        if page_num in pages_data:
            overlay_buffer = io.BytesIO()
            c = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

            for info in pages_data[page_num]:
                bbox = info["bbox"]
                font_size = info["font_size"]
                text = info["translated_text"] or ""

                if not text.strip():
                    continue

                x = float(bbox["x"])
                w = float(bbox["w"])
                h = float(bbox["h"])
                
                # pdfplumber: y=0 at TOP, increases downward
                # reportlab: y=0 at BOTTOM, increases upward
                # Convert: reportlab_y = page_height - pdfplumber_y - h
                y_rl = page_height - float(bbox["y"]) - h

                logger.info(f"  Block at x={x}, y_rl={y_rl}, w={w}, h={h}, font={font_size}, text={text[:20]}")

                # White rectangle to cover original text
                c.setFillColorRGB(1, 1, 1)
                c.rect(x - 1, y_rl - 1, w + 2, h + 2, fill=1, stroke=0)

                # Translated text
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica", font_size)

                # Simple text with wrap
                text_y = y_rl + h - font_size - 1
                words = text.split()
                line = ""
                for word in words:
                    test = (line + " " + word).strip()
                    if c.stringWidth(test, "Helvetica", font_size) <= w:
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

            # Merge overlay onto page
            overlay_reader = PdfReader(overlay_buffer)
            overlay_page = overlay_reader.pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    result = output_buffer.getvalue()
    logger.info(f"Output PDF size: {len(result)} bytes")
    return result
