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
}

FAMILY_SUBDIR = {
    "IMPERIAL": "IMPERIAL",
    "ASSERO": "ASSERO/IMPERIAL",
}

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
    ),
    "checked_date": SlotSpec(
        names=("Text9",),
        defaults=("DYMNYR", "MM/DD/YY"),
        y_rank="lower",
        required=False,
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


def _resolve_slot(spec: SlotSpec, discovered: dict[str, DiscoveredWidget]):
    """Return (DiscoveredWidget, strategy) or (None, 'not_found')."""
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
            if val is None or val == "":
                if spec.required:
                    report.missing[slot] = "no_value_supplied"
                    log.warning("fill_datasheet: %s missing from input values", slot)
                continue

            chosen, strategy = _resolve_slot(spec, discovered_flat)
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

        for page_idx, writes in page_to_writes.items():
            page = doc[page_idx]
            for widget in page.widgets():
                if widget.field_name in writes:
                    widget.field_value = writes[widget.field_name]
                    widget.update()

        # CRITICAL: bake the widgets into the page content stream so the
        # values survive the orchestrator's fitz.insert_pdf() merge. Without
        # this step the values are stored in AcroForm fields that the merge
        # strips out.
        doc.bake(annots=False, widgets=True)

        # Serialize
        pdf_bytes = doc.tobytes()

        if output_path:
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)

    finally:
        doc.close()

    log.info("fill_datasheet: complete\n%s", report.summary())
    return pdf_bytes, report
