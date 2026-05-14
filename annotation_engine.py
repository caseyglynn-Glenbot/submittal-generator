"""
Annotation engine for Neptune Benson submittal generation.

Takes a blank template PDF and a list of annotation specs.
Returns an annotated PDF page matching the visual style of the
existing Ontario Aquatic Center submittal.

Two annotation types:
- "yellow_callout": yellow filled rectangle with bordered text
- "red_box": red unfilled rectangle (typically around a table row)
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


def annotate_template(spec: AnnotationSpec, output_path: str) -> str:
    """Apply annotations to a template PDF and write the result.

    Returns the output path on success.
    """
    doc = fitz.open(spec.template_path)
    if len(doc) != 1:
        raise ValueError(
            f"Template {spec.template_path} must be a single page, "
            f"got {len(doc)} pages"
        )
    page = doc[0]

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
