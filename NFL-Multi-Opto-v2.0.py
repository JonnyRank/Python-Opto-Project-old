"""
DraftKings NFL Multi-Lineup Optimizer.

This script ingests a CSV file with player projections and uses linear
programming to find a specified number of unique, optimal lineups that
maximize a chosen scoring target (projection, ceiling, or a 50/50 mix of the
two), subject to DraftKings' classic NFL contest rules.

The script is run from the command line, specifying the path to the
projections CSV file as an argument.

Input Arguments:
    python NFL-Multi-Opto-v2.0.py "path" -n -u -e -te -x -l -s -srb -ndo -c -pj -sf -ls
    python <script> <proj file> <# of lineups> <min uniques> <max TE> <exclude> <export to CSV> <lock players> <stack QB with WR/TE> <stack QB with RB> <no DST vs Opp> <optimize on ceiling> <optimize on 50/50 proj+ceiling> <use small-field ownership> <late swap DKEntries.csv>
    python NFL-Multi-Opto-v2.0.py "C:\\path\\to\\projections.csv" -n 5 -u 2 -e -l "Josh Allen" -s -ndo
    python NFL-Multi-Opto-v2.0.py "C:\\path\\to\\projections.csv" -n 5 -u 2 -c
    python NFL-Multi-Opto-v2.0.py "C:\\path\\to\\projections.csv" -n 5 -u 2 -pj
    python NFL-Multi-Opto-v2.0.py "C:\\path\\to\\projections.csv" -ls -u 2

Late Swap (-ls / --late-swap):
    Reads the newest DKEntries*.csv in your Downloads folder and re-optimizes
    every entry in it. A player whose game has started -- judged from Game
    Info against the clock at runtime, or DraftKings' own LOCKED tag -- stays
    in his slot. Every other slot, including unstarted players already in the
    lineup, is refilled from players whose games have not started. -u is
    enforced between entries of the same contest only. All entries are written
    to Downloads\\upload-ready-DKEntries-<timestamp>.csv, each cell holding
    DraftKings' own "Name + ID" text. -l, -x, -s, -srb, -te, -ndo, -c, -pj and
    -sf apply; -n and -e do not.

Optimization Targets:
    (default)               Maximize total projection.
    -c / --ceiling          Maximize total ceiling.
    -pj / --projceiling     Maximize an equally weighted 50/50 blend of the two.
    The two flags are mutually exclusive; omitting both keeps the historical
    projection-only behavior.

Input Columns:
    Headers are resolved through COLUMN_ALIASES, so both the legacy and the
    current projections headers load without editing the CSV:
        ID          <- "ID", "id", "DK ID", or "Player ID"
        Player      <- "Player", "Name", or "Player Name"
        Position    <- "Position", "DK Pos", or "Pos"
        Team        <- "Team" or "Tm"
        Opp         <- "Opp" or "Opponent"
        Salary      <- "Salary" or "DK Salary"
        Projection  <- "Projection", "Proj", or "DK Proj"
        Ceiling     <- "Ceiling" or "DK Ceiling"      (optional)
        Ownership   <- "Large Field", "Ownership", or "Own"   (optional;
                       -sf / --small-field takes "Small Field" only, never an
                       unlabeled legacy column that may hold the other field)
    A header matching nothing in the table falls back to fuzzy matching and is
    reported when it resolves; a required column that stays unresolved raises
    with the headers the file actually contained.

Key Features:
- Loads player data from a command-line specified CSV file.
- Accepts either the legacy or the current projections headers.
- Cleans and validates player salary, projection, and ownership data.
- Identifies unique games to enforce the "at least two games" rule.
- Uses the PuLP library to model and solve the optimization problem.
- Optimizes on projection, ceiling, or a 50/50 blend of the two.
- Enforces constraints for salary cap, roster composition (QB, RB, WR, TE, FLEX, DST),
  and lineup diversity.
- Prints a well-formatted, human-readable optimal lineup.
"""

import os
import re
import csv
import glob
import difflib
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import pulp

# --- Constants ---
SALARY_CAP: int = 50000
ROSTER_SIZE: int = 9

# --- Optimization Targets ---
# Each target weights the per-player "Projection" and "Ceiling" columns into
# the single value the solver maximizes. Projection-only is the default and
# reproduces the behavior this script had before the other targets existed.
TARGET_PROJECTION: str = "projection"
TARGET_CEILING: str = "ceiling"
TARGET_BLEND: str = "blend"

# target -> (label for output, projection weight, ceiling weight)
OPTIMIZATION_TARGETS: Dict[str, Tuple[str, float, float]] = {
    TARGET_PROJECTION: ("Projection", 1.0, 0.0),
    TARGET_CEILING: ("Ceiling", 0.0, 1.0),
    TARGET_BLEND: ("50/50 Projection + Ceiling", 0.5, 0.5),
}

# Suffix added to the export filename so a run's target is obvious on disk.
TARGET_FILE_SUFFIXES: Dict[str, str] = {
    TARGET_PROJECTION: "",
    TARGET_CEILING: "_ceiling",
    TARGET_BLEND: "_projceiling",
}

EXPORT_DIR: str = r"G:\My Drive\Documents\NFL-DFS\csv-exports"

# DraftKings entries file used to translate rostered players into the
# "Name + ID" values DraftKings expects when a lineup is uploaded.
DK_ENTRIES_PATH: str = r"C:\Users\jrank\Downloads\DKEntries.csv"
# The entries file is jagged: contest entries come first and the player pool
# section (its own header, then one row per player) starts here.
DK_POOL_START_ROW: int = 8
# Name suffixes dropped when a projections name and a DraftKings name disagree.
NAME_SUFFIXES: Tuple[str, ...] = ("jr", "sr", "ii", "iii", "iv", "v")

# Column order of the exported CSV. The DraftKings upload row reuses the
# columns after "Lineup_ID" as anonymous slots, one per rostered player.
EXPORT_COLUMNS: List[str] = [
    "Lineup_ID",
    "Player_ID",
    "Slot",
    "Player",
    "Position",
    "Team",
    "Salary",
    "Projection",
    "Ownership",
    "Ceiling",
]
# John Rankin's (Dad) Downloads folder path: r"C:\Users\jdr0824\Downloads" r"C:\Users\jdr0824\Downloads"

# --- Late Swap ---
# -ls / --late-swap re-optimizes the entries in a DraftKings entries file
# mid-slate. The file is found in the current user's Downloads folder and the
# finished lineups go back there in DraftKings' own upload layout.
DOWNLOADS_DIR: str = os.path.join(os.path.expanduser("~"), "Downloads")
# The newest match wins, so a browser re-download ("DKEntries (1).csv") is
# picked up. Files that merely contain the name ("SD-DKEntries.csv",
# "upload-ready-DKEntries-...") do not match.
DK_ENTRIES_GLOB: str = "DKEntries*.csv"
UPLOAD_FILE_PREFIX: str = "upload-ready-DKEntries"
# Game Info reads "GB@MIN 09/13/2026 04:25PM ET" before kickoff and
# "In Progress" after it. Kickoffs are Eastern wherever the script runs, so the
# clock comparison is made in that zone.
GAME_INFO_TIMEZONE: str = "America/New_York"
KICKOFF_PATTERN = re.compile(r"(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}[AP]M) ET")
KICKOFF_FORMAT: str = "%m/%d/%Y %I:%M%p"
# DraftKings tags a rostered player whose game has started. The clock decides
# on its own, but the tag is honored too: DraftKings rejects any change to a
# player it has already locked, whatever the local clock says.
DK_LOCKED_TAG: str = "(LOCKED)"
DK_ID_PATTERN = re.compile(r"\((\d+)\)")
# Entry ID, Contest Name, Contest ID, and Entry Fee precede the roster slots.
ENTRY_SLOT_START_COL: int = 4
FLEX_SLOT: str = "FLEX"

# --- Projections CSV headers ---
# Internal column name -> the source headers that may carry it, in preference
# order. The projections source renamed several columns ("DK Pos", "DK Salary",
# "DK Proj", "DK Ceiling", "id"), so both generations of header are accepted.
# The first alias actually present wins, which keeps two source columns from
# ever collapsing onto one internal name.
COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ID": ("ID", "id", "DK ID", "Player ID"),
    "Player": ("Player", "Name", "Player Name"),
    "Position": ("Position", "DK Pos", "Pos"),
    "Team": ("Team", "Tm"),
    "Opp": ("Opp", "Opponent"),
    "Salary": ("Salary", "DK Salary"),
    "Projection": ("Projection", "Proj", "DK Proj"),
    "Ceiling": ("Ceiling", "DK Ceiling"),
}

# The new file ships two ownership projections. -sf / --small-field chooses
# which one becomes the "Ownership" column.
#
# Only the large-field (default) request accepts the unlabeled legacy headers:
# "Own" does not say which field it measures, and on the files that carried it
# it was the only ownership column there was, so reading it under the default
# preserves the historical behavior. -sf is an explicit request for the other
# measure, so it takes a column that actually says "Small Field" or nothing at
# all -- falling back to "Own" there would hand back large-field numbers under
# a flag asking for small-field ones. Labeled names lead in both tuples, the
# same new-ahead-of-legacy order the other columns use.
OWNERSHIP_LARGE_FIELD: str = "large"
OWNERSHIP_SMALL_FIELD: str = "small"

OWNERSHIP_ALIASES: Dict[str, Tuple[str, ...]] = {
    OWNERSHIP_LARGE_FIELD: ("Large Field", "Ownership", "Own"),
    OWNERSHIP_SMALL_FIELD: ("Small Field",),
}

OWNERSHIP_LABELS: Dict[str, str] = {
    OWNERSHIP_LARGE_FIELD: "large field",
    OWNERSHIP_SMALL_FIELD: "small field",
}

# Columns the optimizer cannot run without. "Ownership" and "Ceiling" stay
# optional and default to 0.00 when the file has neither name for them.
REQUIRED_COLUMNS: Tuple[str, ...] = (
    "ID",
    "Player",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "Projection",
)

# Headers the fuzzy fallback must never claim. These are real columns with
# meanings of their own that sit one word away from a column we do want --
# "DK Proj" vs "DK Floor" vs "DK Ceiling" vs "DK Value" -- and a wrong guess
# among them would silently optimize on the wrong numbers.
UNMATCHABLE_HEADERS: Tuple[str, ...] = ("DK Value", "Value", "DK Floor", "Floor")

# Minimum difflib similarity before an unrecognized header is accepted as a
# match. Deliberately high: erroring out is better than a silent mismatch.
FUZZY_HEADER_CUTOFF: float = 0.85

# Shortest target the fuzzy pass will match against. difflib's ratio is 2M/T
# over the combined length, so the cutoff gets weaker as the target gets
# shorter: against "own" any four-letter header containing that run -- "Down",
# "Town" -- scores 2*3/7 = 0.857 and clears 0.85. Six characters is where the
# ratio starts meaning what it says. Targets below it are simply not fuzzed,
# which costs nothing: a header close enough to "Tm" or "Opp" to be worth
# guessing at is already an exact alias hit.
MIN_FUZZY_TARGET_LENGTH: int = 6


def resolve_optimization_target(use_ceiling: bool, use_blend: bool) -> str:
    """
    Turns the --ceiling / --projceiling flags into a single target key.

    Args:
        use_ceiling: True when -c / --ceiling was passed.
        use_blend: True when -pj / --projceiling was passed.

    Returns:
        One of TARGET_CEILING, TARGET_BLEND, or TARGET_PROJECTION (the default).

    Raises:
        ValueError: If both flags are supplied together. argparse's mutually
            exclusive group rejects that first, so a user never reaches this;
            it keeps the helper correct when called outside main().
    """
    if use_ceiling and use_blend:
        raise ValueError(
            "Choose only one optimization target: --ceiling or --projceiling."
        )
    if use_ceiling:
        return TARGET_CEILING
    if use_blend:
        return TARGET_BLEND
    return TARGET_PROJECTION


def target_value(player: Dict[str, Any], target: str) -> float:
    """Returns the value the solver maximizes for one player under `target`."""
    _, proj_weight, ceiling_weight = OPTIMIZATION_TARGETS[target]
    return proj_weight * float(player["Projection"]) + ceiling_weight * float(
        player["Ceiling"]
    )


def validate_target_data(df: pd.DataFrame, target: str) -> None:
    """
    Checks the ceiling data a ceiling-weighted target is about to maximize.

    An all-zero Ceiling column is fatal: optimizing on it would silently return
    an arbitrary salary-feasible lineup. A partly-populated column is legal but
    consequential -- a zero ceiling is indistinguishable from a blank one once
    filled, and those players score nothing on the ceiling half of the
    objective -- so name them rather than letting the pool shrink invisibly.

    Raises:
        ValueError: If the target weights ceiling but no positive ceiling exists.
    """
    label, _, ceiling_weight = OPTIMIZATION_TARGETS[target]
    if ceiling_weight == 0:
        return
    if "Ceiling" not in df.columns or not (df["Ceiling"] > 0).any():
        raise ValueError(
            f"The '{label}' target needs a populated 'Ceiling' column, but the "
            f"projections file has no ceiling values. Re-run without "
            f"--ceiling/--projceiling to optimize on projection."
        )

    zeroed = df[df["Ceiling"] <= 0]
    if zeroed.empty:
        return
    consequence = (
        "they cannot be rostered unless locked"
        if target == TARGET_CEILING
        else "they are scored on their projection alone"
    )
    names = ", ".join(str(name) for name in zeroed["Player"].head(5))
    if len(zeroed) > 5:
        names += f", +{len(zeroed) - 5} more"
    print(
        f"  WARNING: {len(zeroed)} of {len(df)} players have a zero or missing "
        f"ceiling. Under the '{label}' target {consequence}: {names}"
    )


def _normalize_name(name: Any) -> str:
    """Lowercases a name and drops punctuation so it can be matched across files."""
    text = re.sub(r"[^a-z0-9 ]", "", str(name).lower())
    return re.sub(r"\s+", " ", text).strip()


def _strip_name_suffix(normalized: str) -> str:
    """Removes a trailing generational suffix (Jr., III, ...) from a normalized name."""
    parts = normalized.split()
    while len(parts) > 2 and parts[-1] in NAME_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def load_dk_name_ids(path: str = DK_ENTRIES_PATH) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Indexes every player's DraftKings "Name + ID" value from the entries file.

    The file is jagged: contest entries occupy the leading columns and the
    player pool section starts at DK_POOL_START_ROW with its own header. Each
    player appears once per roster slot, so the Captain and FLEX versions of a
    player carry different IDs and must be looked up by slot.

    Args:
        path: Location of the DraftKings entries CSV.

    Returns:
        A dict with "by_slot" (slot + name keys), "by_name" (name keys, with
        ambiguous names mapped to None), and "by_team" (slot + team keys, used
        for DST rows whose names differ between the two files). Returns None
        when the file is missing or carries no readable player pool, which
        tells the caller to omit the DraftKings upload rows entirely.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
    except OSError:
        return None

    # Find the pool header, starting at the documented row and scanning on in
    # case a future export shifts the instructions block by a line or two.
    # The pool's columns are read from the full row: the entry-list columns to
    # its left are blank on every pool row, so no slicing is needed.
    header_idx = None
    for idx in range(max(DK_POOL_START_ROW - 1, 0), len(rows)):
        if "Name + ID" in rows[idx]:
            header_idx = idx
            break
    if header_idx is None:
        return None

    header = rows[header_idx]
    try:
        name_id_col = header.index("Name + ID")
        name_col = header.index("Name")
        slot_col = header.index("Roster Position")
    except ValueError:
        return None
    team_col = header.index("TeamAbbrev") if "TeamAbbrev" in header else None
    position_col = header.index("Position") if "Position" in header else None

    by_slot: Dict[str, str] = {}
    by_name: Dict[str, Optional[str]] = {}
    by_team: Dict[str, str] = {}

    for cells in rows[header_idx + 1 :]:
        if len(cells) <= max(name_id_col, name_col, slot_col):
            continue
        name_id = cells[name_id_col].strip()
        name = _normalize_name(cells[name_col])
        slot = cells[slot_col].strip().upper()
        if not name_id or not name or not slot:
            continue

        for key in {name, _strip_name_suffix(name)}:
            by_slot.setdefault(f"{slot}|{key}", name_id)
            # A name that resolves to more than one player is unusable on its
            # own, so mark it ambiguous rather than guessing.
            if key in by_name and by_name[key] != name_id:
                by_name[key] = None
            else:
                by_name.setdefault(key, name_id)

        is_dst = position_col is not None and cells[position_col].strip().upper() in (
            "DST",
            "DEF",
            "D",
        )
        if is_dst and team_col is not None and len(cells) > team_col:
            team = _normalize_name(cells[team_col])
            if team:
                by_team.setdefault(f"{slot}|{team}", name_id)

    if not by_slot:
        return None
    return {"by_slot": by_slot, "by_name": by_name, "by_team": by_team}


def lookup_dk_name_id(
    lookup: Dict[str, Dict[str, Any]],
    player: Any,
    slot: Any,
    team: Any,
    position: Any,
) -> Optional[str]:
    """
    Resolves one rostered player to its slot-specific DraftKings "Name + ID".

    Tries the slot-qualified name first, then the suffix-stripped name, then an
    unambiguous name-only match, and finally the team abbreviation for defenses
    (DraftKings names them by nickname, projections rarely do).

    Returns:
        The "Name + ID" string, or None when no confident match exists.
    """
    name = _normalize_name(player)
    slot_key = _dk_roster_position(slot)
    candidates = [name, _strip_name_suffix(name)]

    for candidate in candidates:
        hit = lookup["by_slot"].get(f"{slot_key}|{candidate}")
        if hit:
            return hit
    for candidate in candidates:
        hit = lookup["by_name"].get(candidate)
        if hit:
            return hit
    if str(position).upper() in ("DST", "DEF", "D"):
        return lookup["by_team"].get(f"{slot_key}|{_normalize_name(team)}")
    return None


def build_dk_upload_values(
    rows: List[Dict[str, Any]], lookup: Optional[Dict[str, Dict[str, Any]]]
) -> Optional[List[str]]:
    """
    Converts a lineup's export rows into DraftKings "Name + ID" values.

    Returns:
        One value per roster slot in display order (QB, RB, RB, WR, WR, WR, TE,
        FLEX, DST), or None when the entries file is unavailable or any player
        could not be matched -- in which case the caller omits the upload row
        for this lineup.
    """
    if not lookup:
        return None

    values: List[str] = []
    for row in rows:
        name_id = lookup_dk_name_id(
            lookup, row["Player"], row["Slot"], row["Team"], row["Position"]
        )
        if not name_id:
            print(
                f"  NOTE: No DraftKings ID found for {row['Player']} "
                f"({row['Slot']}); skipping the upload row for this lineup."
            )
            return None
        values.append(name_id)
    return values


def _dk_roster_position(slot: Any) -> str:
    """
    Maps a display slot to the roster position DraftKings uses.

    The display slots number the repeated positions (RB1, RB2, WR3), while the
    entries file lists them unnumbered.
    """
    return re.sub(r"\d+$", "", str(slot)).upper()


def _normalize_header(header: Any) -> str:
    """Reduces a CSV header to a comparable token: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def _alias_table(ownership_field: str) -> Dict[str, Tuple[str, ...]]:
    """Returns the full alias table with the requested ownership column folded in."""
    aliases = dict(COLUMN_ALIASES)
    aliases["Ownership"] = OWNERSHIP_ALIASES[ownership_field]
    return aliases


def resolve_columns(
    columns: List[Any], ownership_field: str = OWNERSHIP_LARGE_FIELD
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Maps each internal column name onto a header actually present in the file.

    Two passes. The first takes exact alias hits, compared case- and
    punctuation-insensitively so "id", "ID", and "DK ID" are one header. The
    second is a difflib fuzzy fallback for anything still unresolved, so a
    future rename the alias table has not caught yet ("DK Projection") still
    lands. The fallback is deliberately narrow: it skips headers already
    claimed, headers that are a known alias of some *other* column, and the
    near-miss decoys in UNMATCHABLE_HEADERS; it ignores alias spellings shorter
    than MIN_FUZZY_TARGET_LENGTH, against which the ratio is too weak to mean
    anything; and it then demands FUZZY_HEADER_CUTOFF similarity. Every fuzzy
    hit is printed, because it is a guess.

    Args:
        columns: The headers as read from the projections file.
        ownership_field: OWNERSHIP_LARGE_FIELD or OWNERSHIP_SMALL_FIELD.

    Returns:
        A (resolved, unresolved) pair: `resolved` maps internal column name to
        the source header supplying it; `unresolved` lists internal names with
        no header at all.
    """
    aliases = _alias_table(ownership_field)

    # First header wins a normalized spelling, so a file carrying both "Own"
    # and "own" does not produce a duplicate-column rename.
    by_normalized: Dict[str, Any] = {}
    for column in columns:
        by_normalized.setdefault(_normalize_header(column), column)

    resolved: Dict[str, Any] = {}
    claimed: Set[str] = set()

    # Pass 1: exact alias hits, in the table's preference order.
    for internal, names in aliases.items():
        for name in names:
            header = by_normalized.get(_normalize_header(name))
            if header is None or _normalize_header(header) in claimed:
                continue
            resolved[internal] = header
            claimed.add(_normalize_header(header))
            break

    # Pass 2: fuzzy fallback. Reserve every alias of every column (both
    # ownership variants, so --small-field never silently eats "Large Field")
    # plus the decoy headers.
    reserved: Set[str] = {
        _normalize_header(name)
        for table in (COLUMN_ALIASES, OWNERSHIP_ALIASES)
        for names in table.values()
        for name in names
    }
    reserved.update(_normalize_header(name) for name in UNMATCHABLE_HEADERS)

    for internal, names in aliases.items():
        if internal in resolved:
            continue
        # Targets are the alias list alone, never the internal name as well.
        # For every column but ownership the internal name *is* the first
        # alias, so this changes nothing; for ownership it is the point.
        # Adding "Ownership" unconditionally would let an unlabeled header
        # like "OwnershipPct" fuzzy-match under -sf (2*9/21 = 0.857), which
        # is exactly the substitution -sf promises never to make. Built this
        # way, the fuzzy pass honors the same labeled/unlabeled rule the
        # exact pass does.
        targets = {
            normalized
            for normalized in (_normalize_header(name) for name in names)
            if len(normalized) >= MIN_FUZZY_TARGET_LENGTH
        }
        if not targets:
            continue
        best_header: Optional[Any] = None
        best_score = 0.0
        for column in columns:
            normalized = _normalize_header(column)
            if normalized in claimed or normalized in reserved:
                continue
            score = max(
                difflib.SequenceMatcher(None, normalized, target).ratio()
                for target in targets
            )
            if score > best_score:
                best_header, best_score = column, score
        if best_header is not None and best_score >= FUZZY_HEADER_CUTOFF:
            resolved[internal] = best_header
            claimed.add(_normalize_header(best_header))
            print(
                f"  NOTE: Unrecognized header '{best_header}' matched to "
                f"'{internal}' (similarity {best_score:.2f})."
            )

    unresolved = [internal for internal in aliases if internal not in resolved]
    return resolved, unresolved


def load_player_data(
    filepath: str, ownership_field: str = OWNERSHIP_LARGE_FIELD
) -> pd.DataFrame:
    """
    Loads and preprocesses player data from the projections CSV file.

    Headers are resolved through COLUMN_ALIASES rather than taken literally, so
    both the legacy names ("Proj", "Own", "ID") and the current ones
    ("DK Proj", "Large Field", "id") load without editing the file by hand.

    Args:
        filepath: The absolute path to the projections CSV file.
        ownership_field: Which ownership projection becomes the "Ownership"
            column -- OWNERSHIP_LARGE_FIELD (default) or OWNERSHIP_SMALL_FIELD.

    Returns:
        A pandas DataFrame containing cleaned and prepared player data ready
        for optimization.

    Raises:
        FileNotFoundError: If the specified file does not exist.
        ValueError: If the file is empty or critical columns are missing.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Projections file not found at: {filepath}")

    try:
        df = pd.read_csv(filepath)
    except pd.errors.EmptyDataError:
        raise ValueError(f"The projections file is empty: {filepath}")

    print(f"Successfully loaded {len(df)} players from {os.path.basename(filepath)}.")

    # --- Data Cleaning and Preparation ---
    # Map whatever headers this file carries onto the internal names the rest
    # of the script uses, then rename in one pass.
    source_headers = list(df.columns)
    resolved, unresolved = resolve_columns(source_headers, ownership_field)

    missing_required = [name for name in REQUIRED_COLUMNS if name in unresolved]
    if missing_required:
        raise ValueError(
            "Projections file is missing required column(s): "
            f"{', '.join(missing_required)}.\n"
            f"  Headers found: {', '.join(str(h) for h in source_headers)}\n"
            "  Add the column to the file, or add its header to COLUMN_ALIASES."
        )

    rename_map = {
        header: internal
        for internal, header in resolved.items()
        if header != internal
    }

    # A header the resolver did not pick can still collide with a rename
    # target, which would leave two columns sharing one name and break every
    # df["<name>"] downstream. The live case is -sf against a file carrying
    # both "Small Field" and a literal "Ownership": the small-field request
    # wins, so the unchosen "Ownership" column is dropped rather than allowed
    # to silently supply ownership the flag explicitly did not ask for.
    chosen = set(resolved.values())
    targets = set(rename_map.values())
    superseded = [
        column for column in df.columns if column in targets and column not in chosen
    ]
    if superseded:
        for column in superseded:
            print(
                f"  NOTE: Ignoring the file's own '{column}' column; "
                f"'{resolved[column]}' supplies it instead."
            )
        df.drop(columns=superseded, inplace=True)

    df.rename(columns=rename_map, inplace=True)
    for internal, header in sorted(resolved.items()):
        if header != internal:
            print(f"  Mapped column '{header}' -> '{internal}'.")

    # Clean Salary column (e.g., "$6,000 " -> 6000)
    df["Salary"] = (
        df["Salary"]
        .astype(str)
        .str.replace(r"[\$,\s]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Ownership is display-and-export only -- no constraint or objective reads
    # it -- so a file without either ownership column still optimizes.
    if "Ownership" not in df.columns:
        print(
            f"  NOTE: No {OWNERSHIP_LABELS[ownership_field]} ownership column found. "
            f"Ownership will display as 0.00%."
        )
        df["Ownership"] = 0.0
    # Clean Ownership column (e.g., "11.70%" -> 11.70)
    df["Ownership"] = (
        df["Ownership"]
        .astype(str)
        .str.replace("%", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Ceiling drives the --ceiling / --projceiling targets and the printed
    # totals, but it is optional: a file without it still optimizes on
    # projection.
    if "Ceiling" not in df.columns:
        print("  NOTE: No 'Ceiling' column found. Ceiling values will display as 0.00.")
        df["Ceiling"] = 0.0
    df["Ceiling"] = (
        df["Ceiling"]
        .astype(str)
        .str.replace(r"[\$,%\s]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Projection may arrive as text, and a source that reformats its headers
    # may reformat its numbers too, so strip it like Salary and Ceiling rather
    # than trusting float parsing. An unparseable value becomes NaN and the row
    # is dropped below with the rest of the missing critical data.
    df["Projection"] = (
        df["Projection"]
        .astype(str)
        .str.replace(r"[\$,%\s]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
    )

    # ID is coerced for the same reason: a non-integral id would otherwise
    # reach .astype(int) below and raise without naming the column or the row.
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")

    # Drop players with missing critical data for optimization
    critical_cols = ["ID", "Salary", "Projection", "Position"]
    df.dropna(subset=critical_cols, inplace=True)
    df["ID"] = df["ID"].astype(int)

    # Ceiling is filled only after that drop, so the count below describes the
    # players actually available to the optimizer. A blank ceiling is not a
    # reason to drop a player -- projection-only runs never touch the column --
    # but it is worth saying out loud, because it becomes a real zero and the
    # ceiling-weighted targets maximize exactly this number.
    missing_ceiling = int(df["Ceiling"].isna().sum())
    if missing_ceiling:
        print(
            f"  NOTE: {missing_ceiling} player(s) have no usable 'Ceiling' value; "
            f"treating it as 0.00."
        )
    df["Ceiling"] = df["Ceiling"].fillna(0.0)

    # --- Game Identification ---
    def get_game_id(row: pd.Series) -> FrozenSet[str]:
        """Creates a canonical, order-independent ID for a game."""
        team1 = str(row["Team"])
        # Opponent can be "@OPP" or "OPP", remove "@"
        team2 = str(row["Opp"]).replace("@", "")
        return frozenset([team1, team2])

    df["game_id"] = df.apply(get_game_id, axis=1)

    # --- Positional Flags for Constraints ---
    df["is_QB"] = (df["Position"] == "QB").astype(int)
    df["is_RB"] = (df["Position"] == "RB").astype(int)
    df["is_WR"] = (df["Position"] == "WR").astype(int)
    df["is_TE"] = (df["Position"] == "TE").astype(int)
    df["is_DST"] = (df["Position"] == "DST").astype(int)
    df["is_FLEX"] = df["Position"].isin(["RB", "WR", "TE"]).astype(int)

    print(f"Data preprocessed. {len(df)} players available for optimization.")
    return df


def _assign_flex_positions(lineup: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """
    Assigns players from an optimal lineup to specific roster slots.
    This helper function fills the primary RB, WR, and TE slots first, sorted
    by salary descending. The single remaining FLEX-eligible player is then
    placed in the FLEX slot.

    Args:
        lineup: A DataFrame of the 9 players in the optimal lineup.

    Returns:
        A dictionary mapping roster slots to player data dictionaries.
    """
    assigned_lineup: Dict[str, Dict[str, Any]] = {}
    unassigned_players = lineup.copy()

    # 1. Assign dedicated slots first (QB, DST)
    for pos in ["QB", "DST"]:
        player = unassigned_players[unassigned_players["Position"] == pos].iloc[0]
        assigned_lineup[pos] = player.to_dict()
        unassigned_players.drop(player.name, inplace=True)

    # 2. Assign RBs, WRs, TEs to their primary slots, sorted by salary descending
    for pos, count in [("RB", 2), ("WR", 3), ("TE", 1)]:
        # Get all players for the position and sort by salary descending
        positional_players = unassigned_players[
            unassigned_players["Position"] == pos
        ].sort_values(by="Salary", ascending=False)

        # Assign the top 'count' players to the primary slots
        players_to_assign_to_slots = positional_players.head(count)
        for i, (idx, player) in enumerate(players_to_assign_to_slots.iterrows()):
            slot = f"{pos}{i + 1}"
            assigned_lineup[slot] = player.to_dict()
            unassigned_players.drop(idx, inplace=True)

    # 3. The single remaining player is the FLEX
    if not unassigned_players.empty:
        flex_player = unassigned_players.iloc[0]
        assigned_lineup["FLEX"] = flex_player.to_dict()

    return assigned_lineup


# --- Late Swap ---


@dataclass(frozen=True)
class DkPoolPlayer:
    """One player from the player pool section of a DraftKings entries file."""

    dk_id: int
    name: str
    name_id: str  # DraftKings' "Name + ID" cell, copied verbatim into the upload
    primary_slot: str  # "QB", "RB", "WR", "TE", or "DST"
    flex_eligible: bool
    salary: int
    team: str
    kickoff: Optional[datetime]  # None once Game Info stops carrying a start time
    started: bool


@dataclass(frozen=True)
class DkEntry:
    """One contest entry: its identifying cells and one cell per roster slot."""

    entry_id: str
    contest_name: str
    contest_id: str
    entry_fee: str
    cells: List[str]


def _game_info_timezone() -> tzinfo:
    """Returns the Eastern zone Game Info kickoffs are written in."""
    try:
        return ZoneInfo(GAME_INFO_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        # Windows ships no zone database; Python reads it from the tzdata
        # package instead.
        raise ValueError(
            f"Time zone '{GAME_INFO_TIMEZONE}' is unavailable. Install it with: "
            f"venv/Scripts/python.exe -m pip install tzdata"
        ) from exc


def find_dk_entries_file(directory: Optional[str] = None) -> str:
    """
    Returns the newest DK_ENTRIES_GLOB match in the Downloads folder.

    Raises:
        FileNotFoundError: If the folder holds no entries file.
    """
    directory = directory or DOWNLOADS_DIR
    matches = glob.glob(os.path.join(directory, DK_ENTRIES_GLOB))
    if not matches:
        raise FileNotFoundError(
            f"No {DK_ENTRIES_GLOB} file found in {directory}. Download your "
            f"entries CSV from DraftKings' Edit Entries page first."
        )
    newest = max(matches, key=os.path.getmtime)
    if len(matches) > 1:
        print(
            f"  NOTE: {len(matches)} entries files in {directory}; using the "
            f"newest, {os.path.basename(newest)}."
        )
    return newest


def parse_kickoff(game_info: str, zone: tzinfo) -> Optional[datetime]:
    """
    Reads the kickoff out of a Game Info cell ("GB@MIN 09/13/2026 04:25PM ET").

    Returns:
        The kickoff as an aware datetime, or None when the cell carries no
        start time -- "In Progress", "Final", or anything unrecognized.
    """
    match = KICKOFF_PATTERN.search(game_info)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), KICKOFF_FORMAT).replace(tzinfo=zone)
    except ValueError:
        return None


def load_dk_entries_file(
    path: str, now: datetime
) -> Tuple[List[str], List[str], List[DkEntry], Dict[int, DkPoolPlayer]]:
    """
    Reads the contest entries and the player pool from a DraftKings entries file.

    The file is jagged: entries fill the leading columns (Entry ID, Contest
    Name, Contest ID, Entry Fee, then one column per roster slot) and the
    player pool sits to their right under its own "Name + ID" header, a few
    rows down. A pool player counts as started when his Game Info no longer
    shows a future kickoff as of `now`, or when DraftKings has tagged him
    LOCKED; a Game Info the parser cannot read counts as started too, so an
    unreadable time can never let a player be swapped in after his game began.

    Args:
        path: Location of the entries file.
        now: The moment eligibility is judged at (an aware datetime).

    Returns:
        (upload header, slot labels, entries, pool keyed by DraftKings ID).

    Raises:
        ValueError: If the file is not a Classic entries file or has no pool.
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
    except OSError as exc:
        raise FileNotFoundError(f"Could not read {path}: {exc}") from exc

    filename = os.path.basename(path)
    if not rows or not rows[0] or rows[0][0].strip() != "Entry ID":
        raise ValueError(f"{filename} is not a DraftKings entries file (no 'Entry ID' header).")

    width = ENTRY_SLOT_START_COL + ROSTER_SIZE
    upload_header = rows[0][:width]
    slot_labels: List[str] = []
    for label in rows[0][ENTRY_SLOT_START_COL:]:
        if not label.strip():
            break
        slot_labels.append(label.strip().upper())
    if len(slot_labels) != ROSTER_SIZE:
        raise ValueError(
            f"{filename} has {len(slot_labels)} roster slots "
            f"({', '.join(slot_labels)}); late swap needs a Classic entries file "
            f"with {ROSTER_SIZE}."
        )

    # Entry rows run on past the pool header -- the two blocks share rows --
    # so the whole file is scanned, and a row counts as an entry only when it
    # opens with a numeric Entry ID. A pool row never does, even if a future
    # export shifted the pool block into column 0.
    entries: List[DkEntry] = []
    for cells in rows[1:]:
        if not cells or not cells[0].strip().isdigit():
            continue
        padded = cells[:width] + [""] * (width - len(cells))
        entries.append(
            DkEntry(
                entry_id=padded[0],
                contest_name=padded[1],
                contest_id=padded[2].strip(),
                entry_fee=padded[3],
                cells=padded[ENTRY_SLOT_START_COL:width],
            )
        )
    if not entries:
        raise ValueError(f"{filename} lists no contest entries.")

    header_idx = next(
        (idx for idx, cells in enumerate(rows) if "Name + ID" in cells), None
    )
    if header_idx is None:
        raise ValueError(f"{filename} has no player pool section ('Name + ID' header).")
    pool_header = rows[header_idx]
    needed = ("Name + ID", "Name", "ID", "Roster Position", "Salary", "Game Info", "TeamAbbrev")
    missing = [name for name in needed if name not in pool_header]
    if missing:
        raise ValueError(f"{filename}'s player pool is missing column(s): {', '.join(missing)}.")
    col = {name: pool_header.index(name) for name in needed}

    # Game Info times are Eastern whatever zone `now` arrives in; a naive
    # `now` is taken as Eastern too.
    zone = _game_info_timezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    pool: Dict[int, DkPoolPlayer] = {}
    for cells in rows[header_idx + 1 :]:
        if len(cells) <= max(col.values()):
            continue
        try:
            dk_id = int(cells[col["ID"]].strip())
            salary = int(cells[col["Salary"]].strip())
        except ValueError:
            continue
        # "RB/FLEX" -> primary slot RB, FLEX-eligible.
        slots = [s.strip().upper() for s in cells[col["Roster Position"]].split("/") if s.strip()]
        if not slots:
            continue
        name_id = cells[col["Name + ID"]]
        kickoff = parse_kickoff(cells[col["Game Info"]], zone)
        pool[dk_id] = DkPoolPlayer(
            dk_id=dk_id,
            name=cells[col["Name"]].strip(),
            name_id=name_id,
            primary_slot=slots[0],
            flex_eligible=FLEX_SLOT in slots[1:],
            salary=salary,
            team=cells[col["TeamAbbrev"]].strip(),
            kickoff=kickoff,
            started=DK_LOCKED_TAG in name_id or kickoff is None or kickoff <= now,
        )
    if not pool:
        raise ValueError(f"{filename}'s player pool has no readable players.")
    return upload_header, slot_labels, entries, pool


def _split_entry_slots(
    entry: DkEntry, pool: Dict[int, DkPoolPlayer]
) -> Tuple[List[Optional[int]], Set[int], List[int]]:
    """
    Sorts an entry's slots into locked and open.

    A slot is locked when its player's game has started (or DraftKings tagged
    the cell LOCKED), and also when its ID is missing from the pool -- the
    caller leaves such an entry unchanged, since that player's salary is
    unknown. Blank cells (reservations) and unstarted players are open.

    Returns:
        (DraftKings ID per slot or None, locked slot indices, open slot indices).
    """
    original_ids: List[Optional[int]] = []
    locked: Set[int] = set()
    open_slots: List[int] = []
    for idx, cell in enumerate(entry.cells):
        match = DK_ID_PATTERN.search(cell)
        dk_id = int(match.group(1)) if match else None
        original_ids.append(dk_id)
        if dk_id is None:
            open_slots.append(idx)
            continue
        player = pool.get(dk_id)
        if player is None or player.started or DK_LOCKED_TAG in cell:
            locked.add(idx)
        else:
            open_slots.append(idx)
    return original_ids, locked, open_slots


def _ids_for_names(players_df: pd.DataFrame, names: Optional[List[str]], action: str) -> Set[int]:
    """Resolves -l / -x player names (case-insensitive) to DraftKings IDs."""
    ids: Set[int] = set()
    for name in names or []:
        matches = players_df[players_df["Player"].str.lower() == name.lower()]
        if matches.empty:
            print(f"  WARNING: Player '{name}' not found in projections. Skipping {action}.")
            continue
        ids.update(int(dk_id) for dk_id in matches["ID"])
    return ids


def optimize_late_swap_entry(
    open_labels: List[str],
    locked_ids: List[int],
    pool: Dict[int, DkPoolPlayer],
    candidates: Dict[int, DkPoolPlayer],
    projections: Dict[int, Dict[str, Any]],
    target: str,
    args: argparse.Namespace,
    lock_ids: Set[int],
    previous: List[FrozenSet[int]],
) -> Optional[List[int]]:
    """
    Picks the best players for one entry's open slots.

    Same count-identity model as the full optimizer, sized to the open slots:
    each open positional slot needs a player of that position, and the open
    FLEX (if any) takes exactly one surplus RB/WR/TE. Locked players are fixed
    constants -- their salary comes off the cap, they count toward the games
    and TE limits, and they count toward shared players under -u -- but the
    stacking and DST rules bind new picks only: a locked player's teammates
    and opponents have started too, so no new pick could change those rules'
    outcome for him.

    Args:
        open_labels: Roster slot label of each open slot ("RB", "FLEX", ...).
        locked_ids: DraftKings IDs of the entry's locked players.
        pool: Every player in the entries file, keyed by DraftKings ID.
        candidates: Players eligible to be swapped in (unstarted, projected,
            not excluded).
        projections: Projection rows keyed by DraftKings ID.
        target: Optimization target key.
        args: Parsed CLI options (stack, stack_rb, max_te, no_dst_opp,
            min_uniques).
        lock_ids: IDs from -l; forced in wherever they fit an open slot.
        previous: Lineups already built for this contest, for -u.

    Returns:
        The chosen DraftKings IDs (one per open slot, unordered), or None when
        no valid set exists.
    """
    open_counts = Counter(label for label in open_labels if label != FLEX_SLOT)
    open_flex = len(open_labels) - sum(open_counts.values())
    flex_positions = {p.primary_slot for p in pool.values() if p.flex_eligible}
    locked_set = set(locked_ids)

    # Only players who fit one of the open slots get a variable. A player
    # already fixed in a locked slot never does -- DraftKings' LOCKED tag can
    # lock a player whose kickoff the clock still calls future, and picking
    # him again would roster him twice.
    fits = {
        dk_id: player
        for dk_id, player in candidates.items()
        if dk_id not in locked_set
        and (player.primary_slot in open_counts or (open_flex and player.flex_eligible))
    }

    def members(predicate) -> List[int]:
        return [dk_id for dk_id, player in fits.items() if predicate(player)]

    prob = pulp.LpProblem("DraftKings_NFL_Late_Swap", pulp.LpMaximize)
    pick = pulp.LpVariable.dicts("Pick", list(fits), cat="Binary")
    prob += pulp.lpSum(target_value(projections[i], target) * pick[i] for i in fits)

    locked_players = [pool[i] for i in locked_ids]
    prob += (
        pulp.lpSum(fits[i].salary * pick[i] for i in fits)
        <= SALARY_CAP - sum(p.salary for p in locked_players),
        "Salary_Cap",
    )
    prob += pulp.lpSum(pick[i] for i in fits) == len(open_labels), "Open_Slots"

    # Every open positional slot needs its own position; the FLEX surplus must
    # be FLEX-eligible. With the slot total above, these two force exactly one
    # surplus RB/WR/TE per open FLEX and no surplus anywhere else.
    for slot, count in open_counts.items():
        eligible = members(lambda p, slot=slot: p.primary_slot == slot)
        if len(eligible) < count:
            return None
        prob += pulp.lpSum(pick[i] for i in eligible) >= count, f"Min_{slot}"
    flex_needed = open_flex + sum(c for s, c in open_counts.items() if s in flex_positions)
    flex_members = members(lambda p: p.flex_eligible)
    if len(flex_members) < flex_needed:
        return None
    if flex_members:
        prob += pulp.lpSum(pick[i] for i in flex_members) == flex_needed, "FLEX_Logic"

    # At least two games across the whole lineup. Started and unstarted games
    # never overlap, so locked games simply add to the count.
    def game_key(dk_id: int) -> Any:
        if dk_id in projections:
            return projections[dk_id]["game_id"]
        return frozenset([pool[dk_id].team])

    locked_games = {game_key(i) for i in locked_ids}
    if len(locked_games) < 2:
        games: Dict[Any, List[int]] = defaultdict(list)
        for i in fits:
            games[game_key(i)].append(i)
        game_vars = pulp.LpVariable.dicts("Game", range(len(games)), cat="Binary")
        for g_idx, game_members in enumerate(games.values()):
            # A game counts only when someone from it is actually picked.
            prob += game_vars[g_idx] <= pulp.lpSum(pick[i] for i in game_members)
        prob += (
            pulp.lpSum(game_vars.values()) >= 2 - len(locked_games),
            "At_Least_Two_Games",
        )

    if args.max_te is not None:
        locked_te = sum(1 for p in locked_players if p.primary_slot == "TE")
        tes = members(lambda p: p.primary_slot == "TE")
        if tes:
            # An open TE slot must be filled whatever the cap says -- a locked
            # FLEX TE under -te 1 would otherwise make the entry infeasible.
            te_allowance = max(args.max_te - locked_te, open_counts.get("TE", 0))
            prob += (
                pulp.lpSum(pick[i] for i in tes) <= te_allowance,
                "Max_TE_Constraint",
            )

    def team(dk_id: int) -> str:
        return str(projections[dk_id]["Team"])

    for qb in members(lambda p: p.primary_slot == "QB"):
        if args.stack > 0:
            mates = [
                i for i in fits
                if team(i) == team(qb) and fits[i].primary_slot in ("WR", "TE")
            ]
            prob += pulp.lpSum(pick[i] for i in mates) >= args.stack * pick[qb]
        if args.stack_rb:
            mates = [i for i in fits if team(i) == team(qb) and fits[i].primary_slot == "RB"]
            prob += pulp.lpSum(pick[i] for i in mates) >= pick[qb]

    if args.no_dst_opp:
        for dst in members(lambda p: p.primary_slot == "DST"):
            opp = str(projections[dst]["Opp"]).replace("@", "")
            for off in members(lambda p: p.primary_slot != "DST"):
                if team(off) == opp:
                    prob += pick[dst] + pick[off] <= 1

    for dk_id in lock_ids & fits.keys():
        prob += pick[dk_id] == 1

    # -u against the lineups already built for this contest. Locked overlap is
    # fixed, so where it alone exceeds the allowance the best achievable is no
    # further overlap among the new picks.
    for prev in previous:
        shared = [i for i in fits if i in prev]
        if shared:
            allowance = ROSTER_SIZE - args.min_uniques - len(locked_set & prev)
            prob += pulp.lpSum(pick[i] for i in shared) <= max(allowance, 0)

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    return [i for i in fits if pick[i].varValue > 0.5]


def _seat_open_slots(
    open_slots: List[int], slot_labels: List[str], picks: List[DkPoolPlayer]
) -> Optional[Dict[int, DkPoolPlayer]]:
    """
    Seats the solver's picks in an entry's open slots.

    The model chooses players by position count, so seating is post-hoc, as
    in the full optimizer. The picks' position counts fix which position
    supplies the FLEX; within that position, earlier kickoffs take the
    positional slots and the latest kickoff goes to FLEX. A FLEX player can
    be swapped for any RB/WR/TE on a later late-swap run, so this keeps the
    most options open that the picks allow.

    Returns:
        Slot index -> player, or None if the picks cannot fill the slots.
    """
    positional: Dict[str, List[int]] = defaultdict(list)
    flex_slots: List[int] = []
    for idx in open_slots:
        if slot_labels[idx] == FLEX_SLOT:
            flex_slots.append(idx)
        else:
            positional[slot_labels[idx]].append(idx)

    by_position: Dict[str, List[DkPoolPlayer]] = defaultdict(list)
    for player in picks:
        by_position[player.primary_slot].append(player)

    seated: Dict[int, DkPoolPlayer] = {}
    surplus: List[DkPoolPlayer] = []
    for position, players in by_position.items():
        # Earliest kickoff first; within one kickoff, highest salary first
        # (the full optimizer's order), so the cheapest late player is surplus.
        players.sort(key=lambda p: (p.kickoff.timestamp() if p.kickoff else 0.0, -p.salary))
        slots = positional.get(position, [])
        seated.update(zip(slots, players))
        surplus.extend(players[len(slots) :])
    if len(surplus) != len(flex_slots) or any(not p.flex_eligible for p in surplus):
        return None
    seated.update(zip(flex_slots, surplus))
    return seated if len(seated) == len(open_slots) else None


def _print_late_swap_entry(
    number: int,
    total: int,
    entry: DkEntry,
    slot_labels: List[str],
    final_ids: List[Optional[int]],
    original_ids: List[Optional[int]],
    locked: Set[int],
    pool: Dict[int, DkPoolPlayer],
    projections: Dict[int, Dict[str, Any]],
    target: str,
    note: str,
) -> None:
    """Prints one late-swapped entry with a per-slot LOCKED/KEEP/MOVE/NEW status."""

    def stat(dk_id: Optional[int], column: str) -> float:
        row = projections.get(dk_id) if dk_id is not None else None
        return float(row[column]) if row else 0.0

    def score(ids: List[Optional[int]]) -> float:
        return sum(target_value(projections[i], target) for i in ids if i in projections)

    target_label = OPTIMIZATION_TARGETS[target][0]
    present = [i for i in final_ids if i is not None]
    salary = sum(pool[i].salary for i in present if i in pool)
    before, after = score(original_ids), score(final_ids)
    # The target's before -> after leads; the other totals describe the result.
    parts = [f"{target_label}: {before:.2f} -> {after:.2f} ({after - before:+.2f})"]
    parts += [
        f"{column}: {sum(stat(i, column) for i in present):.2f}"
        for column in ("Projection", "Ceiling")
        if column != target_label
    ]
    parts.append(f"Ownership: {sum(stat(i, 'Ownership') for i in present):.2f}%")
    parts.append(f"Salary: ${salary:,}")
    print(f"\n--- Entry {number}/{total}: {entry.entry_id} | {entry.contest_name} ---")
    print(" | ".join(parts))
    if note:
        print(f"  NOTE: {note}")
    print("-" * 97)
    print(
        f"{'Slot':<5} {'Player':<25} {'Pos':<5} {'Team':<5} "
        f"{'Salary':>8} {'Proj':>8} {'Own%':>8} {'Ceiling':>8}  Status"
    )
    print("-" * 97)
    original_set = {i for i in original_ids if i is not None}
    for idx, dk_id in enumerate(final_ids):
        if dk_id is None:
            print(f"{slot_labels[idx]:<5} - EMPTY -")
            continue
        if idx in locked:
            status = "LOCKED"
        elif dk_id == original_ids[idx]:
            status = "KEEP"
        elif dk_id in original_set:
            status = "MOVE"
        else:
            status = "NEW"
        player = pool.get(dk_id)
        name = player.name if player else entry.cells[idx]
        position = player.primary_slot if player else ""
        team = player.team if player else ""
        player_salary = player.salary if player else 0
        print(
            f"{slot_labels[idx]:<5} {name[:25]:<25} {position:<5} {team:<5} "
            f"${player_salary:>7,} {stat(dk_id, 'Projection'):>8.2f} "
            f"{stat(dk_id, 'Ownership'):>7.2f}% {stat(dk_id, 'Ceiling'):>8.2f}  {status}"
        )
    print("-" * 97)


def run_late_swap(
    args: argparse.Namespace,
    players_df: pd.DataFrame,
    target: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """
    Late-swaps every entry in the newest DraftKings entries file in Downloads.

    Players whose games have started stay in their slots. Every other slot is
    re-optimized from the players whose games have not started, and each
    entry must differ from the entries already built for the same contest by
    -u players. An entry with no valid swap is written back unchanged. The
    result is an upload-ready-DKEntries-<timestamp>.csv in Downloads holding
    every entry, each cell DraftKings' own "Name + ID" text.

    Args:
        args: Parsed CLI options.
        players_df: Projections from load_player_data(); matched by DK ID.
        target: Optimization target key.
        now: Moment eligibility is judged at; defaults to the current time.

    Returns:
        Path of the upload file written, or None if nothing was written.
    """
    zone = _game_info_timezone()
    now = now.astimezone(zone) if now else datetime.now(zone)

    entries_path = find_dk_entries_file()
    upload_header, slot_labels, entries, pool = load_dk_entries_file(entries_path, now)
    contests = {entry.contest_id for entry in entries}
    print("\n--- Late Swap ---")
    print(f"Entries file: {entries_path}")
    print(
        f"Clock: {now:%m/%d/%Y %I:%M%p} ET. {len(entries)} entries across "
        f"{len(contests)} contest(s)."
    )
    if args.num_lineups != 1 or args.export:
        print(
            "  NOTE: -n and -e do not apply to --late-swap; every entry is "
            "re-optimized and written to the upload file."
        )

    projections = (
        players_df.drop_duplicates("ID").set_index("ID", drop=False).to_dict("index")
    )
    if not pool.keys() & projections.keys():
        raise ValueError(
            f"No projections ID matches a DraftKings ID in {os.path.basename(entries_path)}. "
            f"Late swap matches players by DraftKings ID, so the projections file "
            f"must carry DraftKings player IDs in its ID column."
        )

    if args.lock:
        print(f"\nLocking players: {args.lock}")
    lock_ids = _ids_for_names(players_df, args.lock, "lock")
    started_locks = sorted(pool[i].name for i in lock_ids if i in pool and pool[i].started)
    if started_locks:
        print(
            f"  NOTE: Games have started for {', '.join(started_locks)}; they "
            f"stay only in the entries that already roster them."
        )
    if args.exclude:
        print(f"\nExcluding players: {args.exclude}")
    exclude_ids = _ids_for_names(players_df, args.exclude, "exclusion")

    candidates = {
        dk_id: player
        for dk_id, player in pool.items()
        if not player.started and dk_id in projections and dk_id not in exclude_ids
    }
    unprojected = sorted(
        p.name for p in pool.values() if not p.started and p.dk_id not in projections
    )
    started_count = sum(1 for p in pool.values() if p.started)
    print(
        f"{started_count} of {len(pool)} pool players' games have started; "
        f"{len(candidates)} players are eligible to swap in."
    )
    if unprojected:
        names = ", ".join(unprojected[:5])
        if len(unprojected) > 5:
            names += f", +{len(unprojected) - 5} more"
        print(
            f"  NOTE: {len(unprojected)} unstarted player(s) have no projection "
            f"and cannot be swapped in: {names}"
        )

    history: Dict[str, List[FrozenSet[int]]] = defaultdict(list)
    upload_rows: List[List[str]] = []
    changed = unchanged_locked = failed = 0

    for number, entry in enumerate(entries, start=1):
        original_ids, locked, open_slots = _split_entry_slots(entry, pool)
        locked_ids = [original_ids[idx] for idx in sorted(locked)]
        final_ids = list(original_ids)
        note = ""
        unknown = [entry.cells[idx] for idx in sorted(locked) if original_ids[idx] not in pool]

        if unknown:
            failed += 1
            note = (
                f"{', '.join(unknown)} not in the player pool, so the salary "
                f"left under the cap is unknown; entry left unchanged."
            )
        elif not open_slots:
            unchanged_locked += 1
            note = "every slot is locked; nothing to swap."
        else:
            open_labels = [slot_labels[idx] for idx in open_slots]
            previous = history[entry.contest_id]
            seated: Optional[Dict[int, DkPoolPlayer]] = None

            # First with -u against this contest's earlier entries, then,
            # if that is infeasible, without it.
            for relaxed, prev in enumerate([previous, []] if previous else [[]]):
                picks = optimize_late_swap_entry(
                    open_labels, locked_ids, pool, candidates, projections,
                    target, args, lock_ids, prev,
                )
                if picks is None:
                    continue
                seated = _seat_open_slots(open_slots, slot_labels, [pool[i] for i in picks])
                if seated is None:
                    # The count identities guarantee a seating, so this means
                    # the model and the seater disagree -- not infeasibility.
                    note = (
                        "the optimizer found a lineup the seating step could not "
                        "place (a bug); entry left unchanged."
                    )
                elif relaxed:
                    note = (
                        f"could not differ by {args.min_uniques} from every earlier "
                        f"entry in this contest; built without that rule."
                    )
                break

            if seated is None:
                failed += 1
                note = note or (
                    "no swap fits the salary cap and your rules "
                    "(-l/-x/-s/-srb/-te/-ndo); entry left unchanged."
                )
            else:
                for idx, player in seated.items():
                    final_ids[idx] = player.dk_id

        if final_ids != original_ids:
            changed += 1
        history[entry.contest_id].append(
            frozenset(i for i in final_ids if i is not None)
        )
        # A cell whose player did not change is copied verbatim; a new one gets
        # the pool's "Name + ID" text, exactly as DraftKings wrote it.
        cells = [
            entry.cells[idx] if dk_id == original_ids[idx] else pool[dk_id].name_id
            for idx, dk_id in enumerate(final_ids)
        ]
        upload_rows.append(
            [entry.entry_id, entry.contest_name, entry.contest_id, entry.entry_fee, *cells]
        )
        _print_late_swap_entry(
            number, len(entries), entry, slot_labels, final_ids, original_ids,
            locked, pool, projections, target, note,
        )

    print(
        f"\nLate swap complete: {changed} of {len(entries)} entries changed, "
        f"{unchanged_locked} fully locked, {failed} with no valid swap."
    )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = os.path.join(DOWNLOADS_DIR, f"{UPLOAD_FILE_PREFIX}-{timestamp}.csv")
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(upload_header)
        writer.writerows(upload_rows)
    print(f"Upload file written to: {output_path}")
    return output_path


def main() -> None:
    """Main orchestrator function for the script."""
    parser = argparse.ArgumentParser(
        description="DraftKings NFL Multi-Lineup Optimizer."
    )
    parser.add_argument(
        "filepath",
        type=str,
        help="Path to the DraftKings projections CSV file.",
    )
    parser.add_argument(
        "-n",
        "--num-lineups",
        type=int,
        default=1,
        help="Number of unique lineups to generate (default: 1).",
    )
    parser.add_argument(
        "-u",
        "--min-uniques",
        type=int,
        default=1,
        help="Minimum number of unique players between lineups (default: 1).",
    )
    parser.add_argument(
        "-e",
        "--export",
        action="store_true",
        help="Export the generated lineups to a CSV file.",
    )
    parser.add_argument(
        "-l",
        "--lock",
        nargs="+",
        help="List of player names to lock into the lineup (case-insensitive).",
    )
    parser.add_argument(
        "-ndo",
        "--no-dst-opp",
        action="store_true",
        help="Disallow selecting a DST and any offensive player from their opponent.",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        nargs="+",
        help="List of player names to exclude from the lineup (case-insensitive).",
    )
    parser.add_argument(
        "-s",
        "--stack",
        type=int,
        nargs="?",
        const=1,
        default=0,
        help="Stack QB with at least N WR/TEs from the same team (default: 1 if flag used).",
    )
    parser.add_argument(
        "-srb",
        "--stack-rb",
        action="store_true",
        help="Stack QB with at least one RB from the same team.",
    )
    parser.add_argument(
        "-te",
        "--max-te",
        type=int,
        help="Maximum number of TEs allowed in a lineup (e.g., 1 to ban TE in FLEX).",
    )
    parser.add_argument(
        "-sf",
        "--small-field",
        action="store_true",
        help=(
            "Display small-field ownership instead of large-field ownership "
            "(display/export only; ownership is not optimized on)."
        ),
    )
    # The scoring target the solver maximizes. Passing neither flag keeps the
    # long-standing projection-only behavior.
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "-c",
        "--ceiling",
        dest="ceiling",
        action="store_true",
        help="Optimize on ceiling instead of projection.",
    )
    target_group.add_argument(
        "-pj",
        "--projceiling",
        dest="projceiling",
        action="store_true",
        help="Optimize on an equally weighted 50/50 blend of projection and ceiling.",
    )
    parser.add_argument(
        "-ls",
        "--late-swap",
        action="store_true",
        help=(
            "Late-swap the entries in the newest DKEntries*.csv in Downloads: "
            "players whose games have started stay put, every other slot is "
            "re-optimized, and upload-ready-DKEntries-<timestamp>.csv is written "
            "to Downloads. -u applies within each contest; -n and -e are ignored."
        ),
    )
    args = parser.parse_args()

    try:
        # 1. Load and prepare data
        target = resolve_optimization_target(args.ceiling, args.projceiling)
        target_label = OPTIMIZATION_TARGETS[target][0]

        ownership_field = (
            OWNERSHIP_SMALL_FIELD if args.small_field else OWNERSHIP_LARGE_FIELD
        )
        players_df = load_player_data(args.filepath, ownership_field)
        print(f"Optimizing on: {target_label}.")
        validate_target_data(players_df, target)

        if args.late_swap:
            run_late_swap(args, players_df, target)
            return

        players_dict = players_df.to_dict("index")
        player_indices = list(players_dict.keys())
        unique_game_ids = list(players_df["game_id"].unique())

        # --- 2. Define the Optimization Problem (once) ---
        prob = pulp.LpProblem("DraftKings_NFL_Multi_Lineup", pulp.LpMaximize)
        player_vars = pulp.LpVariable.dicts("Player", player_indices, cat="Binary")
        game_vars = pulp.LpVariable.dicts("Game", unique_game_ids, cat="Binary")

        # --- 3. Define Objective and Base Constraints (once) ---
        # The objective is a weighted mix of projection and ceiling; the
        # weights come from the chosen target and never change mid-run.
        prob += (
            pulp.lpSum(
                target_value(players_dict[i], target) * player_vars[i]
                for i in player_indices
            ),
            "Total_Target_Value",
        )

        # Salary Cap
        prob += (
            pulp.lpSum(
                players_dict[i]["Salary"] * player_vars[i] for i in player_indices
            )
            <= SALARY_CAP,
            "Salary_Cap",
        )
        # Roster Size
        prob += (
            pulp.lpSum(player_vars[i] for i in player_indices) == ROSTER_SIZE,
            "Total_Players",
        )
        # Positional Requirements
        prob += (
            pulp.lpSum(
                players_dict[i]["is_QB"] * player_vars[i] for i in player_indices
            )
            == 1,
            "QB_Slot",
        )
        prob += (
            pulp.lpSum(
                players_dict[i]["is_RB"] * player_vars[i] for i in player_indices
            )
            >= 2,
            "Min_RB",
        )
        prob += (
            pulp.lpSum(
                players_dict[i]["is_WR"] * player_vars[i] for i in player_indices
            )
            >= 3,
            "Min_WR",
        )
        prob += (
            pulp.lpSum(
                players_dict[i]["is_TE"] * player_vars[i] for i in player_indices
            )
            >= 1,
            "Min_TE",
        )
        prob += (
            pulp.lpSum(
                players_dict[i]["is_DST"] * player_vars[i] for i in player_indices
            )
            == 1,
            "DST_Slot",
        )
        prob += (
            pulp.lpSum(
                players_dict[i]["is_FLEX"] * player_vars[i] for i in player_indices
            )
            == 7,
            "FLEX_Logic",
        )
        # Game Diversity Rule
        for p_idx in player_indices:
            game_id = players_dict[p_idx]["game_id"]
            prob += (
                game_vars[game_id] >= player_vars[p_idx],
                f"Link_Player_{p_idx}_to_Game",
            )
        prob += (
            pulp.lpSum(game_vars[gid] for gid in unique_game_ids) >= 2,
            "At_Least_Two_Games",
        )

        # --- Locking Players ---
        if args.lock:
            print(f"\nLocking players: {args.lock}")
            for player_name in args.lock:
                # Case-insensitive matching
                matches = players_df[players_df["Player"].str.lower() == player_name.lower()]
                if matches.empty:
                    print(f"  WARNING: Player '{player_name}' not found in projections. Skipping lock.")
                    continue
                
                for idx in matches.index:
                    prob += player_vars[idx] == 1, f"Lock_{idx}"
                    print(f"  Locked: {players_df.loc[idx, 'Player']} (ID: {players_df.loc[idx, 'ID']})")

        # --- Excluding Players ---
        if args.exclude:
            print(f"\nExcluding players: {args.exclude}")
            for player_name in args.exclude:
                # Case-insensitive matching
                matches = players_df[players_df["Player"].str.lower() == player_name.lower()]
                if matches.empty:
                    print(f"  WARNING: Player '{player_name}' not found in projections. Skipping exclusion.")
                    continue
                
                for idx in matches.index:
                    prob += player_vars[idx] == 0, f"Exclude_{idx}"
                    print(f"  Excluded: {players_df.loc[idx, 'Player']} (ID: {players_df.loc[idx, 'ID']})")

        # --- Stacking Rules ---
        if args.stack > 0:
            print(f"\nEnforcing 'QB + {args.stack} WR/TE Stack' rule...")
            qb_players = players_df[players_df["Position"] == "QB"]
            for qb_idx, qb_row in qb_players.iterrows():
                team = qb_row["Team"]
                stack_partners_indices = players_df[
                    (players_df["Team"] == team)
                    & (players_df["Position"].isin(["WR", "TE"]))
                ].index
                prob += (
                    pulp.lpSum(player_vars[i] for i in stack_partners_indices) >= args.stack * player_vars[qb_idx],
                    f"Stack_QB_{qb_idx}_{team}_WRTE",
                )

        if args.stack_rb:
            print("\nEnforcing 'QB + RB Stack' rule...")
            qb_players = players_df[players_df["Position"] == "QB"]
            for qb_idx, qb_row in qb_players.iterrows():
                team = qb_row["Team"]
                rb_partners_indices = players_df[
                    (players_df["Team"] == team)
                    & (players_df["Position"] == "RB")
                ].index
                prob += (
                    pulp.lpSum(player_vars[i] for i in rb_partners_indices) >= player_vars[qb_idx],
                    f"Stack_QB_{qb_idx}_{team}_RB",
                )

        # --- No DST vs Opponent Constraint ---
        if args.no_dst_opp:
            print("\nEnforcing 'No DST vs Opponent' rule...")
            dst_players = players_df[players_df["Position"] == "DST"]
            for dst_idx, dst_row in dst_players.iterrows():
                opp_team = str(dst_row["Opp"]).replace("@", "")
                # Find offensive players on the opponent team
                opp_offense_indices = players_df[
                    (players_df["Team"] == opp_team) & 
                    (players_df["Position"].isin(["QB", "RB", "WR", "TE"]))
                ].index
                for off_idx in opp_offense_indices:
                    prob += player_vars[dst_idx] + player_vars[off_idx] <= 1, f"No_DST_{dst_idx}_vs_Opp_{off_idx}"

        # --- Max TE Constraint ---
        if args.max_te is not None:
            print(f"\nEnforcing maximum of {args.max_te} TE(s)...")
            prob += (
                pulp.lpSum(
                    players_dict[i]["is_TE"] * player_vars[i] for i in player_indices
                )
                <= args.max_te,
                "Max_TE_Constraint",
            )

        # --- 4. Iterative Optimization Loop ---
        generated_lineups_indices = []
        max_players_can_share = ROSTER_SIZE - args.min_uniques
        all_lineups_export_data = []

        # DraftKings "Name + ID" values for the upload row that follows each
        # lineup's totals. Absent or unreadable entries file: no upload rows.
        dk_lookup = load_dk_name_ids() if args.export else None
        if args.export and dk_lookup is None:
            print(
                f"\nNOTE: No readable DraftKings entries file at {DK_ENTRIES_PATH}. "
                f"The export will omit the upload rows."
            )

        for i in range(args.num_lineups):
            print(f"\n--- Generating Lineup #{i + 1} ---")

            # Solve the problem
            prob.solve(pulp.PULP_CBC_CMD(msg=0))
            status = pulp.LpStatus[prob.status]

            if status != "Optimal":
                print(f"Could not find an optimal lineup. Status: {status}")
                if i == 0:
                    print(
                        "This means no lineup exists that satisfies the base constraints."
                    )
                else:
                    print(f"Stopped after generating {i} unique lineups.")
                break

            # Extract and store the new lineup
            selected_indices = [
                p_idx for p_idx in player_indices if player_vars[p_idx].varValue > 0.5
            ]

            # Add diversity constraint for THIS lineup to prevent it from being chosen in future iterations
            prob += (
                pulp.lpSum(player_vars[p_idx] for p_idx in selected_indices)
                <= max_players_can_share,
                f"Diversity_from_lineup_{i + 1}",
            )

            generated_lineups_indices.append(selected_indices)

            # --- 5. Display the current lineup ---
            lineup_df = players_df.loc[selected_indices].copy()
            projection = lineup_df["Projection"].sum()
            salary = int(lineup_df["Salary"].sum())

            # Assign players to specific slots for display
            assigned_lineup = _assign_flex_positions(lineup_df)

            # Define display order
            display_order = [
                "QB",
                "RB1",
                "RB2",
                "WR1",
                "WR2",
                "WR3",
                "TE1",
                "FLEX",
                "DST",
            ]

            print(f"\n--- Optimal NFL Lineup #{i + 1} ---")
            total_ownership = lineup_df["Ownership"].sum()
            total_ceiling = lineup_df["Ceiling"].sum()
            # A blended run's score matches neither printed total, so show it;
            # for the other targets the Projection/Ceiling lines already are it.
            # The weights come from OPTIMIZATION_TARGETS, the same place the
            # objective reads them, so a retuned blend can never print one
            # number while the solver maximizes another.
            if target == TARGET_BLEND:
                _, proj_weight, ceiling_weight = OPTIMIZATION_TARGETS[target]
                print(f"Blend Score ({target_label}): "
                      f"{proj_weight * projection + ceiling_weight * total_ceiling:.2f}")
            print(f"Projection: {projection:.2f}")
            print(f"Ownership: {total_ownership:.2f}%")
            print(f"Ceiling: {total_ceiling:.2f}")
            print(f"Salary: ${salary:,}")
            print("-" * 90)
            print(
                f"{'Slot':<5} {'Player':<25} {'Pos':<5} {'Team':<5} "
                f"{'Salary':>8} {'Proj':>8} {'Own%':>8} {'Ceiling':>8}"
            )
            print("-" * 90)

            for slot in display_order:
                player = assigned_lineup.get(slot)
                if player:
                    # Re-label slot for cleaner output (e.g., "RB1" -> "RB")
                    display_slot = re.sub(r"\d", "", slot)
                    print(
                        f"{display_slot:<5} {player['Player']:<25} {player['Position']:<5} "
                        f"{player['Team']:<5} ${int(player['Salary']):>7,} "
                        f"{player['Projection']:>8.2f} {player['Ownership']:>7.2f}% "
                        f"{player['Ceiling']:>8.2f}"
                    )
                else:
                    print(f"{slot:<5} - ERROR ASSIGNING PLAYER -")

            print("-" * 90)

            # Collect data for export if requested
            if args.export:
                lineup_export_rows = []
                for slot in display_order:
                    player_data = assigned_lineup.get(slot)
                    if player_data:
                        row = player_data.copy()
                        row["Lineup_ID"] = i + 1
                        row["Slot"] = slot
                        row["Player_ID"] = row.get("ID")
                        lineup_export_rows.append(row)
                all_lineups_export_data.extend(lineup_export_rows)

                # Summary row for the lineup, matching the totals printed above.
                all_lineups_export_data.append(
                    {
                        "Lineup_ID": i + 1,
                        "Player_ID": "",
                        "Slot": "TOTAL",
                        "Player": "",
                        "Position": "",
                        "Team": "",
                        "Salary": salary,
                        "Projection": round(projection, 2),
                        "Ownership": round(total_ownership, 2),
                        "Ceiling": round(total_ceiling, 2),
                    }
                )

                # DraftKings upload row: the same lineup laid out horizontally,
                # one "Name + ID" per roster slot in display order.
                dk_values = build_dk_upload_values(lineup_export_rows, dk_lookup)
                if dk_values:
                    dk_row = {"Lineup_ID": i + 1}
                    dk_row.update(zip(EXPORT_COLUMNS[1:], dk_values))
                    all_lineups_export_data.append(dk_row)

        # --- 5. Export All Lineups to CSV ---
        if args.export and all_lineups_export_data:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = (
                f"nfl_classic_multi_lineups"
                f"{TARGET_FILE_SUFFIXES[target]}_{timestamp}.csv"
            )
            filepath = os.path.join(EXPORT_DIR, filename)

            export_df = pd.DataFrame(all_lineups_export_data)
            export_df = export_df.reindex(columns=EXPORT_COLUMNS)

            export_df.to_csv(filepath, index=False)
            print(f"\nAll generated lineups exported to: {filepath}")

    except (FileNotFoundError, ValueError) as e:
        print(f"\nFATAL ERROR: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")


if __name__ == "__main__":
    main()
