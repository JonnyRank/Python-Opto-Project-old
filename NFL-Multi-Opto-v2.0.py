"""
DraftKings NFL Multi-Lineup Optimizer.

This script ingests a CSV file with player projections and uses linear
programming to find a specified number of unique, optimal lineups that
maximize total projected points, subject to DraftKings' classic NFL contest rules.

The script is run from the command line, specifying the path to the
projections CSV file as an argument.

Input Arguments:
    python NFL-Multi-Opto-v2.0.py "path" -n -u -e -l -s -ndo
    python <script> <proj file> <# of lineups> <min uniques> <export to CSV> <lock players> <stack QB with WR/TE> <no DST vs Opp>
    python NFL-Multi-Opto-v2.0.py "C:\\path\\to\\projections.csv" -n 5 -u 2 -e -l "Josh Allen" -s -ndo

Key Features:
- Loads player data from a command-line specified CSV file.
- Cleans and validates player salary, projection, and ownership data.
- Identifies unique games to enforce the "at least two games" rule.
- Uses the PuLP library to model and solve the optimization problem.
- Enforces constraints for salary cap, roster composition (QB, RB, WR, TE, FLEX, DST),
  and lineup diversity.
- Prints a well-formatted, human-readable optimal lineup.
"""

import os
import re
import argparse
from datetime import datetime
from typing import Any, Dict, FrozenSet

import pandas as pd
import pulp

# --- Constants ---
SALARY_CAP: int = 50000
ROSTER_SIZE: int = 9
EXPORT_DIR: str = r"G:\My Drive\Documents\NFL-DFS\csv-exports"


def load_player_data(filepath: str) -> pd.DataFrame:
    """
    Loads and preprocesses player data from the projections CSV file.

    Args:
        filepath: The absolute path to the projections CSV file.

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
    # Rename columns for consistency
    df.rename(columns={"Proj": "Projection", "Own": "Ownership"}, inplace=True)

    # Clean Salary column (e.g., "$6,000 " -> 6000)
    df["Salary"] = (
        df["Salary"]
        .astype(str)
        .str.replace(r"[\$,\s]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Clean Ownership column (e.g., "11.70%" -> 11.70)
    df["Ownership"] = (
        df["Ownership"]
        .astype(str)
        .str.replace("%", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Drop players with missing critical data for optimization
    critical_cols = ["ID", "Salary", "Projection", "Position"]
    df.dropna(subset=critical_cols, inplace=True)
    df["ID"] = df["ID"].astype(int)

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
        action="store_true",
        help="Stack QB with at least one WR/TE from the same team.",
    )
    args = parser.parse_args()

    try:
        # 1. Load and prepare data
        players_df = load_player_data(args.filepath)
        players_dict = players_df.to_dict("index")
        player_indices = list(players_dict.keys())
        unique_game_ids = list(players_df["game_id"].unique())

        # --- 2. Define the Optimization Problem (once) ---
        prob = pulp.LpProblem("DraftKings_NFL_Multi_Lineup", pulp.LpMaximize)
        player_vars = pulp.LpVariable.dicts("Player", player_indices, cat="Binary")
        game_vars = pulp.LpVariable.dicts("Game", unique_game_ids, cat="Binary")

        # --- 3. Define Objective and Base Constraints (once) ---
        prob += (
            pulp.lpSum(
                players_dict[i]["Projection"] * player_vars[i] for i in player_indices
            ),
            "Total_Projection",
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

        # --- Stacking Rule ---
        if args.stack:
            print("\nEnforcing 'QB + WR/TE Stack' rule...")
            qb_players = players_df[players_df["Position"] == "QB"]
            for qb_idx, qb_row in qb_players.iterrows():
                team = qb_row["Team"]
                stack_partners_indices = players_df[
                    (players_df["Team"] == team)
                    & (players_df["Position"].isin(["WR", "TE"]))
                ].index
                prob += (
                    pulp.lpSum(player_vars[i] for i in stack_partners_indices) >= player_vars[qb_idx],
                    f"Stack_QB_{qb_idx}_{team}",
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

        # --- 4. Iterative Optimization Loop ---
        generated_lineups_indices = []
        max_players_can_share = ROSTER_SIZE - args.min_uniques
        all_lineups_export_data = []

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
                for slot in display_order:
                    player_data = assigned_lineup.get(slot)
                    if player_data:
                        row = player_data.copy()
                        row["Lineup_ID"] = i + 1
                        row["Slot"] = slot
                        row["Player_ID"] = row.get("ID")
                        all_lineups_export_data.append(row)

        # --- 5. Export All Lineups to CSV ---
        if args.export and all_lineups_export_data:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"nfl_multi_lineups_{timestamp}.csv"
            filepath = os.path.join(EXPORT_DIR, filename)

            export_df = pd.DataFrame(all_lineups_export_data)
            columns_to_export = [
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
            columns_to_export = [c for c in columns_to_export if c in export_df.columns]
            export_df = export_df[columns_to_export]

            export_df.to_csv(filepath, index=False)
            print(f"\nAll generated lineups exported to: {filepath}")

    except (FileNotFoundError, ValueError) as e:
        print(f"\nFATAL ERROR: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")


if __name__ == "__main__":
    main()
