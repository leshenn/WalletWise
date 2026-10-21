import re
from collections import defaultdict
from io import BytesIO

import pymupdf

from walletwise.extraction.banks.fnb.config import FNBConfig
from walletwise.extraction.banks.fnb.models import FNBRawRow
from walletwise.utils.date_utils import parse_day_month
from walletwise.utils.text_utils import clean_description, clean_text

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageOps

    OCR_PACKAGES_AVAILABLE = True
except ImportError:
    OCR_PACKAGES_AVAILABLE = False


class FNBOCRService:
    """FNB OCR fallback for descriptions that are visible but not selectable text."""

    def __init__(self, config: FNBConfig, table_detector):
        self.config = config
        self.table_detector = table_detector

    def configure(self) -> bool:
        if not self.config.enable_ocr:
            return False

        if not OCR_PACKAGES_AVAILABLE:
            print("OCR packages not installed. Run: pip install pytesseract pillow")
            return False

        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd

        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            print()
            print("Tesseract OCR was not found.")
            print("The parser will continue, but image-based descriptions may remain blank.")
            print()
            return False

    def description_needs_ocr(self, row: FNBRawRow) -> bool:
        valid_date = parse_day_month(row.date) is not None
        has_money = self.table_detector.row_has_money(row)
        has_description_text = bool(re.search(r"[A-Za-z]", row.description))
        return (valid_date or has_money) and not has_description_text

    def render_description_column(self, page, table_start_y: float, table_end_y: float):
        c = self.config
        clip_rect = pymupdf.Rect(
            c.description_x0,
            max(0, table_start_y),
            c.description_x1,
            min(page.rect.height, table_end_y),
        )

        pix = page.get_pixmap(
            dpi=c.ocr_dpi,
            clip=clip_rect,
            alpha=False,
        )

        image = Image.open(BytesIO(pix.tobytes("png"))).convert("L")
        image = ImageOps.autocontrast(image)
        image = ImageEnhance.Contrast(image).enhance(1.8)
        return image, clip_rect

    def ocr_description_column(self, page, table_start_y: float, table_end_y: float):
        image, clip_rect = self.render_description_column(page, table_start_y, table_end_y)

        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            config="--psm 6",
        )

        x_scale = clip_rect.width / image.width
        y_scale = clip_rect.height / image.height
        words = []

        for i, raw_text in enumerate(data["text"]):
            text = clean_text(raw_text)
            if not text:
                continue

            try:
                confidence = float(data["conf"][i])
            except (TypeError, ValueError):
                confidence = -1

            if confidence < self.config.ocr_min_confidence:
                continue

            left = data["left"][i]
            top = data["top"][i]
            width = data["width"][i]
            height = data["height"][i]

            words.append({
                "text": text,
                "x0": clip_rect.x0 + left * x_scale,
                "y0": clip_rect.y0 + top * y_scale,
                "x1": clip_rect.x0 + (left + width) * x_scale,
                "y1": clip_rect.y0 + (top + height) * y_scale,
                "confidence": confidence,
                "block_num": data["block_num"][i],
                "par_num": data["par_num"][i],
                "line_num": data["line_num"][i],
            })

        return words, image, clip_rect

    @staticmethod
    def group_ocr_words_into_lines(ocr_words):
        grouped = defaultdict(list)

        for word in ocr_words:
            key = (word["block_num"], word["par_num"], word["line_num"])
            grouped[key].append(word)

        lines = []
        for words in grouped.values():
            words.sort(key=lambda item: item["x0"])
            text = clean_description(" ".join(item["text"] for item in words))
            if not text:
                continue

            lines.append({
                "text": text,
                "x0": min(item["x0"] for item in words),
                "y0": min(item["y0"] for item in words),
                "x1": max(item["x1"] for item in words),
                "y1": max(item["y1"] for item in words),
                "center_y": sum((item["y0"] + item["y1"]) / 2 for item in words) / len(words),
                "confidence": sum(item["confidence"] for item in words) / len(words),
            })

        lines.sort(key=lambda line: (line["center_y"], line["x0"]))
        return lines

    def get_ocr_row_pdf_rect(self, clip_rect, row: FNBRawRow):
        c = self.config

        top_pdf = max(clip_rect.y0, row.y0 - c.ocr_row_padding)
        bottom_pdf = min(clip_rect.y1, row.y1 + c.ocr_row_padding)

        # Trim the left/right edges slightly so Tesseract never sees the
        # vertical FNB table borders as |, I, or l.
        left_pdf = clip_rect.x0 + c.ocr_side_trim
        right_pdf = clip_rect.x1 - c.ocr_side_trim

        return pymupdf.Rect(left_pdf, top_pdf, right_pdf, bottom_pdf)

    def ocr_row_crop_from_column_image(self, image, clip_rect, row: FNBRawRow) -> str:
        ocr_rect = self.get_ocr_row_pdf_rect(clip_rect, row)

        x_scale = image.width / clip_rect.width
        y_scale = image.height / clip_rect.height

        left_px = max(0, int((ocr_rect.x0 - clip_rect.x0) * x_scale))
        right_px = min(image.width, int((ocr_rect.x1 - clip_rect.x0) * x_scale))
        top_px = max(0, int((ocr_rect.y0 - clip_rect.y0) * y_scale))
        bottom_px = min(image.height, int((ocr_rect.y1 - clip_rect.y0) * y_scale))

        if bottom_px <= top_px or right_px <= left_px:
            return ""

        crop = image.crop((left_px, top_px, right_px, bottom_px))

        # White padding gives Tesseract some breathing room after the actual
        # FNB borders have already been removed from the crop.
        crop = ImageOps.expand(crop, border=30, fill=255)
        crop = ImageOps.autocontrast(crop)
        crop = ImageEnhance.Contrast(crop).enhance(2.0)

        candidates = []
        for psm in (7, 6, 13, 11):
            result = pytesseract.image_to_string(crop, config=f"--psm {psm}")
            result = clean_description(result)

            if result and re.search(r"[A-Za-z]", result):
                candidates.append(result)

        if not candidates:
            return ""

        return max(
            candidates,
            key=lambda value: len(re.sub(r"[^A-Za-z0-9]", "", value)),
        )

    def repair_missing_descriptions(self, pdf_file: str, rows: list[FNBRawRow]):
        if not self.configure():
            return rows, 0

        rows_needing_ocr = [row for row in rows if self.description_needs_ocr(row)]
        if not rows_needing_ocr:
            return rows, 0

        rows_by_page = defaultdict(list)
        for row in rows_needing_ocr:
            rows_by_page[row.page].append(row)

        pdf = pymupdf.open(pdf_file)
        repaired = 0

        try:
            for page_number, page_rows in rows_by_page.items():
                page = pdf[page_number - 1]
                normal_words = page.get_text("words", sort=False)
                table_start_y, table_end_y, _ = self.table_detector.get_page_table_bounds(
                    normal_words,
                    page,
                    page_number,
                )

                print(
                    f"OCR: screenshotting Description column on page {page_number} "
                    f"for {len(page_rows)} missing description(s)..."
                )

                ocr_words, column_image, clip_rect = self.ocr_description_column(
                    page,
                    table_start_y,
                    table_end_y,
                )
                ocr_lines = self.group_ocr_words_into_lines(ocr_words)

                print(
                    f"  OCR found {len(ocr_words)} word(s) across "
                    f"{len(ocr_lines)} line(s) in the Description column."
                )

                for row in page_rows:
                    # Strict mode: Excel is populated ONLY from this row's exact
                    # crop. Whole-column OCR is retained solely for debugging.
                    description = self.ocr_row_crop_from_column_image(
                        column_image,
                        clip_rect,
                        row,
                    )

                    if description:
                        row.description = description
                        row.ocr_used = True
                        repaired += 1
                        print(f"  Recovered: {row.date or '[no date]'} -> {description}")
                    else:
                        print(
                            f"  Could not recover description at page {page_number}, "
                            f"y={row.y0:.2f}-{row.y1:.2f}"
                        )
        finally:
            pdf.close()

        return rows, repaired
