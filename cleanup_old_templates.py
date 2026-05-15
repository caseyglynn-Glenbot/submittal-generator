"""
cleanup_old_templates.py

Moves bulky pre-built submittals and compound source files OUT of templates/
into sibling folders, so the deployed Docker image stays small.

What it does:

1. templates/Defender Submittal SP-XX-XX-XXXX 120V N-N-N-N.pdf (16 files,
   ~10MB each) → ../reference_submittals/
   These are complete past submittals, kept for reference but not deployed.

2. templates/Defender submittal {*}.pdf (the 3 files starting with lower-case
   'submittal' that are also complete submittals)
     → ../reference_submittals/

3. Compound source files that have been split → templates/_archive/
   These remain in the repo (in case we need to re-split) but are excluded
   from the Docker build via templates/.dockerignore.

4. The standalone files that have correctly-named duplicates → templates/_archive/

After this script runs, templates/ should contain ONLY the snake_cased
mapping_table.py-targeted files (plus 0.pdf and a few schematics we haven't
remapped yet).

Run from inside your local repo:
    python cleanup_old_templates.py
"""

from pathlib import Path
import shutil
import sys


TEMPLATES_DIR = Path("templates")
REFERENCE_DIR = Path("reference_submittals")
ARCHIVE_DIR = TEMPLATES_DIR / "_archive"


# Filenames starting with "Defender Submittal SP-" (capital S) plus the three
# lowercase "Defender submittal" complete submittals
BULK_SUBMITTAL_FILENAMES = [
    "Defender Submittal SP-27-48-487 120V  6-4-3-3.pdf",
    "Defender Submittal SP-27-48-487 120V  6-6-3-3.pdf",
    "Defender Submittal SP-27-48-487 120V  8-6-3-3.pdf",
    "Defender Submittal SP-33-48-732 120V  8-6-3-3.pdf",  # the master we split from
    "Defender Submittal SP-33-48-732 120V  8-6-4-4.pdf",
    "Defender Submittal SP-33-48-732 120V  8-8-4-4.pdf",
    "Defender Submittal SP-41-48-1038 120V  8-6-4-4.pdf",
    "Defender Submittal SP-41-48-1038 120V  8-8-4-4.pdf",
    "Defender Submittal SP-41-48-1038 120V 10-8-4-4.pdf",
    "Defender Submittal SP-49-48-1548 120V 10-8-4-4.pdf",
    "Defender Submittal SP-49-48-1548 120V 10-8-6-6.pdf",
    "Defender Submittal SP-49-48-1548 120V 12-10-6-6.pdf",
    "Defender Submittal SP-55-48-2076 120V 12-10-6-6.pdf",
    "Defender Submittal SP-55-48-2076 120V 12-10-8-8.pdf",
    "Defender Submittal SP-55-48-2076 120V 14-10-6-6.pdf",
    "Defender Submittal SP-55-48-2076 120V 14-12-8-8.pdf",
    "Defender submittal 120V Reduced Height - NEW LOGO.pdf",
    "Defender submittal SP-27-55-120V D7 Automated - NEW LOGO.pdf",
    "Defender submittal SP-29-120V D7 Automated - NEW LOGO.pdf",
]

# Compound files that we split from. Archived in case we need to re-split.
COMPOUND_SOURCE_FILENAMES = [
    "VALVE KIT.pdf",
    "Precoat valve & Sight glass Information.pdf",
    "PNEUMATIC ACTUATOR PAGE W SAP.pdf",
    "MEDIA PAGES - BLANK - for large SUBMITTALS.pdf",
]

# Standalone files that have been replaced by master-submittal-derived pages.
# These get archived for reference (vintage comparison, etc.).
REPLACED_STANDALONE_FILENAMES = [
    "0.pdf",                                            # → effluent_precoat_valves
    "Bi-Torque dimensions.pdf",                         # → pneumatic_actuator_dimensions
    "Defender controller MICRORMF9.pdf",                # → rmf_programmer (we chose RMF 12)
    "Drain Valve with Extension.pdf",                   # → drain_valve_extension
    "Filter Regulator.pdf",                             # → filter_regulator
    "OLD PROGRAMMER PAGE for submittals - 120V.pdf",    # outdated
    "Pneumatic Actuated Drain Valve.pdf",               # → pneumatic_drain_valve (after split copies it)
    "RMF 12 cut sheet.pdf",                             # → rmf_programmer (after split copies it)
    "SIGHTGLASS PAGE WITH 10IN.pdf",                    # → inline_sightglass
    "System Fill & Drain Valve Cut Sheet.pdf",          # → system_fill_drain_valve
    "Vacuum Transfer Unit -120V.pdf",                   # → vacuum_transfer_unit (text page is master p19)
    "vacuum transfer unit details.pdf",                 # → vacuum_transfer_system (diagram is master p18)
    "Defender Reduced Height Shipping Dimensions.pdf",  # not in mapping_table
    "Defender SP-27-55 Shipping Dimensions.pdf",        # not in mapping_table
    "Defender SP-27-55 Schematic.pdf",                  # standalone version; master p4 already used
    "Defender SP-29 Schematic.pdf",                     # standalone version; copy used as activity placeholder
]


def move_with_log(src: Path, dst: Path, label: str):
    if not src.exists():
        print(f"  · {label}: {src.name} not present (skip)")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    target = dst / src.name
    if target.exists():
        target.unlink()
    shutil.move(str(src), str(target))
    print(f"  → {label}: {src.name}")


def main():
    if not TEMPLATES_DIR.exists():
        print(f"ERROR: {TEMPLATES_DIR} not found. Run this from your repo root.")
        sys.exit(1)

    # Before we touch anything, refuse to run if split hasn't been done yet.
    # We check for one of the split-produced files.
    if not (TEMPLATES_DIR / "flexsol_3000_lining.pdf").exists():
        print("ERROR: split_master_template.py hasn't been run yet "
              "(flexsol_3000_lining.pdf is missing). Run split first.")
        sys.exit(1)

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Moving bulk submittals to {REFERENCE_DIR}/ ===")
    for name in BULK_SUBMITTAL_FILENAMES:
        move_with_log(TEMPLATES_DIR / name, REFERENCE_DIR, "REF")

    print(f"\n=== Archiving compound source files to {ARCHIVE_DIR}/ ===")
    for name in COMPOUND_SOURCE_FILENAMES:
        move_with_log(TEMPLATES_DIR / name, ARCHIVE_DIR, "ARCHIVE")

    print(f"\n=== Archiving replaced standalone files to {ARCHIVE_DIR}/ ===")
    for name in REPLACED_STANDALONE_FILENAMES:
        move_with_log(TEMPLATES_DIR / name, ARCHIVE_DIR, "ARCHIVE")

    # Create a .dockerignore inside templates/ so Docker build skips _archive/
    dockerignore = TEMPLATES_DIR / ".dockerignore"
    dockerignore.write_text("_archive/\n")
    print(f"\nWrote {dockerignore} to exclude _archive/ from Docker build")

    # Summary: what's left in templates/?
    remaining = sorted(p.name for p in TEMPLATES_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() == ".pdf")
    print(f"\n=== templates/ now contains {len(remaining)} PDFs ===")
    for name in remaining:
        size_kb = (TEMPLATES_DIR / name).stat().st_size / 1024
        print(f"  {name} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
