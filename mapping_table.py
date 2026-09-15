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

import re

SECTION_TO_POOL_LABEL = {
    "Lap Pool": "LAP",
    "Training pool": "ACTIVITY",
    "Items": "",
}


def get_pool_label(section: str) -> str:
    return SECTION_TO_POOL_LABEL.get(section, section.upper())


# ---------------------------------------------------------------------------
# Generic-section pool lettering
# ---------------------------------------------------------------------------
# Multi-system quotes usually name their sections ("Lap Pool", "SWIMMING POOL",
# "SPLASH PAD") and those names go straight onto the callouts. But the standard
# Evoqua layout puts every line under one unnamed "Items" heading, and the
# parser then splits it at each 1001-9810 parent row — producing two or three
# sections with nothing to call them. Those get lettered POOL A, POOL B, POOL C
# in quote order so the callouts on shared pages can still be told apart.
#
# A quote with a SINGLE unnamed section is lettered POOL A as well, so every
# pool callout carries a pool label whether or not the quote names the system.
# Set LABEL_LONE_GENERIC_SECTION = False to leave a lone system unlabeled
# ("(1) 8\" REQ'D" instead of "(1) 8\" REQ'D - POOL A").
GENERIC_SECTION_NAMES = {"", "items", "item", "equipment", "products"}
# The parser names a split system from the uppercase run in its alternative
# description ("SWIMMING POOL", "SPLASH PAD"); when that line carries no label
# it falls back to "SYSTEM 1", "SYSTEM 2". Those fallbacks are the ones that
# want lettering — the real names are left alone.
GENERIC_SECTION_RE = re.compile(r"^\s*system\s*\d+\s*$", re.I)
GENERIC_POOL_LETTERS = ["A", "B", "C", "D", "E", "F"]
GENERIC_POOL_LABEL_FMT = "POOL {letter}"
LABEL_LONE_GENERIC_SECTION = True


def is_generic_section(section: str) -> bool:
    """True when a section heading carries no usable pool name of its own."""
    s = (section or "").strip()
    return s.lower() in GENERIC_SECTION_NAMES or bool(GENERIC_SECTION_RE.match(s))


def build_pool_labels(sections) -> dict:
    """Map each section name to the pool label its callouts should carry.

    `sections` is the ordered, de-duplicated list of section names as they
    appear on the quote. Named sections keep their own label via
    get_pool_label; generic ones are lettered A, B, C in that order.

    Beyond the available letters, the section falls back to its own name so
    nothing silently collides.
    """
    ordered = list(dict.fromkeys(sections))
    lone_section = len(ordered) == 1

    labels = {}
    letter_at = 0
    for section in ordered:
        if not is_generic_section(section):
            labels[section] = get_pool_label(section)
            continue
        if lone_section and not LABEL_LONE_GENERIC_SECTION:
            labels[section] = ""
            continue
        if letter_at < len(GENERIC_POOL_LETTERS):
            labels[section] = GENERIC_POOL_LABEL_FMT.format(
                letter=GENERIC_POOL_LETTERS[letter_at])
            letter_at += 1
        else:
            labels[section] = get_pool_label(section)
    return labels


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

    # ----- Filter cleaner (Chem-Clean Express) — typically ships with filter.
    # Both pail sizes land on the same cut sheet; {pail_label} carries the
    # per-part size so mixed 25#/55# quotes stack correct lines in one box.
    # Callout xy sits on the sheet's yellow placeholder (template strips it).
    # aggregate_key="pail_label": quantities are summed PER PAIL SIZE across
    # the whole quote (a two-system quote with a 25# pail in each system shows
    # "(2) 25# PAIL(S) REQ'D", not a deduped "(1)"). Both the legacy 132xx and
    # the SAP 1000-58xx part numbers appear on real quotes (Ulster uses the
    # SAP form), so all four are mapped.
    "13250": {
        "template": "filter_cleaner.pdf",
        "callout_template": "({qty}) {pail_label} PAIL(S) REQ'D",
        "callout_xy": (383, 394),
        "pail_label": "25#",
        "aggregate_key": "pail_label",
        "red_box_rows": [],
    },
    "13251": {
        "template": "filter_cleaner.pdf",
        "callout_template": "({qty}) {pail_label} PAIL(S) REQ'D",
        "callout_xy": (383, 394),
        "pail_label": "55#",
        "aggregate_key": "pail_label",
        "red_box_rows": [],
    },
    "1000-5865": {   # CHEMICAL, CLEAN EXPRESS 25LB (SAP part number)
        "template": "filter_cleaner.pdf",
        "callout_template": "({qty}) {pail_label} PAIL(S) REQ'D",
        "callout_xy": (383, 394),
        "pail_label": "25#",
        "aggregate_key": "pail_label",
        "red_box_rows": [],
    },
    "1000-5866": {   # CHEMICAL, CLEAN EXPRESS 55LB (SAP part number)
        "template": "filter_cleaner.pdf",
        "callout_template": "({qty}) {pail_label} PAIL(S) REQ'D",
        "callout_xy": (383, 394),
        "pail_label": "55#",
        "aggregate_key": "pail_label",
        "red_box_rows": [],
    },

    # ----- Parts that don't produce their own page -----
    "1000-5852": {"skip": True},   # perlite — always-included via STATIC_PAGES; bag qty annotated in orchestrator 4a
    "1000-8397": {"skip": True},   # spare strainer basket — shown with strainer
    "1000-8399": {"skip": True},   # basket strainer 10-12 T316SS — shown with Guardian strainer callout
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

# (re is imported at the top of this module)


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
        "callout_template": '({influent_qty}) {influent_size}" REQ\'D - {pool_label}',
        "callout_xy": (365, 450),
        # The influent table's leftmost column is the Part #, so the OCR row
        # label starts with the part number, not the size. Match the unique
        # DN code (e.g. "DN200") as a substring instead — see
        # find_row_by_label's distinctive-term rule.
        "row_search": 'DN{influent_dn}',
    },
    "effluent_precoat_valves.pdf": {
        "callout_template": (
            '({effluent_qty}) {effluent_size}" EFFLUENT REQ\'D - {pool_label}\n'
            '({precoat_qty}) {precoat_size}" PRECOAT REQ\'D - {pool_label}'
        ),
        # Anchored in the clear full-width band between the drawings and the
        # dimensions table. The template's baked example callout sits at
        # ~(245, 528) but is stripped at annotation time, so the band from
        # y=470 down to the table top at y=600 is free across x=32..580.
        # The previous anchor (374, 416) left only 226pt of width, which forced
        # the engine to shrink the font to ~7pt once extra-valve lines (longer
        # than "(1) 12\" EFFLUENT REQ'D - COMPETITION POOL") joined the box.
        # 8 lines at the default 14pt line height end at y=590, clear of the table.
        "callout_xy": (40, 470),
        # This template is a flattened raster scan: the ONLY real text is the
        # yellow callout — every table value (sizes AND part #s) is pixels, so
        # neither OCR row detection nor text-layer search lands the dense 3"/6"
        # rows reliably. Instead the row rectangles were measured once off the
        # raster and pinned per size. size_keys names which ctx sizes to box;
        # each is looked up in pinned_rows (str(size) -> [box, ...]). One
        # full-width Dimensions table here, so one box per size.
        "size_keys": ["effluent_size", "precoat_size"],
        # Extra valves quoted on a Defender beyond the four service roles
        # (e.g. a gear-operated isolation valve) are listed on THIS page:
        # one callout line "({qty}) {size}\" {NAME}" per valve, plus a red box
        # on its size row when the table carries one. Keeps every valve on the
        # job visible to the reviewer instead of only in the run log.
        "accepts_extra_valves": True,
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
        "callout_template": '({system_fill_qty}) {system_fill_size}" SYSTEM FILL REQ\'D - {pool_label}',
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
        #
        # Was ["precoat_size"]: the kit string carries no system-fill token, so
        # the precoat size stood in for it. That holds only while the precoat is
        # 3" or 4" — the system fill is ALWAYS one of those two. Now driven by
        # system_fill_size, which the orchestrator resolves and leaves None
        # (page skipped for that section, warning logged) when no valid fill
        # size can be determined.
        "size_keys": ["system_fill_size"],
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
        # filters, which carry both the fill and the drain on one page.
        "callout_template": (
            '({system_fill_qty}) {system_fill_size}" SYSTEM FILL REQ\'D - {pool_label}\n'
            '({system_fill_qty}) {system_fill_size}" DRAIN VALVE REQ\'D - {pool_label}'
        ),
        "callout_xy": (362, 472),
        # WAS: red_boxes_fixed pinned to the 3" rows, with the note "the
        # standard SP-29 fill/drain size. Revisit if a non-3" SP-29 appears."
        # Quote 05043737 (SPLEX MD) was that non-3" SP-29 — a 4/4/4 kit — and
        # the page shipped with the callout reading 4" while both boxes stayed
        # baked on the 3" row. The upper box was also 113pt wide against a
        # 182pt table, stopping mid-column.
        #
        # Now size-keyed like the Imperial sheet. Row geometry measured off
        # this raster at 288 DPI (horizontal rules at 545.0/554.5/564.2/573.5/
        # 583.0/592.5/602.2 upper and 640.0/649.5/658.5/667.8/678.2/687.5/697.2
        # lower; verticals at 348.0-530.0 upper and 76.0-531.0 lower) and it
        # matches system_fill_drain_valve.pdf row for row — the two cut sheets
        # share a table block.
        "size_keys": ["system_fill_size"],
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
        "only_for_filter_family": "ASSERO",
    },
    "drain_valve_extension.pdf": {
        "callout_template": '({precoat_qty}) {precoat_size}" DRAIN VALVE REQ\'D - {pool_label}',
        # Seated in the white space BELOW the dimensions table. The table
        # bottom border sits at ~626pt; the box top is placed at 636 (≈10pt
        # gap). At 4 pool lines (64pt tall) it ends at ~700pt, clear of the
        # page edge. Was (75, 550), which overlapped the table's upper rows.
        "callout_xy": (75, 636),
        # Unlike effluent/system_fill this template HAS a real text layer, but
        # the table only ever lists drain sizes 3 and 4, so the rows are pinned
        # (deterministic; matches the prior job's baked row-3 box exactly).
        # drain size = precoat_size for these systems. Unlike the sightglass
        # size (read off a real quoted sightglass line), this one is INFERRED,
        # so a size with no row here means the inference does not hold for this
        # system — skip the section entirely rather than print an unboxed
        # callout asserting e.g. a 10" drain valve on a 2000 GPM Defender.
        "size_keys": ["precoat_size"],
        "skip_section_if_no_row": True,
        "pinned_rows": {
            "3": [{"x": 164.6, "y": 604.9, "width": 283.3, "height": 13.4}],
            "4": [{"x": 164.6, "y": 614.7, "width": 283.3, "height": 13.4}],
        },
        # Only Imperial-family filters get a separate drain extension page;
        # Assero filters have the drain on the system fill/drain page
        "only_for_filter_family": "IMPERIAL",
    },
    "inline_sightglass.pdf": {
        "callout_template": '({sightglass_qty}) {sightglass_size}" REQ\'D - {pool_label}',
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
        # Callout sits in the clear gap between the table (right edge x≈213)
        # and the reducer drawings (left edge x≈435); min box width 195 keeps
        # the right edge at ~421.
        "callout_xy": (226, 248),
        "table_x": (23, 213),
    },
    "eccentric_reducer.pdf": {              # = reducer_fg.pdf (FG eccentric)
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (226, 248),           # same sheet layout — same clear gap
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
        "callout_xy": (373, 273),   # top-left of baked placeholder (R 7/21/14 sheet)
        "row_from_size": True,
    },
    "precoat_tee_ss.pdf": {
        "callout_template": "({qty}) {size} REQ'D",
        "callout_xy": (324, 235),
        "red_boxes_fixed": [{"x": 29, "y": 229, "width": 219, "height": 32}],
    },
    # ----- Strainers -----
    "guardian_strainer_straight.pdf": {     # FG Guardian, straight (R 1/13/15)
        "callout_template": "({qty}) {size} REQ'D w/ SPARE BASKET(S)",
        "callout_xy": (216, 435),           # top-left of baked placeholder
        "table_x": (26, 589),
    },
    "guardian_strainer_reducing.pdf": {     # FG Guardian, reducing (R 11/12/18)
        "callout_template": "({qty}) {size} REQ'D W/ SPARE BASKET(S)",
        "callout_xy": (194, 399),           # top-left of baked placeholder
        "table_x": (27, 585),
    },

    # ----- Surge tank accessories -----
    "anti_vortex_plate.pdf": {              # PVC + SS plates (R 11/29/17)
        "callout_template": "({qty}) {size}\" {mat} REQ'D",
        "callout_xy": (35, 270),            # top-left of baked placeholder
        "size_re": r"PLATE\s+(\d+)",        # 'ANTI-VORTEX PLATE 8" W/HDW PVC' -> 8
        "table_x": (22, 389),
    },
    "surge_ladder.pdf": {                   # SS access ladders (R 5/12/16)
        "callout_template": "({qty}) {size} RUNG REQ'D",
        "callout_xy": (411, 506),
        "size_re": r"(\d+)\s*RUNG",         # 'LADDER SURGE 8 RUNG ...' -> 8
        "table_x": (345, 587),
    },
    # Diversion valve sheets have image-only tables (no text layer), so rows
    # are fixed boxes keyed by parsed pipe size; bands measured by line scan
    # and anchored to each sheet's baked example box (inline: 12, vert: 10).
    "diversion_valve_inline.pdf": {         # PVC inline (R 5/6/11)
        "callout_template": "({qty}) {size}\" DUAL FLOAT REQ'D",
        "callout_xy": (185, 499),           # top-left of baked placeholder
        "size_re": r"(\d+)\s*PVC",          # 'IN-LINE 14PVC2 FLT' -> 14
        "size_rows": {
            "4":  {"x": 40, "y": 603.8, "width": 416, "height": 12.5},
            "6":  {"x": 40, "y": 615.8, "width": 416, "height": 13.5},
            "8":  {"x": 40, "y": 628.8, "width": 416, "height": 12.5},
            "10": {"x": 40, "y": 640.8, "width": 416, "height": 12.0},
            "12": {"x": 40, "y": 652.2, "width": 416, "height": 13.5},
            "14": {"x": 40, "y": 665.2, "width": 416, "height": 14.5},
            "16": {"x": 40, "y": 679.2, "width": 416, "height": 12.5},
            "18": {"x": 40, "y": 691.2, "width": 416, "height": 13.0},
            "20": {"x": 40, "y": 703.7, "width": 416, "height": 13.3},
        },
    },
    "diversion_valve_vertical.pdf": {       # PVC vertical (R 5/6/11)
        "callout_template": "({qty}) {size}\" REQ'D",
        "callout_xy": (184, 551),           # top-left of baked placeholder
        "size_re": r"(\d+)\s*PVC",
        "size_rows": {
            "4":  {"x": 114, "y": 621.8, "width": 385, "height": 12.5},
            "6":  {"x": 114, "y": 633.8, "width": 385, "height": 13.5},
            "8":  {"x": 114, "y": 646.8, "width": 385, "height": 11.7},
            "10": {"x": 114, "y": 658.0, "width": 385, "height": 14.0},
            "12": {"x": 114, "y": 671.5, "width": 385, "height": 11.8},
            "14": {"x": 114, "y": 682.8, "width": 385, "height": 14.9},
            "16": {"x": 114, "y": 697.2, "width": 385, "height": 12.5},
            "18": {"x": 114, "y": 709.2, "width": 385, "height": 13.0},
            "20": {"x": 114, "y": 721.7, "width": 385, "height": 13.3},
        },
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

    # ----- VFD (description-driven): 2 static pages + annotated frame chart -----
    "vfd_no_bypass.pdf",
    "vfd_no_bypass_chart.pdf",
    "vfd_bypass.pdf",
    "vfd_bypass_chart.pdf",

    # ----- Page 22 — Defender Tool Kit (part-driven, aggregate qty) -----
    "tool_kit.pdf",

    # ----- Page 23 — Par Light (pending template) -----
    "par_light.pdf",

    # ----- Page 24 — Filter Cleaner (part-driven) -----
    "filter_cleaner.pdf",

    # ----- Page 25 — Perlite (always-included) -----
    "perlite.pdf",

    # ----- Strainers (part-driven) -----
    "guardian_strainer_straight.pdf",   # FG guardian straight
    "guardian_strainer_reducing.pdf",   # FG guardian reducing
    "strainer_reducing.pdf",        # SS reducing
    "strainer_straight.pdf",        # SS straight

    # ----- Precoat tees (part-driven) -----
    "precoat_tee.pdf",              # FG
    "precoat_tee_ss.pdf",           # SS

    # ----- Reducers (part-driven) -----
    "concentric_reducer.pdf",       # FG concentric
    "eccentric_reducer.pdf",        # FG eccentric (was reducer_fg.pdf)
    "reducer_ss.pdf",               # SS concentric + eccentric

    # ----- Surge tank accessories (part/description-driven) -----
    "diversion_valve_inline.pdf",
    "diversion_valve_vertical.pdf",
    "anti_vortex_plate.pdf",
    "surge_ladder.pdf",

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
# VFD pages (greendrive) — description-driven. Each variant is a 2-page
# static intro (merged verbatim) + a 1-page frame chart that takes the
# yellow callout and a red box on the matched frame row. callout_xy is the
# top-left of each sheet's baked placeholder.
# ---------------------------------------------------------------------------
VFD_PAGES = {
    "no_bypass": {
        "static": "vfd_no_bypass.pdf",
        "chart": "vfd_no_bypass_chart.pdf",
        "callout_xy": (175, 554),
    },
    "bypass": {
        "static": "vfd_bypass.pdf",
        "chart": "vfd_bypass_chart.pdf",
        "callout_xy": (161, 562),
    },
}

# Frame Sizing Chart geometry (identical on both variants' chart page):
# full-row red boxes located by measured row tops; table spans x 30-582.
VFD_FRAME_ROWS = {
    "A5": {"x": 30, "y": 644.6, "width": 552, "height": 14},
    "B1": {"x": 30, "y": 656.6, "width": 552, "height": 14},
    "B2": {"x": 30, "y": 668.6, "width": 552, "height": 14},
    "C1": {"x": 30, "y": 680.8, "width": 552, "height": 14},
    "C2": {"x": 30, "y": 692.8, "width": 552, "height": 14},
}

# HP -> frame row per voltage column (200-240V / 380-480V / 575V).
_VFD_HP_TO_FRAME = {
    "200": {"A5": {0.5, 0.75, 1, 1.5, 2, 3, 5},
            "B1": {7.5, 10, 15}, "B2": {20},
            "C1": {25, 30, 40}, "C2": {50, 60}},
    "480": {"A5": {0.5, 0.75, 1, 1.5, 2, 3, 5, 7.5, 10},
            "B1": {15, 20, 25}, "B2": {30, 40},
            "C1": {50, 60, 75}, "C2": {100, 125}},
    "575": {"A5": {1, 1.5, 2, 3, 5, 7.5, 10},
            "B1": {15, 20, 25}, "B2": {30, 40},
            "C1": {50, 60, 75}, "C2": {100, 125}},
}


def vfd_frame_row(volt: int, hp: float):
    """Map a VFD's voltage + HP to its Frame Sizing Chart row ('A5'..'C2')."""
    if volt <= 240:
        col = "200"
    elif volt <= 480:
        col = "480"
    else:
        col = "575"
    for row, hps in _VFD_HP_TO_FRAME[col].items():
        if hp in hps:
            return row
    return None


# ---------------------------------------------------------------------------
# Wafer UV (ETS-UV WF series) — model + enclosure-rating driven.
# Each package = a 1-page spec sheet (annotated: "(qty) REQUIRED" over the
# baked placeholder at the same spot on every model) + a docs file (EZ
# Strainer / quartz basket sheets) merged VERBATIM so each model's baked
# strainer-row red boxes are preserved. Template names:
#   uv_wf_<model>_<rating>.pdf / uv_wf_<model>_<rating>_docs.pdf
# rating: "n4x" (Nema4x, loaded) or "n12" (Nema12, pending cut sheets).
# ---------------------------------------------------------------------------
UV_WAFER_MODELS = ["115_3", "115_4", "125_6", "215_6", "215_8",
                   "225_8", "230_10", "430_12"]
UV_WAFER_RATINGS = ["n12", "n4x"]
UV_WAFER_CALLOUT_XY = (264, 641)
# Per-template overrides for sheets whose baked placeholder sits elsewhere.
UV_WAFER_CALLOUT_XY_OVERRIDES = {
    "uv_wf_215_8_n12.pdf": (197, 633),
}
UV_WAFER_CALLOUT_TEMPLATE = "({qty}) REQUIRED"


def uv_wafer_templates(model: str, rating: str):
    """Return (spec_page, docs_page) template names for a WF model/rating.

    model is the digits form ('225-8' or '225_8'); rating 'n12' or 'n4x'.
    Returns None for unknown models so the caller can warn.
    """
    m = model.replace("-", "_")
    if m not in UV_WAFER_MODELS:
        return None
    return (f"uv_wf_{m}_{rating}.pdf", f"uv_wf_{m}_{rating}_docs.pdf")


# Append UV pages to PAGE_ORDER programmatically (32 names would drown the
# hand-maintained list above).
for _m in UV_WAFER_MODELS:
    for _r in UV_WAFER_RATINGS:
        PAGE_ORDER.append(f"uv_wf_{_m}_{_r}.pdf")
        PAGE_ORDER.append(f"uv_wf_{_m}_{_r}_docs.pdf")


# ---------------------------------------------------------------------------
# SIGNET 2551 MAGMETER (part-number driven, size-driven boxes)
# ---------------------------------------------------------------------------
# Two variants ship, distinguished by the sensor output and therefore by a
# different ordering page:
#
#   FIELD MOUNT  = frequency / digital (S3L) output, paired with a Signet 9900
#                  field-mount transmitter. Carries the 9900 pages.
#   FOR VFD      = 4 to 20 mA output wired straight to the drive. No transmitter
#                  pages (the drive is the readout).
#
# Everything else (data sheet, dimensions, saddles, fittings, K-factors) is
# common to both, so those templates are shared.
#
# The source submittals arrived pre-annotated for ONE job. Those markups were
# stripped when the templates were split (split_magmeter_templates.py); the
# boxes below are rebuilt per quote from the size in the line description, so
# a 6" magmeter boxes the 5-8 in. body and a 12" boxes the 10-36 in. one.
#
# NOTE on the field-mount ordering page: the released file boxed 3-2551-P0-11
# (½ to 4 in.) while its own dimensions page boxed the 5-8 in. row — the two
# disagreed. Treated here as stale markup: BOTH are now driven off the quoted
# size, so they can no longer contradict each other.

MAGMETER_FIELD_MOUNT_PARTS = {
    # SAP part -> nominal pipe size, inches
    "1002-4990": 3,
    "1000-6132": 4,
    "1000-6133": 6,
    "1000-6134": 8,
    "1000-6136": 10,
    "1000-6137": 12,      # product_line 'Defender' in the catalog, not 'Flowmeter'
    # Blind sensors sold without the saddle assembly (size band, not a single
    # size — sensor body code is what matters, so map to the band's midpoint).
    "1000-6127": 4,       # 0.5"-4"  field mount -> -X0 body
    "1000-6129": 10,      # 10"-12"  field mount -> -X2 body
}

MAGMETER_VFD_PARTS = {
    "1000-6121": 3,
    "1000-6122": 4,
    "1000-6123": 6,
    "1000-6124": 8,
    "1000-6125": 10,
    "1000-6126": 12,
    "1000-6107": 4,       # 0.5"-4"  blind for VFD -> -X0 body
    "1000-6108": 6,       # 5"-8"    blind for VFD -> -X1 body
    "1000-6109": 10,      # 10"-12"  blind for VFD -> -X2 body
}


def magmeter_variant(part_number: str, description: str = ""):
    """Return ('field_mount'|'vfd', size_inches) for a magmeter line, else None.

    Part number wins. Description is the fallback for a magmeter part that
    isn't catalogued yet, e.g. 'MAGMETER 6" FIELD MOUNT W/SENSOR&SADDLE'.
    """
    pn = (part_number or "").strip()
    if pn in MAGMETER_FIELD_MOUNT_PARTS:
        return "field_mount", MAGMETER_FIELD_MOUNT_PARTS[pn]
    if pn in MAGMETER_VFD_PARTS:
        return "vfd", MAGMETER_VFD_PARTS[pn]

    d = (description or "").upper()
    if "MAGMETER" not in d:
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*"', d)
    if not m:
        return None
    size = float(m.group(1))
    size = int(size) if size == int(size) else size
    variant = "vfd" if "VFD" in d else "field_mount"
    return variant, size


# Sensor body code by pipe size: -X0 (½-4 in.), -X1 (5-8 in.), -X2 (10-36 in.).
# Drives BOTH the dimensions-table row and the ordering-table row.
MAGMETER_BODY_BANDS = [
    (0.5, 4, "0", {"x": 75.6, "y": 166.0, "width": 160.0, "height": 13.0}),
    (5, 8, "1", {"x": 75.6, "y": 179.2, "width": 160.0, "height": 13.1}),
    (10, 36, "2", {"x": 75.6, "y": 192.4, "width": 160.0, "height": 13.1}),
]


def magmeter_body_code(size_inches: float):
    """Return (body_digit, dimensions_row_box) for a pipe size, or (None, None)."""
    for lo, hi, code, box in MAGMETER_BODY_BANDS:
        if lo <= size_inches <= hi:
            return code, box
    return None, None


# PVC-U clamp-on saddles SCH 80 (PV8S0xx) — the default saddle per Casey.
# The catalog carries a PVC and an iron saddle for most sizes; the NB magmeter
# assemblies quote the PVC one (e.g. 1000-6489 'SADDLE, FLM SIGNET 6 PVC SCH80').
# Rows measured off the sheet; x span matches the table width used by the
# original markup (159.7 -> 537.9).
MAGMETER_SADDLE_ROWS = {
    "2":   {"x": 159.7, "y": 364.2, "width": 378.2, "height": 13.5},
    "2.5": {"x": 159.7, "y": 378.2, "width": 378.2, "height": 13.5},
    "3":   {"x": 159.7, "y": 391.6, "width": 378.2, "height": 13.5},
    "4":   {"x": 159.7, "y": 405.5, "width": 378.2, "height": 13.5},
    "6":   {"x": 159.7, "y": 419.4, "width": 378.2, "height": 13.5},
    "8":   {"x": 159.7, "y": 433.3, "width": 378.2, "height": 13.5},
}

# The 4-20 mA ordering page is an image-only scan with no table text layer, so
# its rows are pinned per model code (measured once). The field-mount ordering
# page HAS a text layer, so its row is found by searching for the model code.
MAGMETER_VFD_ORDER_ROWS = {
    "0": {"x": 184.4, "y": 165.0, "width": 282.2, "height": 14.0},   # 3-2551-P0-12
    "1": {"x": 184.4, "y": 325.0, "width": 282.2, "height": 14.0},   # 3-2551-P1-12
    "2": {"x": 184.4, "y": 487.8, "width": 282.2, "height": 14.0},   # 3-2551-P2-12
}

MAGMETER_PAGES = {
    "field_mount": [
        {"template": "magmeter_intro.pdf", "verbatim": True},
        {"template": "magmeter_dimensions.pdf", "box": "body_band"},
        {"template": "magmeter_ordering_field_mount.pdf",
         "callout_template": "({qty}) REQ'D",
         "callout_xy": (255.3, 704.4),
         "box": "order_text",
         # Polypropylene / 316L SS body, frequency-digital output, no display —
         # the standard NB field-mount build. Change the P if a T (PVDF/Ti) or
         # V (PVDF/Hastelloy-C) body is ever quoted.
         "order_code": "3-2551-P{body}-11",
         "table_x": (174.0, 458.0)},
        {"template": "magmeter_saddles.pdf",
         "callout_template": "({qty}) {size}\" REQ'D",
         "callout_xy": (309.6, 251.4),
         "box": "saddle_row"},
        {"template": "magmeter_fittings.pdf",
         "callout_template": "({qty}) {size}\" REQ'D",
         # Inside the PVC Saddles row, below its two bullets. The narrow width
         # and smaller font keep the box inside the left table (which ends at
         # x≈306) instead of spilling into the right-hand table at x≈313.
         "callout_xy": (210.0, 166.0),
         "callout_width": 90.0,
         "callout_font_size": 9.0,
         "callout_line_height": 11.0,
         "callout_padding": 3.0,
         # PVC Saddles row (rules at y 139.7 and 183.6). The source file boxed
         # the PP Clamp-on row below it, which is a 10/12 in.-only fitting —
         # wrong for every PVC saddle job.
         "red_boxes_fixed": [{"x": 43.3, "y": 140.2, "width": 262.8, "height": 42.9}]},
        {"template": "magmeter_kfactors.pdf",
         "callout_template": "({qty}) {size}\" REQ'D",
         "callout_xy": (319.4, 346.7),
         "box": "kfactor_row"},
        {"template": "magmeter_9900_transmitter.pdf",
         "callout_template": "({qty}) REQ'D",
         "callout_xy": (122.4, 326.1),
         "red_boxes_fixed": [{"x": 176.1, "y": 120.0, "width": 129.5, "height": 188.0}]},
        {"template": "magmeter_9900_specs.pdf", "verbatim": True},
    ],
    "vfd": [
        {"template": "magmeter_intro.pdf", "verbatim": True},
        {"template": "magmeter_dimensions.pdf", "box": "body_band"},
        {"template": "magmeter_ordering_vfd.pdf",
         "callout_template": "({qty}) REQ'D",
         "callout_xy": (235.8, 659.4),
         "box": "order_pinned"},
        {"template": "magmeter_saddles.pdf",
         "callout_template": "({qty}) {size}\" REQ'D",
         "callout_xy": (309.6, 251.4),
         "box": "saddle_row"},
        {"template": "magmeter_fittings.pdf",
         "callout_template": "({qty}) {size}\" REQ'D",
         "callout_xy": (210.0, 166.0),
         "callout_width": 90.0,
         "callout_font_size": 9.0,
         "callout_line_height": 11.0,
         "callout_padding": 3.0,
         "red_boxes_fixed": [{"x": 43.3, "y": 140.2, "width": 262.8, "height": 42.9}]},
        {"template": "magmeter_kfactors.pdf",
         "callout_template": "({qty}) {size}\" REQ'D",
         "callout_xy": (319.4, 346.7),
         "box": "kfactor_row"},
    ],
}

# K-factor rows. The sheet prints K-factors for PP true-union tees, PP clamp-on
# saddles (10 and 12 in. ONLY), PVDF and PVC true-union tees, iron saddles,
# and bronze/copper — but NOT for the PVC-U clamp-on saddles (PV8S0xx) this
# generator defaults to. So for any PVC saddle under 10 in. there is no correct
# row to box, and the page is emitted with its callout and no red box rather
# than boxing a row that doesn't apply (which is what the source file did: it
# boxed the PP clamp-on 10/12 rows on a 6 in. job).
MAGMETER_KFACTOR_ROWS = {
    # PP clamp-on saddles on SCH 80 PP pipe — the only clamp-on rows printed.
    "10": {"x": 38.8, "y": 359.0, "width": 273.5, "height": 12.0},
    "12": {"x": 38.8, "y": 371.0, "width": 273.5, "height": 12.0},
}


MAGMETER_PAGE_SEQUENCE = [
    "magmeter_intro.pdf",
    "magmeter_dimensions.pdf",
    "magmeter_ordering_field_mount.pdf",
    "magmeter_ordering_vfd.pdf",
    "magmeter_saddles.pdf",
    "magmeter_fittings.pdf",
    "magmeter_kfactors.pdf",
    "magmeter_9900_transmitter.pdf",
    "magmeter_9900_specs.pdf",
]

# Placed with the other flow/drive equipment: after the water separator and
# immediately before the VFD block. Move this anchor to move the whole set.
_MAGMETER_ANCHOR = "vfd_no_bypass.pdf"
_at = (PAGE_ORDER.index(_MAGMETER_ANCHOR)
       if _MAGMETER_ANCHOR in PAGE_ORDER else len(PAGE_ORDER))
PAGE_ORDER[_at:_at] = MAGMETER_PAGE_SEQUENCE


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
