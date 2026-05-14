"""
Master mapping table for the Neptune Benson submittal generator.

Three categories of pages in a submittal:

1. STATIC PAGES — always included regardless of quote contents
2. PART-NUMBER-DRIVEN PAGES — included only if matching part is on quote
3. VALVE-KIT-DERIVED PAGES — multiple pages whose annotations come from
   the Defender valve kit spec (e.g. "8/6/3/3SG")
"""

SECTION_TO_POOL_LABEL = {
    "Lap Pool": "LAP",
    "Training pool": "ACTIVITY",
    "Items": "",
}


def get_pool_label(section: str) -> str:
    return SECTION_TO_POOL_LABEL.get(section, section.upper())


# ---------------------------------------------------------------------------
# 1. STATIC PAGES — always included, in submittal order
# ---------------------------------------------------------------------------
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
    "defender_filter_schematic_lap.pdf",
    "defender_filter_schematic_activity.pdf",
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
    # ----- Filters → page 2-3 fillable datasheets -----
    "1000-8906": {"page2_3_template": True, "filter_model": "SP-33-48-732"},
    "1000-8895": {"page2_3_template": True, "filter_model": "SP-29-36-250-A"},
    # NB: extend for every filter you sell; pattern: any reference SP-* → filter

    # ----- Guardian strainer (page 26) -----
    "1000-6270": {
        "template": "guardian_strainer.pdf",
        "callout_template": '({qty}) 8" REQ\'D w/ SPARE BASKET(S)',
        "callout_xy": (365, 700),
        "red_box_rows": ["8"],
    },

    # ----- Precoat tee (page 27) -----
    "1000-6232": {
        "template": "precoat_tee.pdf",
        "callout_template": '({qty}) 8" x 5" x 3" REQ\'D',
        "callout_xy": (520, 440),
        "red_box_rows": ["8 x 5 x 3"],
    },

    # ----- Concentric reducer (page 28) -----
    "1000-6213": {
        "template": "concentric_reducer.pdf",
        "callout_template": '({qty}) 6" X 4" REQ\'D',
        "callout_xy": (440, 220),
        "red_box_rows": ["6 x 4"],
    },

    # ----- Perlite (page 24) — both pool callouts share one page -----
    "1000-5852": {
        "template": "perlite.pdf",
        "callout_template": "({qty}) 25# BAGS REQ'D - {pool_label}",
        "callout_xy": (365, 700),
        "red_box_rows": [],
    },

    # ----- Compressor (page 21) — one per filter, aggregated -----
    "1000-5648": {
        "template": "compressor.pdf",
        "callout_template": "({qty}) REQ'D",
        "callout_xy": (100, 100),
        "red_box_rows": [],
        "aggregate_qty": True,
    },

    # ----- Tool kit (page 23) — one per filter, aggregated -----
    "1000-5562": {
        "template": "tool_kit.pdf",
        "callout_template": "({qty}) KIT(S) REQ'D",
        "callout_xy": (75, 580),
        "red_box_rows": [],
        "aggregate_qty": True,
    },

    # ----- Grating (pages 29-30) -----
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

    # ----- Filter cleaner (page 25) — typically ships with filter -----
    "13251": {
        "template": "filter_cleaner.pdf",
        "callout_template": "({qty}) 55# PAIL(S) REQ'D",
        "callout_xy": (650, 600),
        "red_box_rows": [],
    },

    # ----- Parts that don't produce their own page -----
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


# Each entry generates one annotated page per pool that has a valve kit.
# Format substitutions: {pool_label}, {influent_size}, {effluent_size},
# {precoat_size}, {sightglass_size}, {influent_dn}
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
# Imperial inch → DN (metric millimeters / 25) lookup for valve row matching
# ---------------------------------------------------------------------------
INCH_TO_DN = {
    2: 50, 3: 80, 4: 100, 5: 125, 6: 150, 8: 200,
    10: 250, 12: 300, 14: 350, 16: 400,
}


def inch_to_dn(inches: int) -> int:
    return INCH_TO_DN.get(inches, inches * 25)
