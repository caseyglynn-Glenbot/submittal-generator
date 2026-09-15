"""
valve_roles.py — Defender loose-valve role inference.

Purpose
-------
A Defender filter system always needs four service valves: INFLUENT, EFFLUENT,
PRECOAT and SYSTEM FILL. Most quotes ship them as a single "DEFENDER VALVE KIT
AUTO IMPERIAL a/b/c" line, which the existing kit path already handles.

Some customers instead quote the valves as individual line items. Before this
module those loose valves produced no callouts and no red boxes, so any
Defender section without a kit came out of the generator unannotated.

Rule (per Defender section, only when no kit line is present)
------------------------------------------------------------
  INFLUENT     the check valve. If several, the largest. If none, largest valve.
  EFFLUENT     largest pneumatically actuated (PA) butterfly valve.
  PRECOAT      next pneumatically actuated valve down.
               Typically 1-2 sizes below the effluent, but not guaranteed, so
               size rank is only the first pass -- see the sightglass override.
  SYSTEM FILL  a manually operated (LO/GO) valve of 3" or 4". Always that size.
  OTHER        any remaining valve keeps NO service role. It is not guessed at:
               it is listed on the effluent/precoat page by its own name from
               the quote -- '(qty) size" NAME' -- and its size row is boxed when
               the sheet carries one, so the reviewer sees every valve quoted.

Sightglass override
-------------------
The in-line sightglass mounts on the precoat line (schematic D10/D11; the
sightglass cut sheet states it explicitly). Its size is therefore a direct
read of the precoat size and outranks the size ordering. If the section quotes
a sightglass whose size matches a pneumatic valve other than the one size rank
picked, that valve becomes the precoat and the ranking is redone around it.

Integration
-----------
    from valve_roles import assign_valve_roles

    result = assign_valve_roles(section.line_items, section_label=section.label)
    if result.applied:
        for a in result.assignments:
            # a.role, a.size_in, a.qty, a.part_no, a.callout_text, a.page_key
            ...
        run_report.extend(result.warnings)

`assign_valve_roles` is pure: it reads line-item dicts and returns data. It does
no PDF work, so it can be unit tested without templates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterable, Sequence


# --------------------------------------------------------------------------
# Roles and target pages
# --------------------------------------------------------------------------

ROLE_INFLUENT = "influent"
ROLE_EFFLUENT = "effluent"
ROLE_PRECOAT = "precoat"
ROLE_SYSTEM_FILL = "system_fill"
ROLE_DRAIN = "drain"
ROLE_SIGHTGLASS = "sightglass"
ROLE_UNASSIGNED = "unassigned"

# Cut sheet each role annotates. Keys match the existing PAGE_ORDER slots.
ROLE_PAGE_KEY = {
    ROLE_INFLUENT: "influent_check_valve",
    ROLE_EFFLUENT: "pneumatic_valves",
    ROLE_PRECOAT: "pneumatic_valves",
    ROLE_SYSTEM_FILL: "system_fill_drain_valve",
    ROLE_DRAIN: "system_fill_drain_valve",
    ROLE_SIGHTGLASS: "sightglass",
    ROLE_UNASSIGNED: None,
}

# Size rows that actually exist on each sheet, so a role can never be boxed
# against a row that is not printed. Values are inches.
#   influent_check_valve    p9  DN80..DN400
#   pneumatic_valves        p10 2..12
#   system_fill_drain_valve p13 2, 2.5, 3, 4, 5, 6, 8
#   sightglass              p14 2, 3, 4, 6, 8
SHEET_SIZE_ROWS = {
    "influent_check_valve": [3, 4, 5, 6, 8, 10, 12, 14, 16],
    "pneumatic_valves": [2, 3, 4, 6, 8, 10, 12],
    "system_fill_drain_valve": [2, Fraction(5, 2), 3, 4, 5, 6, 8],
    "sightglass": [2, 3, 4, 6, 8],
}

SYSTEM_FILL_SIZES = {3, 4}


# --------------------------------------------------------------------------
# Description parsing
# --------------------------------------------------------------------------

# Nominal bore to inches, as printed on the influent check valve sheet.
DN_TO_INCHES = {
    80: 3, 100: 4, 125: 5, 150: 6, 200: 8,
    250: 10, 300: 12, 350: 14, 400: 16,
}

_DN_RE = re.compile(r"\bDN\s*(\d{2,3})\b", re.I)

# Trailing size token. Handles: 14"  12  8.00  2 1/2  10" PVC
_SIZE_RE = re.compile(
    r"""(?<![\w.\-])
        (?P<whole>\d{1,2})
        (?:\s+(?P<num>\d)\s*/\s*(?P<den>\d))?       # 2 1/2
        (?:\.(?P<dec>\d{1,2}))?                     # 8.00
        \s*(?:"|''|\bIN\b|\bINCH(?:ES)?\b)?
    """,
    re.I | re.X,
)

ACT_PNEUMATIC = "pneumatic"
ACT_GEAR = "gear"
ACT_LEVER = "lever"
ACT_CHECK = "check"
ACT_UNKNOWN = "unknown"

# Order matters: check is detected from the valve style, not an actuation code.
_ACTUATION_PATTERNS = [
    (ACT_CHECK, re.compile(r"\bCHECK\b", re.I)),
    (ACT_PNEUMATIC, re.compile(r"\b(PA|PNEUMATIC|DOUBLE\s+ACTING|ACTUAT)", re.I)),
    (ACT_GEAR, re.compile(r"\b(GO|GEAR\s*OPERATED)\b", re.I)),
    (ACT_LEVER, re.compile(r"\b(LO|LEVER\s*OPERATED|HANDLE)\b", re.I)),
]

_VALVE_RE = re.compile(r"\bVALVE\b|\bVLV\b", re.I)
_SIGHTGLASS_RE = re.compile(r"\bSIGHT\s*GLASS\b|\bSIGHTGLASS\b", re.I)
_KIT_RE = re.compile(r"\bVALVE\s+KIT\b", re.I)

# Things that read like valves but are not Defender service valves.
_EXCLUDE_RE = re.compile(
    r"\bVACUUM\s+(VENT|TRANSFER|HOSE)\b"
    r"|\bBALL\s+VALVE\b"
    r"|\bQUICK\s+EXHAUST\b"
    r"|\bRELIEF\b"
    r"|\bSOLENOID\b"
    r"|\bREGULATOR\b",
    re.I,
)


def _to_inches(whole: str, num: str | None, den: str | None, dec: str | None):
    """Return an int where possible, a Fraction for halves, else a float."""
    value = int(whole)
    if num and den and int(den):
        return Fraction(value) + Fraction(int(num), int(den))
    if dec is not None:
        frac = float(f"0.{dec}")
        if frac == 0.0:
            return value
        as_fraction = Fraction(f"{value}.{dec}").limit_denominator(16)
        if as_fraction.denominator <= 4:
            return as_fraction
        return float(f"{value}.{dec}")
    return value


def parse_size(text: str):
    """Pull a nominal size in inches out of a quote description.

    DN notation wins when present because it is unambiguous:
        'VALVE CHECK WAFER DN350 14"'   -> 14
        'VALVE BF DOMINION LO 8'        -> 8     (no inch mark on this one)
        'SIGHTGLASS INLINE 8.00 PVC'    -> 8
        'VALVE BF DOMINION PA 2 1/2"'   -> Fraction(5, 2)
    Returns None when no size can be read.
    """
    if not text:
        return None

    dn = _DN_RE.search(text)
    if dn:
        bore = int(dn.group(1))
        if bore in DN_TO_INCHES:
            return DN_TO_INCHES[bore]

    # Strip tokens that carry digits but are never the size.
    cleaned = re.sub(r"\b\d{3,4}-\d{3,4}\b", " ", text)          # SAP part nos
    cleaned = re.sub(r"\bDN\s*\d+\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d+(\.\d+)?\s*LNG\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bSCH\.?\s*\d+\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d+\s*(HP|PH|V|LB|LBS|GPM|PSI)\b", " ", cleaned, flags=re.I)

    best = None
    for match in _SIZE_RE.finditer(cleaned):
        size = _to_inches(
            match.group("whole"), match.group("num"),
            match.group("den"), match.group("dec"),
        )
        if size is None or not (1 <= float(size) <= 24):
            continue
        # Prefer a size carrying an explicit inch mark.
        explicit = bool(re.match(r'\s*(?:"|\'\'|IN\b|INCH)', cleaned[match.end():], re.I))
        rank = (1 if explicit else 0, float(size))
        if best is None or rank > best[0]:
            best = (rank, size)
    return best[1] if best else None


def parse_actuation(text: str) -> str:
    if not text:
        return ACT_UNKNOWN
    for name, pattern in _ACTUATION_PATTERNS:
        if pattern.search(text):
            return name
    return ACT_UNKNOWN


# Abbreviations as they appear in NB quote descriptions, expanded to the
# wording the cut sheets themselves use ("LEVER OPERATED" is printed on the
# system fill sheet). Unknown tokens are kept verbatim, so a description this
# map does not cover still produces a readable label rather than a blank.
_VALVE_ABBREV = {
    "BF": "BUTTERFLY",
    "PA": "PNEUMATIC",
    "GO": "GEAR OP",
    "LO": "LEVER OP",
    "CK": "CHECK",
    "FG": "FIBERGLASS",
    "SS": "STAINLESS STEEL",
    "PVC": "PVC",
}

# Tokens carrying no meaning once the size is shown separately.
_LABEL_DROP = {"VALVE", "INLINE", "IN-LINE", "LNG"}


def descriptive_name(description: str) -> str:
    """Turn a quote description into a callout label for an extra valve.

    'VALVE BF DOMINION GO 10"'   -> 'DOMINION GEAR OP BUTTERFLY VALVE'
    'VALVE BF DOMINION LO 8'     -> 'DOMINION LEVER OP BUTTERFLY VALVE'
    'VALVE CHECK WAFER DN350 14"'-> 'CHECK WAFER VALVE'

    The word VALVE is moved to the end so the line reads as a name after the
    size, and the style token (BUTTERFLY / CHECK) is placed last before it.
    """
    text = (description or "").upper()
    text = _DN_RE.sub(" ", text)
    text = re.sub(r"\b\d{3,4}-\d{3,4}\b", " ", text)                # part numbers
    text = re.sub(r"\b\d+(?:\s+\d/\d)?(?:\.\d+)?\s*(?:\"|''|IN\b)?", " ", text)
    text = re.sub(r"[^A-Z0-9/\- ]+", " ", text)

    style, motion, rest = [], [], []
    for token in text.split():
        if token in _LABEL_DROP:
            continue
        word = _VALVE_ABBREV.get(token, token)
        if word in ("BUTTERFLY", "CHECK"):
            style.append(word)
        elif word in ("PNEUMATIC", "GEAR OP", "LEVER OP"):
            motion.append(word)
        elif word not in rest:
            rest.append(word)

    parts = rest + motion + style
    if not parts:
        return "VALVE"
    # De-dup while preserving order (descriptions sometimes repeat a token).
    seen, ordered = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return " ".join(ordered) + " VALVE"


def _size_label(size) -> str:
    """Render a size the way the cut sheets print it: 4, 2 1/2, 10."""
    if isinstance(size, Fraction):
        if size.denominator == 1:
            return str(size.numerator)
        whole, rem = divmod(size.numerator, size.denominator)
        return f"{whole} {rem}/{size.denominator}" if whole else f"{rem}/{size.denominator}"
    if isinstance(size, float) and size.is_integer():
        return str(int(size))
    return str(size)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class ValveCandidate:
    item_no: str
    part_no: str
    description: str
    qty: int
    size_in: object
    actuation: str
    is_sightglass: bool = False

    @property
    def size_label(self) -> str:
        return _size_label(self.size_in)


@dataclass
class ValveAssignment:
    role: str
    item_no: str
    part_no: str
    description: str
    qty: int
    size_in: object
    actuation: str
    section_label: str
    page_key: str | None
    callout_text: str
    box_row_size: object | None   # None when the sheet has no row for this size
    box_by_part_no: str | None    # preferred key on the check valve sheet

    @property
    def size_label(self) -> str:
        return _size_label(self.size_in)


@dataclass
class ValveRoleResult:
    applied: bool
    section_label: str
    assignments: list[ValveAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_kit: bool = False

    def by_role(self, role: str) -> ValveAssignment | None:
        for a in self.assignments:
            if a.role == role:
                return a
        return None


# --------------------------------------------------------------------------
# Candidate extraction
# --------------------------------------------------------------------------

def _description_of(item) -> str:
    """Description text for matching.

    Works with both quote_parser.LineItem (a single `description` field) and
    plain dicts carrying an optional alternative-description field.
    """
    if isinstance(item, dict):
        parts = [
            item.get("description") or "",
            item.get("alt_description") or item.get("alternative_description") or "",
        ]
        return " ".join(p for p in parts if p).strip()
    return " ".join(
        p for p in (
            getattr(item, "description", "") or "",
            getattr(item, "alt_description", "") or "",
        ) if p
    ).strip()


def _field(item, *names, default=""):
    for name in names:
        if isinstance(item, dict):
            if item.get(name) not in (None, ""):
                return item[name]
        else:
            value = getattr(item, name, None)
            if value not in (None, ""):
                return value
    return default


def section_has_valve_kit(line_items: Iterable) -> bool:
    return any(_KIT_RE.search(_description_of(i)) for i in line_items)


def extract_valve_candidates(line_items: Iterable) -> list[ValveCandidate]:
    candidates: list[ValveCandidate] = []
    for item in line_items:
        desc = _description_of(item)
        if not desc or _KIT_RE.search(desc) or _EXCLUDE_RE.search(desc):
            continue

        is_sg = bool(_SIGHTGLASS_RE.search(desc))
        if not is_sg and not _VALVE_RE.search(desc):
            continue

        size = parse_size(desc)
        if size is None:
            continue

        try:
            qty = int(float(_field(item, "qty", "quantity", default=1) or 1))
        except (TypeError, ValueError):
            qty = 1

        ident = str(_field(item, "item_no", "item", "line_no", default=""))
        candidates.append(ValveCandidate(
            # quote_parser.LineItem carries no line number, so fall back to the
            # part number — review messages have to name something findable.
            item_no=ident or str(_field(item, "part_no", "part_number", default="")),
            part_no=str(_field(item, "part_no", "part_number", "part", default="")),
            description=desc,
            qty=max(qty, 1),
            size_in=size,
            actuation=ACT_UNKNOWN if is_sg else parse_actuation(desc),
            is_sightglass=is_sg,
        ))
    return candidates


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------

def _rank(candidates: Sequence[ValveCandidate]) -> list[ValveCandidate]:
    return sorted(candidates, key=lambda c: float(c.size_in), reverse=True)


def _row_for(page_key: str | None, size) -> object | None:
    if page_key is None:
        return None
    rows = SHEET_SIZE_ROWS.get(page_key)
    if not rows:
        return None
    for row in rows:
        if float(row) == float(size):
            return row
    return None


def _callout(role: str, size_label: str, qty: int, section_label: str) -> str:
    role_word = {
        ROLE_INFLUENT: "",
        ROLE_EFFLUENT: "EFFLUENT ",
        ROLE_PRECOAT: "PRECOAT ",
        ROLE_SYSTEM_FILL: "SYSTEM FILL ",
        ROLE_DRAIN: "DRAIN VALVE ",
        ROLE_SIGHTGLASS: "",
    }.get(role, "")
    suffix = f" - {section_label}" if section_label else ""
    return f'({qty}) {size_label}" {role_word}REQ\'D{suffix}'


def _make(role: str, c: ValveCandidate, section_label: str) -> ValveAssignment:
    page_key = ROLE_PAGE_KEY.get(role)
    return ValveAssignment(
        role=role,
        item_no=c.item_no,
        part_no=c.part_no,
        description=c.description,
        qty=c.qty,
        size_in=c.size_in,
        actuation=c.actuation,
        section_label=section_label,
        page_key=page_key,
        callout_text=_callout(role, c.size_label, c.qty, section_label),
        box_row_size=_row_for(page_key, c.size_in),
        box_by_part_no=c.part_no if role == ROLE_INFLUENT and c.part_no else None,
    )


def assign_valve_roles(
    line_items: Iterable,
    section_label: str = "",
    *,
    force: bool = False,
) -> ValveRoleResult:
    """Assign Defender service-valve roles to loose valve line items.

    Returns applied=False (and does nothing) when the section already carries a
    DEFENDER VALVE KIT line, unless force=True.
    """
    items = list(line_items)
    label = (section_label or "").strip().upper()
    result = ValveRoleResult(applied=False, section_label=label)

    if section_has_valve_kit(items) and not force:
        result.skipped_kit = True
        return result

    candidates = extract_valve_candidates(items)
    if not candidates:
        return result

    sightglasses = [c for c in candidates if c.is_sightglass]
    valves = [c for c in candidates if not c.is_sightglass]
    if not valves:
        return result

    result.applied = True
    remaining = list(valves)

    # --- INFLUENT: check valve wins, largest if several, else largest valve ---
    checks = _rank([c for c in remaining if c.actuation == ACT_CHECK])
    if checks:
        influent = checks[0]
        for extra in checks[1:]:
            result.warnings.append(
                f"{label or 'section'}: extra check valve item {extra.item_no} "
                f'({extra.size_label}") not assigned; only one influent per Defender.'
            )
            remaining.remove(extra)
    else:
        influent = _rank(remaining)[0]
        result.warnings.append(
            f"{label or 'section'}: no check valve quoted, using largest valve "
            f'(item {influent.item_no}, {influent.size_label}") as influent.'
        )
    remaining.remove(influent)
    result.assignments.append(_make(ROLE_INFLUENT, influent, label))

    # --- SYSTEM FILL: manual valve at 3" or 4", always ---
    manual = [c for c in remaining if c.actuation in (ACT_LEVER, ACT_GEAR)]
    fill_candidates = [c for c in manual if float(c.size_in) in SYSTEM_FILL_SIZES]
    if fill_candidates:
        system_fill = _rank(fill_candidates)[0]
        remaining.remove(system_fill)
        result.assignments.append(_make(ROLE_SYSTEM_FILL, system_fill, label))

    # --- EFFLUENT and PRECOAT: pneumatic valves, largest first ---
    pneumatic = _rank([c for c in remaining if c.actuation == ACT_PNEUMATIC])

    if not pneumatic:
        result.warnings.append(
            f"{label or 'section'}: no pneumatically actuated valves found; "
            "effluent and precoat not assigned."
        )
    else:
        precoat = None

        # Sightglass override: the sightglass sits on the precoat line, so its
        # size is a direct read of the precoat size and outranks size order.
        if sightglasses:
            sg = _rank(sightglasses)[0]
            matches = [c for c in pneumatic if float(c.size_in) == float(sg.size_in)]
            if matches:
                precoat = matches[-1]     # smallest match, if the size repeats
                if len(pneumatic) > 1 and precoat is pneumatic[0]:
                    result.warnings.append(
                        f"{label or 'section'}: sightglass ({sg.size_label}\") matches the "
                        f"largest pneumatic valve; precoat and effluent may be reversed - "
                        "confirm before release."
                    )
            elif pneumatic:
                result.warnings.append(
                    f'{label or "section"}: sightglass is {sg.size_label}" but no '
                    "pneumatic valve matches that size; precoat assigned by size rank."
                )

        if precoat is None and len(pneumatic) >= 2:
            precoat = pneumatic[1]

        effluent_pool = [c for c in pneumatic if c is not precoat]
        effluent = effluent_pool[0] if effluent_pool else None

        if effluent is not None:
            remaining.remove(effluent)
            result.assignments.append(_make(ROLE_EFFLUENT, effluent, label))
        if precoat is not None:
            remaining.remove(precoat)
            result.assignments.append(_make(ROLE_PRECOAT, precoat, label))

        if effluent is None or precoat is None:
            missing = "precoat" if effluent is not None else "effluent"
            result.warnings.append(
                f"{label or 'section'}: only {len(pneumatic)} pneumatic valve(s) quoted; "
                f"{missing} not assigned."
            )
        elif float(precoat.size_in) > float(effluent.size_in):
            result.warnings.append(
                f'{label or "section"}: precoat ({precoat.size_label}") is larger than '
                f'effluent ({effluent.size_label}") - unusual, confirm before release.'
            )

        for extra in pneumatic:
            if extra in remaining:
                remaining.remove(extra)
                result.assignments.append(_make(ROLE_UNASSIGNED, extra, label))
                result.warnings.append(
                    f"{label or 'section'}: extra pneumatic valve item {extra.item_no} "
                    f'({extra.size_label}") beyond effluent and precoat - role undetermined.'
                )

    # --- Sightglass gets its own callout regardless ---
    for sg in _rank(sightglasses):
        result.assignments.append(_make(ROLE_SIGHTGLASS, sg, label))

    # --- Leftover manual valves above 4": reported, never guessed ---
    for leftover in _rank(remaining):
        result.assignments.append(_make(ROLE_UNASSIGNED, leftover, label))
        result.warnings.append(
            f"{label or 'section'}: item {leftover.item_no} "
            f'({leftover.size_label}" {leftover.actuation}) - manual valve, role '
            "undetermined; listed by name on the effluent/precoat page for review."
        )

    # --- Rows that do not exist on the target sheet ---
    for a in result.assignments:
        if a.page_key and a.box_row_size is None:
            result.warnings.append(
                f'{label or "section"}: {a.role} is {a.size_label}" but the '
                f"{a.page_key} sheet has no row for that size - callout will be "
                "placed, red box skipped."
            )

    return result


# --------------------------------------------------------------------------
# Adapter for the VALVE_KIT_PAGES pipeline
# --------------------------------------------------------------------------

def resolve_section_valve_sizes(line_items: Iterable, section_label: str = ""):
    """Return a kit-shaped sizes dict for a section that has loose valves.

    The valve-kit page loop in orchestrator.py is driven by
    `kits_by_section[section] = {"influent", "effluent", "precoat",
    "sightglass"}` from parse_valve_kit_sizes(). This produces the same shape
    from individually quoted valves, plus the extras the loose path can supply
    that a kit string cannot:

        system_fill     always 3 or 4 (Casey's rule), or None if not quoted
        *_qty           quantity per role, so a pair of check valves prints
                        "(2)" instead of the kit path's hardcoded "(1)"
        _warnings       messages for the run report
        _unassigned     valves deliberately left for a human to rule on

    Returns (sizes_dict, result) or (None, result) when nothing can be assigned.
    Sizes are ints where the cut sheets print ints, so they key straight into
    `pinned_rows`.
    """
    result = assign_valve_roles(line_items, section_label=section_label)
    if not result.applied:
        return None, result

    def size_of(role):
        a = result.by_role(role)
        if a is None:
            return None
        size = a.size_in
        return int(size) if float(size).is_integer() else size

    def qty_of(role, default=1):
        a = result.by_role(role)
        return a.qty if a is not None else default

    influent = size_of(ROLE_INFLUENT)
    effluent = size_of(ROLE_EFFLUENT)
    precoat = size_of(ROLE_PRECOAT)
    sightglass = size_of(ROLE_SIGHTGLASS)
    system_fill = size_of(ROLE_SYSTEM_FILL)

    # The kit path treats these four as required; without an effluent or
    # precoat the downstream pages have nothing to box, so bail rather than
    # emit a half-annotated sheet.
    if influent is None or effluent is None:
        result.warnings.append(
            f"{result.section_label or 'section'}: could not resolve influent "
            "and effluent from loose valves; section left unannotated."
        )
        return None, result

    if precoat is None:
        precoat = effluent
    if sightglass is None:
        sightglass = precoat

    sizes = {
        "influent": influent,
        "effluent": effluent,
        "precoat": precoat,
        "sightglass": sightglass,
        "system_fill": system_fill,
        "influent_qty": qty_of(ROLE_INFLUENT),
        "effluent_qty": qty_of(ROLE_EFFLUENT),
        "precoat_qty": qty_of(ROLE_PRECOAT),
        "sightglass_qty": qty_of(ROLE_SIGHTGLASS),
        "system_fill_qty": qty_of(ROLE_SYSTEM_FILL),
        "_source": "loose_valves",
        "_warnings": list(result.warnings),
        # Extra valves beyond the four service roles. These are NOT guessed at
        # — they keep their own name off the quote and are listed on the
        # effluent/precoat page so the reviewer sees every valve on the job.
        "_unassigned": [
            {"item_no": a.item_no, "part_no": a.part_no,
             "size": a.size_label,
             "size_in": int(a.size_in) if float(a.size_in).is_integer() else a.size_in,
             "qty": a.qty, "actuation": a.actuation,
             "name": descriptive_name(a.description),
             "description": a.description}
            for a in result.assignments if a.role == ROLE_UNASSIGNED
        ],
    }
    return sizes, result


def resolve_system_fill_size(sizes: dict):
    """System fill size for a section, per the rule that it is always 3 or 4.

    Loose-valve sections supply it directly. Kit strings do not carry a system
    fill token at all -- the kit path has always reused the precoat size, which
    is correct whenever the precoat is 3" or 4" (every kit seen so far: 4/4/4,
    8/6/3). When the precoat is larger than 4" that reuse would box a system
    fill row that cannot exist, so return None and let the caller warn rather
    than annotate a wrong row.

    Returns (size_or_None, warning_or_None).
    """
    explicit = sizes.get("system_fill")
    if explicit is not None:
        if float(explicit) in {float(s) for s in SYSTEM_FILL_SIZES}:
            return explicit, None
        return None, (
            f'system fill resolved to {_size_label(explicit)}" but system fill '
            "is always 3\" or 4\"; page skipped for review."
        )

    precoat = sizes.get("precoat")
    if precoat is None:
        return None, None
    if float(precoat) in {float(s) for s in SYSTEM_FILL_SIZES}:
        return precoat, None
    return None, (
        f'no system fill valve quoted and the precoat is {_size_label(precoat)}", '
        "which is not a valid system fill size (always 3\" or 4\"); "
        "system fill page skipped - confirm the fill size."
    )


__all__ = [
    "assign_valve_roles",
    "descriptive_name",
    "resolve_section_valve_sizes",
    "resolve_system_fill_size",
    "extract_valve_candidates",
    "section_has_valve_kit",
    "parse_size",
    "parse_actuation",
    "ValveAssignment",
    "ValveCandidate",
    "ValveRoleResult",
    "ROLE_INFLUENT",
    "ROLE_EFFLUENT",
    "ROLE_PRECOAT",
    "ROLE_SYSTEM_FILL",
    "ROLE_SIGHTGLASS",
    "ROLE_UNASSIGNED",
    "SHEET_SIZE_ROWS",
]
