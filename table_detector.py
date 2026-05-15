"""
Auto-detect parts table rows on a template page.

Strategy:
  1. Rasterize the template page at high DPI
  2. Find horizontal lines using OpenCV morphology — capture each line's
     full bounding box (x, y, width, height), not just y
  3. Cluster nearby lines into "row separators", carrying their X spans
     so each detected row has both vertical AND horizontal bounds
  4. Use OCR (tesseract) on each row to read the leftmost columns
     (Part # and Size) — these are what the quote keys off

Returns a list of detected rows:
  [{label, y_top_pt, y_bottom_pt, height_pt, x_left_pt, x_right_pt, width_pt}, ...]

The X bounds are computed by INTERSECTING the X spans of the two bracketing
horizontal lines. This gives the rectangle both lines actually span, which
matches the table's column bounds for the typical case where tables are
drawn with consistent-width horizontal rules between rows. Red box annotations
use this so they stay within the table borders on narrow tables (system fill
valve, drain valve, etc.) instead of bleeding past the left/right edges.
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
    """Return list of (y_center, x_start, x_end) per detected line, plus img shape."""
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    h, w = img.shape

    _, binary = cv2.threshold(img, 128, 255, cv2.THRESH_BINARY_INV)
    kernel_w = max(50, w // 3)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    detected = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(detected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lines = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw > w * 0.3 and ch < 10:
            lines.append({
                "y": y + ch // 2,
                "x_start": x,
                "x_end": x + cw,
            })

    lines.sort(key=lambda L: L["y"])
    return lines, img.shape


def cluster_into_rows(lines, min_gap=15, max_gap=80):
    """Pair consecutive lines into rows, carrying intersected X span.

    The row's X bounds are the INTERSECTION of the two bracketing lines'
    X spans — i.e. the rectangle both lines actually cover. This way a
    narrow table whose rules are e.g. x∈[310, 560] in pixels produces
    a row with x∈[310, 560], not a page-wide rectangle.
    """
    rows = []
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        gap = b["y"] - a["y"]
        if min_gap <= gap <= max_gap:
            rows.append({
                "y_top": a["y"],
                "y_bottom": b["y"],
                "height": gap,
                "x_left": max(a["x_start"], b["x_start"]),
                "x_right": min(a["x_end"], b["x_end"]),
            })
    return rows


def ocr_row(image, row, left_fraction=0.25):
    """OCR the leftmost portion of a row (typically contains Part # / Size).

    OCR-crop X uses 0 → image_width * left_fraction (page-relative, not
    table-relative). This intentionally hasn't changed: the OCR window
    needs to start at the page's left edge to catch leftmost text even
    when the table itself is offset right.
    """
    h, w = image.shape[:2]
    x1, x2 = 0, int(w * left_fraction)
    y1, y2 = row["y_top"] + 2, row["y_bottom"] - 2
    crop = image[y1:y2, x1:x2]
    text = pytesseract.image_to_string(crop, config="--psm 7").strip()
    return text


def detect_table_rows(template_pdf: str):
    """Return list of rows with OCR'd labels, vertical PDF-space coordinates,
    AND horizontal column bounds.

    Each row dict has:
        label        — OCR text from the leftmost columns of the row
        y_top_pt     — top edge of the row in PDF points
        y_bottom_pt  — bottom edge of the row in PDF points
        height_pt    — row height in PDF points
        x_left_pt    — left edge of the table at this row (PDF points)
        x_right_pt   — right edge of the table at this row (PDF points)
        width_pt     — table width at this row in PDF points
    """
    img_path = rasterize_page(template_pdf, "/tmp/td_rasterized")
    img = cv2.imread(img_path)
    lines, shape = find_horizontal_lines(img_path)
    rows = cluster_into_rows(lines)

    px_to_pt = PDF_DPI / DPI  # convert image pixels back to PDF points

    results = []
    for r in rows:
        label = ocr_row(img, r)
        results.append({
            "label": label,
            "y_top_pt":  r["y_top"] * px_to_pt,
            "y_bottom_pt": r["y_bottom"] * px_to_pt,
            "height_pt":  r["height"] * px_to_pt,
            "x_left_pt":  r["x_left"] * px_to_pt,
            "x_right_pt": r["x_right"] * px_to_pt,
            "width_pt":   (r["x_right"] - r["x_left"]) * px_to_pt,
        })
    return results


def main():
    template_pdf = "/home/claude/prototype/templates/influent_check_valve_BLANK.pdf"
    rows = detect_table_rows(template_pdf)
    print(f"Detected {len(rows)} rows\n")
    hdr = f"{'label (OCR)':<40} {'y_top':>8} {'height':>7} {'x_left':>8} {'width':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        label = r["label"][:38].replace("\n", " | ")
        print(f"{label:<40} {r['y_top_pt']:>8.1f} {r['height_pt']:>7.1f} "
              f"{r['x_left_pt']:>8.1f} {r['width_pt']:>7.1f}")


if __name__ == "__main__":
    main()
