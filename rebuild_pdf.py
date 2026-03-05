"""
PDF Rebuild module using pypdf + reportlab
Strategy: overlay translated text on top of original PDF
"""
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io


def rebuild_pdf(original_pdf_bytes: bytes, rebuild_payload: dict, translation_map: dict) -> bytes:
    """
    Rebuild PDF bằng cách:
    1. Đọc PDF gốc
    2. Với mỗi trang, tạo overlay layer chứa text đã dịch
    3. Merge overlay lên trang gốc (xóa vùng text cũ, ghi text mới)
    """

    # Build block_map: unit_id → {page, bbox, font_size}
    block_map = {}
    for unit in rebuild_payload.get("structure", {}).get("document_units", []):
        if unit.get("unit_type") == "text_block":
            spans = unit.get("lines", [{}])[0].get("spans", [{}])
            font_size = spans[0].get("font_size", 11) if spans else 11
            block_map[unit["unit_id"]] = {
                "page_number": unit["page_number"],
                "bbox": unit["bbox"],
                "font_size": min(font_size, 11)  # cap font size
            }

    # Build seg → block mapping (seg_pdf_001 → block_001)
    seg_to_block = {}
    for seg_id, translated_text in translation_map.items():
        num = seg_id.replace("seg_pdf_", "")
        block_id = f"block_{num}"
        if block_id in block_map:
            seg_to_block[seg_id] = {
                **block_map[block_id],
                "translated_text": translated_text
            }

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
        
        # Get page dimensions
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        if page_num in pages_data:
            # Create overlay with translated text
            overlay_buffer = io.BytesIO()
            c = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

            for info in pages_data[page_num]:
                bbox = info["bbox"]
                font_size = info["font_size"]
                text = info["translated_text"] or ""

                if not text.strip():
                    continue

                x = bbox["x"]
                # Convert PDF coordinate (top-down → bottom-up for reportlab)
                y_top = bbox["y"]
                y_bottom = page_height - y_top - bbox["h"]
                w = bbox["w"]
                h = bbox["h"]

                # Draw white rectangle to cover original text
                c.setFillColorRGB(1, 1, 1)
                c.rect(x, y_bottom, w, h, fill=1, stroke=0)

                # Draw translated text
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica", font_size)
                
                # Text box with word wrap
                text_obj = c.beginText(x, y_bottom + h - font_size)
                words = text.split()
                line = ""
                for word in words:
                    test_line = line + " " + word if line else word
                    if c.stringWidth(test_line, "Helvetica", font_size) <= w:
                        line = test_line
                    else:
                        if line:
                            text_obj.textLine(line)
                        line = word
                if line:
                    text_obj.textLine(line)
                c.drawText(text_obj)

            c.save()
            overlay_buffer.seek(0)

            # Merge overlay onto original page
            from pypdf import PdfReader as PR
            overlay_reader = PR(overlay_buffer)
            overlay_page = overlay_reader.pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    # Output
    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()
