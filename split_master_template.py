"""
split_master_template.py

Splits the master 25-page Defender submittal into individual
mapping_table-named PDFs, plus splits MEDIA PAGES into perlite + filter_cleaner.

Run from inside your local repo, with the existing templates/ folder present.
Will write into templates/, overwriting any existing files with matching names.

Usage:
    cd <repo root>
    python split_master_template.py
"""

from pathlib import Path
import shutil
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF (fitz) is not installed.")
    print("Install with: pip install pymupdf==1.24.10")
    sys.exit(1)


TEMPLATES_DIR = Path("templates")

# Source files (must exist in templates/ before running)
MASTER_SUBMITTAL = TEMPLATES_DIR / "Defender Submittal SP-33-48-732 120V  8-6-3-3.pdf"
MEDIA_PAGES = TEMPLATES_DIR / "MEDIA PAGES - BLANK - for large SUBMITTALS.pdf"
RMF_12 = TEMPLATES_DIR / "RMF 12 cut sheet.pdf"

# Map of master-submittal page number (1-based) -> output filename
# Derived from inspection of SP-33-48-732 8-6-3-3 submittal.
MASTER_PAGE_MAP = {
    4:  "defender_filter_schematic_lap.pdf",      # IMPERIAL schematic
    6:  "flexsol_3000_lining.pdf",
    7:  "dominion_butterfly_valves.pdf",
    8:  "influent_check_valve.pdf",
    9:  "effluent_precoat_valves.pdf",
    10: "pneumatic_actuator_specs.pdf",
    11: "pneumatic_actuator_dimensions.pdf",
    12: "system_fill_drain_valve.pdf",
    13: "drain_valve_extension.pdf",
    14: "inline_sightglass.pdf",
    15: "gauge_panel.pdf",
    # 16: skipped — using RMF 12 cut sheet.pdf instead per user choice
    # 17: filter_regulator — also in standalone Filter Regulator.pdf, but the
    #     master submittal page is identical, so we use it for consistency
    17: "filter_regulator.pdf",
    18: "vacuum_transfer_system.pdf",
    19: "vacuum_transfer_unit.pdf",
    20: "compressor.pdf",
    21: "water_separator.pdf",
    22: "tool_kit.pdf",
}

# Map of MEDIA PAGES page -> output filename
MEDIA_PAGE_MAP = {
    1: "perlite.pdf",
    2: "filter_cleaner.pdf",
}


def extract_page(src_doc: fitz.Document, page_num: int, out_path: Path):
    """Extract one page (1-based) from src_doc into a new single-page PDF."""
    out_doc = fitz.open()
    # PyMuPDF page indices are 0-based; convert
    out_doc.insert_pdf(src_doc, from_page=page_num - 1, to_page=page_num - 1)
    out_doc.save(str(out_path))
    out_doc.close()


def main():
    if not TEMPLATES_DIR.exists():
        print(f"ERROR: {TEMPLATES_DIR} not found. Run this from your repo root.")
        sys.exit(1)

    failures = []

    # ------------------------------------------------------------------
    # 1. Split master submittal
    # ------------------------------------------------------------------
    if not MASTER_SUBMITTAL.exists():
        print(f"ERROR: master submittal not found at {MASTER_SUBMITTAL}")
        sys.exit(1)

    print(f"\n=== Splitting {MASTER_SUBMITTAL.name} ===")
    master = fitz.open(str(MASTER_SUBMITTAL))
    if len(master) != 25:
        print(f"WARNING: expected 25 pages, got {len(master)}. "
              f"Page map may be wrong. Continuing anyway.")

    for page_num, out_name in MASTER_PAGE_MAP.items():
        out_path = TEMPLATES_DIR / out_name
        try:
            extract_page(master, page_num, out_path)
            size_kb = out_path.stat().st_size / 1024
            print(f"  p{page_num:2d} -> {out_name} ({size_kb:.0f} KB)")
        except Exception as e:
            print(f"  p{page_num:2d} -> {out_name} FAILED: {e}")
            failures.append((out_name, str(e)))
    master.close()

    # Also extract page 4 a second time as the Assero/Activity schematic.
    # Note: page 4 in this submittal is the SP-33 imperial schematic; for a
    # cleaner solution you'd source the Assero schematic from a different
    # master submittal (e.g. Defender submittal SP-29-120V D7 Automated).
    # For now we duplicate the imperial one; rename it manually later if needed.
    activity_path = TEMPLATES_DIR / "defender_filter_schematic_activity.pdf"
    shutil.copyfile(TEMPLATES_DIR / "defender_filter_schematic_lap.pdf",
                    activity_path)
    print(f"  (copied to defender_filter_schematic_activity.pdf as placeholder)")

    # ------------------------------------------------------------------
    # 2. Split MEDIA PAGES
    # ------------------------------------------------------------------
    if MEDIA_PAGES.exists():
        print(f"\n=== Splitting {MEDIA_PAGES.name} ===")
        media = fitz.open(str(MEDIA_PAGES))
        for page_num, out_name in MEDIA_PAGE_MAP.items():
            out_path = TEMPLATES_DIR / out_name
            try:
                extract_page(media, page_num, out_path)
                size_kb = out_path.stat().st_size / 1024
                print(f"  p{page_num} -> {out_name} ({size_kb:.0f} KB)")
            except Exception as e:
                print(f"  p{page_num} -> {out_name} FAILED: {e}")
                failures.append((out_name, str(e)))
        media.close()
    else:
        print(f"\nWARNING: {MEDIA_PAGES.name} not found, skipping media pages")

    # ------------------------------------------------------------------
    # 3. Copy RMF 12 cut sheet to mapping_table name
    # ------------------------------------------------------------------
    if RMF_12.exists():
        out_path = TEMPLATES_DIR / "rmf_programmer.pdf"
        shutil.copyfile(RMF_12, out_path)
        size_kb = out_path.stat().st_size / 1024
        print(f"\n=== RMF programmer ===")
        print(f"  RMF 12 cut sheet.pdf -> rmf_programmer.pdf ({size_kb:.0f} KB)")
    else:
        print(f"\nWARNING: {RMF_12.name} not found, rmf_programmer.pdf not created")
        failures.append(("rmf_programmer.pdf", "RMF 12 cut sheet.pdf missing"))

    # ------------------------------------------------------------------
    # 4. Use existing standalone files for ones already correctly sourced
    # ------------------------------------------------------------------
    # The standalone "Pneumatic Actuated Drain Valve.pdf" isn't in the master
    # submittal but is a useful future page. Rename it.
    pneumatic_drain_src = TEMPLATES_DIR / "Pneumatic Actuated Drain Valve.pdf"
    if pneumatic_drain_src.exists():
        out_path = TEMPLATES_DIR / "pneumatic_drain_valve.pdf"
        shutil.copyfile(pneumatic_drain_src, out_path)
        print(f"\n=== Optional accessories ===")
        print(f"  Pneumatic Actuated Drain Valve.pdf -> pneumatic_drain_valve.pdf")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    expected = set(MASTER_PAGE_MAP.values()) | set(MEDIA_PAGE_MAP.values()) | {
        "rmf_programmer.pdf",
        "defender_filter_schematic_activity.pdf",
    }
    present = {p.name for p in TEMPLATES_DIR.iterdir()
               if p.name in expected and p.is_file()}
    missing = expected - present

    print(f"\n=== Summary ===")
    print(f"  Expected: {len(expected)} mapping_table-named files")
    print(f"  Present:  {len(present)}")
    if missing:
        print(f"  MISSING:")
        for m in sorted(missing):
            print(f"    - {m}")
    if failures:
        print(f"  FAILURES:")
        for name, err in failures:
            print(f"    - {name}: {err}")
    if not missing and not failures:
        print(f"  ✓ All mapping_table-targeted files are in place.")

    print(f"\nNext step: run cleanup_old_templates.py to move out the originals.")


if __name__ == "__main__":
    main()
