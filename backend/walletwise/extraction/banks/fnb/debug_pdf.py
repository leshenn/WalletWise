from collections import defaultdict

import pymupdf

from walletwise.extraction.banks.fnb.models import FNBRawRow


class FNBDebugPDFGenerator:
    def __init__(self, config, table_detector, ocr_service):
        self.config = config
        self.table_detector = table_detector
        self.ocr_service = ocr_service

    def create_table_debug_pdf(self, pdf_file: str, output_file: str):
        pdf = pymupdf.open(pdf_file)
        c = self.config

        try:
            for page_index, page in enumerate(pdf):
                page_number = page_index + 1
                words = page.get_text("words", sort=False)

                table_start_y, table_end_y, has_closing_balance = (
                    self.table_detector.get_page_table_bounds(words, page, page_number)
                )

                table_rect = pymupdf.Rect(
                    c.table_x0,
                    table_start_y,
                    c.table_x1,
                    table_end_y,
                )

                annotation = page.add_rect_annot(table_rect)
                annotation.set_colors(stroke=(0, 0, 1))
                annotation.set_border(width=1.5)
                annotation.set_info(
                    title="WalletWise FNB table region",
                    content=f"Page {page_number}: FNB table area read by WalletWise",
                )
                annotation.update()

                if has_closing_balance:
                    break

            pdf.save(output_file)
        finally:
            pdf.close()

    def create_ocr_debug_pdf(
        self,
        pdf_file: str,
        rows: list[FNBRawRow],
        output_file: str,
    ) -> bool:
        if not self.ocr_service.configure():
            print("OCR debug PDF skipped because Tesseract is unavailable.")
            return False

        rows_by_page = defaultdict(list)
        for row in rows:
            rows_by_page[row.page].append(row)

        pdf = pymupdf.open(pdf_file)
        c = self.config

        try:
            for page_index, page in enumerate(pdf):
                page_number = page_index + 1
                normal_words = page.get_text("words", sort=False)

                table_start_y, table_end_y, has_closing_balance = (
                    self.table_detector.get_page_table_bounds(
                        normal_words,
                        page,
                        page_number,
                    )
                )

                # OCR happens before annotations are drawn so the debug marks can
                # never contaminate the image sent to Tesseract.
                ocr_words, _, clip_rect = self.ocr_service.ocr_description_column(
                    page,
                    table_start_y,
                    table_end_y,
                )
                ocr_lines = self.ocr_service.group_ocr_words_into_lines(ocr_words)

                table_rect = pymupdf.Rect(
                    c.table_x0,
                    table_start_y,
                    c.table_x1,
                    table_end_y,
                )
                annot = page.add_rect_annot(table_rect)
                annot.set_colors(stroke=(0, 0, 1))
                annot.set_border(width=1.2)
                annot.set_info(
                    title="WalletWise FNB table region",
                    content=f"Page {page_number}: table area read by WalletWise",
                )
                annot.update()

                # Entire description column rendered for page-level OCR/debug.
                annot = page.add_rect_annot(clip_rect)
                annot.set_colors(stroke=(0.6, 0, 0.8))
                annot.set_border(width=1.2)
                annot.set_info(
                    title="OCR description region",
                    content=(
                        f"Page {page_number}: this FNB Description column was rendered "
                        "as an image and sent to Tesseract."
                    ),
                )
                annot.update()

                # Text Tesseract recognized during whole-column debug OCR.
                for index, line in enumerate(ocr_lines, start=1):
                    rect = pymupdf.Rect(
                        max(c.description_x0, line["x0"] - 1),
                        max(table_start_y, line["y0"] - 1),
                        min(c.description_x1, line["x1"] + 1),
                        min(table_end_y, line["y1"] + 1),
                    )
                    if rect.width <= 0 or rect.height <= 0:
                        continue

                    annot = page.add_rect_annot(rect)
                    annot.set_colors(stroke=(0, 0.65, 0))
                    annot.set_border(width=0.8)
                    annot.set_info(
                        title=f"OCR line {index}",
                        content=(
                            f"Text: {line['text']}\n"
                            f"Average confidence: {line['confidence']:.1f}%"
                        ),
                    )
                    annot.update()

                # Cyan = exact strict row crop used for the value written to Excel.
                for row in rows_by_page.get(page_number, []):
                    if self.ocr_service.description_needs_ocr(row) or row.ocr_used:
                        exact_ocr_rect = self.ocr_service.get_ocr_row_pdf_rect(clip_rect, row)

                        if exact_ocr_rect.width > 0 and exact_ocr_rect.height > 0:
                            annot = page.add_rect_annot(exact_ocr_rect)
                            annot.set_colors(stroke=(0, 0.8, 0.9))
                            annot.set_border(width=1.8)
                            annot.set_info(
                                title="Exact OCR row capture",
                                content=(
                                    "This cyan rectangle is the exact FNB area sent to "
                                    "row-level Tesseract OCR.\n"
                                    f"Date: {row.date}\n"
                                    f"Amount: {row.amount}\n"
                                    f"PDF rect: x0={exact_ocr_rect.x0:.2f}, "
                                    f"y0={exact_ocr_rect.y0:.2f}, "
                                    f"x1={exact_ocr_rect.x1:.2f}, "
                                    f"y1={exact_ocr_rect.y1:.2f}"
                                ),
                            )
                            annot.update()

                for row in rows_by_page.get(page_number, []):
                    if row.ocr_used:
                        row_rect = pymupdf.Rect(
                            c.description_x0,
                            max(table_start_y, row.y0 - 2),
                            c.description_x1,
                            min(table_end_y, row.y1 + 2),
                        )
                        annot = page.add_rect_annot(row_rect)
                        annot.set_colors(stroke=(1, 0.55, 0))
                        annot.set_border(width=1.4)
                        annot.set_info(
                            title="OCR-recovered description",
                            content=f"Recovered text: {row.description}",
                        )
                        annot.update()

                    elif self.ocr_service.description_needs_ocr(row):
                        row_rect = pymupdf.Rect(
                            c.description_x0,
                            max(table_start_y, row.y0 - 2),
                            c.description_x1,
                            min(table_end_y, row.y1 + 2),
                        )
                        annot = page.add_rect_annot(row_rect)
                        annot.set_colors(stroke=(1, 0, 0))
                        annot.set_border(width=1.4)
                        annot.set_info(
                            title="OCR failed to recover description",
                            content=(
                                f"Date: {row.date}\n"
                                f"Amount: {row.amount}\n"
                                "No usable description was recovered."
                            ),
                        )
                        annot.update()

                if has_closing_balance:
                    break

            pdf.save(output_file)
        finally:
            pdf.close()

        print(f"Created OCR debug PDF: {output_file}")
        return True
