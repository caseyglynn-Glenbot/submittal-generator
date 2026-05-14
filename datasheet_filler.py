"""
datasheet_filler.py

Fills the title-block fields on a Neptune Benson / Evoqua filter datasheet PDF
template. Replaces the previous version whose silent failure was masking that
real production templates use field names like `-` and `Text2`/`Text3` rather
than self-describing names.

Key design decisions:

1. Slots are matched by **field name first** (cheapest, most stable), then by
   **default-value string**, then by **rect proximity** to a known anchor.
2. We never let a missing field be a silent skip. Every slot logs SUCCESS or
   the reason for failure (NOT_FOUND / NO_VALUE / WRITE_ERROR).
3. The two INIT boxes and two DATE boxes share default-values across the
   template, so they are disambiguated by widget Y position (upper = drawn,
   lower = checked).
4. We return a structured report so the orchestrator (and tests) can assert
   that every required field actually landed.

Field-name map below was verified against all five Imperial filter sizes
(SP-27-48-487, SP-33-48-732, SP-41-48-1038, SP-49-48-1548, SP-55-48-2076)
including all valve-kit variants.
"""

from __future__ import annotations

import logging
import io
from dataclasses import dataclass, field
from typing import Optional

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject, BooleanObject

log = logging.getLogger(__name__)


# --- Slot definition ---------------------------------------------------------

@dataclass
class SlotSpec:
    """How to find one logical title-block slot on a template."""
    # Preferred field name(s), tried in order. Most templates use these.
    names: tuple[str, ...] = ()
    # Default-value strings to match as fallback.
    defaults: tuple[str, ...] = ()
    # Rough rect (x0, y0, x1, y1) used as final positional fallback. Optional.
    rect_hint: Optional[tuple[float, float, float, float]] = None
    # For duplicate-default slots (INIT, DYMNYR), which Y-rank to take.
    # "upper" = max Y among matches; "lower" = min Y among matches; None = first.
    y_rank: Optional[str] = None
    required: bool = True


# Verified against the five SP-* templates in DEFENDER3_DRAWINGS/IMPERIAL/.
# Field names are consistent; default-value strings drift (CUSTOMER vs CLIENT NAME,
# INIT vs INT, DYMNYR vs MM/DD/YY).
TITLE_BLOCK_SLOTS: dict[str, SlotSpec] = {
    "project_name": SlotSpec(
        names=("-",),
        defaults=("PROJECT NAME",),
        rect_hint=(1604, 480, 1806, 492),
    ),
    "pool_name": SlotSpec(
        names=("Text2",),
        defaults=("POOL NAME",),
        rect_hint=(1604, 468, 1805, 480),
    ),
    "customer": SlotSpec(
        names=("Text3",),
        defaults=("CUSTOMER", "CLIENT NAME", "CLIENT"),
        rect_hint=(1603, 452, 1805, 465),
    ),
    "drawn_by": SlotSpec(
        names=("Text6",),
        defaults=("INIT", "INT"),
        rect_hint=(1536, 478, 1560, 488),
        y_rank="upper",
    ),
    "drawn_date": SlotSpec(
        names=("Text7",),
        defaults=("DYMNYR", "MM/DD/YY"),
        rect_hint=(1563, 478, 1586, 487),
        y_rank="upper",
    ),
    "checked_by": SlotSpec(
        names=("Text8",),
        defaults=("INIT", "INT"),
        rect_hint=(1535, 463, 1559, 472),
        y_rank="lower",
        required=False,  # often left blank pre-review
    ),
    "checked_date": SlotSpec(
        names=("Text9",),
        defaults=("DYMNYR", "MM/DD/YY"),
        rect_hint=(1563, 462, 1586, 472),
        y_rank="lower",
        required=False,
    ),
    "job_number": SlotSpec(
        # The "####" box (Text12) is the primary DMS REFERENCE / job number box.
        # Some templates also have a separate Text10 with default "JOB#" or "-"
        # — that's the project_code slot below, NOT job_number.
        names=("Text12",),
        defaults=("####",),
        rect_hint=(1693, 415, 1754, 423),
    ),
    "part_number": SlotSpec(
        names=("Text11",),
        defaults=("SP-33-48-732", "SP-27-48-487", "SP-41-48-1038",
                  "SP-49-48-1548", "SP-55-48-2076"),
        rect_hint=(1641, 415, 1691, 423),
        required=False,  # pre-populated on the template; only override if asked
    ),
    "project_code": SlotSpec(
        # Bottom strip leftmost cell, labeled "PROJECT". Short project code,
        # distinct from the full project_name above.
        names=("Text10",),
        defaults=("-", "JOB#"),
        rect_hint=(1590, 415, 1639, 423),
        required=False,
    ),
    "sheet_num": SlotSpec(
        names=("Text13",),
        defaults=("1",),
        rect_hint=(1757, 415, 1772, 423),
        required=False,
    ),
    "sheet_total": SlotSpec(
        names=("Text14",),
        defaults=("1",),
        rect_hint=(1782, 415, 1797, 423),
        required=False,
    ),
    "revision": SlotSpec(
        names=("Text15",),
        defaults=("-",),
        rect_hint=(1800, 415, 1809, 421),
        required=False,
    ),
}


# --- Field discovery --------------------------------------------------------

@dataclass
class DiscoveredField:
    name: str
    default: str        # /V or /DV string, whichever present
    rect: tuple[float, float, float, float] | None


def discover_fields(reader: PdfReader) -> dict[str, DiscoveredField]:
    """Enumerate every form field with its name, current value, and rect."""
    fields = reader.get_fields() or {}
    rects: dict[str, tuple[float, float, float, float]] = {}

    for page in reader.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for ref in annots:
            obj = ref.get_object()
            if obj.get("/Subtype") != "/Widget":
                continue
            t = obj.get("/T")
            if t is None:
                parent = obj.get("/Parent")
                if parent is not None:
                    t = parent.get_object().get("/T")
            if t is None:
                continue
            rect = obj.get("/Rect")
            if rect is not None:
                rects[str(t)] = tuple(float(x) for x in rect)  # type: ignore[assignment]

    discovered: dict[str, DiscoveredField] = {}
    for name, fobj in fields.items():
        dv = str(fobj.get("/V") or fobj.get("/DV") or "")
        discovered[str(name)] = DiscoveredField(
            name=str(name),
            default=dv,
            rect=rects.get(str(name)),
        )
    return discovered


def _rect_distance(a: tuple[float, float, float, float],
                   b: tuple[float, float, float, float]) -> float:
    """Center-to-center distance between two rects."""
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def resolve_slot(slot: str,
                 spec: SlotSpec,
                 discovered: dict[str, DiscoveredField]
                 ) -> tuple[Optional[DiscoveredField], str]:
    """
    Returns (field, strategy) where strategy is one of
    'name' | 'default' | 'rect' | 'not_found'.
    """
    # Tier 1: exact name
    for nm in spec.names:
        if nm in discovered:
            return discovered[nm], "name"

    # Tier 2: default-value match (filter then disambiguate by Y)
    matches = [f for f in discovered.values() if f.default in spec.defaults]
    if matches:
        if len(matches) == 1 or spec.y_rank is None:
            return matches[0], "default"
        with_rect = [m for m in matches if m.rect is not None]
        if with_rect:
            with_rect.sort(key=lambda m: m.rect[1])  # type: ignore[index]
            chosen = with_rect[-1] if spec.y_rank == "upper" else with_rect[0]
            return chosen, "default"
        return matches[0], "default"

    # Tier 3: rect proximity to hint
    if spec.rect_hint is not None:
        candidates = [(f, _rect_distance(f.rect, spec.rect_hint))  # type: ignore[arg-type]
                      for f in discovered.values() if f.rect is not None]
        if candidates:
            candidates.sort(key=lambda c: c[1])
            best, dist = candidates[0]
            if dist < 25:  # within ~25pt of the expected center
                return best, "rect"

    return None, "not_found"


# --- Filling ----------------------------------------------------------------

@dataclass
class FillReport:
    template: str
    written: dict[str, tuple[str, str]] = field(default_factory=dict)  # slot -> (field_name, strategy)
    missing: dict[str, str] = field(default_factory=dict)               # slot -> reason
    extra_discovered: int = 0

    def all_required_present(self) -> bool:
        return all(
            slot in self.written or not TITLE_BLOCK_SLOTS[slot].required
            for slot in TITLE_BLOCK_SLOTS
        )

    def summary(self) -> str:
        lines = [f"FillReport[{self.template}]"]
        for slot in TITLE_BLOCK_SLOTS:
            if slot in self.written:
                name, strat = self.written[slot]
                lines.append(f"  ✓ {slot:<14} -> {name!r} (via {strat})")
            else:
                reason = self.missing.get(slot, "skipped")
                marker = "✗" if TITLE_BLOCK_SLOTS[slot].required else "·"
                lines.append(f"  {marker} {slot:<14} {reason}")
        return "\n".join(lines)


def fill_datasheet(template_path: str,
                   values: dict[str, str],
                   output_path: Optional[str] = None,
                   ) -> tuple[bytes, FillReport]:
    """
    Fill the title-block fields on a filter datasheet template.

    Args:
        template_path: path to the blank template PDF.
        values: mapping from slot name (project_name, pool_name, customer,
            drawn_by, checked_by, drawn_date, checked_date, job_number,
            part_number) to the string to write. Missing keys are skipped.
        output_path: if given, also writes the filled PDF to disk.

    Returns:
        (pdf_bytes, report). The report tells the caller exactly which slots
        were filled, which were missing, and via which lookup strategy.

    Raises:
        ValueError: if a required slot could not be resolved on the template.
    """
    reader = PdfReader(template_path)
    discovered = discover_fields(reader)

    report = FillReport(
        template=template_path.split("/")[-1],
        extra_discovered=len(discovered),
    )

    if not discovered:
        raise ValueError(
            f"No form fields found on template {template_path}. "
            f"Template may be flattened or use a different form mechanism."
        )

    log.info("fill_datasheet: %d fields discovered on %s",
             len(discovered), report.template)

    writer = PdfWriter(clone_from=reader)

    # Build slot -> (field_name, value) plan
    plan: dict[str, tuple[str, str]] = {}
    for slot, spec in TITLE_BLOCK_SLOTS.items():
        if slot not in values or values[slot] is None or values[slot] == "":
            if spec.required:
                report.missing[slot] = "no_value_supplied"
                log.warning("fill_datasheet: %s missing from input values", slot)
            continue

        chosen, strategy = resolve_slot(slot, spec, discovered)
        if chosen is None:
            report.missing[slot] = "field_not_found_on_template"
            log.error("fill_datasheet: %s NOT FOUND on %s (tried names=%s, defaults=%s)",
                      slot, report.template, spec.names, spec.defaults)
            if spec.required:
                # Don't raise mid-loop — collect all failures first for better debugging
                pass
            continue

        plan[chosen.name] = (slot, str(values[slot]))
        report.written[slot] = (chosen.name, strategy)
        log.info("fill_datasheet: %s -> field %r via %s",
                 slot, chosen.name, strategy)

    # Apply updates: pypdf writes per-page, so group by page
    for page_idx, page in enumerate(writer.pages):
        page_updates = {}
        annots = page.get("/Annots")
        if not annots:
            continue
        page_field_names = set()
        for ref in annots:
            obj = ref.get_object()
            if obj.get("/Subtype") != "/Widget":
                continue
            t = obj.get("/T")
            if t is None:
                parent = obj.get("/Parent")
                if parent is not None:
                    t = parent.get_object().get("/T")
            if t is not None:
                page_field_names.add(str(t))

        for field_name, (slot, val) in plan.items():
            if field_name in page_field_names:
                page_updates[field_name] = val

        if page_updates:
            try:
                writer.update_page_form_field_values(page, page_updates)
            except Exception as e:
                log.exception("fill_datasheet: write failed on page %d: %s",
                              page_idx, e)
                for fn in page_updates:
                    slot = next((s for s, (n, _) in plan.items() if False), None)
                    # rebuild: which slot used field fn
                    for s, (fname, _) in report.written.items():
                        if fname == fn:
                            report.missing[s] = f"write_error: {e}"
                            report.written.pop(s, None)
                            break

    # Ensure form appearance is rebuilt by the viewer so values actually display
    try:
        if "/AcroForm" in writer._root_object:  # type: ignore[attr-defined]
            acroform = writer._root_object["/AcroForm"]  # type: ignore[attr-defined]
            acroform[NameObject("/NeedAppearances")] = BooleanObject(True)
    except Exception:
        log.debug("fill_datasheet: could not set NeedAppearances", exc_info=True)

    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    if output_path:
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    # Final guard: if any *required* slot failed AND we had a value for it,
    # raise. Required-but-no-value-supplied is the caller's problem, not ours.
    hard_failures = [
        slot for slot, reason in report.missing.items()
        if TITLE_BLOCK_SLOTS[slot].required
        and slot in values
        and values[slot]
        and reason != "no_value_supplied"
    ]
    if hard_failures:
        raise ValueError(
            f"fill_datasheet: required slots could not be written: {hard_failures}\n"
            f"{report.summary()}"
        )

    log.info("fill_datasheet: complete\n%s", report.summary())
    return pdf_bytes, report
