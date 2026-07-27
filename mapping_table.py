"""
Master mapping table for the Neptune Benson submittal generator.

Page categories:

1. STATIC PAGES — always included regardless of quote contents
2. PART-NUMBER-DRIVEN PAGES — included only if matching part is on quote
3. VALVE-KIT-DERIVED PAGES — multiple pages whose annotations come from
   the Defender valve kit spec (e.g. "8/6/3/3SG")
4. PER-FILTER SCHEMATICS — one schematic page per filter on the quote,
   keyed by filter family (Imperial vs Assero)

Ordering:
    PAGE_ORDER is the canonical sequence the orchestrator emits non-special
    pages in. Cover page and filter datasheets are special-cased BEFORE this
    list (always pages 1 and 2..N). Per-filter schematics are emitted AFTER
    the datasheets and BEFORE the PAGE_ORDER sequence.

    Pages produced this run but NOT in PAGE_ORDER are appended at the end
    with a WARNING log so they don't get lost silently.
"""

SECTION_TO_POOL_LABEL = {
    "Lap Pool": "LAP",
    "Training pool": "ACTIVITY",
    "Items": "",
}


def get_pool_label(section: str) -> str:
    return SECTION_TO_POOL_LABEL.get(section, section.upper())


# ---------------------------------------------------------------------------
# 1. STATIC PAGES — always included
# ---------------------------------------------------------------------------
# Position of each page is determined by PAGE_ORDER below, NOT by the order
# in this list. This list answers "is it always included?" — not "where?".
STATIC_PAGES = [
    "flexsol_3000_lining.pdf",
    "dominion_butterfly_valves.pdf",
    "pneumatic_actuator_specs.pdf",
    "pneumatic_actuator_dimensions.pdf",
    "rmf_programmer.pdf",
    "filter_regulator.pdf",
    "gauge_panel.pdf",
    "vacuum_transfer_system.pdf",
    "vacuum_transfer_unit.pdf",
    "water_separator.pdf",
    # ----- Now always-included (was part-driven on 1000-5852) -----
    "perlite.pdf",
    # ----- Placeholders for templates not yet provided -----
    # When the templates arrive, drop them into templates/ and uncomment
    # the corresponding entries below + PAGE_ORDER entries. Until then,
    # they're silently skipped by the "page missing on disk → skip" rule
    # in the orchestrator.
    "filter_model_information.pdf",   # page 5  — pending template
    "par_light.pdf",                   # page 23 — pending template
    # eccentric_reducer.pdf removed from static placeholders (Jul 2026):
    # its template now exists, which made this "pending" entry include the
    # reducer sheet on every submittal. Reducers are quote-driven only,
    # via ACCESSORY_PAGES + parts_catalog part-number routing.
]


# ---------------------------------------------------------------------------
# 2. PART-NUMBER-DRIVEN PAGES
# ---------------------------------------------------------------------------
# Entry fields:
#   template          — filename in the template library
#   callout_template  — string with {qty} and {pool_label} placeholders
#   callout_xy        — (x, y) of the yellow callout
#   red_box_rows      — list of search terms for row auto-detection
#   aggregate_qty     — sum qty across all sections (e.g. compressor)
#   skip              — part doesn't produce its own page
#   page2_3_template  — filter datasheet (handled separately)
#
# OPTIONAL DECLARATIVE-PLACEMENT FIELDS (override the inferred path per page):
#
#   callouts          — list of explicit yellow-callout PLACEMENTS. When
#                       present, this REPLACES the single stacked
#                       callout_template/callout_xy box: one box is drawn per
#                       placement, each at its own (x, y). Each placement:
#                         {
#                           "template": '({qty}) 8" REQ\'D - {pool_label}',
#                           "xy": (365, 450),          # top-left, PDF points
#                           "only_pool": "LAP",         # optional: restrict to
#                                                       #   one pool label; omit
#                                                       #   to stack all pools'
#                                                       #   lines into this box
#                           "width": 195,               # optional box overrides
#                           "font_size": 12,            #   (defaults match the
#                           "line_height": 14,          #    YellowCallout class)
#                           "padding": 4,
#                         }
#                       callout_template/callout_xy are still honored for any
#                       entry WITHOUT a callouts list, so existing pages are
#                       unchanged.
#
#   red_boxes_fixed   — list of EXPLICIT red boxes, drawn verbatim (PDF points),
#                       bypassing OCR auto-detection for those boxes. Coexists
#                       with red_box_rows: fixed boxes are drawn first, then any
#                       auto-detected ones are appended. Use this to pin boxes
#                       the detector places wrong. Each box:
#                         {"x": 66, "y": 683, "width": 763, "height": 14}
#                         # optional: "line_width": 1.2
#
# These same two fields are also honored on VALVE_KIT_PAGES entries below.

PART_MAPPING = {
    # ----- Filters → datasheet pages (special-cased; not in PAGE_ORDER) -----
    "1000-8906": {"page2_3_template": True, "filter_model": "SP-33-48-732"},
    "1000-8895": {"page2_3_template": True, "filter_model": "SP-29-36-250-A"},
    # NB: extend for every filter you sell; pattern: any reference SP-* → filter

    # ----- Guardian strainer (FG) → see ACCESSORY_PAGES; routed by description
    #       in the orchestrator, size parsed from the quote -----

    # ----- Precoat tee → see ACCESSORY_PAGES (parts_catalog routes all sizes) -----

    # ----- Concentric reducer → see ACCESSORY_PAGES (parts_catalog routes all sizes) -----

    # ----- Compressor — one per filter, aggregated -----
    "1000-5648": {
        "template": "compressor.pdf",
        "callout_template": "({qty}) REQ'D",
        "callout_xy": (100, 100),
        "red_box_rows": [],
        "aggregate_qty": True,
    },

    # ----- Tool kit — one per filter, aggregated -----
    "1000-5562": {
        "template": "tool_kit.pdf",
        "callout_template": "({qty}) KIT(S) REQ'D",
        "callout_xy": (75, 580),
        "red_box_rows": [],
        "aggregate_qty": True,
    },

    # ----- Grating -----
    # Parallel STRAIGHT grating (grate PA-<n>-WT + curb-angle CA-* + fastening
    # PA-FASTSET*) is handled as a grouped, band-sized page — see
    # GRATING_PARALLEL_STRAIGHT below and step 4e in orchestrator.py. The corner
    # is a separate page:
    "1000-8601": {
        "template": "grating_corner.pdf",
        "callout_template": "({qty}) 90CN-OUT REQ'D",
        "callout_xy": (60, 720),
        "red_box_rows": [],
    },

    # ----- Filter cleaner — typically ships with filter -----
    "13251": {
        "template": "filter_cleaner.pdf",
        "callout_template": "({qty}) 55# PAIL(S) REQ'D",
        "callout_xy": (650, 600),
        "red_box_rows": [],
    },

    # ----- Parts that don't produce their own page -----
    "1000-5852": {"skip": True},   # perlite — always-included via STATIC_PAGES; bag qty annotated in orchestrator 4a
    "1000-8397": {"skip": True},   # spare strainer basket — shown with strainer
    "1001-9810": {"skip": True},   # "Filter System - Defender" parent line
}


# ---------------------------------------------------------------------------
# 3. VALVE-KIT-DERIVED PAGES
# ---------------------------------------------------------------------------
# Kit description format: "DEFENDER VALVE KIT 120V AUTO 8/6/3/3SG"
#   8  = influent valve size
#   6  = effluent valve size
#   3  = precoat valve size (and system fill, and drain)
#   3SG = 3" sightglass

import re


def parse_valve_kit_sizes(description: str):
    """Parse valve-kit sizes from a description, or None if no match.

    Two code forms appear across quote layouts:
      4-part: "8/6/3/3SG"  -> influent/effluent/precoat/sightglass
      3-part: "8/6/3"      -> influent/effluent/precoat (no sightglass token)

    The in-line sightglass always matches the precoat size (verified across
    every example: 8/6/3 -> SG3, 12/10/6 -> SG6, 10/8/4 -> SG4), so for the
    3-part form the sightglass is taken from the precoat value.
    """
    m = re.search(r'(\d+)/(\d+)/(\d+)/(\d+)SG', description)
    if m:
        return {
            "influent": int(m.group(1)),
            "effluent": int(m.group(2)),
            "precoat": int(m.group(3)),
            "sightglass": int(m.group(4)),
        }
    m = re.search(r'(\d+)/(\d+)/(\d+)(?!\s*/)', description)
    if m:
        return {
            "influent": int(m.group(1)),
            "effluent": int(m.group(2)),
            "precoat": int(m.group(3)),
            "sightglass": int(m.group(3)),
        }
    return None


# Each entry generates one annotated page covering all pools that have a
# valve kit. Format substitutions: {pool_label}, {influent_size},
# {effluent_size}, {precoat_size}, {sightglass_size}, {influent_dn}
VALVE_KIT_PAGES = {
    "influent_check_valve.pdf": {
        "callout_template": '(1) {influent_size}" REQ\'D - {pool_label}',
        "callout_xy": (365, 450),
        # The influent table's leftmost column is the Part #, so the OCR row
        # label starts with the part number, not the size. Match the unique
        # DN code (e.g. "DN200") as a substring instead — see
        # find_row_by_label's distinctive-term rule.
        "row_search": 'DN{influent_dn}',
    },
    "effluent_precoat_valves.pdf": {
        "callout_template": (
            '(1) {effluent_size}" EFFLUENT REQ\'D - {pool_label}\n'
            '(1) {precoat_size}" PRECOAT REQ\'D - {pool_label}'
        ),
        # Centered on the requested spot (green-X mark): box center ≈ (471, 448)
        # in PDF points, so the top-left anchor is (374, 416).
        "callout_xy": (374, 416),
        # This template is a flattened raster scan: the ONLY real text is the
        # yellow callout — every table value (sizes AND part #s) is pixels, so
        # neither OCR row detection nor text-layer search lands the dense 3"/6"
        # rows reliably. Instead the row rectangles were measured once off the
        # raster and pinned per size. size_keys names which ctx sizes to box;
        # each is looked up in pinned_rows (str(size) -> [box, ...]). One
        # full-width Dimensions table here, so one box per size.
        "size_keys": ["effluent_size", "precoat_size"],
        "pinned_rows": {
            "2":  [{"x": 36.9, "y": 648.2, "width": 538.7, "height": 9.6}],
            "3":  [{"x": 36.9, "y": 657.8, "width": 538.7, "height": 10.0}],
            "4":  [{"x": 36.9, "y": 667.8, "width": 538.7, "height": 9.0}],
            "6":  [{"x": 36.9, "y": 676.8, "width": 538.7, "height": 10.0}],
            "8":  [{"x": 36.9, "y": 686.8, "width": 538.7, "height": 9.2}],
            "10": [{"x": 36.9, "y": 696.0, "width": 538.7, "height": 9.2}],
            "12": [{"x": 36.9, "y": 705.2, "width": 538.7, "height": 9.6}],
        },
    },
    "system_fill_drain_valve.pdf": {
        "callout_template": '(1) {precoat_size}" SYSTEM FILL REQ\'D - {pool_label}',
        # Was centered on the green-X (10, 422); shifted +100 to the right.
        "callout_xy": (110, 422),
        # Imperial filters use this fill-only page + the separate drain
        # extension page. Assero (SP-29) uses the combined page below instead.
        "only_for_filter_family": "IMPERIAL",
        # Raster scan, no table text layer (same situation as
        # effluent_precoat). Two tables per size: Part-Numbers (upper) +
        # Dimensions (lower) — one box each. precoat_size is the system-fill
        # valve size for Imperial filters. (Distinct raster from
        # system_fill_drain_valve_assero.pdf — coords differ; measure per file.)
        # NOTE: both tables' right borders are ~530 (Part# 530.0, Dims H-col
        # 530.6). The full-height vertical at 587.5 is the PAGE FRAME, not a
        # table border — do not extend box widths to it (that overshoot was the
        # bug fixed here: dims width is 530.6-75.5=455.1, not 512.0).
        "size_keys": ["precoat_size"],
        "pinned_rows": {
            "2":     [{"x": 348.2, "y": 545.2, "width": 181.8, "height": 9.3},
                      {"x": 75.5,  "y": 640.2, "width": 455.1, "height": 9.6}],
            "2 1/2": [{"x": 348.2, "y": 554.5, "width": 181.8, "height": 9.7},
                      {"x": 75.5,  "y": 649.8, "width": 455.1, "height": 9.7}],
            "3":     [{"x": 348.2, "y": 564.2, "width": 181.8, "height": 9.6},
                      {"x": 75.5,  "y": 659.5, "width": 455.1, "height": 9.3}],
            "4":     [{"x": 348.2, "y": 573.8, "width": 181.8, "height": 9.4},
                      {"x": 75.5,  "y": 668.8, "width": 455.1, "height": 9.4}],
            "5":     [{"x": 348.2, "y": 583.2, "width": 181.8, "height": 9.6},
                      {"x": 75.5,  "y": 678.2, "width": 455.1, "height": 9.6}],
            "6":     [{"x": 348.2, "y": 592.8, "width": 181.8, "height": 9.4},
                      {"x": 75.5,  "y": 687.8, "width": 455.1, "height": 9.4}],
            "8":     [{"x": 348.2, "y": 602.2, "width": 181.8, "height": 9.6},
                      {"x": 75.5,  "y": 697.2, "width": 455.1, "height": 9.6}],
        },
    },
    "system_fill_drain_valve_assero.pdf": {
        # Combined System Fill & Drain Valve cut sheet — used for Assero (SP-29)
        # filters, which carry both the fill and the drain on one page. precoat
        # size is the system-fill AND drain valve size for these systems.
        "callout_template": (
            '(1) {precoat_size}" SYSTEM FILL REQ\'D - {pool_label}\n'
            '(1) {precoat_size}" DRAIN VALVE REQ\'D - {pool_label}'
        ),
        "callout_xy": (362, 472),
        # Red boxes pinned to the 3" rows (Part-Numbers + Dimensions tables) —
        # the standard SP-29 fill/drain size. Revisit if a non-3" SP-29 appears.
        "red_boxes_fixed": [
            {"x": 346, "y": 562, "width": 113, "height": 14},
            {"x": 74,  "y": 657, "width": 458, "height": 13},
        ],
        "only_for_filter_family": "ASSERO",
    },
    "drain_valve_extension.pdf": {
        "callout_template": '(1) {precoat_size}" DRAIN VALVE REQ\'D - {pool_label}',
        # Seated in the white space BELOW the dimensions table. The table
        # bottom border sits at ~626pt; the box top is placed at 636 (≈10pt
        # gap). At 4 pool lines (64pt tall) it ends at ~700pt, clear of the
        # page edge. Was (75, 550), which overlapped the table's upper rows.
        "callout_xy": (75, 636),
        # Unlike effluent/system_fill this template HAS a real text layer, but
        # the table only ever lists drain sizes 3 and 4, so the rows are pinned
        # (deterministic; matches the prior job's baked row-3 box exactly).
        # drain size = precoat_size for these systems. A requested size with no
        # row here (e.g. 6") draws no box, which is correct — there is no 6" row.
        "size_keys": ["precoat_size"],
        "pinned_rows": {
            "3": [{"x": 164.6, "y": 604.9, "width": 283.3, "height": 13.4}],
            "4": [{"x": 164.6, "y": 614.7, "width": 283.3, "height": 13.4}],
        },
        # Only Imperial-family filters get a separate drain extension page;
        # Assero filters have the drain on the system fill/drain page
        "only_for_filter_family": "IMPERIAL",
    },
    "inline_sightglass.pdf": {
        "callout_template": '(1) {sightglass_size}" REQ\'D - {pool_label}',
        # Centered on the requested spot (green-X mark): box center ≈ (290, 495)
        # in PDF points, so the top-left anchor is (193, 477).
        "callout_xy": (193, 477),
        # Raster scan, no table text layer — OCR row detection drew nothing on
        # this page. Rows pinned off the real grid (288 DPI); the row-3 anchor
        # was confirmed against the template's baked prior-job box (the top
        # detected rule is the "in" subheader, not size 2 — the baked box keeps
        # the labeling honest). One full-width box per size; sightglass size =
        # precoat size for these systems.
        "size_keys": ["sightglass_size"],
        "pinned_rows": {
            "2": [{"x": 208.6, "y": 652.1, "width": 181.8, "height": 11.7}],
            "3": [{"x": 208.6, "y": 663.8, "width": 181.8, "height": 11.7}],
            "4": [{"x": 208.6, "y": 675.5, "width": 181.8, "height": 11.9}],
            "6": [{"x": 208.6, "y": 687.4, "width": 181.8, "height": 11.7}],
            "8": [{"x": 208.6, "y": 699.1, "width": 181.8, "height": 12.7}],
        },
    },
}


# ---------------------------------------------------------------------------
# 3b. ACCESSORY PAGES — reducers / precoat tees / strainers / radial &
#     perpendicular grating.
#
# Part routing comes from parts_catalog.py (exact SAP part#) or, for radial /
# perpendicular grating, from a Reference# rule in the orchestrator. The yellow
# callout SIZE is parsed from the quote line description
# (quote_parser.accessory_size) — NOT stored per part.
#
# Coordinate sourcing:
#   - NEW pages (eccentric_reducer, reducer_ss, precoat_tee_ss, strainer_*,
#     grating_radial, grating_perpendicular_*) use red_boxes_fixed +
#     callout_xy lifted from each sheet's baked annotation (exact historical
#     placement).
#   - EXISTING pages (concentric_reducer, precoat_tee, guardian_strainer) keep
#     their established callout_xy and detect the red row by the parsed size
#     ("row_from_size": True) since those template files predate this batch.
#
# Entries flagged "verify": True have callout wording/coords that haven't been
# checked against a real quote line yet (no radial/perpendicular grating on the
# sample quote).
# ---------------------------------------------------------------------------
ACCESSORY_PAGES = {
    # ----- Reducers -----
    "concentric_reducer.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        # Callout placed at the sheet's intended spot (was (440,220), which
        # landed off the mark). Red box located by the unique part number via
        # PDF text search, within the table's column span x[23,213] — reliable
        # on this dense table where OCR row detection failed.
        "callout_xy": (261, 138),
        "table_x": (23, 213),
    },
    "eccentric_reducer.pdf": {              # = reducer_fg.pdf (FG eccentric)
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (261, 138),
        "red_boxes_fixed": [{"x": 23, "y": 62, "width": 190, "height": 21}],
    },
    "reducer_ss.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (263, 140),
        "red_boxes_fixed": [{"x": 25, "y": 51, "width": 190, "height": 32}],
    },
    # ----- Precoat tees -----
    "precoat_tee.pdf": {                    # existing FG page
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (520, 440),
        "row_from_size": True,
    },
    "precoat_tee_ss.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (324, 235),
        "red_boxes_fixed": [{"x": 29, "y": 229, "width": 219, "height": 32}],
    },
    # ----- Strainers -----
    "guardian_strainer.pdf": {              # existing FG guardian
        "callout_template": "({qty}) {size} REQ'D w/ SPARE BASKET(S)",
        "callout_xy": (365, 700),
        "row_from_size": True,
    },
    "strainer_reducing.pdf": {
        "callout_template": "({qty}) {size} REQ'D w/ SPARE BASKET(S)",
        "callout_xy": (196, 401),
        "red_boxes_fixed": [{"x": 27, "y": 487, "width": 559, "height": 27}],
    },
    "strainer_straight.pdf": {
        "callout_template": "({qty}) {size} REQ'D w/ SPARE BASKET(S)",
        "callout_xy": (209, 455),
        "red_boxes_fixed": [{"x": 23, "y": 527, "width": 566, "height": 23}],
    },
    # ----- Radial / perpendicular grating (routed by Reference#) -----
    # qty is footage (FOT) or each from the quote; wording pending real lines.
    "grating_radial.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (158, 509),
        "red_boxes_fixed": [{"x": 424, "y": 550, "width": 158, "height": 15}],
        "verify": True,
    },
    "grating_perpendicular_0406.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (5, 384),
        "verify": True,
    },
    "grating_perpendicular_0812.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (13, 427),
        "red_boxes_fixed": [{"x": 18, "y": 622, "width": 196, "height": 13}],
        "verify": True,
    },
    "grating_perpendicular_1420.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (7, 541),
        "red_boxes_fixed": [{"x": 81, "y": 588, "width": 195, "height": 11}],
        "verify": True,
    },
}


# ---------------------------------------------------------------------------
# 3c. PARALLEL STRAIGHT GRATING — grouped, band-sized page.
#
# A parallel grate order is several lines that belong on ONE page: the grate
# (Reference# PA-<width>-WT), the curb angle (CA-*), and the fastening set
# (PA-FASTSET*). The page is chosen by the GRATE's width band. Callout-only —
# these sheets have no size table to red-box. callout_xy is the placeholder
# position measured per band sheet. (The 90° corner PA-*CN-* is a separate
# page — see PART_MAPPING 1000-8601.)
#
# Width band (inches, inclusive) -> (template filename, callout top-left xy)
# ---------------------------------------------------------------------------
GRATING_PARALLEL_STRAIGHT = {
    (6, 10):  ("grating_parallel_0610.pdf", (74, 571)),
    (11, 14): ("grating_parallel_1114.pdf", (88, 550)),
    (15, 18): ("grating_parallel_1518.pdf", (91, 558)),
}


# ---------------------------------------------------------------------------
# 4. PER-FILTER SCHEMATICS
# ---------------------------------------------------------------------------
# One schematic page per filter line item on the quote. Schematic file is
# looked up by filter family. Emitted by orchestrator AFTER datasheets and
# BEFORE the PAGE_ORDER pages, in the same order as datasheets (largest
# SP-XX first, tie-break by quote order).
#
# Today there are two real schematics:
#   - defender_filter_schematic_lap.pdf      (Imperial: SP-27-48, SP-33, SP-41, SP-49, SP-55)
#   - defender_filter_schematic_activity.pdf (Assero placeholder — currently a copy of lap;
#                                             replace with real Assero file when available)
#
# To extend later to per-model schematics (SP-29 vs SP-27-55 etc), change
# this dict to keyed by the exact model and add a fallback to family.
SCHEMATIC_BY_FAMILY = {
    "IMPERIAL": "defender_filter_schematic_lap.pdf",
    "ASSERO":   "defender_sp29_schematic.pdf",
}


# ---------------------------------------------------------------------------
# 5. PAGE ORDER — strict sequence for non-special pages
# ---------------------------------------------------------------------------
# Position in this list = position in the final submittal (after cover,
# filter datasheets, and per-filter schematics).
#
# RULES the orchestrator applies:
#   - Pages in PAGE_ORDER but not produced this run  → silently skipped
#   - Pages produced this run but not in PAGE_ORDER  → appended at end + WARNING log
#   - A page's template missing from templates/      → silently skipped (with MISSING log)
#
# To add a new page, drop the PDF into templates/, add it to PART_MAPPING
# (if part-driven) or STATIC_PAGES (if always included), then insert the
# filename into this list at the desired position. No orchestrator change
# is ever needed for new pages.
PAGE_ORDER = [
    # ----- Page 5 — Filter Model Information (pending template) -----
    "filter_model_information.pdf",

    # ----- Page 6 — Flexsol 3000 Interior Lining -----
    "flexsol_3000_lining.pdf",

    # ----- Page 7 — Dominion Butterfly Valves -----
    "dominion_butterfly_valves.pdf",

    # ----- Page 8 — Influent Check Valve (valve-kit) -----
    "influent_check_valve.pdf",

    # ----- Page 9 — Effluent & Precoat Pneumatic Valves (valve-kit) -----
    # User-facing name: "Influent & Precoat Pneumatic Actuated Valves"
    "effluent_precoat_valves.pdf",

    # ----- Page 10 — Pneumatic Actuator General -----
    "pneumatic_actuator_specs.pdf",

    # ----- Page 11 — Pneumatic Actuator Submittal (dimensions) -----
    "pneumatic_actuator_dimensions.pdf",

    # ----- Page 12 — System Fill & Drain Valve (valve-kit) -----
    "system_fill_drain_valve.pdf",
    "system_fill_drain_valve_assero.pdf",

    # ----- Page 13 — Drain Valve with Extension (valve-kit, Imperial only) -----
    "drain_valve_extension.pdf",

    # ----- Page 14 — Inline Sight Glass (valve-kit) -----
    "inline_sightglass.pdf",

    # ----- Page 15 — Gauge Panel Kit -----
    "gauge_panel.pdf",

    # ----- Page 16 — RMF Programmer -----
    "rmf_programmer.pdf",

    # ----- Page 17 — Filter Regulator -----
    "filter_regulator.pdf",

    # ----- Page 18 — Vacuum Transfer System Overall -----
    "vacuum_transfer_system.pdf",

    # ----- Page 19 — Vacuum Transfer System Vacuum Info -----
    "vacuum_transfer_unit.pdf",

    # ----- Page 20 — Compressor (part-driven, aggregate qty) -----
    "compressor.pdf",

    # ----- Page 21 — Water Separator -----
    "water_separator.pdf",

    # ----- Page 22 — Defender Tool Kit (part-driven, aggregate qty) -----
    "tool_kit.pdf",

    # ----- Page 23 — Par Light (pending template) -----
    "par_light.pdf",

    # ----- Page 24 — Filter Cleaner (part-driven) -----
    "filter_cleaner.pdf",

    # ----- Page 25 — Perlite (always-included) -----
    "perlite.pdf",

    # ----- Strainers (part-driven) -----
    "guardian_strainer.pdf",        # FG guardian
    "strainer_reducing.pdf",        # SS reducing
    "strainer_straight.pdf",        # SS straight

    # ----- Precoat tees (part-driven) -----
    "precoat_tee.pdf",              # FG
    "precoat_tee_ss.pdf",           # SS

    # ----- Reducers (part-driven) -----
    "concentric_reducer.pdf",       # FG concentric
    "eccentric_reducer.pdf",        # FG eccentric (was reducer_fg.pdf)
    "reducer_ss.pdf",               # SS concentric + eccentric

    # ----- Grating (part-driven) -----
    "grating_parallel_0610.pdf",    # parallel straight (6-10")
    "grating_parallel_1114.pdf",    # parallel straight (11-14")
    "grating_parallel_1518.pdf",    # parallel straight (15-18")
    "grating_corner.pdf",           # parallel corner
    "grating_radial.pdf",           # parallel radial
    "grating_perpendicular_0406.pdf",
    "grating_perpendicular_0812.pdf",
    "grating_perpendicular_1420.pdf",
]


# ---------------------------------------------------------------------------
# Imperial inch → DN (metric millimeters / 25) lookup for valve row matching
# ---------------------------------------------------------------------------
INCH_TO_DN = {
    2: 50, 3: 80, 4: 100, 5: 125, 6: 150, 8: 200,
    10: 250, 12: 300, 14: 350, 16: 400,
}


def inch_to_dn(inches: int) -> int:
    return INCH_TO_DN.get(inches, inches * 25)


# ---------------------------------------------------------------------------
# Filter sorting helper — used by orchestrator to order datasheets and
# schematics largest-first
# ---------------------------------------------------------------------------
def filter_size_key(reference: str) -> int:
    """Return the numeric size suffix from an SP-XX-... model reference.

    Larger filters return larger numbers; use with sorted(reverse=True) to
    order largest first. Unknown / non-SP references sort to the bottom
    (return -1).

    >>> filter_size_key("SP-55-48-2076")
    55
    >>> filter_size_key("SP-33-48-732")
    33
    >>> filter_size_key("SP-29-36-250-A")
    29
    >>> filter_size_key("SP-27-55-...")
    27
    >>> filter_size_key("unknown")
    -1
    """
    if not reference:
        return -1
    m = re.match(r'\s*SP-(\d+)', reference.upper())
    if not m:
        return -1
    return int(m.group(1))
