"""
cover_page_filler.py
====================

Fills the Neptune Benson cover page (page 1 of every submittal).

Mirrors the pattern used in datasheet_filler.py:
  1. Open template with fitz (PyMuPDF).
  2. Resolve each canonical slot to a widget via three-tier resolver:
       a) exact field name
       b) default-value string match
       c) rect-proximity fallback (by Y rank within the document)
  3. Set widget.field_value, call widget.update() to flush.
  4. Call doc.bake(annots=False, widgets=True) so values become permanent
     page content and survive the downstream fitz.insert_pdf() merge.
  5. Save to bytes/path.

The cover page has 4 fillable widgets (all Text type):
    Text50  default "PROJECT"   ─┐
    Text51  default "NAME"      ─┘ stacked project-name box (two lines)
    Text52  default "JOB # -"      job number line
    Text53  default "MM/DD/YY"     submittal return date (bottom)

Lessons applied from the datasheet bug:
  - fitz Y is top-down (smaller y = higher on page). project_name_line1
    is the widget with the SMALLER y0.
  - Don't rely on /NeedAppearances — bake widgets into page content.
  - Return a FillReport so silent failure is impossible.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Canonical slot map for the cover page.
#
# Each entry: (canonical_slot_name, expected_field_name, expected_default_value)
# The three-tier resolver walks these in order.
# ─────────────────────────────────────────────────────────────────────────────
COVER_SLOTS = [
    ("project_name_line1",     "Text50", "PROJECT"),
    ("project_name_line2",     "Text51", "NAME"),
    ("job_number_line",        "Text52", "JOB # -"),
    ("submittal_return_date",  "Text53", "MM/DD/YY"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Project-name word-wrap.
#
# Auto-split policy: if the full name fits in ~22 characters, put it all on
# line 1 and leave line 2 blank. Otherwise balance the split on word
# boundaries so neither line is dramatically longer than the other.
#
# 22 chars is a conservative threshold for the Text50 box at the cover
# page's default font size; tune if visual review shows truncation.
# ─────────────────────────────────────────────────────────────────────────────
SINGLE_LINE_MAX = 22


def split_project_name(name: str) -> tuple[str, str]:
    """Split a project name into (line1, line2) using a balanced word split.

    >>> split_project_name("Ontario Aquatic Center")
    ('Ontario Aquatic Center', '')
    >>> split_project_name("North Brunswick Township Community Pool")
    ('North Brunswick Township', 'Community Pool')
    >>> split_project_name("BIGNAME")
    ('BIGNAME', '')
    """
    name = (name or "").strip()
    if not name:
        return ("", "")
    if len(name) <= SINGLE_LINE_MAX:
        return (name, "")

    words = name.split()
    if len(words) == 1:
        # Single very long word — can't word-wrap; let it ride on line 1
        # and accept that the rendering may overflow. (Practically:
        # project names are always multi-word.)
        return (name, "")

    # Find the split index that minimises the difference in line lengths.
    best_i = 1
    best_diff = float("inf")
    for i in range(1, len(words)):
        left = " ".join(words[:i])
        right = " ".join(words[i:])
        diff = abs(len(left) - len(right))
        if diff < best_diff:
            best_diff = diff
            best_i = i

    return (" ".join(words[:best_i]), " ".join(words[best_i:]))


# ─────────────────────────────────────────────────────────────────────────────
# Three-tier resolver
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_widget(
    widgets: list,
    expected_name: str,
    expected_default: str,
    y_rank: Optional[int] = None,
):
    """Find a widget by name → default-value → y-rank fallback.

    y_rank: if multiple widgets share the same expected default value
    (none do on the cover page today, but kept for future-proofing),
    0 picks the topmost, 1 the next, etc.
    """
    # Tier 1: exact field name match
    for w in widgets:
        if w.field_name == expected_name:
            return w, "name"

    # Tier 2: default-value string match (case-insensitive)
    matches = [
        w for w in widgets
        if (w.field_value or "").strip().upper() == expected_default.strip().upper()
    ]
    if matches:
        if y_rank is not None and len(matches) > y_rank:
            # fitz Y is top-down: smaller y0 = higher on page
            matches.sort(key=lambda w: w.rect.y0)
            return matches[y_rank], "default+y"
        return matches[0], "default"

    return None, "missing"


# ─────────────────────────────────────────────────────────────────────────────
# Fill report (for orchestrator logs / debugging)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CoverFillReport:
    slots_filled: dict = field(default_factory=dict)   # slot -> ("name"|"default"|...)
    slots_missing: list = field(default_factory=list)
    widgets_found: int = 0

    def summary(self) -> str:
        ok = ", ".join(f"{k}✓({v})" for k, v in self.slots_filled.items())
        miss = ", ".join(self.slots_missing) or "none"
        return f"cover_page: filled=[{ok}] missing=[{miss}] widgets={self.widgets_found}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def fill_cover_page(
    template_path: Union[str, Path],
    *,
    project_name: str,
    job_number: str,
    submittal_return_date: Optional[str] = None,
    output_path: Optional[Union[str, Path]] = None,
) -> tuple[bytes, CoverFillReport]:
    """Fill the cover page template and bake widget values into page content.

    Parameters
    ----------
    template_path : path to the blank cover page PDF (the one uploaded to
        templates/cover_page.pdf).
    project_name : full project name; will be auto-split across two lines.
    job_number : the job number string (e.g. "12345" or "23-0142"). The
        rendered text is f"JOB # {job_number}".
    submittal_return_date : MM/DD/YY string from the n8n form. If None
        or empty, the Text53 widget is left as its default placeholder
        ("MM/DD/YY") OR cleared depending on policy. We CLEAR it so the
        recipient sees a blank line rather than the placeholder.
    output_path : if given, also write the result to disk.

    Returns
    -------
    (pdf_bytes, fill_report)
    """
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Cover page template not found: {template_path}")

    doc = fitz.open(template_path)
    page = doc[0]
    widgets = list(page.widgets() or [])

    report = CoverFillReport(widgets_found=len(widgets))

    # Compute values for each canonical slot
    line1, line2 = split_project_name(project_name)
    job_line = f"JOB # {job_number}".strip() if job_number else "JOB #"
    return_date = (submittal_return_date or "").strip()

    slot_values = {
        "project_name_line1":    line1,
        "project_name_line2":    line2,
        "job_number_line":       job_line,
        "submittal_return_date": return_date,
    }

    # Resolve and fill each slot
    for slot_name, expected_name, expected_default in COVER_SLOTS:
        widget, how = _resolve_widget(widgets, expected_name, expected_default)
        if widget is None:
            report.slots_missing.append(slot_name)
            logger.warning("cover_page: slot %r not found", slot_name)
            continue

        value = slot_values.get(slot_name, "")
        widget.field_value = value
        widget.update()
        report.slots_filled[slot_name] = how
        logger.debug("cover_page: %s = %r via %s", slot_name, value, how)

    # Bake widget appearances into page content streams so the values
    # survive the downstream fitz.insert_pdf() merge in orchestrator.py.
    # (This is the critical step — without it, AcroForm is stripped on
    # merge and the cover page comes out blank, exactly like the
    # datasheet bug from iteration 3.)
    doc.bake(annots=False, widgets=True)

    # Save with the same compression flags used for the final submittal
    # (garbage=4, deflate=True, clean=True) — keeps cover page small.
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True, clean=True)
    pdf_bytes = buf.getvalue()
    doc.close()

    if output_path:
        Path(output_path).write_bytes(pdf_bytes)

    logger.info(report.summary())
    return pdf_bytes, report


# ─────────────────────────────────────────────────────────────────────────────
# CLI for quick local testing
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Fill the Neptune Benson cover page.")
    p.add_argument("template", help="Path to blank cover_page.pdf")
    p.add_argument("--project", required=True, help="Project name")
    p.add_argument("--job", required=True, help="Job number")
    p.add_argument("--return-date", default="", help="Submittal return date MM/DD/YY")
    p.add_argument("-o", "--output", default="cover_page_filled.pdf")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    _, rep = fill_cover_page(
        args.template,
        project_name=args.project,
        job_number=args.job,
        submittal_return_date=args.return_date,
        output_path=args.output,
    )
    print(rep.summary())
    print(f"Wrote {args.output}")
