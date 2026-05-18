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
    that overpaints those baked-in artifacts with white so only the
    workflow's current annotations appear in the output.

    Strategy: rather than redacting (which either misses thin strokes or
    takes neighboring artwork with it), we OVERPAINT the detected shapes
    with white at slightly larger dimensions than the original. White
    against the page background is invisible, but it cleanly covers the
    red/yellow geometry without touching the surrounding table grid.

    Controlled via AnnotationSpec.strip_baked (default True).
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
# Baked-annotation stripping (overpaint strategy)
# ---------------------------------------------------------------------------
def _is_color_match(color, target, tolerance):
    """Check if an RGB color tuple matches a target within per-channel tolerance."""
    if color is None or len(color) < 3:
        return False
    return all(abs(color[i] - target[i]) <= tolerance for i in range(3))


def _is_rectangle_drawing(drawing):
    """Return True if the drawing is composed only of rectangles or straight
    line segments (not curves)."""
    items = drawing.get("items", [])
    if not items:
        return False
    kinds = {it[0] for it in items}
    return kinds.issubset({"re", "l"})


def strip_red_rectangles(page, tolerance=0.15, min_area=100):
    """Erase pre-existing red-stroked, unfilled rectangles from a page.

    Detects rectangles whose stroke color is ~red and whose fill is None,
    then overpaints them with a slightly-wider white stroke along the same
    path. This covers the red rectangle without touching neighboring black
    table borders.

    Returns the count of rectangles erased.
    """
    targets = []

    for drawing in page.get_drawings():
        stroke = drawing.get("color")
        fill = drawing.get("fill")

        # Hollow red rectangles only. Filled red shapes are intentional
        # artwork (e.g. logos) — leave them alone.
        if stroke is None or fill is not None:
            continue
        if not _is_color_match(stroke, (1.0, 0.0, 0.0), tolerance):
            continue
        if not _is_rectangle_drawing(drawing):
            continue

        rect = drawing.get("rect")
        if rect is None or rect.get_area() < min_area:
            continue

        # Capture the original stroke width so the overpaint is wide enough
        # to fully cover it. Default to 1.5pt if absent.
        width = drawing.get("width") or 1.5
        targets.append((rect, width))

    if not targets:
        return 0

    # Overpaint with white stroke at 1.5x the original width to ensure full
    # coverage of antialiased edges without bleeding noticeably into the
    # surrounding artwork.
    for rect, orig_width in targets:
        overpaint_width = max(orig_width * 1.5, 2.0)
        page.draw_rect(rect, color=WHITE, fill=None, width=overpaint_width)

    return len(targets)


def strip_yellow_callouts(page, tolerance=0.2, min_area=400):
    """Erase pre-existing yellow-filled callout boxes (and any text inside)
    from a page.

    Unlike the red-box case, the text inside a yellow callout is itself
    stale annotation content from a prior job and SHOULD be removed. We
    overpaint with a white-filled rectangle slightly larger than the
    callout box, which covers both the yellow fill, the black border, and
    any text glyphs that fell inside.

    Returns the count of callouts erased.
    """
    targets = []

    for drawing in page.get_drawings():
        fill = drawing.get("fill")
        if fill is None:
            continue
        if not _is_color_match(fill, (1.0, 1.0, 0.0), tolerance):
            continue
        if not _is_rectangle_drawing(drawing):
            continue

        rect = drawing.get("rect")
        if rect is None or rect.get_area() < min_area:
            continue

        targets.append(rect)

    if not targets:
        return 0

    # Overpaint with white fill, expanding the rect by 1pt on each side so
    # the original border (typically 0.5pt black) and any glyphs clipping
    # the box edge are also covered.
    for rect in targets:
        expanded = fitz.Rect(rect.x0 - 1, rect.y0 - 1,
                             rect.x1 + 1, rect.y1 + 1)
        page.draw_rect(expanded, color=WHITE, fill=WHITE, width=0.5)

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
    yellow callouts are erased from the template page before fresh
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
