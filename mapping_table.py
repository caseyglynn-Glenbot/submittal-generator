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
    "eccentric_reducer.pdf",           # page 28 — pending template
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

PART_MAPPING = {
    # ----- Filters → datasheet pages (special-cased; not in PAGE_ORDER) -----
    "1000-8906": {"page2_3_template": True, "filter_model": "SP-33-48-732"},
    "1000-8895": {"page2_3_template": True, "filter_model": "SP-29-36-250-A"},
    # NB: extend for every filter you sell; pattern: any reference SP-* → filter

    # ----- Guardian strainer -----
    "1000-6270": {
        "template": "guardian_strainer.pdf",
        "callout_template": '({qty}) 8" REQ\'D w/ SPARE BASKET(S)',
        "callout_xy": (365, 700),
        "red_box_rows": ["8"],
    },

    # ----- Precoat tee -----
    "1000-6232": {
        "template": "precoat_tee.pdf",
        "callout_template": '({qty}) 8" x 5" x 3" REQ\'D',
        "callout_xy": (520, 440),
        "red_box_rows": ["8 x 5 x 3"],
    },

    # ----- Concentric reducer -----
    "1000-6213": {
        "template": "concentric_reducer.pdf",
        "callout_template": '({qty}) 6" X 4" REQ\'D',
        "callout_xy": (440, 220),
        "red_box_rows": ["6 x 4"],
    },

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
    "1002-5406": {
        "template": "grating_straight.pdf",
        "callout_template": "({qty}') PA-12 WHITE - REQ'D",
        "callout_xy": (60, 720),
        "red_box_rows": [],
    },
    "1000-7906": {
        "template": "grating_straight.pdf",
        "callout_template": "({qty}') CA-NOTAIL WHITE - REQ'D",
        "callout_xy": (60, 736),
        "red_box_rows": [],
    },
    "1000-8601": {
        "template": "grating_corner.pdf",
        "callout_template": "({qty}) 90CN-OUT REQ'D",
        "callout_xy": (60, 720),
        "red_box_rows": [],
    },
    "1000-8653": {
        "template": "grating_straight.pdf",
        "callout_template": "({qty}) FASTENING SETS OF 50",
        "callout_xy": (60, 752),
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
    "1000-5852": {"skip": True},   # perlite — now always-included via STATIC_PAGES
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
    """Parse '8/6/3/3SG' → dict of sizes, or None if no match."""
    m = re.search(r'(\d+)/(\d+)/(\d+)/(\d+)SG', description)
    if not m:
        return None
    return {
        "influent": int(m.group(1)),
        "effluent": int(m.group(2)),
        "precoat": int(m.group(3)),
        "sightglass": int(m.group(4)),
    }


# Each entry generates one annotated page covering all pools that have a
# valve kit. Format substitutions: {pool_label}, {influent_size},
# {effluent_size}, {precoat_size}, {sightglass_size}, {influent_dn}
VALVE_KIT_PAGES = {
    "influent_check_valve.pdf": {
        "callout_template": '(1) {influent_size}" REQ\'D - {pool_label}',
        "callout_xy": (365, 450),
        "row_search": 'DN{influent_dn} - {influent_size}"',
    },
    "effluent_precoat_valves.pdf": {
        "callout_template": (
            '(1) {effluent_size}" EFFLUENT REQ\'D - {pool_label}\n'
            '(1) {precoat_size}" PRECOAT REQ\'D - {pool_label}'
        ),
        "callout_xy": (365, 690),
        "row_search_multi": [
            '{effluent_size}',  # match by size column
            '{precoat_size}',
        ],
    },
    "system_fill_drain_valve.pdf": {
        "callout_template": '(1) {precoat_size}" SYSTEM FILL REQ\'D - {pool_label}',
        "callout_xy": (365, 580),
        "row_search": '{precoat_size}',
    },
    "drain_valve_extension.pdf": {
        "callout_template": '(1) {precoat_size}" DRAIN VALVE REQ\'D - {pool_label}',
        "callout_xy": (75, 500),
        "row_search": '{precoat_size}',
        # Only Imperial-family filters get a separate drain extension page;
        # Assero filters have the drain on the system fill/drain page
        "only_for_filter_family": "IMPERIAL",
    },
    "inline_sightglass.pdf": {
        "callout_template": '(1) {sightglass_size}" REQ\'D - {pool_label}',
        "callout_xy": (365, 590),
        "row_search": '{sightglass_size}',
    },
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
    "ASSERO":   "defender_filter_schematic_activity.pdf",
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

    # ----- Page 26 — Guardian Fiberglass Hair Strainer (part-driven) -----
    "guardian_strainer.pdf",

    # ----- Page 27 — Precoat Tee (part-driven) -----
    "precoat_tee.pdf",

    # ----- Page 28 — Concentric Reducers (part-driven) -----
    "concentric_reducer.pdf",

    # ----- Page 29 — Eccentric Reducers (pending template, part-driven) -----
    "eccentric_reducer.pdf",

    # ----- Page 30 — Grating Straight (part-driven) -----
    "grating_straight.pdf",

    # ----- Page 31 — Grating Corner (part-driven) -----
    "grating_corner.pdf",
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
