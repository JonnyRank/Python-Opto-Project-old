"""
DraftKings NFL Showdown (Captain Mode) Multi-Lineup Optimizer.

This script ingests a CSV file with Showdown player projections and uses mixed
integer linear programming (PuLP + HiGHS) to find a specified number of unique,
optimal lineups that maximize total projected points, subject to DraftKings'
Showdown contest rules.

Showdown roster construction:
    - 6 total roster slots: 1 Captain (CPT) + 5 FLEX.
    - Any position (QB, RB, WR, TE, K, DST) is eligible for any slot.
    - The Captain scores 1.5x fantasy points and costs 1.5x salary.
    - A player may occupy the Captain slot OR a FLEX slot, never both.
    - The lineup must contain at least one player from each of the two teams.
    - Total salary must stay within the $50,000 cap.

The script is run from the command line, specifying the path to the
projections CSV file as an argument.

Input Arguments:
    python NFL-SD-Multi-Opto-v1.0.py "path" -n -u -e -l -x -ms
    python <script> <proj file> <# of lineups> <min uniques> <export to CSV> <lock players> <exclude players> <max salary>
    # Means: python <script> <projections file> -n <number of lineups> -u <min uniques> -e <export to CSV>
    python NFL-SD-Multi-Opto-v1.0.py "C:\\path\\to\\projections.csv" -n 5 -u 2 -e -l "Drake Maye:CPT" -ms 49800

Key Features:
- Loads Showdown player data (CPT salary / CPT projection / CPT ownership) from
  a command-line specified CSV file.
- Cleans and validates player salary, projection, and ownership data.
- Models the Captain and FLEX slots as separate binary decisions per player.
- Uses the HiGHS solver (via highspy) through PuLP to solve each lineup.
- Enforces constraints for salary cap, max salary, roster composition,
  both-teams representation, and lineup diversity.
- Slot-aware ownership: the Captain contributes its CPT ownership and each FLEX
  contributes (Total Own - CPT Own), so the printed total is true product
  ownership for the exact lineup built.
- Prints a well-formatted, human-readable lineup as CPT + 5 FLEX, with the FLEX
  players ordered from highest to lowest salary.
"""

import os
import re
import argparse
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import pulp

# --- Constants ---
SALARY_CAP: int = 50000
ROSTER_SIZE: int = 6
FLEX_SLOTS: int = ROSTER_SIZE - 1
CAPTAIN_MULTIPLIER: float = 1.5
EXPORT_DIR: str = r"G:\My Drive\Documents\NFL-DFS\csv-exports"
# John Rankin's (Dad) Downloads folder path: r"C:\Users\jdr0824\Downloads"

# Maps the header names used by the Showdown projections export to the internal
# names used throughout this script.
# Order matters: when a file carries more than one alias for the same internal
# name (e.g. both "Total Own" and "Own"), the first one listed wins and the
# rest are left untouched, so the rename can never produce duplicate columns.
COLUMN_ALIASES: Dict[str, str] = {
    "Pos": "Position",
    "Proj": "Projection",
    "Total Own": "Ownership",
    "Own": "Ownership",
    "CPT Own": "CptOwnership",
    "CPT Salary": "CptSalary",
    "CPT Proj": "CptProjection",
}

# Valid slot qualifiers for the -l / -x arguments, e.g. "Drake Maye:CPT".
SLOT_CPT: str = "CPT"
SLOT_FLEX: str = "FLEX"
VALID_SLOTS: Tuple[str, str] = (SLOT_CPT, SLOT_FLEX)

TABLE_WIDTH: int = 91


def _clean_numeric(series: pd.Series) -> pd.Series:
    """Strips currency, percent, and whitespace characters, then coerces to numeric."""
    return (
        series.astype(str)
        .str.replace(r"[\$,%\s]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
    )


def _safe_name(text: Any) -> str:
    """Sanitizes arbitrary text into a token safe for use in a PuLP constraint name."""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(text))


def load_player_data(filepath: str) -> pd.DataFrame:
    """
    Loads and preprocesses Showdown player data from the projections CSV file.

    Args:
        filepath: The absolute path to the projections CSV file.

    Returns:
        A pandas DataFrame containing cleaned and prepared player data ready
        for optimization, including derived Captain-slot columns.

    Raises:
        FileNotFoundError: If the specified file does not exist.
        ValueError: If the file is empty, critical columns are missing, or the
            slate does not consist of exactly two teams.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Projections file not found at: {filepath}")

    try:
        df = pd.read_csv(filepath, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        raise ValueError(f"The projections file is empty: {filepath}")

    print(f"Successfully loaded {len(df)} players from {os.path.basename(filepath)}.")

    # --- Data Cleaning and Preparation ---
    # Rename columns for consistency, applying only the aliases actually present
    # and never letting two of them collapse onto the same internal name.
    rename_map: Dict[str, str] = {}
    claimed = set(df.columns)
    for source, target in COLUMN_ALIASES.items():
        if source not in df.columns or target in claimed:
            continue
        rename_map[source] = target
        claimed.add(target)
    df.rename(columns=rename_map, inplace=True)

    required_cols = ["Player", "Position", "Team", "Salary", "Projection"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"Projections file is missing required column(s): {', '.join(missing)}"
        )

    # Clean every numeric column that may arrive as "$10,600" or "67.4%".
    for col in [
        "Salary",
        "Projection",
        "Ceiling",
        "Ownership",
        "CptOwnership",
        "CptSalary",
        "CptProjection",
    ]:
        if col in df.columns:
            df[col] = _clean_numeric(df[col])

    # Optional columns get sensible defaults so downstream math never breaks.
    if "Ceiling" not in df.columns:
        print("  NOTE: No 'Ceiling' column found. Ceiling values will display as 0.00.")
        df["Ceiling"] = 0.0
    if "Ownership" not in df.columns:
        print("  NOTE: No 'Total Own' column found. Ownership will display as 0.00%.")
        df["Ownership"] = 0.0
    if "CptOwnership" not in df.columns:
        print("  NOTE: No 'CPT Own' column found. Captain ownership treated as 0.00%.")
        df["CptOwnership"] = 0.0

    df["Ceiling"] = df["Ceiling"].fillna(0.0)
    df["Ownership"] = df["Ownership"].fillna(0.0)
    df["CptOwnership"] = df["CptOwnership"].fillna(0.0)

    # Drop players with missing critical data for optimization.
    df.dropna(subset=["Player", "Position", "Team", "Salary", "Projection"], inplace=True)
    df["Salary"] = df["Salary"].astype(int)

    # --- Derived Captain-Slot Columns ---
    # Prefer the values supplied by the projections file; fall back to the
    # standard 1.5x Captain multiplier when a column is absent or blank.
    if "CptSalary" in df.columns:
        df["CptSalary"] = df["CptSalary"].fillna(df["Salary"] * CAPTAIN_MULTIPLIER)
    else:
        df["CptSalary"] = df["Salary"] * CAPTAIN_MULTIPLIER
    df["CptSalary"] = df["CptSalary"].round().astype(int)

    if "CptProjection" in df.columns:
        df["CptProjection"] = df["CptProjection"].fillna(
            df["Projection"] * CAPTAIN_MULTIPLIER
        )
    else:
        df["CptProjection"] = df["Projection"] * CAPTAIN_MULTIPLIER

    # No Captain ceiling is published, so derive it from the 1.5x multiplier.
    df["CptCeiling"] = df["Ceiling"] * CAPTAIN_MULTIPLIER

    # Slot-aware ownership: "Total Own" already includes "CPT Own", so a player's
    # FLEX-only ownership is the difference between the two.
    df["FlexOwnership"] = (df["Ownership"] - df["CptOwnership"]).clip(lower=0.0)

    # --- Slate Validation ---
    teams = sorted(df["Team"].astype(str).unique())
    if len(teams) != 2:
        raise ValueError(
            f"Showdown slates must contain exactly two teams, but {len(teams)} were "
            f"found: {', '.join(teams)}. Filter the projections file down to a "
            f"single game before optimizing."
        )

    print(
        f"Data preprocessed. {len(df)} players available for optimization "
        f"({teams[0]} vs {teams[1]})."
    )
    return df


def parse_player_selector(token: str) -> Tuple[str, Optional[str]]:
    """
    Splits a -l / -x argument into a player name and an optional slot qualifier.

    Accepts a bare name ("Drake Maye") meaning "any slot", or a name with a
    trailing ":CPT" / ":FLEX" suffix to target one specific roster slot.

    Args:
        token: The raw command-line token.

    Returns:
        A (player_name, slot) tuple where slot is "CPT", "FLEX", or None.
    """
    if ":" in token:
        name, _, suffix = token.rpartition(":")
        if name.strip() and suffix.strip().upper() in VALID_SLOTS:
            return name.strip(), suffix.strip().upper()
    return token.strip(), None


def _dedupe_selectors(tokens: Sequence[str]) -> List[Tuple[str, Optional[str]]]:
    """
    Parses -l / -x tokens into (name, slot) pairs, dropping exact repeats.

    Repeating a selector is a no-op for the model but would otherwise generate
    two PuLP constraints with the same name, which PuLP rejects outright.
    """
    seen = set()
    parsed: List[Tuple[str, Optional[str]]] = []
    for token in tokens:
        name, slot = parse_player_selector(token)
        key = (name.lower(), slot)
        if key in seen:
            print(f"  NOTE: Ignoring repeated selector '{token}'.")
            continue
        seen.add(key)
        parsed.append((name, slot))
    return parsed


def _find_player_indices(players_df: pd.DataFrame, player_name: str) -> pd.Index:
    """
    Returns the DataFrame indices matching a player name, case-insensitively.

    Names are matched without regard to team. Two players sharing a name on a
    single Showdown slate is vanishingly rare, but the selector would then apply
    to both of them, so say so rather than doing it silently.
    """
    matches = players_df[
        players_df["Player"].astype(str).str.strip().str.lower() == player_name.lower()
    ].index
    teams = sorted(players_df.loc[matches, "Team"].astype(str).unique())
    if len(teams) > 1:
        print(
            f"  WARNING: '{player_name}' matches players on more than one team "
            f"({', '.join(teams)}). The selector applies to all of them."
        )
    return matches


def build_lineup_rows(
    players_df: pd.DataFrame, captain_idx: Any, flex_indices: Sequence[Any]
) -> List[Dict[str, Any]]:
    """
    Converts a solved lineup into ordered, slot-adjusted display rows.

    The Captain is listed first with its 1.5x salary, projection, ceiling, and
    its Captain-specific ownership. The five FLEX players follow, ordered from
    highest to lowest FLEX salary.

    Args:
        players_df: The full player DataFrame.
        captain_idx: Index of the player selected at Captain.
        flex_indices: Indices of the five players selected at FLEX.

    Returns:
        A list of six dictionaries, one per roster slot, in display order.
    """
    captain = players_df.loc[captain_idx]
    rows: List[Dict[str, Any]] = [
        {
            "Slot": SLOT_CPT,
            "Player": captain["Player"],
            "Position": captain["Position"],
            "Team": captain["Team"],
            "Salary": int(captain["CptSalary"]),
            "Projection": float(captain["CptProjection"]),
            "Ownership": float(captain["CptOwnership"]),
            "Ceiling": float(captain["CptCeiling"]),
        }
    ]

    flex_df = players_df.loc[list(flex_indices)].sort_values(
        by=["Salary", "Projection"], ascending=False
    )
    for _, player in flex_df.iterrows():
        rows.append(
            {
                "Slot": SLOT_FLEX,
                "Player": player["Player"],
                "Position": player["Position"],
                "Team": player["Team"],
                "Salary": int(player["Salary"]),
                "Projection": float(player["Projection"]),
                "Ownership": float(player["FlexOwnership"]),
                "Ceiling": float(player["Ceiling"]),
            }
        )
    return rows


def print_lineup(lineup_number: int, rows: List[Dict[str, Any]]) -> None:
    """Prints a single Showdown lineup in a human-readable table."""
    captain_ownership = rows[0]["Ownership"]
    total_projection = sum(r["Projection"] for r in rows)
    total_ownership = sum(r["Ownership"] for r in rows)
    total_ceiling = sum(r["Ceiling"] for r in rows)
    total_salary = sum(r["Salary"] for r in rows)

    print(f"\n--- Optimal NFL Showdown Lineup #{lineup_number} ---")
    print(f"Projection: {total_projection:.2f}")
    print(f"Total Ownership: {total_ownership:.2f}%")
    print(f"Captain Ownership: {captain_ownership:.2f}%")
    print(f"Ceiling: {total_ceiling:.2f}")
    print(f"Salary: ${total_salary:,} (${SALARY_CAP - total_salary:,} remaining)")
    print("-" * TABLE_WIDTH)
    print(
        f"{'Slot':<6} {'Player':<25} {'Pos':<5} {'Team':<6} "
        f"{'Salary':>8} {'Proj':>8} {'Own%':>8} {'Ceiling':>9}"
    )
    print("-" * TABLE_WIDTH)
    for row in rows:
        print(
            f"{row['Slot']:<6} {str(row['Player']):<25} {str(row['Position']):<5} "
            f"{str(row['Team']):<6} ${row['Salary']:>7,} "
            f"{row['Projection']:>8.2f} {row['Ownership']:>7.2f}% "
            f"{row['Ceiling']:>9.2f}"
        )
    print("-" * TABLE_WIDTH)


def main() -> None:
    """Main orchestrator function for the script."""
    parser = argparse.ArgumentParser(
        description="DraftKings NFL Showdown Multi-Lineup Optimizer."
    )
    parser.add_argument(
        "filepath",
        type=str,
        help="Path to the DraftKings Showdown projections CSV file.",
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
        help=(
            "Minimum number of unique roster spots between lineups (default: 1). "
            "Uniqueness is slot-aware, so the same six players with a different "
            "Captain counts as two uniques."
        ),
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
        help=(
            "Player names to lock into the lineup (case-insensitive). Append "
            "':CPT' or ':FLEX' to lock a player into a specific slot, "
            'e.g. -l "Drake Maye:CPT" "A.J. Brown".'
        ),
    )
    parser.add_argument(
        "-x",
        "--exclude",
        nargs="+",
        help=(
            "Player names to exclude from the lineup (case-insensitive). Append "
            "':CPT' or ':FLEX' to ban a player from only that slot, "
            'e.g. -x "Sam Darnold:CPT".'
        ),
    )
    parser.add_argument(
        "-ms",
        "--max-salary",
        type=int,
        default=SALARY_CAP,
        help=(
            f"Maximum total lineup salary (default: {SALARY_CAP:,}). Values above "
            f"the ${SALARY_CAP:,} DraftKings cap are clamped to the cap."
        ),
    )
    args = parser.parse_args()

    try:
        # --- 1. Validate arguments ---
        if args.num_lineups < 1:
            raise ValueError("--num-lineups must be at least 1.")
        if not 1 <= args.min_uniques <= ROSTER_SIZE:
            raise ValueError(
                f"--min-uniques must be between 1 and {ROSTER_SIZE} (roster size)."
            )
        max_salary = min(args.max_salary, SALARY_CAP)
        if args.max_salary > SALARY_CAP:
            print(
                f"\nNOTE: --max-salary ${args.max_salary:,} exceeds the DraftKings "
                f"cap. Clamping to ${SALARY_CAP:,}."
            )
        if max_salary <= 0:
            raise ValueError("--max-salary must be a positive number.")

        # --- 2. Load and prepare data ---
        players_df = load_player_data(args.filepath)
        players_dict = players_df.to_dict("index")
        player_indices = list(players_dict.keys())
        teams = sorted(players_df["Team"].astype(str).unique())

        # --- 3. Define the Optimization Problem (once) ---
        prob = pulp.LpProblem("DraftKings_NFL_Showdown_Multi_Lineup", pulp.LpMaximize)
        # Two independent binary decisions per player: Captain and FLEX.
        cpt_vars = pulp.LpVariable.dicts("CPT", player_indices, cat="Binary")
        flex_vars = pulp.LpVariable.dicts("FLEX", player_indices, cat="Binary")

        # --- 4. Objective and Base Constraints (once) ---
        prob += (
            pulp.lpSum(
                players_dict[i]["CptProjection"] * cpt_vars[i]
                + players_dict[i]["Projection"] * flex_vars[i]
                for i in player_indices
            ),
            "Total_Projection",
        )

        # Salary Cap (respects --max-salary, which never exceeds the DK cap)
        prob += (
            pulp.lpSum(
                players_dict[i]["CptSalary"] * cpt_vars[i]
                + players_dict[i]["Salary"] * flex_vars[i]
                for i in player_indices
            )
            <= max_salary,
            "Salary_Cap",
        )
        # Exactly one Captain
        prob += (
            pulp.lpSum(cpt_vars[i] for i in player_indices) == 1,
            "Captain_Slot",
        )
        # Exactly five FLEX
        prob += (
            pulp.lpSum(flex_vars[i] for i in player_indices) == FLEX_SLOTS,
            "Flex_Slots",
        )
        # A player cannot be rostered at both Captain and FLEX
        for i in player_indices:
            prob += (cpt_vars[i] + flex_vars[i] <= 1, f"One_Slot_Per_Player_{i}")
        # A player listed on more than one row (duplicate projections export)
        # must still occupy at most one roster spot.
        duplicate_groups = {
            key: idxs
            for key, idxs in players_df.groupby(
                [
                    players_df["Player"].astype(str).str.strip().str.lower(),
                    players_df["Team"].astype(str),
                ]
            ).groups.items()
            if len(idxs) > 1
        }
        if duplicate_groups:
            print(
                f"\nNOTE: {len(duplicate_groups)} player(s) appear on multiple rows. "
                f"Constraining each to at most one roster spot:"
            )
            for (name, team), idxs in duplicate_groups.items():
                print(f"  {name} ({team}) - {len(idxs)} rows")
                prob += (
                    pulp.lpSum(cpt_vars[i] + flex_vars[i] for i in idxs) <= 1,
                    f"One_Row_Per_Player_{_safe_name(name)}_{_safe_name(team)}",
                )

        # Lineups must include at least one player from each team
        for team in teams:
            team_indices = players_df[players_df["Team"].astype(str) == team].index
            prob += (
                pulp.lpSum(cpt_vars[i] + flex_vars[i] for i in team_indices) >= 1,
                f"Min_One_From_{_safe_name(team)}",
            )

        # --- Locking Players ---
        if args.lock:
            print(f"\nLocking players: {args.lock}")
            for player_name, slot in _dedupe_selectors(args.lock):
                matches = _find_player_indices(players_df, player_name)
                if matches.empty:
                    print(
                        f"  WARNING: Player '{player_name}' not found in projections. "
                        f"Skipping lock."
                    )
                    continue

                tag = _safe_name(f"{player_name}_{slot or 'ANY'}")
                if slot == SLOT_CPT:
                    expression = pulp.lpSum(cpt_vars[i] for i in matches)
                elif slot == SLOT_FLEX:
                    expression = pulp.lpSum(flex_vars[i] for i in matches)
                else:
                    expression = pulp.lpSum(
                        cpt_vars[i] + flex_vars[i] for i in matches
                    )
                prob += (expression == 1, f"Lock_{tag}")
                print(
                    f"  Locked: {players_df.loc[matches[0], 'Player']} "
                    f"@ {slot or 'ANY SLOT'}"
                )

        # --- Excluding Players ---
        if args.exclude:
            print(f"\nExcluding players: {args.exclude}")
            for player_name, slot in _dedupe_selectors(args.exclude):
                matches = _find_player_indices(players_df, player_name)
                if matches.empty:
                    print(
                        f"  WARNING: Player '{player_name}' not found in projections. "
                        f"Skipping exclusion."
                    )
                    continue

                for idx in matches:
                    tag = _safe_name(f"{idx}_{slot or 'ANY'}")
                    if slot == SLOT_CPT:
                        prob += (cpt_vars[idx] == 0, f"Exclude_{tag}")
                    elif slot == SLOT_FLEX:
                        prob += (flex_vars[idx] == 0, f"Exclude_{tag}")
                    else:
                        prob += (
                            cpt_vars[idx] + flex_vars[idx] == 0,
                            f"Exclude_{tag}",
                        )
                    print(
                        f"  Excluded: {players_df.loc[idx, 'Player']} "
                        f"@ {slot or 'ANY SLOT'}"
                    )

        # --- 5. Iterative Optimization Loop ---
        solver = pulp.HiGHS(msg=False)
        if not solver.available():
            raise ValueError(
                "The HiGHS solver is not available. Install it with: pip install highspy"
            )

        max_slots_can_share = ROSTER_SIZE - args.min_uniques
        all_lineups_export_data: List[Dict[str, Any]] = []

        for i in range(args.num_lineups):
            print(f"\n--- Generating Lineup #{i + 1} ---")

            prob.solve(solver)
            status = pulp.LpStatus[prob.status]

            if status != "Optimal":
                print(f"Could not find an optimal lineup. Status: {status}")
                if i == 0:
                    print(
                        "This means no lineup exists that satisfies the constraints."
                    )
                    if args.lock or args.exclude or max_salary < SALARY_CAP:
                        print(
                            "  Check your --lock / --exclude selections and "
                            "--max-salary; they are the usual cause."
                        )
                else:
                    print(f"Stopped after generating {i} unique lineups.")
                break

            captain_idx = next(
                idx
                for idx in player_indices
                if (cpt_vars[idx].varValue or 0) > 0.5
            )
            flex_indices = [
                idx
                for idx in player_indices
                if (flex_vars[idx].varValue or 0) > 0.5
            ]

            # Diversity constraint: this exact set of roster spots (slot-aware)
            # may not repeat in any future lineup. Promoting a FLEX to Captain
            # therefore counts as two unique roster spots.
            prob += (
                cpt_vars[captain_idx]
                + pulp.lpSum(flex_vars[idx] for idx in flex_indices)
                <= max_slots_can_share,
                f"Diversity_from_lineup_{i + 1}",
            )

            # --- 6. Display the current lineup ---
            rows = build_lineup_rows(players_df, captain_idx, flex_indices)
            print_lineup(i + 1, rows)

            # Collect data for export if requested
            if args.export:
                for row in rows:
                    export_row = row.copy()
                    export_row["Lineup_ID"] = i + 1
                    all_lineups_export_data.append(export_row)

        # --- 7. Export All Lineups to CSV ---
        if args.export and all_lineups_export_data:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"nfl_showdown_multi_lineups_{timestamp}.csv"
            export_path = os.path.join(EXPORT_DIR, filename)

            export_df = pd.DataFrame(all_lineups_export_data)
            columns_to_export = [
                "Lineup_ID",
                "Slot",
                "Player",
                "Position",
                "Team",
                "Salary",
                "Projection",
                "Ownership",
                "Ceiling",
            ]
            export_df = export_df[
                [c for c in columns_to_export if c in export_df.columns]
            ]
            # Round derived floats so the export doesn't carry binary-float noise.
            for col in ["Projection", "Ownership", "Ceiling"]:
                if col in export_df.columns:
                    export_df[col] = export_df[col].round(2)

            export_df.to_csv(export_path, index=False)
            print(f"\nAll generated lineups exported to: {export_path}")

    except (FileNotFoundError, ValueError) as e:
        print(f"\nFATAL ERROR: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {type(e).__name__}: {e}")
        # Send the traceback to stderr so stdout stays clean for the lineups
        # while a bug report still carries something actionable.
        traceback.print_exc()


if __name__ == "__main__":
    main()
