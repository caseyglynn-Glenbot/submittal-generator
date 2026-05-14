"""
Filter datasheet filler — selects the right Defender3 template variant
based on quote contents, then fills the title block.

Variant selection logic:
  The quote's FILTER DEFENDER line gives the base model (e.g. SP-33-48-732).
  Additional line items in the quote disambiguate to a specific variant:
    - 'BUSHING REDUCER ASSY 8" X 6"' line → use the "8X6 REDUCING BUSHING" variant
    - Reference ending in '-R' → reduced height variant
    - (Mirrored davit detection TBD — depends on what quote signal exists)

Once a variant is resolved to a file path, fill the title block fields:
  PROJECT NAME, POOL NAME, CLIENT NAME, JOB#, DWN initials, dates.
"""
import re
import fitz
from pathlib import Path


def find_field_by_value(page, current_value: str):
    """Locate a form field by its current default value (used for PROJECT NAME)."""
    for w in page.widgets():
        if w.field_value == current_value:
            return w
    return None


def fill_datasheet(
    template_path: str,
    output_path: str,
    *,
    project_name: str,
    pool_name: str,
    client_name: str,
    job_number: str,
    engineer_initials: str = "",
    drawn_date: str = "",
):
    """Open a fillable datasheet PDF, fill the title block fields, save."""
    doc = fitz.open(template_path)
    page = doc[0]
    fields_by_name = {w.field_name: w for w in page.widgets()}

    def set_field(name: str, value: str):
        w = fields_by_name.get(name)
        if w is None:
            return False
        w.field_value = value
        w.update()
        return True

    # Project name field has inconsistent names — find it by its placeholder
    proj_widget = find_field_by_value(page, "PROJECT NAME")
    if proj_widget:
        proj_widget.field_value = project_name
        proj_widget.update()

    set_field("Text2", pool_name)
    set_field("Text3", client_name)
    set_field("Text12", job_number)

    if engineer_initials:
        set_field("Text6", engineer_initials)
        set_field("Text8", engineer_initials)
    if drawn_date:
        set_field("Text7", drawn_date)
        set_field("Text9", drawn_date)

    doc.save(output_path)
    doc.close()
    return output_path


# ---------------------------------------------------------------------------
# Template resolution: filter model + variants → file path
# ---------------------------------------------------------------------------

import os
FILTER_TEMPLATE_BASE = Path(
    os.environ.get("FILTER_TEMPLATE_BASE", "defender_drawings/DEFENDER3 DRAWINGS")
)

FILTER_FAMILIES = {
    "ASSERO": ["SP-29-36-200", "SP-29-36-250", "SP-29-36-300",
               "SP-29-36-350", "SP-29-36-400", "SP-29-36-450", "SP-29-36-500"],
    "IMPERIAL": ["SP-27-48-487", "SP-33-48-732", "SP-41-48-1038",
                 "SP-49-48-1548", "SP-55-48-2076"],
}


def normalize_model(filter_model: str) -> str:
    """Strip suffix qualifiers like '-A' → SP-29-36-250-A becomes SP-29-36-250."""
    return re.sub(r"-[A-Z]$", "", filter_model)


def detect_bushing_for_filter(quote_line_items, filter_section: str):
    """Determine if a reducing bushing applies to a specific filter, based
    on the valve-kit line item in the same quote section.

    The Defender valve kit description encodes its configuration as
    'N/N/N/NSG' where the first two numbers are influent and effluent
    sizes. If those are smaller than the filter's native tank
    connections, a reducing bushing is required.

    Filter native connection sizes (inches):
      SP-29-36-xxx (Assero) → 6"
      SP-27-48-487          → 6"
      SP-33-48-732          → 8"
      SP-41-48-1038         → 8"
      SP-49-48-1548         → 10"
      SP-55-48-2076         → 12"

    Returns a bushing spec like 'NXM' (e.g. '8X6', '6X4') or None.
    """
    # Find the valve kit in the same section as the filter
    kit_desc = None
    for li in quote_line_items:
        if (li.section == filter_section
                and "DEFENDER VALVE KIT" in li.description.upper()):
            kit_desc = li.description
            break
    if not kit_desc:
        return None

    # Pull the size pattern out: 'AUTO 8/6/3/3SG' or '120V AUTO 4/4/3/3SG'
    m = re.search(r'(\d+)/(\d+)/\d+/\d+', kit_desc)
    if not m:
        return None
    influent_size = int(m.group(1))
    effluent_size = int(m.group(2))

    # Find the filter's native size
    filter_native_size = None
    for li in quote_line_items:
        if li.section != filter_section:
            continue
        if "FILTER DEFENDER" not in li.description.upper():
            continue
        base = normalize_model(li.reference or "")
        if base in FILTER_FAMILIES["ASSERO"] or base == "SP-27-48-487":
            filter_native_size = 6
        elif base in {"SP-33-48-732", "SP-41-48-1038"}:
            filter_native_size = 8
        elif base == "SP-49-48-1548":
            filter_native_size = 10
        elif base == "SP-55-48-2076":
            filter_native_size = 12
        break

    if filter_native_size is None:
        return None

    # If the effluent is smaller than the filter's native size, we need a bushing
    bushing_target = min(influent_size, effluent_size)
    if bushing_target < filter_native_size:
        return f"{filter_native_size}X{bushing_target}"
    return None


def detect_reduced_height(filter_model: str, reference: str = "") -> bool:
    """Filter part reference ending in -R means reduced height."""
    return reference.endswith("-R") or filter_model.endswith("-R")


def resolve_filter_template(
    filter_model: str,
    quote_line_items=None,
    reference: str = "",
    section: str = "",
) -> Path:
    """Pick the most specific template file matching the quote contents.

    Args:
      filter_model: the model from the FILTER DEFENDER line item
      quote_line_items: full list so we can detect variants from other lines
      reference: the line item's reference field (used for -R detection)
      section: which quote section this filter is in (so we look at the
               correct valve kit for bushing detection)
    """
    base = normalize_model(filter_model)
    quote_line_items = quote_line_items or []

    bushing = detect_bushing_for_filter(quote_line_items, section) if section else None
    reduced = detect_reduced_height(filter_model, reference)
    family = "ASSERO" if base in FILTER_FAMILIES["ASSERO"] else "IMPERIAL"

    candidates = []
    variant = f" - {bushing} REDUCING BUSHING" if bushing else ""

    if family == "ASSERO":
        candidates.append(f"ASSERO/IMPERIAL/TEMPLATE - {base}{variant}.pdf")
        candidates.append(f"ASSERO/IMPERIAL/TEMPLATE - {base}.pdf")
    else:
        if reduced:
            candidates.append(
                f"IMPERIAL/REDUCED HEIGHT/TEMPLATE - {base}-R{variant}.pdf"
            )
            candidates.append(f"IMPERIAL/REDUCED HEIGHT/TEMPLATE - {base}-R.pdf")
        candidates.append(f"IMPERIAL/TEMPLATE - {base}{variant}.pdf")
        candidates.append(f"IMPERIAL/TEMPLATE - {base}.pdf")

    for candidate in candidates:
        full = FILTER_TEMPLATE_BASE / candidate
        if full.exists():
            return full

    raise KeyError(
        f"No template found for model={filter_model!r} "
        f"(bushing={bushing}, reduced={reduced}). "
        f"Tried:\n  " + "\n  ".join(candidates)
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from quote_parser import parse_quote

    Path("/home/claude/prototype/output").mkdir(exist_ok=True)

    quote = parse_quote(
        "/mnt/user-data/uploads/Ontario_Aquatic_Center-AS_SOLD__1_.pdf"
    )

    # Test bushing detection per section
    for section in ["Lap Pool", "Training pool"]:
        bushing = detect_bushing_for_filter(quote.line_items, section)
        print(f"Section '{section}' → bushing variant: {bushing}")
    print()

    # Resolve templates for each filter line in the quote
    for li in quote.line_items:
        if li.reference and li.reference.startswith("SP-"):
            print(f"Filter: section='{li.section}' model='{li.reference}'")
            try:
                tmpl = resolve_filter_template(
                    li.reference,
                    quote.line_items,
                    reference=li.reference,
                    section=li.section,
                )
                print(f"  → {tmpl.relative_to(FILTER_TEMPLATE_BASE)}")
            except KeyError as e:
                print(f"  ERROR: {e}")
            print()
