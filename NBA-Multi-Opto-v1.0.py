import time
import os
import glob  # Library for finding files matching patterns
import csv

# --- Add Game Start Time Parsing ---
from datetime import datetime
import re

script_total_start_time = time.time()  # Begin timing entire script

# Other imports, use noqa: E402 to ignore pedantic pep 8 standard
import pandas as pd  # noqa: E402
import pulp  # noqa: E402

# --- Configuration ---
# !!! IMPORTANT: Update this path to the directory containing your input files !!!
TARGET_DIRECTORY = r"C:\Users\jrank\OneDrive\Documents\CSV-Exports"
EXPORT_LINEUPS_DIRECTORY = r"C:\Users\jrank\OneDrive\Documents\Exported-Lineups"

# +++ START OF NEW MULTI-LINEUP CONFIGURATION (Step 1.1) +++
NUMBER_OF_LINEUPS = 20  # How many unique lineups to attempt to generate
MIN_UNIQUES = (
    1  # Minimum number of players that must be different from any previous lineup
)
# +++ END OF NEW MULTI-LINEUP CONFIGURATION +++

# Define file naming patterns
dk_pattern = "DKEntries*.csv"
proj_pattern = "NBA-Projs-*.csv"

# --- Find Input Files Automatically ---
print(f"Searching for input files in: {TARGET_DIRECTORY}")

# Find DK Entries file (expecting exactly one match)
dk_file_pattern_path = os.path.join(TARGET_DIRECTORY, dk_pattern)
dk_files_found = glob.glob(dk_file_pattern_path)

if len(dk_files_found) == 0:
    print(
        f"ERROR: No DraftKings file found matching '{dk_pattern}' in '{TARGET_DIRECTORY}'."
    )
    exit()
elif len(dk_files_found) > 1:
    print(
        f"ERROR: Multiple DraftKings files found matching '{dk_pattern}' in '{TARGET_DIRECTORY}'."
    )
    print("Please ensure only the single, correct DKEntries CSV file is present.")
    print(f"Files found: {dk_files_found}")
    exit()
else:
    dk_filepath = dk_files_found[0]
    print(f"Found DK file: {dk_filepath}")

# Find the latest Projections file
proj_file_pattern_path = os.path.join(TARGET_DIRECTORY, proj_pattern)
proj_files_found = glob.glob(proj_file_pattern_path)

if len(proj_files_found) == 0:
    print(
        f"ERROR: No Projections file found matching '{proj_pattern}' in '{TARGET_DIRECTORY}'."
    )
    exit()
else:
    # Find the most recently modified file among the matches
    try:
        proj_filepath = max(proj_files_found, key=os.path.getmtime)
        print(f"Found latest Projections file: {proj_filepath}")
    except Exception as e:
        print(f"ERROR finding latest projection file: {e}")
        exit()

print("---------------------------\n")

# --- Load DraftKings Player Data ---
print(f"Loading DraftKings data from: {dk_filepath}")
try:
    # Assuming headers are on row 8 (index 7) based on previous info
    dk_columns_to_use = ["ID", "Name", "Position", "Salary", "Game Info", "TeamAbbrev"]
    dk_players = pd.read_csv(dk_filepath, header=7, usecols=dk_columns_to_use)
    print(f"Successfully loaded {dk_players.shape[0]} players from DK file.")

    # Clean Salary
    dk_players["Salary"] = pd.to_numeric(
        dk_players["Salary"]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False),
        errors="coerce",
    )
    original_count = len(dk_players)
    dk_players.dropna(subset=["Salary"], inplace=True)
    if len(dk_players) < original_count:
        print(
            f"Warning: Removed {original_count - len(dk_players)} players with invalid salary data."
        )

except FileNotFoundError:
    # This shouldn't happen if glob found the file, but good practice
    print(
        f"ERROR: File not found at {dk_filepath} (unexpected error after finding file)."
    )
    exit()
except KeyError as e:
    print(f"ERROR: A required column was not found in the DK file: {e}")
    print(f"Please ensure the DK file has columns named: {dk_columns_to_use}")
    exit()
except Exception as e:
    print(f"An unexpected error occurred while loading the DK file: {e}")
    exit()


# --- Load Projections Data ---
print(f"Loading Projections data from: {proj_filepath}")
try:
    # Assuming headers are on row 1 (index 0) for the cleaned file
    proj_columns_map = {
        "DK ID": "ID",
        "Blend Points": "Projection",
        "Stok Own": "Ownership",
    }
    projections = pd.read_csv(proj_filepath, header=0, usecols=proj_columns_map.keys())
    projections.rename(columns=proj_columns_map, inplace=True)
    print(f"Successfully loaded {projections.shape[0]} projections.")

    # Ensure projection/ownership columns are numeric
    projections["Projection"] = pd.to_numeric(
        projections["Projection"], errors="coerce"
    )
    projections["Ownership"] = pd.to_numeric(projections["Ownership"], errors="coerce")

    proj_missing = projections["Projection"].isnull().sum()
    own_missing = projections["Ownership"].isnull().sum()
    if proj_missing > 0:
        print(f"Warning: {proj_missing} players have missing projection data (NaN).")
    if own_missing > 0:
        print(f"Warning: {own_missing} players have missing ownership data (NaN).")

except FileNotFoundError:
    print(
        f"ERROR: File not found at {proj_filepath} (unexpected error after finding file)."
    )
    exit()
except KeyError as e:
    print(f"ERROR: A required column was not found in the projections file: {e}")
    print(
        f"Please ensure the projections file has columns named: {list(proj_columns_map.keys())}"
    )
    exit()
except Exception as e:
    print(f"An unexpected error occurred while loading the projections file: {e}")
    exit()


# --- Merge Data ---
print("Merging DraftKings data and Projections...")
try:
    # Ensure ID columns are compatible types before merging
    dk_players["ID"] = dk_players["ID"].astype(str)
    projections["ID"] = projections["ID"].astype(str)

    # Perform an inner merge
    merged_data = pd.merge(dk_players, projections, on="ID", how="inner")
    print(f"Successfully merged data. Result has {merged_data.shape[0]} players.")

    if merged_data.shape[0] < min(dk_players.shape[0], projections.shape[0]):
        print(
            "Note: Some players were likely dropped during merge because they weren't present in both files or had salary/projection issues."
        )

except Exception as e:
    print(f"An error occurred during merging: {e}")
    exit()


# --- Display Results ---
print("\n--- Merged Data Sample (First 5 Rows) ---")
print(merged_data.head())

print("\n--- Merged Data Shape (Rows, Columns) ---")
print(merged_data.shape)

print("\n--- Merged Data Types ---")
print(merged_data.dtypes)

# Keep players with valid projections and ownership for optimization
final_players = merged_data.dropna(subset=["Projection", "Ownership"]).copy()
print("\n--- Final Player Pool for Optimization ---")
print(
    f"Retained {final_players.shape[0]} players with valid Salary, Projection, and Ownership."
)

# Optional: Save the merged data to a new CSV for inspection
# final_players.to_csv("merged_nba_data.csv", index=False)
# print("\nSaved merged data to merged_nba_data.csv")

# print("\nData preparation script finished.")


# +++ START: ADD GAME IDENTIFICATION +++
def extract_game_id(row):
    game_info = str(row["Game Info"])
    own_team = str(row["TeamAbbrev"])

    # Regex to find team abbreviations (typically 2-3 uppercase letters)
    teams_in_game_info = re.findall(r"\b([A-Z]{2,3})\b", game_info)

    opponents = [t for t in teams_in_game_info if t != own_team]

    if not opponents:
        # Fallback or error: could not determine opponent from Game Info
        # This might happen if Game Info format is unexpected or only shows one team.
        # For a robust solution, you might need to handle various Game Info formats.
        # As a simple fallback, create a "game" based on own_team vs an "UNKNOWN_OPPONENT"
        # print(f"Warning: Could not determine opponent for {own_team} from '{game_info}'. Using fallback game ID.")
        # For now, let's assume Game Info always has both, or we can make a simpler game ID if needed.
        # If own_team is in teams_in_game_info, and there's another, that's good.
        # If own_team is NOT in teams_in_game_info (e.g. "MIL @ BOS" and player is for MIL but TeamAbbrev is "MIL"),
        # we need to ensure we get both.
        # A common format is "AWAY@HOME". Player's TeamAbbrev is one of them.

        all_teams = set(teams_in_game_info)
        if len(all_teams) >= 2:  # Usually it's exactly 2
            return frozenset(
                list(all_teams)[:2]
            )  # Take the first two found, make them a frozenset
        elif len(all_teams) == 1 and own_team in all_teams:
            # This case implies the Game Info might only list one team, or parsing failed to get two.
            # This is problematic for identifying a unique game matchup.
            # print(f"Warning: Only one team '{own_team}' found in Game Info '{game_info}' for player {row['Name']}. Game rule might be affected.")
            return frozenset(
                {own_team, f"OPP_OF_{own_team}"}
            )  # Placeholder for single team found
        else:
            # print(f"Warning: Could not reliably parse teams from '{game_info}' for player {row['Name']}. Using placeholder game ID.")
            return frozenset({own_team, "UNKNOWN_GAME"})

    # More reliable: find own_team, then find the other team.
    # Example: "LAL@GSW 7:00PM ET" and player's TeamAbbrev is "LAL"
    # teams_in_game_info would be ['LAL', 'GSW']

    # Ensure own_team is one of them, find the other.
    if own_team in teams_in_game_info:
        other_teams = [t for t in teams_in_game_info if t != own_team]
        if other_teams:
            # Take the first opponent found if multiple (shouldn't happen for a single game_info)
            return frozenset({own_team, other_teams[0]})

    # If own_team wasn't directly in the parsed list (e.g. TeamAbbrev is 'GS', Game Info is 'GSW@LAL')
    # This indicates a need for more sophisticated matching or cleaning of TeamAbbrev / Game Info teams.
    # For now, assuming teams_in_game_info will usually contain 2 distinct team codes.
    if len(teams_in_game_info) >= 2:
        # Sort to ensure ('TEAM_A', 'TEAM_B') is same as ('TEAM_B', 'TEAM_A')
        return frozenset(
            sorted(list(set(teams_in_game_info))[:2])
        )  # Use set to remove duplicates if any, then sort
    elif len(teams_in_game_info) == 1:
        # If only one team is found in game_info, assume it's a game involving that team and player's team
        # This is a fallback, ideally game_info gives both teams clearly.
        # print(f"Warning: Only one team found in Game Info '{game_info}' for {row['Name']}. Combining with player's team {own_team}.")
        return frozenset({own_team, teams_in_game_info[0]})
    else:
        # print(f"Error: Could not parse game teams from '{game_info}' for player {row['Name']}. Defaulting to isolated game.")
        return frozenset(
            {row["TeamAbbrev"], f"UNKNOWN_OPPONENT_FOR_{row['TeamAbbrev']}"}
        )


final_players["game_id"] = final_players.apply(extract_game_id, axis=1)
unique_game_ids = list(final_players["game_id"].unique())

print(f"\nIdentified {len(unique_game_ids)} unique games in the slate.")
# For debugging, you can print them:
# for i, gid in enumerate(unique_game_ids):
# print(f"  Game {i+1}: {gid}")
# print(final_players[['Name', 'TeamAbbrev', 'Game Info', 'game_id']].head())


# --- Map players to their game_id ---
# We'll use this to link player_vars to is_game_used_vars
player_to_game_map = {}  # player_idx -> game_id
for idx, row in final_players.iterrows():
    player_to_game_map[idx] = row["game_id"]

# +++ END: ADD GAME IDENTIFICATION +++

print("\nData preparation script finished (including game identification).")

# --- OPTIMIZATION LOGIC ---
# Assuming 'final_players' DataFrame is available from the previous steps

# import pulp # imported at the top of script but retained here for reference
# import pandas as pd # Ensure pandas is imported if not already

print("\n--- Starting Optimization ---")

# --- 1. Data Preparation for PuLP ---
# Ensure the DataFrame index is clean if we're using it for variable keys
final_players = final_players.reset_index(drop=True)

# Add positional eligibility flags (0 or 1) to the DataFrame.
# This makes defining constraints much cleaner.
# Handles players with multiple position eligibility (e.g., 'PG/SG')
final_players["is_PG"] = final_players["Position"].apply(
    lambda x: 1 if "PG" in x else 0
)
final_players["is_SG"] = final_players["Position"].apply(
    lambda x: 1 if "SG" in x else 0
)
final_players["is_SF"] = final_players["Position"].apply(
    lambda x: 1 if "SF" in x else 0
)
final_players["is_PF"] = final_players["Position"].apply(
    lambda x: 1 if "PF" in x else 0
)
final_players["is_C"] = final_players["Position"].apply(lambda x: 1 if "C" in x else 0)
# G = PG or SG eligible
final_players["is_G"] = final_players["Position"].apply(
    lambda x: 1 if ("PG" in x or "SG" in x) else 0
)
# F = SF or PF eligible
final_players["is_F"] = final_players["Position"].apply(
    lambda x: 1 if ("SF" in x or "PF" in x) else 0
)

# +++ START: ADD C-ONLY FLAG +++
final_players["is_C_only"] = final_players["Position"].apply(
    lambda x: 1 if x.strip() == "C" else 0
)
# +++ END: ADD C-ONLY FLAG +++

# Create a dictionary for easier lookup by PuLP, using the DataFrame index as keys
# Each value in the dictionary is another dictionary holding that player's data.
players_dict = final_players.to_dict("index")
player_indices = list(players_dict.keys())  # List of player indices [0, 1, 2, ...]
# +++ START OF MULTI-LINEUP LOGIC (Phase 1 - Step 1: Config and Loop Setup) +++

# Initialize structures to hold results from multiple lineups
generated_lineups_player_indices = []  # Stores lists of player indices for each unique lineup found
successfully_generated_lineup_count = 0

# The PuLP problem, variables, and base constraints will be defined ONCE here.
# Diversity constraints will be ADDED to 'prob' in each iteration in a later step.
prob = pulp.LpProblem(
    "DraftKings_NBA_Multi_Lineup", pulp.LpMaximize
)  # New problem name
player_vars = pulp.LpVariable.dicts("Player", player_indices, cat="Binary")
is_game_used_vars = pulp.LpVariable.dicts(
    "IsGameUsed", unique_game_ids, cat="Binary"
)  # For the 'at least two games' rule

# --- Objective Function (defined once) ---
prob += (
    pulp.lpSum(players_dict[i]["Projection"] * player_vars[i] for i in player_indices),
    "Total_Projection",
)

# --- BASE Constraints (defined once, includes all rules for a single valid lineup) ---
# Salary Cap
prob += (
    pulp.lpSum(players_dict[i]["Salary"] * player_vars[i] for i in player_indices)
    <= 50000,
    "Salary_Cap",
)
# Total Players
prob += (pulp.lpSum(player_vars[i] for i in player_indices) == 8, "Total_Players")
# Positional Requirements
prob += (
    pulp.lpSum(players_dict[i]["is_PG"] * player_vars[i] for i in player_indices) >= 1,
    "Min_PG",
)
prob += (
    pulp.lpSum(players_dict[i]["is_SG"] * player_vars[i] for i in player_indices) >= 1,
    "Min_SG",
)
prob += (
    pulp.lpSum(players_dict[i]["is_SF"] * player_vars[i] for i in player_indices) >= 1,
    "Min_SF",
)
prob += (
    pulp.lpSum(players_dict[i]["is_PF"] * player_vars[i] for i in player_indices) >= 1,
    "Min_PF",
)
prob += (
    pulp.lpSum(players_dict[i]["is_C"] * player_vars[i] for i in player_indices) >= 1,
    "Min_C",
)
prob += (
    pulp.lpSum(players_dict[i]["is_G"] * player_vars[i] for i in player_indices) >= 4,
    "Min_Guard_Slots",
)
# Changed from >= 3 to >= 4 after an SG/SF player was used at SF and "optimal" 8 players couldn't fill G spot
prob += (
    pulp.lpSum(players_dict[i]["is_F"] * player_vars[i] for i in player_indices) >= 3,
    "Min_Forward_Slots",
)

# +++ START: MAX 2 C-ONLY PLAYERS CONSTRAINT +++
prob += (
    pulp.lpSum(players_dict[i]["is_C_only"] * player_vars[i] for i in player_indices)
    <= 2,
    "Max_Two_C_Only_Players",
)
# +++ END: MAX 2 C-ONLY PLAYERS CONSTRAINT +++

# Game Rule Constraints (defined once)
# Ensure player_to_game_map and unique_game_ids are defined before this point (they are in your script)
for p_idx_game_rule in player_indices:
    game_id_for_player = player_to_game_map[p_idx_game_rule]
    prob += (
        is_game_used_vars[game_id_for_player] >= player_vars[p_idx_game_rule],
        f"Link_Player_{p_idx_game_rule}_to_Game_{game_id_for_player}",
    )
prob += (
    pulp.lpSum(is_game_used_vars[gid] for gid in unique_game_ids) >= 2,
    "At_Least_Two_Games",
)
# --- End of BASE Constraints ---


# --- Main Optimization Loop (Modified for Diversity) ---
max_players_can_share = 8 - MIN_UNIQUES  # Calculate this once

for lineup_iteration_number in range(
    NUMBER_OF_LINEUPS
):  # NUMBER_OF_LINEUPS is from your config at the top
    print(
        f"\n--- Attempting to generate lineup #{successfully_generated_lineup_count + 1} (Iteration {lineup_iteration_number + 1}) ---"
    )

    # +++ Step 2.2: Add Diversity Constraints +++
    # Add a constraint for EACH previously generated unique lineup
    if successfully_generated_lineup_count > 0:  # Only if we have previous lineups
        print(
            f"Adding {len(generated_lineups_player_indices)} diversity constraint(s) for this iteration..."
        )
        for i, prev_lineup_indices in enumerate(generated_lineups_player_indices):
            constraint_name = (
                f"Diversity_vs_Lineup_{i + 1}_Iter_{lineup_iteration_number + 1}"
            )
            # Ensure the new lineup shares at most 'max_players_can_share' players with this specific previous lineup
            prob += (
                pulp.lpSum(player_vars[p_idx] for p_idx in prev_lineup_indices)
                <= max_players_can_share,
                constraint_name,
            )
            # print(f"  DEBUG: Added constraint {constraint_name} (sum of players from stored lineup {i+1} <= {max_players_can_share})")
    # +++ End of Step 2.2 +++

    # --- Solve the Problem ---
    print(f"Solving for lineup {successfully_generated_lineup_count + 1}...")

    # +++ Start timing the solve +++
    solve_start_time = time.time()

    prob.solve(pulp.PULP_CBC_CMD(msg=0))  # Suppress solver messages
    # prob.solve() # If you want to see solver messages, comment out prob.solve(<variables>) above and remove this comment

    # +++ End timing the solve +++
    solve_end_time = time.time()
    solve_duration = solve_end_time - solve_start_time

    print(
        f"Solver status: {pulp.LpStatus[prob.status]} (Solve time: {solve_duration:.4f} seconds)"
    )  # Modified print with time
    # print(f"Solver status: {pulp.LpStatus[prob.status]}")

    if pulp.LpStatus[prob.status] == "Optimal":
        current_selected_player_indices = [
            i for i in player_indices if player_vars[i].varValue > 0.5
        ]

        if len(current_selected_player_indices) != 8:
            print(
                "Warning: Optimal status but not 8 players selected. Skipping this iteration."
            )
            if successfully_generated_lineup_count == 0:
                print(
                    "Critical: Failed to get 8 players on the first optimal solve. Check base constraints."
                )
                break
            # If not the very first attempt, it might be due to too many diversity constraints making it impossible
            # to pick 8 players while satisfying all rules.
            print(
                "This might indicate the problem is becoming too constrained to select a full team of 8."
            )
            break  # Stop if we can't form a full lineup

        # +++ Step 2.3: Check for Identical Lineup +++
        is_new_lineup_truly_unique = True
        if (
            successfully_generated_lineup_count > 0
        ):  # Only check if we have previous lineups
            for prev_indices in generated_lineups_player_indices:
                if set(current_selected_player_indices) == set(
                    prev_indices
                ):  # Compare sets of indices
                    is_new_lineup_truly_unique = False
                    break

        if not is_new_lineup_truly_unique:
            print(
                "Lineup found is identical to a previously generated one. Stopping generation."
            )
            # Don't increment successfully_generated_lineup_count for this identical one
            break  # Exit the main loop
        # +++ End of Step 2.3 +++

        # If we're here, the lineup is Optimal, has 8 players, and is unique from previous ones
        successfully_generated_lineup_count += 1
        # +++ Step 2.4: Store it +++
        generated_lineups_player_indices.append(current_selected_player_indices)

        current_projection = sum(
            players_dict[i]["Projection"] for i in current_selected_player_indices
        )
        total_salary = sum(
            players_dict[i]["Salary"] for i in current_selected_player_indices
        )

        print(f"\n--- Unique Lineup #{successfully_generated_lineup_count} ---")
        print(f"  Projection: {current_projection:.2f}")
        print(f"  Salary: ${int(total_salary):,}")
        print(
            f"  Selected Player Names & IDs ({len(current_selected_player_indices)} players):"
        )
        for p_idx in current_selected_player_indices:  # Iterate through the sorted list
            print(
                f"    - {players_dict[p_idx]['Name']} (ID: {players_dict[p_idx]['ID']})"
            )

        # For this step, generated_lineups_player_indices is not yet populated.
        # Uniqueness checks and diversity constraints are for the next step.

        # To see the loop run multiple times (generating identical lineups for now):
        # You can remove or comment out the break below if you want to see it try all NUMBER_OF_LINEUPS.
        if (
            lineup_iteration_number >= 0
        ):  # Adjust this condition to control breaks for testing
            # print("\n(Intentionally generating only one lineup in this step for brevity.)")
            # break
            pass  # Let it continue for now

    elif pulp.LpStatus[prob.status] == "Infeasible":
        print(f"Problem became Infeasible at iteration {lineup_iteration_number + 1}.")
        if successfully_generated_lineup_count == 0:
            print("No unique lineups were generated before infeasibility.")
        else:
            print(
                f"Successfully generated {successfully_generated_lineup_count} unique lineup(s) before infeasibility."
            )
        break
    else:
        print(
            f"Optimal solution not found or error at iteration {lineup_iteration_number + 1}. Status: {pulp.LpStatus[prob.status]}"
        )
        if successfully_generated_lineup_count > 0:
            print(
                f"Successfully generated {successfully_generated_lineup_count} unique lineup(s) before this issue."
            )
        break
# --- End of Main Optimization Loop ---

print("\n--- Multi-Lineup Generation Summary ---")
print(f"Requested to generate up to {NUMBER_OF_LINEUPS} lineups.")
print(f"Successfully generated {successfully_generated_lineup_count} unique lineups.")
print(
    f"MIN_UNIQUES setting was: {MIN_UNIQUES} (meaning lineups shared at most {max_players_can_share} players)."
)

# The detailed slot assignment and CSV output logic from NBA-Single-Opto-v1.0.py
# is intentionally omitted here for this phase. We will re-integrate parts of it carefully
# once the multi-lineup generation with diversity is working.
print(
    "\nNote: Detailed slot assignment and CSV output for each lineup will be added in later steps."
)
print("For now, this script focuses on the multi-lineup generation loop structure.")

# +++ END OF MULTI-LINEUP LOGIC (Phase 1 - Step 1) +++

print("\n--- NBA-Multi-Opto-v1.0 Script Finished ---")

# Calculate script execution duration and print in seconds
script_total_end_time = time.time()
script_total_duration = script_total_end_time - script_total_start_time
print(f"\n--- Total script execution time: {script_total_duration:.2f} seconds ---")
