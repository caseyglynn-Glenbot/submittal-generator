"""
Orchestrator — main entry point for submittal generation.

Builds the submittal as a strict sequence:

  1. Cover page                                       (special-cased)
  2. Filter datasheets, one per filter line item      (sorted largest SP-XX first)
  3. Per-filter schematics, one per filter line item  (same order as datasheets)
  4. Static spec pages + part-driven pages            (ordered per PAGE_ORDER)
  5. Pages produced this run but not in PAGE_ORDER    (appended with WARNING)

Cover and filter datasheets stay at the front. The PAGE_ORDER list in
mapping_table.py is the single source of truth for everything after the
schematics — to reposition any page, edit that list.
"""
import re
import fitz
from pathlib import Path
from quote_parser import parse_quote
from annotation_engine import annotate_template, AnnotationSpec, YellowCallout, RedBox
from table_detector import detect_table_rows
from mapping_table import (
    PART_MAPPING, STATIC_PAGES, VALVE_KIT_PAGES, PAGE_ORDER,
    SCHEMATIC_BY_FAMILY,
    parse_valve_kit_sizes, inch_to_dn, get_pool_label, filter_size_key,
)
from datasheet_filler import (
    fill_datasheet, resolve_filter_template, FILTER_FAMILIES, normalize_model,
)
from cover_page_filler import fill_cover_page


import os
TEMPLATE_DIR = Path(os.environ.get("TEMPLATE_DIR", "templates"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/tmp/submittal_output"))


def normalize_for_search(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def find_row_by_label(rows, search_terms):
    """Return the first row whose OCR label contains any search term."""
    if isinstance(search_terms, str):
        search_terms = [search_terms]
    normalized = [normalize_for_search(t) for t in search_terms]
    for row in rows:
        label_norm = normalize_for_search(row["label"])
        for term in normalized:
            if term and term in label_norm:
                return row
    return None


def annotate_page(
    template_path: Path,
    callout_lines: list,
    callout_xy: tuple,
    row_search_terms: list,
    output_path: Path,
):
    """Wrap annotation_engine: auto-detect rows, draw callout + boxes.

    Red boxes follow the actual table column bounds reported by the detector.
    Before drawing, annotate_template() strips any pre-existing baked-in
    red Square / yellow FreeText annotations (per-page Acrobat annotations
    inherited from the source job-specific submittal that split produced
    these templates from).
    """
    BOX_INSET = 2.0
    FALLBACK_X = 40.0
    FALLBACK_WIDTH = 540.0

    detected = detect_table_rows(str(template_path)) if row_search_terms else []
    red_boxes = []
    for term in row_search_terms:
        row = find_row_by_label(detected, term)
        if row is None:
            print(f"    WARNING: no row matched '{term}' in {template_path.name}")
            continue

        x_left = row.get("x_left_pt")
        width = row.get("width_pt")
        if x_left is None or width is None or width <= 0:
            x_left = FALLBACK_X
            width = FALLBACK_WIDTH
            print(f"    WARNING: no x-bounds for row '{term}' in {template_path.name}; using fallback")
        else:
            x_left = x_left + BOX_INSET
            width = max(width - 2 * BOX_INSET, 1.0)

        red_boxes.append(RedBox(
            x=x_left, y=row["y_top_pt"],
            width=width, height=row["height_pt"],
        ))
    callouts = [YellowCallout(x=callout_xy[0], y=callout_xy[1], lines=callout_lines)]
    spec = AnnotationSpec(
        template_path=str(template_path),
        yellow_callouts=callouts,
        red_boxes=red_boxes,
        strip_baked=True,
    )
    annotate_template(spec, str(output_path))
    return output_path


def get_filter_family_for_section(quote, section: str):
    """Look up which filter family is in this section (IMPERIAL or ASSERO)."""
    for li in quote.line_items:
        if li.section != section:
            continue
        if "FILTER DEFENDER" in li.description.upper() and li.reference:
            base = normalize_model(li.reference)
            if base in FILTER_FAMILIES["ASSERO"]:
                return "ASSERO"
            return "IMPERIAL"
    return None


def get_filter_family_for_reference(reference: str) -> str:
    """Determine the filter family (IMPERIAL or ASSERO) from a model reference."""
    if not reference:
        return "IMPERIAL"
    base = normalize_model(reference)
    if base in FILTER_FAMILIES["ASSERO"]:
        return "ASSERO"
    return "IMPERIAL"


def _project_name_for_cover(raw: str) -> str:
    """Strip a trailing ' Renovation' (case-insensitive) for cover page display."""
    if not raw:
        return ""
    cleaned = re.sub(r"\s+renovation\s*$", "", raw, flags=re.IGNORECASE).strip()
    return cleaned or raw


def _filter_line_items_sorted(quote):
    """Return the filter line items from the quote, sorted largest SP-XX first.

    Tie-breaker for filters with the same model size: original quote order.
    This is critical for both the datasheet step and the schematic step,
    which must agree on filter sequence.

    Implementation: enumerate to capture original index, sort by
    (-size, original_index). Python's sort is stable, so the original index
    handles the tie-break automatically — but using it explicitly makes the
    intent obvious.
    """
    indexed = []
    for idx, li in enumerate(quote.line_items):
        m = PART_MAPPING.get(li.part_number)
        if m and m.get("page2_3_template"):
            indexed.append((idx, li))
    indexed.sort(key=lambda pair: (-filter_size_key(pair[1].reference), pair[0]))
    return [li for _, li in indexed]


def _emit_in_page_order(produced_pages: dict, pages_to_merge: list):
    """Emit pages from `produced_pages` in PAGE_ORDER, append leftovers.

    produced_pages maps template_name → produced PDF path. After this
    function: pages_to_merge has the produced pages appended in PAGE_ORDER
    sequence, with any template_name not in PAGE_ORDER appended at the
    very end (with a WARNING log).
    """
    emitted = set()
    for template_name in PAGE_ORDER:
        if template_name in produced_pages:
            pages_to_merge.append(produced_pages[template_name])
            emitted.add(template_name)
        # else: page wasn't produced this run (either not on quote, or
        # template missing on disk) — silently skip per the rule

    # Pages we produced but didn't find a slot for — append at end with warning
    leftovers = [name for name in produced_pages if name not in emitted]
    for template_name in leftovers:
        print(f"  WARNING: '{template_name}' produced but not in PAGE_ORDER; appending at end")
        pages_to_merge.append(produced_pages[template_name])


def generate_submittal(
    quote_pdf: str,
    output_pdf: str,
    job_number: str = "",
    engineer_initials: str = "",
    submittal_return_date: str = "",
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    quote = parse_quote(quote_pdf)
    print(f"Parsed quote: {quote.project_name} ({len(quote.line_items)} items)")

    pages_to_merge = []

    # ─────────────────────────────────────────────────────────────────────
    # Step 1 — Cover page (page 1)
    # ─────────────────────────────────────────────────────────────────────
    cover_template = TEMPLATE_DIR / "cover_page.pdf"
    if cover_template.exists():
        cover_out = OUTPUT_DIR / "cover_page_filled.pdf"
        effective_job = job_number or quote.quote_number
        cover_project_name = _project_name_for_cover(quote.project_name)
        _, cover_report = fill_cover_page(
            template_path=cover_template,
            project_name=cover_project_name,
            job_number=effective_job,
            submittal_return_date=submittal_return_date,
            output_path=cover_out,
        )
        pages_to_merge.append(cover_out)
        print(f"  Cover: {cover_out.name} — {cover_report.summary()}")
    else:
        print(f"  MISSING: {cover_template}")

    # ─────────────────────────────────────────────────────────────────────
    # Step 2 — Filter datasheets (largest SP-XX first, then by quote order)
    # ─────────────────────────────────────────────────────────────────────
    # Sort filter line items so the same ordering can be reused for
    # schematics in step 3. This guarantees datasheet[i] and schematic[i]
    # describe the same filter.
    sorted_filters = _filter_line_items_sorted(quote)
    print(f"\n--- Filter datasheets ({len(sorted_filters)} filter(s), largest first) ---")
    for li in sorted_filters:
        pool_name = "Training Pool" if li.section.lower() == "training pool" else li.section
        try:
            template_path = resolve_filter_template(
                li.reference,
                quote.line_items,
                reference=li.reference,
                section=li.section,
            )
        except KeyError as e:
            print(f"  SKIP: {e}")
            continue
        out_path = OUTPUT_DIR / f"datasheet_{li.section.replace(' ', '_')}.pdf"
        fill_datasheet(
            str(template_path), str(out_path),
            project_name=f"{quote.project_name} Renovation",
            pool_name=pool_name,
            client_name=quote.customer,
            job_number=job_number or quote.quote_number,
            engineer_initials=engineer_initials,
            drawn_date=quote.quote_date,
        )
        pages_to_merge.append(out_path)
        print(f"  {li.reference} → {out_path.name}")

    # ─────────────────────────────────────────────────────────────────────
    # Step 3 — Per-filter schematics (same order as datasheets)
    # ─────────────────────────────────────────────────────────────────────
    # One schematic per filter, even when two filters share the same model.
    # File chosen by family lookup; orchestrator does NOT special-case the
    # filename, so adding per-model schematics later is a mapping_table edit.
    print(f"\n--- Per-filter schematics ({len(sorted_filters)} filter(s)) ---")
    for li in sorted_filters:
        family = get_filter_family_for_reference(li.reference)
        schematic_name = SCHEMATIC_BY_FAMILY.get(family)
        if not schematic_name:
            print(f"  WARNING: no schematic configured for family {family!r} ({li.reference})")
            continue
        schematic_path = TEMPLATE_DIR / schematic_name
        if not schematic_path.exists():
            print(f"  MISSING: {schematic_name} (for filter {li.reference}, family {family})")
            continue
        pages_to_merge.append(schematic_path)
        print(f"  {li.reference} ({family}) → {schematic_name}")

    # ─────────────────────────────────────────────────────────────────────
    # Steps 4a/4b/4c — Build the produced-pages dict before emitting in order
    # ─────────────────────────────────────────────────────────────────────
    # Everything below produces files keyed by their template filename. We
    # don't append to pages_to_merge here — instead we collect into
    # produced_pages and use _emit_in_page_order() at the end to apply the
    # strict PAGE_ORDER sequence.
    produced_pages: dict = {}

    # ----- 4a: Static spec pages -----
    print("\n--- Static spec pages ---")
    for static_name in STATIC_PAGES:
        path = TEMPLATE_DIR / static_name
        if path.exists():
            produced_pages[static_name] = path
            print(f"  {static_name}")
        else:
            print(f"  MISSING (skipped): {static_name}")

    # ----- 4b: Valve-kit-derived annotated pages -----
    print("\n--- Valve-kit-derived pages ---")
    kits_by_section = {}
    for li in quote.line_items:
        if "DEFENDER VALVE KIT" in li.description.upper():
            sizes = parse_valve_kit_sizes(li.description)
            if sizes:
                kits_by_section[li.section] = sizes

    for template_name, recipe in VALVE_KIT_PAGES.items():
        template_path = TEMPLATE_DIR / template_name
        if not template_path.exists():
            print(f"  MISSING (skipped): {template_name}")
            continue

        callout_lines = []
        row_terms = []

        for section, sizes in kits_by_section.items():
            family = get_filter_family_for_section(quote, section)
            only_for = recipe.get("only_for_filter_family")
            if only_for and family != only_for:
                continue

            ctx = {
                "pool_label": get_pool_label(section),
                "influent_size": sizes["influent"],
                "effluent_size": sizes["effluent"],
                "precoat_size": sizes["precoat"],
                "sightglass_size": sizes["sightglass"],
                "influent_dn": inch_to_dn(sizes["influent"]),
            }
            callout = recipe["callout_template"].format(**ctx)
            for line in callout.split("\n"):
                if line not in callout_lines:
                    callout_lines.append(line)

            if "row_search" in recipe:
                term = recipe["row_search"].format(**ctx)
                if term not in row_terms:
                    row_terms.append(term)
            if "row_search_multi" in recipe:
                for fmt in recipe["row_search_multi"]:
                    term = fmt.format(**ctx)
                    if term not in row_terms:
                        row_terms.append(term)

        if not callout_lines:
            continue
        out_path = OUTPUT_DIR / f"vk_{template_name}"
        annotate_page(template_path, callout_lines, recipe["callout_xy"],
                      row_terms, out_path)
        produced_pages[template_name] = out_path
        print(f"  {template_name} ← {len(callout_lines)} callouts, {len(row_terms)} red boxes")

    # ----- 4c: Part-number-driven annotated pages -----
    print("\n--- Part-driven accessory pages ---")
    page_jobs = {}
    for li in quote.line_items:
        m = PART_MAPPING.get(li.part_number)
        if not m or m.get("skip") or m.get("page2_3_template"):
            continue

        template = m["template"]
        job = page_jobs.setdefault(template, {
            "callout_lines": [], "row_terms": [],
            "callout_xy": m["callout_xy"],
            "is_aggregate": m.get("aggregate_qty", False),
            "aggregate_total": 0,
            "callout_pattern": m["callout_template"],
            "rows_pattern": m.get("red_box_rows", []),
        })

        if job["is_aggregate"]:
            job["aggregate_total"] += li.quantity
            continue

        ctx = {"qty": li.quantity, "pool_label": get_pool_label(li.section)}
        callout = m["callout_template"].format(**ctx)
        if callout not in job["callout_lines"]:
            job["callout_lines"].append(callout)
        for term in m.get("red_box_rows", []):
            if term not in job["row_terms"]:
                job["row_terms"].append(term)

    # Finalize aggregate-qty callouts
    for template, job in page_jobs.items():
        if job["is_aggregate"] and job["aggregate_total"] > 0:
            callout = job["callout_pattern"].format(
                qty=job["aggregate_total"], pool_label="",
            )
            job["callout_lines"].append(callout)

    for template, job in page_jobs.items():
        template_path = TEMPLATE_DIR / template
        if not template_path.exists():
            print(f"  MISSING (skipped): {template}")
            continue
        out_path = OUTPUT_DIR / f"part_{template}"
        annotate_page(template_path, job["callout_lines"], job["callout_xy"],
                      job["row_terms"], out_path)
        produced_pages[template] = out_path
        print(f"  {template} ← {len(job['callout_lines'])} callouts")

    # ─────────────────────────────────────────────────────────────────────
    # Step 5 — Emit produced pages in PAGE_ORDER, append leftovers at end
    # ─────────────────────────────────────────────────────────────────────
    print(f"\n--- Applying PAGE_ORDER ({len(PAGE_ORDER)} slots, {len(produced_pages)} pages produced) ---")
    _emit_in_page_order(produced_pages, pages_to_merge)

    # ─────────────────────────────────────────────────────────────────────
    # Step 6 — Merge into final submittal
    # ─────────────────────────────────────────────────────────────────────
    print(f"\nMerging {len(pages_to_merge)} pages into final submittal")
    final = fitz.open()
    for path in pages_to_merge:
        src = fitz.open(str(path))
        final.insert_pdf(src)
        src.close()
    final.save(output_pdf, garbage=4, deflate=True, clean=True)
    final.close()
    print(f"Written: {output_pdf}")
    return output_pdf


if __name__ == "__main__":
    generate_submittal(
        "/mnt/user-data/uploads/Ontario_Aquatic_Center-AS_SOLD__1_.pdf",
        "/home/claude/prototype/output/FINAL_SUBMITTAL.pdf",
        job_number="76284",
        engineer_initials="LRH",
        submittal_return_date="12/15/26",
    )
