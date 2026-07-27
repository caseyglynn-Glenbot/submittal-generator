"""
Orchestrator — main entry point for submittal generation.

Builds the submittal as a strict sequence:

  1. Cover page                                       (special-cased)
  2. Filter datasheets, one per filter line item      (sorted largest SP-XX first)
  3. Filter schematics, one per distinct type/family   (IMPERIAL + SP-29 max)
  4. Static spec pages + part-driven pages            (ordered per PAGE_ORDER)
  5. Pages produced this run but not in PAGE_ORDER    (appended with WARNING)

Cover and filter datasheets stay at the front. The PAGE_ORDER list in
mapping_table.py is the single source of truth for everything after the
schematics — to reposition any page, edit that list.
"""
import re
import gc
import shutil
import subprocess
import fitz
from pathlib import Path
from quote_parser import parse_quote, accessory_size, accessory_material
from annotation_engine import annotate_template, AnnotationSpec, YellowCallout, RedBox
from table_detector import detect_table_rows
from mapping_table import (
    PART_MAPPING, STATIC_PAGES, VALVE_KIT_PAGES, PAGE_ORDER,
    SCHEMATIC_BY_FAMILY, ACCESSORY_PAGES, GRATING_PARALLEL_STRAIGHT,
    parse_valve_kit_sizes, inch_to_dn, get_pool_label, filter_size_key,
)
import parts_catalog
from datasheet_filler import (
    fill_datasheet, resolve_filter_template, FILTER_FAMILIES, normalize_model,
)
from cover_page_filler import fill_cover_page


import os
TEMPLATE_DIR = Path(os.environ.get("TEMPLATE_DIR", "templates"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/tmp/submittal_output"))


_PA_GRATE_RE = re.compile(r"PA-(\d+)-WT$", re.I)


def parallel_straight_grating(line_items):
    """Resolve the parallel STRAIGHT grating page from a quote's lines.

    The grate (Reference# PA-<width>-WT), curb angle (CA-*), and fastening set
    (PA-FASTSET*) all belong on one page, sized by the grate's width band.
    Returns (template, (cx, cy), [callout_lines]) or None. Callout-only — no
    red box (these sheets carry no size table). The 90° corner (PA-*CN-*) is a
    separate page and is intentionally not matched here.
    """
    grate = curb = fasten = None
    for li in line_items:
        ref = (li.reference or "").upper()
        if _PA_GRATE_RE.match(ref):
            grate = li
        elif ref.startswith("CA-"):
            curb = li
        elif "FASTSET" in ref:
            fasten = li
    if grate is None:
        return None
    width = int(_PA_GRATE_RE.match(grate.reference.upper()).group(1))
    for (lo, hi), (tmpl, xy) in GRATING_PARALLEL_STRAIGHT.items():
        if lo <= width <= hi:
            lines = [f"({grate.quantity}') {grate.reference} REQ'D"]
            if curb:
                lines.append(f"({curb.quantity}') {curb.reference} REQ'D")
            if fasten:
                lines.append(f"({fasten.quantity}) STR FASTENING SET REQ'D")
            return tmpl, xy, lines
    print(f"  WARNING: parallel grate width {width}\" outside known bands; no page")
    return None


def normalize_for_search(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def find_row_by_label(rows, search_terms, prefer="first"):
    """Return the row whose SIZE column matches a search term.

    Matching is anchored to the START of the normalized label (the leftmost /
    size column) rather than a substring of the whole row. This prevents a
    bare size like "4" from matching dimension fractions ("2 1/4", "3 7/8")
    that appear elsewhere in an earlier row — the bug that collapsed every red
    box onto the size-2 row.

    Exception: a *distinctive* term (length >= 4 after normalization, e.g. a
    DN code like "dn200") may also match as a substring anywhere in the label.
    This handles tables whose leftmost column is the Part # rather than the
    size (the influent check valve sheet), where the row label normalizes to
    e.g. "10018102dn2008" and so never starts with the size key. Short bare
    sizes (len < 4) keep startswith-only matching, preserving the fraction
    protection above.

    prefer="first" returns the topmost match (default; correct for single-table
    pages and reducer sheets where the wanted table is first). prefer="last"
    returns the bottommost match — for pages carrying two size tables (e.g.
    effluent/precoat valves: a Part-Numbers table above a Dimensions table that
    share the same sizes), so the box lands on the lower Dimensions table.
    """
    if isinstance(search_terms, str):
        search_terms = [search_terms]
    normalized = [normalize_for_search(t) for t in search_terms]
    match = None
    for row in rows:
        label_norm = normalize_for_search(row["label"])
        for term in normalized:
            hit = bool(term) and (
                label_norm.startswith(term)
                or (len(term) >= 4 and term in label_norm)
            )
            if hit:
                if prefer == "first":
                    return row
                match = row  # keep scanning; return the last match
                break
    return match


def _safe_format(template: str, ctx: dict) -> str:
    """Format a callout template, tolerating missing/extra placeholders."""
    try:
        return template.format(**ctx)
    except (KeyError, IndexError):
        return template


def build_yellow_callouts(entry: dict, pool_ctx: dict, legacy_lines: list,
                          default_xy: tuple):
    """Build the list of YellowCallout objects for one page.

    entry        — the mapping dict (PART_MAPPING value or VALVE_KIT_PAGES value)
    pool_ctx     — { pool_label: context_dict } for .format() substitution.
                   Single-pool / aggregate pages use {"": ctx}.
    legacy_lines — the already-formatted lines for the legacy single-box path
                   (used only when entry has no "callouts" list).
    default_xy   — fallback (x, y) for the legacy box and for placements that
                   omit "xy".

    If entry has a "callouts" list: one YellowCallout per placement, each at
    its own xy. A placement with "only_pool" renders just that pool's context
    (skipped if that pool isn't present this run); without "only_pool", all
    present pools' lines are stacked into that one box. Otherwise: the legacy
    single stacked box at default_xy.
    """
    placements = entry.get("callouts")
    if not placements:
        if not legacy_lines:
            return []
        return [YellowCallout(x=default_xy[0], y=default_xy[1], lines=legacy_lines)]

    box_kwargs = ("width", "font_size", "line_height", "padding")
    callouts = []
    for p in placements:
        xy = p.get("xy", default_xy)
        tmpl = p["template"]
        kw = {k: p[k] for k in box_kwargs if k in p}
        only = p.get("only_pool")

        if only is not None:
            ctxs = [pool_ctx[only]] if only in pool_ctx else []
            for ctx in ctxs:
                lines = _safe_format(tmpl, ctx).split("\n")
                callouts.append(YellowCallout(x=xy[0], y=xy[1], lines=lines, **kw))
        else:
            lines = []
            for ctx in (pool_ctx.values() or [{}]):
                for ln in _safe_format(tmpl, ctx).split("\n"):
                    if ln not in lines:
                        lines.append(ln)
            if lines:
                callouts.append(YellowCallout(x=xy[0], y=xy[1], lines=lines, **kw))
    return callouts


def red_box_by_text(template_path, search_text, x_left, x_right, pad=2.0):
    """Locate a catalog-table row by searching the PDF text layer for
    `search_text` (typically the unique part number) and return a RedBox
    spanning x_left..x_right at that row's vertical position, or None if not
    found.

    Deterministic and reliable on the dense reducer/tee/strainer tables, where
    OCR-based row auto-detection mis-fires. Part numbers are unique per row, so
    this lands on exactly the right size (e.g. 1000-6213 = the concentric 6x4
    row), unlike a bare size like "6 x 4" which can appear in two tables.
    """
    try:
        doc = fitz.open(str(template_path))
        hits = doc[0].search_for(search_text)
        doc.close()
    except Exception:
        return None
    if not hits:
        return None
    r = hits[0]
    return RedBox(x=x_left, y=r.y0 - pad,
                  width=max(x_right - x_left, 1.0),
                  height=(r.y1 - r.y0) + 2 * pad)


def build_fixed_red_boxes(entry: dict):
    """Convert an entry's red_boxes_fixed dicts into explicit RedBox objects."""
    out = []
    for b in entry.get("red_boxes_fixed", []):
        out.append(RedBox(
            x=b["x"], y=b["y"], width=b["width"], height=b["height"],
            line_width=b.get("line_width", 1.2),
        ))
    return out


def build_size_pinned_boxes(entry: dict, ctx: dict):
    """Resolve an entry's size-keyed pinned_rows into explicit RedBox objects.

    For raster templates that have NO usable table text layer (the scan is a
    flat image, so both OCR row detection and search_for() are unreliable), the
    row rectangles are measured once per template and pinned per size in
    `pinned_rows`: {str(size): [{x, y, width, height[, line_width]}, ...]}.

    `size_keys` names which ctx size value(s) drive the lookup for this page
    (e.g. ["effluent_size", "precoat_size"]). Each resolved size contributes
    the box(es) listed for it (one for a single-table page, two for the
    Part-Numbers + Dimensions stack). Unknown sizes are skipped silently — the
    caller already logs callouts, and a missing size means that table simply
    has no row to box.

    Returns a list of RedBox for the given single section's ctx; the valve-kit
    loop accumulates and de-dups across sections.
    """
    rows_by_size = entry.get("pinned_rows")
    if not rows_by_size:
        return []
    out = []
    for key in entry.get("size_keys", []):
        size = ctx.get(key)
        if size is None:
            continue
        for b in rows_by_size.get(str(size), []):
            out.append(RedBox(
                x=b["x"], y=b["y"], width=b["width"], height=b["height"],
                line_width=b.get("line_width", 1.2),
            ))
    return out


_PE_RE = re.compile(r"PE[S]?-(\d{2})", re.I)


def resolve_accessory_page(li):
    """Resolve a quote line item to an ACCESSORY_PAGES template, or None.

    Resolution order:
      1. Exact SAP part number via parts_catalog (reducers, tees, SS strainers).
      2. Reference# rule for radial / perpendicular grating (alpha SKUs).
      3. Description rule for the FG Guardian strainer (no SS sheet enumerates it).
    """
    page = parts_catalog.page_for(li.part_number)
    if page:
        return page

    ref = (li.reference or "").upper()
    if ref.startswith("PA-R"):
        return "grating_radial.pdf"
    m = _PE_RE.search(ref)
    if m:
        w = int(m.group(1))
        if w <= 6:
            return "grating_perpendicular_0406.pdf"
        if w <= 12:
            return "grating_perpendicular_0812.pdf"
        return "grating_perpendicular_1420.pdf"

    d = (li.description or "").upper()
    if "STRAINER" in d and "GUARDIAN" in d and accessory_material(d) == "FG":
        return "guardian_strainer.pdf"

    return None


def annotate_page(
    template_path: Path,
    callouts: list,
    row_search_terms: list,
    output_path: Path,
    fixed_red_boxes: list = None,
    row_match: str = "first",
):
    """Wrap annotation_engine: draw explicit callouts + fixed/auto-detected boxes.

    callouts          — list of YellowCallout objects, already placed.
    row_search_terms  — OCR auto-detection terms (the inferred red-box path).
    fixed_red_boxes   — explicit RedBox objects drawn verbatim. These are drawn
                        FIRST, then any auto-detected boxes are appended, so a
                        page can mix pinned boxes with auto-detected ones.

    Red boxes from the detector follow the actual table column bounds reported
    by the detector. Before drawing, annotate_template() strips any pre-existing
    baked-in red Square / yellow FreeText annotations inherited from the source
    job-specific submittal that split produced these templates from.
    """
    BOX_INSET = 2.0
    FALLBACK_X = 40.0
    FALLBACK_WIDTH = 540.0

    red_boxes = list(fixed_red_boxes or [])

    detected = detect_table_rows(str(template_path)) if row_search_terms else []
    for term in row_search_terms:
        row = find_row_by_label(detected, term, prefer=row_match)
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

    spec = AnnotationSpec(
        template_path=str(template_path),
        yellow_callouts=list(callouts),
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


def _is_filter_line(li) -> bool:
    """A line item is a filter if it's a 'FILTER DEFENDER' line carrying a
    recognizable SP-XX model. This is layout- and catalog-agnostic: it does
    not require the part number to be pre-registered in PART_MAPPING, so new
    filter part numbers (e.g. SP-41/SP-55) are picked up automatically as
    long as the parser populates the reference from the description.
    """
    return (
        "FILTER DEFENDER" in li.description.upper()
        and filter_size_key(li.reference) > 0
    )


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
        if _is_filter_line(li):
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


def _compress_pdf(src_path: str, out_path: str, dpi: int = 150):
    """Downsample images via Ghostscript to shrink the merged submittal so it
    doesn't blow n8n's per-execution memory when returned as a binary.

    Vector line work (the CAD drawings, the drawn annotations) is unaffected;
    only embedded raster images (product photos, 3D renders) are downsampled
    to `dpi`. Falls back to the uncompressed file if gs is missing or fails,
    and keeps whichever file is smaller.
    """
    try:
        subprocess.run([
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
            "-dPDFSETTINGS=/ebook", "-dDetectDuplicateImages=true",
            f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={out_path}", src_path,
        ], check=True)
        if os.path.getsize(out_path) > os.path.getsize(src_path):
            shutil.copyfile(src_path, out_path)  # gs made it bigger; keep original
        print(f"  compressed: {os.path.getsize(src_path)//1024}KB -> "
              f"{os.path.getsize(out_path)//1024}KB")
    except Exception as e:
        print(f"  compress: gs unavailable/failed ({e}); using uncompressed output")
        shutil.copyfile(src_path, out_path)
    finally:
        try:
            os.remove(src_path)
        except OSError:
            pass


def generate_submittal(
    quote_pdf: str,
    output_pdf: str,
    job_number: str = "",
    engineer_initials: str = "",
    submittal_return_date: str = "",
    project_name: str = "",
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    quote = parse_quote(quote_pdf)
    print(f"Parsed quote: {quote.project_name} ({len(quote.line_items)} items)")

    # Optional override from the n8n form. Falls back to the quote-parsed name.
    effective_project_name = (project_name or "").strip() or quote.project_name
    if (project_name or "").strip():
        print(f"  Project name override: {effective_project_name}")

    pages_to_merge = []

    # ─────────────────────────────────────────────────────────────────────
    # Step 1 — Cover page (page 1)
    # ─────────────────────────────────────────────────────────────────────
    cover_template = TEMPLATE_DIR / "cover_page.pdf"
    if cover_template.exists():
        cover_out = OUTPUT_DIR / "cover_page_filled.pdf"
        effective_job = job_number or quote.quote_number
        cover_project_name = _project_name_for_cover(effective_project_name)
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
            project_name=f"{_project_name_for_cover(effective_project_name)} Renovation",
            pool_name=pool_name,
            client_name=quote.customer,
            job_number=job_number or quote.quote_number,
            engineer_initials=engineer_initials,
            drawn_date=quote.quote_date,
        )
        pages_to_merge.append(out_path)
        print(f"  {li.reference} → {out_path.name}")

    # ─────────────────────────────────────────────────────────────────────
    # Step 3 — Filter schematics (one per schematic TYPE, deduped)
    # ─────────────────────────────────────────────────────────────────────
    # Schematics are generic per filter FAMILY, not per filter: every IMPERIAL
    # filter shares defender_filter_schematic_lap.pdf and every ASSERO (SP-29)
    # filter shares defender_sp29_schematic.pdf. So a job with several filters
    # of the same family only needs that schematic once. Walk filters in
    # datasheet order and include each distinct schematic file the first time
    # its family appears; later filters of an already-included type are skipped.
    # Result: at most one IMPERIAL schematic and one SP-29 schematic.
    print(f"\n--- Filter schematics (one per type) ---")
    included_schematics = set()
    for li in sorted_filters:
        family = get_filter_family_for_reference(li.reference)
        schematic_name = SCHEMATIC_BY_FAMILY.get(family)
        if not schematic_name:
            print(f"  WARNING: no schematic configured for family {family!r} ({li.reference})")
            continue
        if schematic_name in included_schematics:
            print(f"  {li.reference} ({family}) → {schematic_name} [duplicate type, skipped]")
            continue
        schematic_path = TEMPLATE_DIR / schematic_name
        if not schematic_path.exists():
            print(f"  MISSING: {schematic_name} (for filter {li.reference}, family {family})")
            continue
        included_schematics.add(schematic_name)
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
    PERLITE_PART = "1000-5852"
    PERLITE_KEYWORDS = ("PERLITE", "AQUAPERL", "HARBORLITE")
    for static_name in STATIC_PAGES:
        path = TEMPLATE_DIR / static_name
        if not path.exists():
            print(f"  MISSING (skipped): {static_name}")
            continue

        if static_name == "perlite.pdf":
            # Bag count from the quote: the perlite part number, or any line
            # whose description names the media. Falls back to the template's
            # baked "(-) 25# BAGS REQ'D" placeholder when the quote has none.
            bags = sum(
                li.quantity for li in quote.line_items
                if li.part_number == PERLITE_PART
                or any(k in li.description.upper() for k in PERLITE_KEYWORDS)
            )
            if bags:
                out_path = OUTPUT_DIR / "acc_perlite.pdf"
                callouts = [YellowCallout(x=227, y=666,
                                          lines=[f"({bags}) 25# BAGS REQ'D"])]
                annotate_page(path, callouts, [], out_path)
                produced_pages[static_name] = out_path
                print(f"  perlite.pdf ← ({bags}) 25# BAGS REQ'D")
                continue

        produced_pages[static_name] = path
        print(f"  {static_name}")

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
        pool_ctx = {}
        pinned_boxes = []
        _seen_boxes = set()

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
            pool_ctx[get_pool_label(section)] = ctx

            if "callout_template" in recipe:
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

            # Size-keyed pinned red boxes (raster pages w/o a table text layer).
            # Accumulate across sections, de-duping identical rects so two pools
            # calling the same size don't stack overlapping boxes.
            for rb in build_size_pinned_boxes(recipe, ctx):
                bkey = (round(rb.x, 1), round(rb.y, 1),
                        round(rb.width, 1), round(rb.height, 1))
                if bkey not in _seen_boxes:
                    _seen_boxes.add(bkey)
                    pinned_boxes.append(rb)

        if not pool_ctx:
            continue
        out_path = OUTPUT_DIR / f"vk_{template_name}"
        callouts = build_yellow_callouts(
            recipe, pool_ctx, callout_lines, recipe.get("callout_xy", (40, 40)),
        )
        annotate_page(template_path, callouts, row_terms, out_path,
                      fixed_red_boxes=build_fixed_red_boxes(recipe) + pinned_boxes,
                      row_match=recipe.get("row_match", "first"))
        produced_pages[template_name] = out_path
        print(f"  {template_name} ← {len(callouts)} callout box(es), "
              f"{len(row_terms) + len(pinned_boxes)} red boxes")

    # ----- 4c: Part-number-driven annotated pages -----
    print("\n--- Part-driven pages ---")
    page_jobs = {}
    for li in quote.line_items:
        if _is_filter_line(li):
            continue  # filters are handled by the datasheet/schematic steps
        if resolve_accessory_page(li):
            continue  # handled by the accessory loop (step 4d)
        m = PART_MAPPING.get(li.part_number)
        if not m or m.get("skip") or m.get("page2_3_template"):
            continue

        template = m["template"]
        job = page_jobs.setdefault(template, {
            "entry": m,
            "callout_lines": [], "row_terms": [],
            "callout_xy": m.get("callout_xy", (40, 40)),
            "is_aggregate": m.get("aggregate_qty", False),
            "aggregate_total": 0,
            "callout_pattern": m.get("callout_template"),
            "rows_pattern": m.get("red_box_rows", []),
            "pool_ctx": {},
        })

        if job["is_aggregate"]:
            job["aggregate_total"] += li.quantity
            continue

        ctx = {"qty": li.quantity, "pool_label": get_pool_label(li.section),
               "pail_label": m.get("pail_label", "")}
        job["pool_ctx"][get_pool_label(li.section)] = ctx
        if job["callout_pattern"]:
            callout = job["callout_pattern"].format(**ctx)
            if callout not in job["callout_lines"]:
                job["callout_lines"].append(callout)
        for term in m.get("red_box_rows", []):
            if term not in job["row_terms"]:
                job["row_terms"].append(term)

    # Finalize aggregate-qty callouts
    for template, job in page_jobs.items():
        if job["is_aggregate"] and job["aggregate_total"] > 0:
            agg_ctx = {"qty": job["aggregate_total"], "pool_label": ""}
            job["pool_ctx"][""] = agg_ctx
            if job["callout_pattern"]:
                job["callout_lines"].append(job["callout_pattern"].format(**agg_ctx))

    for template, job in page_jobs.items():
        template_path = TEMPLATE_DIR / template
        if not template_path.exists():
            print(f"  MISSING (skipped): {template}")
            continue
        out_path = OUTPUT_DIR / f"part_{template}"
        callouts = build_yellow_callouts(
            job["entry"], job["pool_ctx"], job["callout_lines"], job["callout_xy"],
        )
        annotate_page(template_path, callouts, job["row_terms"], out_path,
                      fixed_red_boxes=build_fixed_red_boxes(job["entry"]))
        produced_pages[template] = out_path
        print(f"  {template} ← {len(callouts)} callout box(es)")

    # ----- 4d: Accessory pages (reducers / precoat tees / strainers / radial
    #           & perpendicular grating). Routing via parts_catalog or a
    #           Reference# rule; the callout SIZE is parsed from the line
    #           description. Multiple sizes of the same product on one quote
    #           stack as separate callout lines in the page's one callout box.
    # -----
    print("\n--- Accessory pages ---")
    acc_jobs = {}
    for li in quote.line_items:
        page = resolve_accessory_page(li)
        if not page:
            continue
        recipe = ACCESSORY_PAGES.get(page)
        if recipe is None:
            print(f"  WARNING: {li.part_number} routed to {page} but no ACCESSORY_PAGES recipe")
            continue
        size = accessory_size(li.description)
        if not size:
            print(f"  WARNING: no size parsed for {li.part_number} {li.description!r}; skipping callout")
            continue
        job = acc_jobs.setdefault(page, {"recipe": recipe, "callout_lines": [],
                                         "row_terms": [], "part_numbers": []})
        line = recipe["callout_template"].format(qty=li.quantity, size=size)
        if line not in job["callout_lines"]:
            job["callout_lines"].append(line)
        if li.part_number not in job["part_numbers"]:
            job["part_numbers"].append(li.part_number)
        # Red-box strategy per recipe: "table_x" => locate by part# text search
        # (reliable); else "row_from_size" => OCR auto-detect by the parsed size.
        if not recipe.get("table_x") and recipe.get("row_from_size") and size not in job["row_terms"]:
            job["row_terms"].append(size)

    for page, job in acc_jobs.items():
        template_path = TEMPLATE_DIR / page
        if not template_path.exists():
            print(f"  MISSING (skipped): {page}")
            continue
        recipe = job["recipe"]
        cx, cy = recipe["callout_xy"]
        callouts = [YellowCallout(x=cx, y=cy, lines=job["callout_lines"])]
        out_path = OUTPUT_DIR / f"acc_{page}"
        # Part-number-located red boxes (text search) + any recipe fixed boxes.
        fixed = build_fixed_red_boxes(recipe)
        tx = recipe.get("table_x")
        if tx:
            for pn in job["part_numbers"]:
                rb = red_box_by_text(template_path, pn, tx[0], tx[1])
                if rb:
                    fixed.append(rb)
                else:
                    print(f"  WARNING: part# {pn} not found on {page}; no red box drawn")
        annotate_page(template_path, callouts, job["row_terms"], out_path,
                      fixed_red_boxes=fixed)
        produced_pages[page] = out_path
        flag = " [VERIFY wording]" if recipe.get("verify") else ""
        print(f"  {page} ← {len(job['callout_lines'])} callout line(s), {len(fixed)} red box(es){flag}")

    # ----- 4e: Parallel straight grating (grouped, band-sized; callout-only).
    pg = parallel_straight_grating(quote.line_items)
    if pg:
        tmpl, (cx, cy), lines = pg
        template_path = TEMPLATE_DIR / tmpl
        if not template_path.exists():
            print(f"  MISSING (skipped): {tmpl}")
        else:
            callouts = [YellowCallout(x=cx, y=cy, lines=lines)]
            out_path = OUTPUT_DIR / f"acc_{tmpl}"
            annotate_page(template_path, callouts, [], out_path)
            produced_pages[tmpl] = out_path
            print(f"  {tmpl} ← parallel grating ({len(lines)} callout line(s)) [VERIFY wording]")

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
    raw_path = str(output_pdf) + ".raw.pdf"
    # garbage=1 (single pass) + no clean=True: a full garbage=4/clean rewrite
    # holds the whole document in memory a second time, which—stacked on the
    # gs /ebook re-render below—OOM-killed the 512MB instance. deflate stays on.
    final.save(raw_path, garbage=1, deflate=True)
    final.close()
    del final
    gc.collect()  # release the merged doc's RSS before gs spawns its own pass
    # Slim the merged output (image downsampling) so the binary stays small
    # enough to pass back through n8n without exhausting its memory.
    _compress_pdf(raw_path, str(output_pdf))
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
