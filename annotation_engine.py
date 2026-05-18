"""
Annotation engine for Neptune Benson submittal generation.

Takes a blank template PDF and a list of annotation specs.
Returns an annotated PDF page matching the visual style of the
existing Ontario Aquatic Center submittal.

Two annotation types:
- "yellow_callout": yellow filled rectangle with bordered text
- "red_box": red unfilled rectangle (typically around a table row)

Baked-annotation stripping:
    Many templates were exported from previous job-specific submittals and
    carry pre-existing red row boxes and yellow callouts from those jobs.
    Before drawing fresh annotations, annotate_template() runs a strip pass
    that removes those baked-in artifacts so only the workflow's current
    annotations appear in the output. Controlled via AnnotationSpec.strip_baked
    (default True).
"""
import fitz
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# Visual style constants matching the existing submittal
YELLOW_FILL = (1.0, 1.0, 0.0)        # bright yellow
BLACK_BORDER = (0, 0, 0)
RED = (1.0, 0.0, 0.0)
WHITE = (1, 1, 1)


@dataclass
class YellowCallout:
    """A yellow-highlighted text box, e.g. '(1) 8" REQ'D - LAP'."""
    x: float                         # left edge, in PDF points
    y: float                         # top edge, in PDF points
    lines: List[str]                 # one or more text lines
    width: float = 195               # box width
    line_height: float = 14          # vertical spacing per line
    font_size: float = 12
    padding: float = 4               # internal padding


@dataclass
class RedBox:
    """A red unfilled rectangle, typically around a table row."""
    x: float
    y: float
    width: float
    height: float
    line_width: float = 1.2


@dataclass
class AnnotationSpec:
    """All annotations to apply to a single template page."""
    template_path: str
    yellow_callouts: List[YellowCallout] = field(default_factory=list)
    red_boxes: List[RedBox] = field(default_factory=list)
    # When True, strip any pre-existing red rectangles and yellow callouts
    # from the template before drawing new ones. Default True so all
    # workflow-annotated pages get a clean slate; flip to False if you ever
    # need to *preserve* baked annotations (e.g. debugging a template).
    strip_baked: bool = True


# ---------------------------------------------------------------------------
# Baked-annotation stripping
# ---------------------------------------------------------------------------
def _is_color_match(color, target, tolerance):
    """Check if an RGB color tuple matches a target within per-channel tolerance."""
    if color is None or len(color) < 3:
        return False
    return all(abs(color[i] - target[i]) <= tolerance for i in range(3))


def strip_red_rectangles(page, tolerance=0.15, min_area=100):
    """Remove pre-existing red-stroked, unfilled rectangles from a page.

    Detects rectangles whose stroke color is ~red and whose fill is None,
    then redacts the geometry while preserving any text inside (so table
    row data — Size, A, B, C, Model, Part # — survives intact).

    Returns the count of rectangles stripped.
    """
    targets = []

    for drawing in page.get_drawings():
        stroke = drawing.get("color")
        fill = drawing.get("fill")

        if stroke is None or fill is not None:
            continue
        if not _is_color_match(stroke, (1.0, 0.0, 0.0), tolerance):
            continue

        items = drawing.get("items", [])
        if not items:
            continue
        kinds = {it[0] for it in items}
        # "re" = rectangle primitive; "l" = line segment. Curves disqualify.
        if not kinds.issubset({"re", "l"}):
            continue

        rect = drawing.get("rect")
        if rect is None or rect.get_area() < min_area:
            continue

        targets.append(rect)

    if not targets:
        return 0

    # Pad inward by half a point so the table border (typically just outside
    # the red box stroke) isn't clipped along with the red rectangle.
    for rect in targets:
        padded = fitz.Rect(rect.x0 + 0.5, rect.y0 + 0.5,
                           rect.x1 - 0.5, rect.y1 - 0.5)
        page.add_redact_annot(padded)

    # graphics=LINE_ART_REMOVE removes the vector strokes; text=TEXT_NONE
    # leaves the row's data alone; images=IMAGE_NONE preserves any rasters
    # (none expected on these templates but safe).
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE,
        text=fitz.PDF_REDACT_TEXT_NONE,
    )

    return len(targets)


def strip_yellow_callouts(page, tolerance=0.2, min_area=400):
    """Remove pre-existing yellow-filled callout boxes (and the text inside).

    Unlike the red-box case, the text inside a yellow callout is itself
    stale annotation content (e.g. "(1) 6" EFFLUENT REQ'D" from a prior
    job) and SHOULD be removed — the workflow will draw the current
    callout back in the right place.

    Returns the count of callouts stripped.
    """
    targets = []

    for drawing in page.get_drawings():
        fill = drawing.get("fill")
        if fill is None:
            continue
        # Yellow ~ (1, 1, 0). Generous tolerance for slightly off-yellow
        # templates (e.g. (0.95, 0.95, 0.1)).
        if not _is_color_match(fill, (1.0, 1.0, 0.0), tolerance):
            continue

        items = drawing.get("items", [])
        if not items:
            continue
        kinds = {it[0] for it in items}
        if not kinds.issubset({"re", "l"}):
            continue

        rect = drawing.get("rect")
        if rect is None or rect.get_area() < min_area:
            continue

        targets.append(rect)

    if not targets:
        return 0

    # Pad OUTWARD by 1pt to catch glyphs that extend slightly past the
    # box edge (text inside a tight callout often clips the fill boundary).
    for rect in targets:
        padded = fitz.Rect(rect.x0 - 1, rect.y0 - 1,
                           rect.x1 + 1, rect.y1 + 1)
        page.add_redact_annot(padded)

    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_REMOVE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )

    return len(targets)


def strip_baked_annotations(page) -> Tuple[int, int]:
    """Strip both red boxes and yellow callouts from a page.

    Returns (red_count, yellow_count). Call this BEFORE drawing the
    workflow's fresh annotations on the page.
    """
    red = strip_red_rectangles(page)
    yellow = strip_yellow_callouts(page)
    return red, yellow


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def annotate_template(spec: AnnotationSpec, output_path: str) -> str:
    """Apply annotations to a template PDF and write the result.

    If spec.strip_baked is True (default), pre-existing red row boxes and
    yellow callouts are removed from the template page before fresh
    annotations are drawn. Strip counts are printed for observability.

    Returns the output path on success.
    """
    doc = fitz.open(spec.template_path)
    if len(doc) != 1:
        raise ValueError(
            f"Template {spec.template_path} must be a single page, "
            f"got {len(doc)} pages"
        )
    page = doc[0]

    # Strip pre-existing baked annotations (red boxes from prior jobs,
    # stale yellow callouts) so only this run's annotations appear.
    if spec.strip_baked:
        red_n, yellow_n = strip_baked_annotations(page)
        if red_n or yellow_n:
            template_name = Path(spec.template_path).name
            print(f"    stripped {red_n} red box(es), {yellow_n} yellow callout(s) "
                  f"from {template_name}")

    # Draw yellow callouts
    for cb in spec.yellow_callouts:
        height = (cb.line_height * len(cb.lines)) + (cb.padding * 2)
        rect = fitz.Rect(cb.x, cb.y, cb.x + cb.width, cb.y + height)
        # Filled yellow rectangle with thin black border
        page.draw_rect(
            rect,
            color=BLACK_BORDER,
            fill=YELLOW_FILL,
            width=0.5,
        )
        # Insert each line of text
        text_x = cb.x + cb.padding
        text_y = cb.y + cb.padding + cb.font_size  # baseline offset
        for i, line in enumerate(cb.lines):
            page.insert_text(
                (text_x, text_y + i * cb.line_height),
                line,
                fontname="helv",
                fontsize=cb.font_size,
                color=BLACK_BORDER,
            )

    # Draw red boxes
    for rb in spec.red_boxes:
        rect = fitz.Rect(rb.x, rb.y, rb.x + rb.width, rb.y + rb.height)
        page.draw_rect(
            rect,
            color=RED,
            fill=None,
            width=rb.line_width,
        )

    doc.save(output_path)
    doc.close()
    return output_path


# ---------------------------------------------------------------------------
# Demo: reproduce page 9 (influent check valve) from the Ontario submittal
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    template = "/home/claude/prototype/templates/influent_check_valve_BLANK.pdf"
    output = "/home/claude/prototype/output/influent_check_valve_FILLED.pdf"
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    # In production, these coordinates come from the mapping table
    # (one-time setup per template).
    spec = AnnotationSpec(
        template_path=template,
        yellow_callouts=[
            YellowCallout(
                x=365, y=450,
                lines=['(1) 8" REQ\'D - LAP', '(1) 4" REQ\'D - ACTIVITY'],
            ),
        ],
        red_boxes=[
            # Row for DN100-4" (Activity pool, 4" influent check valve)
            RedBox(x=66, y=683, width=763, height=14),
            # Row for DN200-8" (Lap pool, 8" influent check valve)
            RedBox(x=66, y=716, width=763, height=14),
        ],
    )

    annotate_template(spec, output)
    print(f"Wrote: {output}")
