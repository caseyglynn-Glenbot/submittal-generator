"""
test_valve_roles.py — exercises valve_roles against real quote data.

Primary fixture is quote 05043737 (SPLEX MD / Main Line Commercial Pools),
which is the run that exposed the gap: three Defender sections, two of them
with loose valves and no kit.

Run:  python3 test_valve_roles.py
"""

from valve_roles import (
    assign_valve_roles, resolve_section_valve_sizes, parse_size, parse_actuation,
    ROLE_INFLUENT, ROLE_EFFLUENT, ROLE_PRECOAT, ROLE_SYSTEM_FILL,
    ROLE_SIGHTGLASS, ROLE_UNASSIGNED,
)

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}: {got!r}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")


def li(item_no, part_no, description, qty=1, alt=""):
    return {"item_no": item_no, "part_no": part_no,
            "description": description, "qty": qty, "alt_description": alt}


def roles(result):
    return {a.role: f'{a.size_label}"' for a in result.assignments
            if a.role != ROLE_UNASSIGNED}


# ---------------------------------------------------------------- parsing ---
print("\n=== size and actuation parsing ===")
cases = [
    ('VALVE CHECK WAFER DN350 14"', 14, "check"),
    ('VALVE CHECK WAFER DN250 10"', 10, "check"),
    ('VALVE BF DOMINION PA 12"', 12, "pneumatic"),
    ('VALVE BF DOMINION PA 10"', 10, "pneumatic"),
    ('VALVE BF DOMINION GO 10"', 10, "gear"),
    ("VALVE BF DOMINION LO 8", 8, "lever"),          # no inch mark in the quote
    ('VALVE BF DOMINION PA 8"', 8, "pneumatic"),
    ('SIGHTGLASS INLINE 10" PVC 17.94 LNG', 10, "unknown"),
    ("SIGHTGLASS INLINE 8.00 PVC 17.94 LNG", 8, "unknown"),
    ('VALVE BF DOMINION PA 2 1/2"', 2.5, "pneumatic"),
]
for desc, want_size, want_act in cases:
    size = parse_size(desc)
    check(f"size  {desc[:38]:38}", float(size) if size is not None else None, float(want_size))
    if want_act != "unknown":
        check(f"act   {desc[:38]:38}", parse_actuation(desc), want_act)


# ------------------------------------------------- SPLEX competition pool ---
print("\n=== SPLEX 05043737 / COMPETITION POOL (SP-55-48-2076) ===")
competition = [
    li("1", "1001-9810", "Filter System - Defender", 1,
       "COMPETITION POOL Flowrate: 2000 GPM Filter Rate: 1.23 GPM/SF"),
    li("2", "1000-8915", "FILTER DEFENDER SP-55-48-2076", 1),
    li("3", "1001-8106", 'VALVE CHECK WAFER DN350 14"', 2),
    li("4", "1000-5949", 'VALVE BF DOMINION PA 12"', 1),
    li("5", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
    li("6", "1000-6117", 'VALVE BF DOMINION GO 10"', 1),
    li("7", "1003-5848", 'SIGHTGLASS INLINE 10" PVC 17.94 LNG', 1),
    li("10", "1000-5852", "MEDIA, AQUAPERL 25# BG 2.8 FT3", 39),
]
comp = assign_valve_roles(competition, section_label="COMPETITION POOL")
check("applied", comp.applied, True)
check("influent", roles(comp).get(ROLE_INFLUENT), '14"')
check("effluent", roles(comp).get(ROLE_EFFLUENT), '12"')
check("precoat", roles(comp).get(ROLE_PRECOAT), '10"')
check("system fill", roles(comp).get(ROLE_SYSTEM_FILL), None)
check("influent boxed by part no", comp.by_role(ROLE_INFLUENT).box_by_part_no, "1001-8106")
check("influent callout", comp.by_role(ROLE_INFLUENT).callout_text,
      '(2) 14" REQ\'D - COMPETITION POOL')
check("effluent callout", comp.by_role(ROLE_EFFLUENT).callout_text,
      '(1) 12" EFFLUENT REQ\'D - COMPETITION POOL')
check("precoat callout", comp.by_role(ROLE_PRECOAT).callout_text,
      '(1) 10" PRECOAT REQ\'D - COMPETITION POOL')
unassigned = [a for a in comp.assignments if a.role == ROLE_UNASSIGNED]
check("unassigned count", len(unassigned), 1)
check("unassigned is the 10in gear valve", unassigned[0].item_no, "6")
check("10in sightglass has no row on its sheet",
      comp.by_role(ROLE_SIGHTGLASS).box_row_size, None)
print("  warnings:")
for w in comp.warnings:
    print(f"    - {w}")


# ------------------------------------------------------ SPLEX leisure pool ---
print("\n=== SPLEX 05043737 / LEISURE POOL (SP-49-48-1548) ===")
leisure = [
    li("12", "1001-9810", "Filter System - Defender", 1,
       "LEISURE POOL Flowrate: 1286 GPM Filter Rate: 1.06 GPM/SF"),
    li("13", "1000-8912", "FILTER DEFENDER SP-49-48-1548", 1),
    li("14", "1001-8103", 'VALVE CHECK WAFER DN250 10"', 1),
    li("15", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
    li("16", "1000-5947", 'VALVE BF DOMINION PA 8"', 1),
    li("17", "1000-7324", "VALVE BF DOMINION LO 8", 1),
    li("18", "1000-6439", "SIGHTGLASS INLINE 8.00 PVC 17.94 LNG", 1),
]
leis = assign_valve_roles(leisure, section_label="LEISURE POOL")
check("influent", roles(leis).get(ROLE_INFLUENT), '10"')
check("effluent", roles(leis).get(ROLE_EFFLUENT), '10"')     # ties influent, allowed
check("precoat", roles(leis).get(ROLE_PRECOAT), '8"')
check("influent boxed by part no", leis.by_role(ROLE_INFLUENT).box_by_part_no, "1001-8103")
check("sightglass row exists", float(leis.by_role(ROLE_SIGHTGLASS).box_row_size), 8.0)
unassigned = [a for a in leis.assignments if a.role == ROLE_UNASSIGNED]
check("unassigned is the 8in lever valve", unassigned[0].item_no, "17")
print("  warnings:")
for w in leis.warnings:
    print(f"    - {w}")


# ----------------------------------------------------------- SPLEX hot tub ---
print("\n=== SPLEX 05043737 / HOT TUB (SP-29-36-350) — kit present ===")
hot_tub = [
    li("22", "1001-9810", "Filter System - Defender", 1, "HOT TUB Flowrate: 255 GPM"),
    li("23", "1000-8898", "FILTER DEFENDER SP-29-36-350-A", 1),
    li("24", "1002-1943", "DEFENDER VALVE KIT AUTO IMPERIAL 4/4/4", 1,
       '4" Influent Check Wafer Style Valve 4" Effluent Pneumatic Double Acting '
       'Actuator 4" Precoat Pneumatic Double Acting Actuator 4" System Fill Lever '
       'Operated Dominion Wafer Style Butterfly Valve 4" In-Line Sightglass'),
]
tub = assign_valve_roles(hot_tub, section_label="HOT TUB")
check("kit short-circuits the rule", tub.applied, False)
check("kit flagged", tub.skipped_kit, True)


# ------------------------------------------------------------- edge cases ---
print("\n=== edge cases ===")

# System fill is always 3 or 4 inches.
fill = assign_valve_roles([
    li("1", "1001-8100", 'VALVE CHECK WAFER DN150 6"', 1),
    li("2", "1000-5946", 'VALVE BF DOMINION PA 6"', 1),
    li("3", "1000-5945", 'VALVE BF DOMINION PA 4"', 1),
    li("4", "1000-7322", 'VALVE BF DOMINION LO 4"', 1),
    li("5", "1000-6435", 'SIGHTGLASS INLINE 4" PVC', 1),
], section_label="POOL")
check("4in lever becomes system fill", roles(fill).get(ROLE_SYSTEM_FILL), '4"')
check("effluent still the large PA", roles(fill).get(ROLE_EFFLUENT), '6"')
check("precoat still the small PA", roles(fill).get(ROLE_PRECOAT), '4"')

# Sightglass overrides size rank when the precoat is not the second largest.
override = assign_valve_roles([
    li("1", "1001-8104", 'VALVE CHECK WAFER DN300 12"', 1),
    li("2", "1000-5949", 'VALVE BF DOMINION PA 12"', 1),
    li("3", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
    li("4", "1000-5947", 'VALVE BF DOMINION PA 8"', 1),
    li("5", "1000-6439", 'SIGHTGLASS INLINE 8" PVC', 1),
], section_label="OVERRIDE")
check("sightglass pulls precoat to 8in", roles(override).get(ROLE_PRECOAT), '8"')
check("effluent stays largest PA", roles(override).get(ROLE_EFFLUENT), '12"')
check("the 10in PA is reported, not guessed",
      [a.size_label for a in override.assignments if a.role == ROLE_UNASSIGNED], ["10"])

# No check valve quoted: fall back to largest, and say so.
nocheck = assign_valve_roles([
    li("1", "1000-5949", 'VALVE BF DOMINION PA 12"', 1),
    li("2", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
], section_label="NOCHECK")
check("largest becomes influent", roles(nocheck).get(ROLE_INFLUENT), '12"')
check("warns about the fallback",
      any("no check valve" in w for w in nocheck.warnings), True)

# Only one pneumatic valve: assign the effluent, flag the missing precoat.
single = assign_valve_roles([
    li("1", "1001-8103", 'VALVE CHECK WAFER DN250 10"', 1),
    li("2", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
], section_label="SINGLE")
check("effluent assigned", roles(single).get(ROLE_EFFLUENT), '10"')
check("precoat absent", roles(single).get(ROLE_PRECOAT), None)
check("warns about the missing precoat",
      any("precoat not assigned" in w for w in single.warnings), True)

# Precoat larger than effluent is legal but called out.
inverted = assign_valve_roles([
    li("1", "1001-8103", 'VALVE CHECK WAFER DN250 10"', 1),
    li("2", "1000-5947", 'VALVE BF DOMINION PA 8"', 1),
    li("3", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
    li("4", "1000-6291", 'SIGHTGLASS INLINE 10" PVC', 1),
], section_label="INVERTED")
check("override can invert the pair",
      (roles(inverted).get(ROLE_EFFLUENT), roles(inverted).get(ROLE_PRECOAT)), ('8"', '10"'))
check("inversion is warned",
      any("larger than" in w for w in inverted.warnings), True)

# Non-service valves must not be picked up.
noise = assign_valve_roles([
    li("1", "1001-8106", 'VALVE CHECK WAFER DN350 14"', 1),
    li("2", "1000-5949", 'VALVE BF DOMINION PA 12"', 1),
    li("3", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
    li("4", "1000-5527", 'FILTER /REGULATOR 1/2" NPT CONNECTION', 1),
    li("5", "1000-7455", 'BALL VALVE 1 1/2" TRUE UNION', 2),
    li("6", "1000-7456", 'VACUUM VENT VALVE 1 1/2"', 1),
], section_label="NOISE")
check("regulator, ball and vacuum valves ignored",
      len([a for a in noise.assignments]), 3)

# --------------------------------------------------- extra-valve labels ---
print("\n=== extra valves listed by name ===")
from valve_roles import descriptive_name

check("gear operated label", descriptive_name('VALVE BF DOMINION GO 10"'),
      "DOMINION GEAR OP BUTTERFLY VALVE")
check("lever operated label", descriptive_name("VALVE BF DOMINION LO 8"),
      "DOMINION LEVER OP BUTTERFLY VALVE")
check("check valve label", descriptive_name('VALVE CHECK WAFER DN350 14"'),
      "WAFER CHECK VALVE")
check("unmapped tokens survive", descriptive_name('VALVE BF ACME XYZ 6"'),
      "ACME XYZ BUTTERFLY VALVE")
check("never returns empty", descriptive_name('VALVE 6"'), "VALVE")

# The extras carry everything the page needs: qty, size, a row key and a name.
extras = {a["part_no"]: a for a in
          resolve_section_valve_sizes(competition, "COMPETITION POOL")[0]["_unassigned"]}
check("one extra on competition", list(extras), ["1000-6117"])
e = extras["1000-6117"]
check("extra qty", e["qty"], 1)
check("extra size label", e["size"], "10")
check("extra row key matches pinned_rows", str(e["size_in"]), "10")
check("extra name", e["name"], "DOMINION GEAR OP BUTTERFLY VALVE")

leis_extras = resolve_section_valve_sizes(leisure, "LEISURE POOL")[0]["_unassigned"]
check("one extra on leisure", len(leis_extras), 1)
check("leisure extra line",
      f'({leis_extras[0]["qty"]}) {leis_extras[0]["size"]}" {leis_extras[0]["name"]}',
      '(1) 8" DOMINION LEVER OP BUTTERFLY VALVE')

# A section with nothing left over reports no extras.
check("no extras when every valve has a role",
      resolve_section_valve_sizes([
          li("1", "1001-8103", 'VALVE CHECK WAFER DN250 10"', 1),
          li("2", "1000-5948", 'VALVE BF DOMINION PA 10"', 1),
          li("3", "1000-5947", 'VALVE BF DOMINION PA 8"', 1),
          li("4", "1000-6439", 'SIGHTGLASS INLINE 8" PVC', 1),
      ], "CLEAN")[0]["_unassigned"], [])

print(f"\n{'='*58}\n{PASS} passed, {FAIL} failed\n{'='*58}")
raise SystemExit(1 if FAIL else 0)
