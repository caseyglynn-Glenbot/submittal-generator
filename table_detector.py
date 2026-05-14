"""
Auto-detect parts table rows on a template page.

Strategy:
  1. Rasterize the template page at high DPI
  2. Find horizontal lines using OpenCV morphology
  3. Cluster nearby lines into "row separators"
  4. Use OCR (tesseract) on each row to read the leftmost columns
     (Part # and Size) — these are what the quote keys off

Returns a list of detected rows: [{y, height, part_no, size, full_text}, ...]

If this works reliably across templates, the mapping table only needs the
SIZE/PART KEY to look up the row, not pixel coordinates.
"""
import subprocess
import cv2
import numpy as np
import pytesseract
from pathlib import Path


DPI = 200  # rendering DPI
PDF_DPI = 72  # PDF user-space DPI (1 point = 1/72 inch)


def rasterize_page(pdf_path: str, out_prefix: str) -> str:
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(DPI), pdf_path, out_prefix],
        check=True, capture_output=True,
    )
    return f"{out_prefix}-1.png"


def find_horizontal_lines(image_path: str):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    h, w = img.shape

    _, binary = cv2.threshold(img, 128, 255, cv2.THRESH_BINARY_INV)
    kernel_w = max(50, w // 3)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    detected = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(detected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    line_ys = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw > w * 0.3 and ch < 10:
            line_ys.append(y + ch // 2)

    return sorted(line_ys), img.shape


def cluster_into_rows(line_ys, min_gap=15, max_gap=80):
    """Return rows with reasonable heights (excludes spurious extra-tall gaps)."""
    rows = []
    for i in range(len(line_ys) - 1):
        gap = line_ys[i + 1] - line_ys[i]
        if min_gap <= gap <= max_gap:
            rows.append({
                "y_top": line_ys[i],
                "y_bottom": line_ys[i + 1],
                "height": gap,
            })
    return rows


def ocr_row(image, row, left_fraction=0.25):
    """OCR the leftmost portion of a row (typically contains Part # / Size)."""
    h, w = image.shape[:2]
    x1, x2 = 0, int(w * left_fraction)
    y1, y2 = row["y_top"] + 2, row["y_bottom"] - 2
    crop = image[y1:y2, x1:x2]
    text = pytesseract.image_to_string(crop, config="--psm 7").strip()
    return text


def detect_table_rows(template_pdf: str):
    """Return list of rows with OCR'd labels and PDF-space coordinates."""
    img_path = rasterize_page(template_pdf, "/tmp/td_rasterized")
    img = cv2.imread(img_path)
    line_ys, shape = find_horizontal_lines(img_path)
    rows = cluster_into_rows(line_ys)

    px_to_pt = PDF_DPI / DPI  # convert image pixels back to PDF points
    img_h_px = shape[0]

    results = []
    for r in rows:
        label = ocr_row(img, r)
        results.append({
            "label": label,
            "y_top_pt": r["y_top"] * px_to_pt,
            "y_bottom_pt": r["y_bottom"] * px_to_pt,
            "height_pt": r["height"] * px_to_pt,
        })
    return results


def main():
    template_pdf = "/home/claude/prototype/templates/influent_check_valve_BLANK.pdf"
    rows = detect_table_rows(template_pdf)
    print(f"Detected {len(rows)} rows\n")
    print(f"{'label (OCR)':<40} {'y_top (pt)':>10} {'height':>8}")
    print("-" * 60)
    for r in rows:
        label = r["label"][:38].replace("\n", " | ")
        print(f"{label:<40} {r['y_top_pt']:>10.1f} {r['height_pt']:>8.1f}")


if __name__ == "__main__":
    main()
