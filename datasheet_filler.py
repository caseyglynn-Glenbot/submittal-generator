"""
datasheet_filler.py

Two responsibilities:

1. resolve_filter_template(): pick the right filter datasheet template PDF
   given a reference model number and the rest of the quote (so we can read
   the valve kit spec to choose the X×Y reducing bushing variant).

2. fill_datasheet(): fill the title-block fields on that template AND BAKE
   the values into the page content stream so they survive the orchestrator's
   downstream fitz.insert_pdf() merge.

History of this file:

- v1 used pypdf with the wrong slot map (project_name field is literally
  named "-"). Title block silently came out blank.
- v2 fixed the slot map but kept pypdf for writing. Filled values were
  stored in AcroForm /V entries with no appearance streams, relying on
  /NeedAppearances. Orchestrator's fitz.insert_pdf() merge stripped the
  AcroForm entirely → title block STILL came out blank in the merged PDF.
- v3 (this file) uses fitz throughout: discover widgets, fill them, then
  doc.bake() converts the filled widgets into permanent page content
  BEFORE the orchestrator's merge step. Now values can never be stripped
  by downstream PDF operations.

Field-name map verified against all five Imperial filter sizes
(SP-27-48-487, SP-33-48-732, SP-41-48-1038, SP-49-48-1548, SP-55-48-2076)
and the Assero family (SP-29-36-*) including their reducing-bushing variants.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF — already a requirement for the orchestrator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filter family catalog
# ---------------------------------------------------------------------------

FILTER_FAMILIES: dict[str, set[str]] = {
    "IMPERIAL": {
        "SP-27-48-487",
        "SP-33-48-732",
        "SP-41-48-1038",
        "SP-49-48-1548",
        "SP-55-48-2076",
    },
    "ASSERO": {
        "SP-29-36-200",
        "SP-29-36-250",
        "SP-29-36-300",
        "SP-29-36-350",
        "SP-29-36-400",
        "SP-29-36-450",
        "SP-29-36-500",
    },
    # 36" element Imperial line (Sep 2026). Engineering drawing numbers:
    #   SP-33-36-732  V113922361 / 6X4  V113859917
    #   SP-39-36-948  V113922362 / 8X6  V113860381
    #   SP-43-36-1182 V113922363 / 8X6  V113860413
    #   SP-47-36-1440 V113922364 / 8X6  V113860436
    #   SP-50-36-1698 V113922365 / 10X8 V113860454
    #   SP-55-36-2076 V113922366 / 10X8 V113860659
    # These drawings use their own field names (see _FIELD_ROLE_PATTERNS) and
    # are drawn at 2x size (2448x1584); fill_datasheet scales them down.
    "IMPERIAL_36": {
        "SP-33-36-732",
        "SP-39-36-948",
        "SP-43-36-1182",
        "SP-47-36-1440",
        "SP-50-36-1698",
        "SP-55-36-2076",
    },
}

FAMILY_SUBDIR = {
    "IMPERIAL": "IMPERIAL",
    "ASSERO": "ASSERO/IMPERIAL",
    "IMPERIAL_36": "IMPERIAL 36",
}

# Datasheet page size used by the original Imperial/Assero drawings (11x17).
DATASHEET_PAGE_SIZE = (1224, 792)

FILTER_TEMPLATE_BASE = Path(os.environ.get(
    "FILTER_TEMPLATE_BASE",
    "defender_drawings/DEFENDER3_DRAWINGS",
))


def normalize_model(reference: str) -> str:
    """Strip trailing -X letter suffix and whitespace, uppercase."""
    if not reference:
        return ""
    s = str(reference).strip().upper()
    s = re.sub(r"-[A-Z]$", "", s)
    return s


def family_for(model: str) -> Optional[str]:
    norm = normalize_model(model)
    for fam, models in FILTER_FAMILIES.items():
        if norm in models:
            return fam
    return None


# ---------------------------------------------------------------------------
# Valve-kit-driven variant selection
# ---------------------------------------------------------------------------

_VALVE_KIT_RE = re.compile(r"(\d+)/(\d+)/(\d+)/(\d+)SG", re.I)


def _valve_kit_for_section(line_items, section: str) -> Optional[tuple[int, int]]:
    """Return (influent, effluent) inches from the valve kit in this section."""
    if not section:
        return None
    for li in line_items:
        if getattr(li, "section", None) != section:
            continue
        desc = (getattr(li, "description", "") or "").upper()
        if "DEFENDER VALVE KIT" not in desc:
            continue
        m = _VALVE_KIT_RE.search(desc)
        if m:
            return int(m.group(1)), int(m.group(2))
    # No valve kit line: the section may quote its valves individually. Use
    # the same role assignment the valve pages use (check valve = influent,
    # next largest = effluent) so the drawing's reducing-bushing variant
    # matches what the valve pages box. Quote 04062392 (12" check + 10" PA)
    # fell through to the base drawing before this.
    try:
        from valve_roles import resolve_section_valve_sizes
        section_items = [li for li in line_items
                         if getattr(li, "section", None) == section]
        sizes, _ = resolve_section_valve_sizes(section_items)
    except Exception as e:  # never let variant selection block the datasheet
        log.warning("loose-valve variant lookup failed for %r: %s", section, e)
        return None
    if sizes and sizes.get("influent") and sizes.get("effluent"):
        inf, eff = sizes["influent"], sizes["effluent"]
        if float(inf).is_integer() and float(eff).is_integer() and inf != eff:
            return int(inf), int(eff)
    return None


def resolve_filter_template(
    ref_positional,
    line_items: Iterable,
    *,
    reference: str = "",
    section: str = "",
    template_base: Optional[Path] = None,
    **_ignored,
) -> Path:
    """
    Pick the filter datasheet template PDF for one filter line item.

    The orchestrator calls this with `reference` both positionally AND as a
    kwarg (an existing quirk). We accept both; kwarg wins if supplied.
    """
    base = Path(template_base) if template_base else FILTER_TEMPLATE_BASE
    effective_ref = reference or ref_positional
    model = normalize_model(effective_ref)
    if not model:
        raise KeyError(f"Empty filter reference (section={section!r})")

    family = family_for(model)
    if family is None:
        raise KeyError(
            f"Unknown filter family for reference {effective_ref!r} "
            f"(normalized {model!r}). Known families: {list(FILTER_FAMILIES)}"
        )

    subdir = base / FAMILY_SUBDIR[family]
    if not subdir.exists():
        raise KeyError(
            f"Template subdir not found: {subdir}. "
            f"Check FILTER_TEMPLATE_BASE env var (currently {base})."
        )

    candidates: list[str] = []
    kit = _valve_kit_for_section(line_items, section)
    if kit is not None:
        inf, eff = kit
        variant = f"{inf}X{eff} REDUCING BUSHING"
        candidates.append(f"TEMPLATE - {model} - {variant}.pdf")
        log.info("resolve_filter_template: %s section=%r kit=%d/%d -> variant %r",
                 model, section, inf, eff, variant)
    else:
        log.info("resolve_filter_template: %s section=%r no valve kit found, "
                 "using base template", model, section)
    candidates.append(f"TEMPLATE - {model}.pdf")

    for filename in candidates:
        path = subdir / filename
        if path.exists():
            log.info("resolve_filter_template: chose %s", path)
            return path

    # Engineering's own file names also resolve, e.g.
    #   "TEMPLATE - V113922366-SP-55-36-2076.pdf"
    #   "TEMPLATE - V113860659-SP-55-36-2076 10X8 REDUCING BUSHINGS.pdf"
    # (drawing number prefix, no " - " before the variant, BUSHINGS plural).
    variant_re = None
    if kit is not None:
        variant_re = re.compile(rf"\b{kit[0]}\s*X\s*{kit[1]}\s+REDUCING\s+BUSHINGS?\b", re.I)
    model_re = re.compile(rf"(?:^|[\s-]){re.escape(model)}(?![\d-])", re.I)
    loose = [p for p in sorted(subdir.glob("*.pdf")) if model_re.search(p.stem)]
    plain = [p for p in loose if "REDUCING" not in p.stem.upper()
             and "MIRRORED" not in p.stem.upper() and "ROTATED" not in p.stem.upper()]
    for path in ([p for p in loose if variant_re.search(p.stem)] if variant_re else []) + plain:
        log.info("resolve_filter_template: chose %s (engineering file name)", path)
        return path

    raise KeyError(
        f"No template file found for {effective_ref!r}. Tried: "
        f"{[str(subdir / c) for c in candidates]}"
    )


# ---------------------------------------------------------------------------
# Title-block filling
# ---------------------------------------------------------------------------
#
# NOTE on coordinate system: fitz Y is top-down (0 at top of page), opposite
# of pypdf. For duplicate-default disambiguation, "upper on the drawing" =
# SMALLER fitz Y. So y_rank "upper" picks the field with the lower y0 value.

@dataclass
class SlotSpec:
    names: tuple[str, ...] = ()
    defaults: tuple[str, ...] = ()
    y_rank: Optional[str] = None  # "upper" / "lower" — disambiguates duplicates
    required: bool = True
    # When True and no value is supplied, the widget is still resolved and
    # cleared (single-space write) so its template placeholder text doesn't
    # leak into the baked output. Use for optional slots whose template
    # default is a placeholder like "INT" / "DYMNYR" / "JOB#" rather than
    # real reference content (e.g. a model number that we want to keep).
    clear_if_unfilled: bool = False


TITLE_BLOCK_SLOTS: dict[str, SlotSpec] = {
    "project_name": SlotSpec(
        names=("-",),
        defaults=("PROJECT NAME",),
    ),
    "pool_name": SlotSpec(
        names=("Text2",),
        defaults=("POOL NAME",),
    ),
    "customer": SlotSpec(
        names=("Text3",),
        defaults=("CUSTOMER", "CLIENT NAME", "CLIENT"),
    ),
    "drawn_by": SlotSpec(
        names=("Text6",),
        defaults=("INIT", "INT"),
        y_rank="upper",
    ),
    "drawn_date": SlotSpec(
        names=("Text7",),
        defaults=("DYMNYR", "MM/DD/YY"),
        y_rank="upper",
    ),
    "checked_by": SlotSpec(
        names=("Text8",),
        defaults=("INIT", "INT"),
        y_rank="lower",
        required=False,
        clear_if_unfilled=True,
    ),
    "checked_date": SlotSpec(
        names=("Text9",),
        defaults=("DYMNYR", "MM/DD/YY"),
        y_rank="lower",
        required=False,
        clear_if_unfilled=True,
    ),
    "job_number": SlotSpec(
        names=("Text12",),
        defaults=("####",),
    ),
    "part_number": SlotSpec(
        names=("Text11",),
        defaults=("SP-33-48-732", "SP-27-48-487", "SP-41-48-1038",
                  "SP-49-48-1548", "SP-55-48-2076",
                  "SP-29-36-200", "SP-29-36-250", "SP-29-36-300",
                  "SP-29-36-350", "SP-29-36-400", "SP-29-36-450",
                  "SP-29-36-500"),
        required=False,
    ),
    "project_code": SlotSpec(
        names=("Text10",),
        defaults=("JOB#",),
        required=False,
        clear_if_unfilled=True,
    ),
    "sheet_num": SlotSpec(
        names=("Text13",),
        required=False,
    ),
    "sheet_total": SlotSpec(
        names=("Text14",),
        required=False,
    ),
    "revision": SlotSpec(
        names=("Text15",),
        required=False,
    ),
}


@dataclass
class DiscoveredWidget:
    name: str
    value: str   # the placeholder/default text currently in the widget
    y: float     # top-down Y (fitz convention) for disambiguation


def _discover_widgets(page) -> dict[str, DiscoveredWidget]:
    """Build {field_name: DiscoveredWidget} for one fitz page."""
    out: dict[str, DiscoveredWidget] = {}
    for w in page.widgets():
        if not w.field_name:
            continue
        out[w.field_name] = DiscoveredWidget(
            name=w.field_name,
            value=w.field_value or "",
            y=w.rect.y0,
        )
    return out


# The 36" line drawings name their fields two different ways, neither of
# which matches the Text* names above:
#   named:   "55-Project Name", "55_1-DWN BY", "43-Project NUMBER"
#   lettered: "F", "43-F", "50-M"  (A..X in title-block order)
# Match on the part after the model prefix. Tier 0 in _resolve_slot.
_FIELD_PREFIX_RE = re.compile(r"^\d+(?:_\d+)?-")
_NAMED_FIELD_ROLES = {
    "PROJECT NAME": "project_name",
    "POOL NAME": "pool_name",
    "CLIENT NAME": "customer",
    "DWN BY": "drawn_by",
    "DWN DT": "drawn_date",
    "CHK BY": "checked_by",
    "CHK DT": "checked_date",
    "PROJECT NUMBER": "job_number",
    "SHEET #": "sheet_num",
    "TOTAL SHEETS": "sheet_total",
    "REV #": "revision",
}
_LETTER_FIELD_ROLES = {
    "F": "project_name", "G": "pool_name", "H": "customer",
    "I": "job_number", "J": "sheet_num", "K": "sheet_total", "L": "revision",
    "M": "drawn_by", "N": "drawn_date", "O": "checked_by", "P": "checked_date",
}


def _field_role(field_name: str) -> Optional[str]:
    base = _FIELD_PREFIX_RE.sub("", field_name or "").strip().upper()
    return _NAMED_FIELD_ROLES.get(base) or _LETTER_FIELD_ROLES.get(base)


# Placeholder text that must never reach a submittal. Any widget still holding
# one of these after the fill is blanked before baking (the lettered 36"
# drawings carry "INIT"/"DYMNYR" in their revision rows too).
_PLACEHOLDER_VALUES = {
    "INIT", "INT", "DYMNYR", "MM/DD/YY", "JOB#", "####", "#####",
    "[PROJECT NAME]", "[POOL NAME]", "[CLIENT NAME]",
    "PROJECT NAME", "POOL NAME", "CLIENT NAME",
}


def _resolve_slot(spec: SlotSpec, discovered: dict[str, DiscoveredWidget],
                  slot: Optional[str] = None):
    """Return (DiscoveredWidget, strategy) or (None, 'not_found')."""
    # Tier 0: role derived from the field name (36" line drawings)
    if slot:
        for w in discovered.values():
            if _field_role(w.name) == slot:
                return w, "role"
    # Tier 1: exact field name
    for nm in spec.names:
        if nm in discovered:
            return discovered[nm], "name"

    # Tier 2: default-value match, disambiguated by Y position if needed
    matches = [w for w in discovered.values() if w.value in spec.defaults]
    if matches:
        if len(matches) == 1 or spec.y_rank is None:
            return matches[0], "default"
        matches.sort(key=lambda m: m.y)
        # fitz Y is top-down, so "upper on drawing" = smallest Y
        chosen = matches[0] if spec.y_rank == "upper" else matches[-1]
        return chosen, "default"

    return None, "not_found"


@dataclass
class FillReport:
    template: str
    written: dict[str, tuple[str, str]] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)

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


# Orchestrator-kwarg -> internal-slot aliases.
_KW_ALIASES = {
    "client_name":       "customer",
    "engineer_initials": "drawn_by",
    "initials":          "drawn_by",
    "date":              "drawn_date",
}


def fill_datasheet(template_path, output_path=None, /, **kwargs):
    """
    Fill the title-block fields on a filter datasheet template and BAKE
    the values into the page content stream so they survive any subsequent
    fitz.insert_pdf() merge.

    Supports two call styles:

    A) Orchestrator-style keyword args (backward-compatible):
         fill_datasheet(template, out_path,
             project_name="X", pool_name="Y", client_name="Z",
             job_number="123", engineer_initials="ABC", drawn_date="...")

    B) Canonical values-dict:
         fill_datasheet(template, out_path,
             values={"project_name": "X", "pool_name": "Y", ...})

    Returns (pdf_bytes, FillReport).
    """
    # Normalize input into a values dict
    if "values" in kwargs and isinstance(kwargs["values"], dict):
        values = dict(kwargs["values"])
        extra = {k: v for k, v in kwargs.items() if k != "values"}
        values.update(extra)
    else:
        values = dict(kwargs)

    # Translate aliases — canonical key wins if both present
    for old_key, new_key in _KW_ALIASES.items():
        if old_key in values and new_key not in values:
            values[new_key] = values.pop(old_key)
        elif old_key in values:
            values.pop(old_key)

    template_path = str(template_path)
    report = FillReport(template=os.path.basename(template_path))

    doc = fitz.open(template_path)
    try:
        # We assume single-page filter templates (verified across all 12 SP-*
        # imperial and assero models). If a multi-page template ever appears,
        # we still try every page in case fields are split across pages.
        all_widgets: dict[str, tuple[int, DiscoveredWidget]] = {}
        for page_idx, page in enumerate(doc):
            for name, w in _discover_widgets(page).items():
                all_widgets.setdefault(name, (page_idx, w))

        if not all_widgets:
            raise ValueError(
                f"No form fields on template {template_path}. "
                f"Template may be flattened or non-AcroForm."
            )

        log.info("fill_datasheet: %d widgets on %s",
                 len(all_widgets), report.template)

        # Build slot -> (page_idx, widget, value) plan
        plan: list[tuple[int, str, str, str, str]] = []  # (page_idx, field_name, value, slot, strategy)
        # Flatten widgets-by-name for resolver
        discovered_flat = {n: w for n, (_, w) in all_widgets.items()}

        for slot, spec in TITLE_BLOCK_SLOTS.items():
            val = values.get(slot)
            # The named 36" drawings leave SHEET __ OF __ blank where the
            # older drawings print "1 OF 1". Every datasheet is a single sheet.
            if (val is None or val == "") and slot in ("sheet_num", "sheet_total"):
                w0, _ = _resolve_slot(spec, discovered_flat, slot)
                if w0 is not None and not (w0.value or "").strip():
                    val = "1"
            if val is None or val == "":
                # Three sub-cases when no value is supplied:
                #   1. Slot is required → log a 'missing' entry, skip.
                #   2. Slot is optional and clear_if_unfilled=True → still
                #      resolve the widget and queue a blank write so the
                #      template placeholder doesn't leak into the baked PDF.
                #   3. Slot is optional and clear_if_unfilled=False → leave
                #      widget alone (preserves whatever default the template
                #      had, e.g. the part_number's real SP-* code).
                if spec.required:
                    report.missing[slot] = "no_value_supplied"
                    log.warning("fill_datasheet: %s missing from input values", slot)
                    continue
                if not spec.clear_if_unfilled:
                    continue
                # Fall through into the resolver with an empty value; the
                # write step below converts "" to a single space.
                val = ""

            chosen, strategy = _resolve_slot(spec, discovered_flat, slot)
            if chosen is None:
                report.missing[slot] = "field_not_found_on_template"
                log.error("fill_datasheet: %s NOT FOUND on %s "
                          "(names=%s defaults=%s)",
                          slot, report.template, spec.names, spec.defaults)
                continue

            page_idx, _ = all_widgets[chosen.name]
            plan.append((page_idx, chosen.name, str(val), slot, strategy))
            report.written[slot] = (chosen.name, strategy)
            log.info("fill_datasheet: %s -> %r via %s",
                     slot, chosen.name, strategy)

        # Apply writes by walking page widgets directly (fitz API needs the
        # live Widget object, not a name; we re-fetch per page).
        page_to_writes: dict[int, dict[str, str]] = {}
        for page_idx, field_name, val, _slot, _strat in plan:
            page_to_writes.setdefault(page_idx, {})[field_name] = val

        # Blank any placeholder the plan didn't touch (revision-row INIT /
        # DYMNYR on the lettered 36" drawings, unfilled optional slots).
        # A reducing-bushing drawing whose note names a different bushing than
        # its file (SP-55-36-2076 10X8, V113860659, shipped reading "12X10
        # REDUCING BUSHINGS INCLUDED") is corrected to match the file name.
        fm = re.search(r"(\d+)\s*X\s*(\d+)\s+REDUCING\s+BUSHING",
                       os.path.basename(template_path), re.I)
        if fm:
            want = f"{fm.group(1)}X{fm.group(2)}"
            for page_idx, page in enumerate(doc):
                for widget in page.widgets():
                    val = widget.field_value or ""
                    nm = re.match(r"^\s*(\d+)\s*X\s*(\d+)(\s+REDUCING\s+BUSHINGS?\s+INCLUDED.*)$", val, re.I)
                    if nm and f"{nm.group(1)}X{nm.group(2)}" != want:
                        page_to_writes.setdefault(page_idx, {})[widget.field_name] = want + nm.group(3)
                        log.warning("fill_datasheet: corrected bushing note %r -> %s", val, want)

        for page_idx, page in enumerate(doc):
            for widget in page.widgets():
                name = widget.field_name
                if not name or name in page_to_writes.get(page_idx, {}):
                    continue
                if (widget.field_value or "").strip().upper() in _PLACEHOLDER_VALUES:
                    page_to_writes.setdefault(page_idx, {})[name] = ""

        for page_idx, writes in page_to_writes.items():
            page = doc[page_idx]
            for widget in page.widgets():
                if widget.field_name in writes:
                    raw = writes[widget.field_name]
                    # Empty string → single space so fitz regenerates the
                    # widget appearance as blank instead of preserving the
                    # template's placeholder text (same fitz quirk that the
                    # cover_page_filler.py addresses).
                    widget.field_value = raw if raw != "" else " "
                    widget.update()

        # CRITICAL: bake the widgets into the page content stream so the
        # values survive the orchestrator's fitz.insert_pdf() merge. Without
        # this step the values are stored in AcroForm fields that the merge
        # strips out.
        doc.bake(annots=False, widgets=True)

        # The 36" line is drawn at 2x (2448x1584). Scale any oversized sheet
        # down to the 11x17 size the rest of the datasheets use so the merged
        # submittal stays uniform.
        oversized = any(pg.rect.width > DATASHEET_PAGE_SIZE[0] * 1.1 for pg in doc)
        if oversized:
            scaled = fitz.open()
            for pg in doc:
                if pg.rect.width > DATASHEET_PAGE_SIZE[0] * 1.1:
                    w, h = DATASHEET_PAGE_SIZE
                    if pg.rect.height > pg.rect.width:
                        w, h = h, w
                    new = scaled.new_page(width=w, height=h)
                    new.show_pdf_page(new.rect, doc, pg.number)
                else:
                    scaled.insert_pdf(doc, from_page=pg.number, to_page=pg.number)
            doc.close()
            doc = scaled

        # Serialize
        pdf_bytes = doc.tobytes(garbage=3, deflate=True) if oversized else doc.tobytes()

        if output_path:
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)

    finally:
        doc.close()

    log.info("fill_datasheet: complete\n%s", report.summary())
    return pdf_bytes, report
