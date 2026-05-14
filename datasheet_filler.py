"""
datasheet_filler.py

Two responsibilities:

1. resolve_filter_template(): pick the right filter datasheet template PDF
   given a reference model number and the rest of the quote (so we can read
   the valve kit spec to choose the X×Y reducing bushing variant).

2. fill_datasheet(): fill the title-block fields on that template.

The previous fill_datasheet was silently failing because real production
templates name their project_name field "-" (a literal hyphen) and use
generic names like Text2/Text3 for everything else. This version uses a
three-tier resolution (name → default-value → rect proximity), logs every
attempt, and returns a structured FillReport so silent failure is no longer
possible.

Field-name map verified against all five Imperial filter sizes
(SP-27-48-487, SP-33-48-732, SP-41-48-1038, SP-49-48-1548, SP-55-48-2076)
and the Assero family (SP-29-36-*) including their reducing-bushing variants.
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filter family catalog
# ---------------------------------------------------------------------------
# Each family lives in its own subfolder under the template base directory.
# The base directory is configurable via FILTER_TEMPLATE_BASE; on Render that's
# /app/defender_drawings/DEFENDER3_DRAWINGS.

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
    "ASSERO": "ASSERO/IMPERIAL",  # Assero imperial templates
}

# Where the DEFENDER3 drawings live. Container layout is
# /app/defender_drawings/DEFENDER3_DRAWINGS/{IMPERIAL,ASSERO/IMPERIAL}/...
FILTER_TEMPLATE_BASE = Path(os.environ.get(
    "FILTER_TEMPLATE_BASE",
    "defender_drawings/DEFENDER3_DRAWINGS",
))


def normalize_model(reference: str) -> str:
    """
    Normalize a filter reference to its bare model number.

    Quote references sometimes carry suffixes ('-A', whitespace, lowercase),
    e.g. mapping_table has "SP-29-36-250-A" while the template filename is
    "TEMPLATE - SP-29-36-250.pdf". This strips trailing -X letter suffixes
    and any whitespace so we always compare on the same key.

    Examples:
        normalize_model("SP-33-48-732") -> "SP-33-48-732"
        normalize_model("sp-29-36-250-a ") -> "SP-29-36-250"
        normalize_model(" SP-29-36-250-A") -> "SP-29-36-250"
    """
    if not reference:
        return ""
    s = str(reference).strip().upper()
    # Strip trailing single-letter revision suffix like "-A" or "-B"
    s = re.sub(r"-[A-Z]$", "", s)
    return s


def family_for(model: str) -> Optional[str]:
    """Return 'IMPERIAL' or 'ASSERO' for a normalized model, or None."""
    norm = normalize_model(model)
    for fam, models in FILTER_FAMILIES.items():
        if norm in models:
            return fam
    return None


# ---------------------------------------------------------------------------
# Valve-kit-driven variant selection
# ---------------------------------------------------------------------------
# Quote line items include a "DEFENDER VALVE KIT 120V AUTO 8/6/3/3SG" entry
# per pool section. The first two digits (influent / effluent) drive the
# reducing-bushing variant we pick:
#   8/6 -> "8X6 REDUCING BUSHING"
#   6/4 -> "6X4 REDUCING BUSHING"
# If we can't find a matching variant template, we fall back to the base
# (no-suffix) template so the pipeline still produces a usable PDF.

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

    Args:
        ref_positional: the filter model (e.g. "SP-33-48-732"), usually
            line_item.reference from the parsed quote. Note the orchestrator
            also passes `reference=` as a kwarg with the same value; we accept
            both and the kwarg wins if present.
        line_items: full list of parsed quote line items, used to look up
            the matching valve kit in the same section.
        reference: kwarg-form of the filter model (duplicate of positional,
            kept for orchestrator backward compatibility).
        section: the quote section this filter belongs to ("Lap Pool" /
            "Training pool" / ...). Used to find the right valve kit.
        template_base: optional override of FILTER_TEMPLATE_BASE.

    Returns:
        Path to the chosen template PDF.

    Raises:
        KeyError: if the reference can't be matched to a known family, or
            if no template file exists for it.
    """
    base = Path(template_base) if template_base else FILTER_TEMPLATE_BASE
    # Kwarg wins over positional if both supplied (orchestrator passes both)
    effective_ref = reference or ref_positional
    model = normalize_model(effective_ref)
    if not model:
        raise KeyError(f"Empty filter reference (section={section!r})")

    family = family_for(model)
    if family is None:
        raise KeyError(
            f"Unknown filter family for reference {reference!r} "
            f"(normalized {model!r}). Known families: {list(FILTER_FAMILIES)}"
        )

    subdir = base / FAMILY_SUBDIR[family]
    if not subdir.exists():
        raise KeyError(
            f"Template subdir not found: {subdir}. "
            f"Check FILTER_TEMPLATE_BASE env var (currently {base})."
        )

    # Build candidate filenames in preference order:
    #   1. with reducing-bushing variant matching the valve kit
    #   2. bare model number (no suffix)
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

@dataclass
class SlotSpec:
    """How to find one logical title-block slot on a template."""
    names: tuple[str, ...] = ()
    defaults: tuple[str, ...] = ()
    rect_hint: Optional[tuple[float, float, float, float]] = None
    y_rank: Optional[str] = None  # "upper" / "lower" / None
    required: bool = True


# Verified field-name map. Stable across IMPERIAL and ASSERO templates.
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
        required=False,
    ),
    "checked_date": SlotSpec(
        names=("Text9",),
        defaults=("DYMNYR", "MM/DD/YY"),
        rect_hint=(1563, 462, 1586, 472),
        y_rank="lower",
        required=False,
    ),
    "job_number": SlotSpec(
        names=("Text12",),
        defaults=("####",),
        rect_hint=(1693, 415, 1754, 423),
    ),
    "part_number": SlotSpec(
        names=("Text11",),
        defaults=("SP-33-48-732", "SP-27-48-487", "SP-41-48-1038",
                  "SP-49-48-1548", "SP-55-48-2076",
                  "SP-29-36-200", "SP-29-36-250", "SP-29-36-300",
                  "SP-29-36-350", "SP-29-36-400", "SP-29-36-450",
                  "SP-29-36-500"),
        rect_hint=(1641, 415, 1691, 423),
        required=False,
    ),
    "project_code": SlotSpec(
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


@dataclass
class DiscoveredField:
    name: str
    default: str
    rect: Optional[tuple[float, float, float, float]]


def _discover_fields(reader: PdfReader) -> dict[str, DiscoveredField]:
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

    out: dict[str, DiscoveredField] = {}
    for name, fobj in fields.items():
        dv = str(fobj.get("/V") or fobj.get("/DV") or "")
        out[str(name)] = DiscoveredField(
            name=str(name),
            default=dv,
            rect=rects.get(str(name)),
        )
    return out


def _rect_distance(a, b) -> float:
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _resolve_slot(spec: SlotSpec, discovered: dict[str, DiscoveredField]):
    # Tier 1: exact name
    for nm in spec.names:
        if nm in discovered:
            return discovered[nm], "name"
    # Tier 2: default-value match (disambiguate duplicates by Y rank)
    matches = [f for f in discovered.values() if f.default in spec.defaults]
    if matches:
        if len(matches) == 1 or spec.y_rank is None:
            return matches[0], "default"
        with_rect = [m for m in matches if m.rect is not None]
        if with_rect:
            with_rect.sort(key=lambda m: m.rect[1])
            chosen = with_rect[-1] if spec.y_rank == "upper" else with_rect[0]
            return chosen, "default"
        return matches[0], "default"
    # Tier 3: rect proximity
    if spec.rect_hint is not None:
        cands = [(f, _rect_distance(f.rect, spec.rect_hint))
                 for f in discovered.values() if f.rect is not None]
        if cands:
            cands.sort(key=lambda c: c[1])
            best, dist = cands[0]
            if dist < 25:
                return best, "rect"
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


# Map orchestrator's kwargs to internal slot names. Backward-compatible alias
# layer: old call sites used client_name/engineer_initials; new code can use
# the canonical slot names directly.
_KW_ALIASES = {
    "client_name":       "customer",
    "engineer_initials": "drawn_by",
    "initials":          "drawn_by",
    "date":              "drawn_date",
}


def fill_datasheet(template_path, output_path=None, /, **kwargs):
    """
    Fill the title-block fields on a filter datasheet template.

    Supports two call styles:

    A) Orchestrator-style keyword args (backward-compatible):
         fill_datasheet(template, out_path,
             project_name="X", pool_name="Y", client_name="Z",
             job_number="123", engineer_initials="ABC", drawn_date="...")

    B) Canonical values-dict:
         fill_datasheet(template, out_path,
             values={"project_name": "X", "pool_name": "Y", ...})

    Both forms accept any of the slot names defined in TITLE_BLOCK_SLOTS.
    Aliases (client_name, engineer_initials, initials, date) are translated.

    Returns (pdf_bytes, FillReport). The report tells the caller exactly
    which slots were filled, which were missing, and via which lookup
    strategy — no more silent failures.
    """
    # Normalize input into a values dict
    if "values" in kwargs and isinstance(kwargs["values"], dict):
        values = dict(kwargs["values"])
        # Allow extra kwargs to merge in
        extra = {k: v for k, v in kwargs.items() if k != "values"}
        values.update(extra)
    else:
        values = dict(kwargs)

    # Translate aliases
    for old_key, new_key in _KW_ALIASES.items():
        if old_key in values and new_key not in values:
            values[new_key] = values.pop(old_key)
        elif old_key in values:
            values.pop(old_key)  # canonical key wins

    template_path = str(template_path)
    reader = PdfReader(template_path)
    discovered = _discover_fields(reader)

    report = FillReport(template=os.path.basename(template_path))

    if not discovered:
        raise ValueError(
            f"No form fields on template {template_path}. "
            f"Template may be flattened or non-AcroForm."
        )

    log.info("fill_datasheet: %d fields on %s", len(discovered), report.template)

    writer = PdfWriter(clone_from=reader)

    plan: dict[str, tuple[str, str]] = {}  # field_name -> (slot, value)
    for slot, spec in TITLE_BLOCK_SLOTS.items():
        val = values.get(slot)
        if val is None or val == "":
            if spec.required:
                report.missing[slot] = "no_value_supplied"
                log.warning("fill_datasheet: %s missing from input values", slot)
            continue

        chosen, strategy = _resolve_slot(spec, discovered)
        if chosen is None:
            report.missing[slot] = "field_not_found_on_template"
            log.error("fill_datasheet: %s NOT FOUND on %s (names=%s defaults=%s)",
                      slot, report.template, spec.names, spec.defaults)
            continue

        plan[chosen.name] = (slot, str(val))
        report.written[slot] = (chosen.name, strategy)
        log.info("fill_datasheet: %s -> %r via %s", slot, chosen.name, strategy)

    # Apply per page (pypdf API)
    for page_idx, page in enumerate(writer.pages):
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

        page_updates = {
            fn: val for fn, (_, val) in plan.items() if fn in page_field_names
        }
        if page_updates:
            try:
                writer.update_page_form_field_values(page, page_updates)
            except Exception as e:
                log.exception("fill_datasheet: write failed page %d", page_idx)
                for fn in page_updates:
                    for s, (fname, _) in list(report.written.items()):
                        if fname == fn:
                            report.missing[s] = f"write_error: {e}"
                            report.written.pop(s, None)
                            break

    # NeedAppearances so Adobe rebuilds the visual rendering of filled fields
    try:
        if "/AcroForm" in writer._root_object:  # type: ignore[attr-defined]
            af = writer._root_object["/AcroForm"]  # type: ignore[attr-defined]
            af[NameObject("/NeedAppearances")] = BooleanObject(True)
    except Exception:
        log.debug("fill_datasheet: could not set NeedAppearances", exc_info=True)

    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    if output_path:
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    log.info("fill_datasheet: complete\n%s", report.summary())
    return pdf_bytes, report
