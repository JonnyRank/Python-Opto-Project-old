import pandas as pd
import pulp
import os
import glob  # Library for finding files matching patterns
import csv

# --- Add Game Start Time Parsing ---
from datetime import datetime
import re

# --- Configuration ---
# !!! IMPORTANT: Update this path to the directory containing your input files !!!
TARGET_DIRECTORY = r"C:\Users\jrank\OneDrive\Documents\CSV-Exports"
EXPORT_LINEUPS_DIRECTORY = r"C:\Users\jrank\OneDrive\Documents\Exported-Lineups"

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

# --- 2. Define the Optimization Problem ---
# We want to MAXIMIZE the total projection
prob = pulp.LpProblem("DraftKings_NBA_Optimal_Lineup", pulp.LpMaximize)

# --- 3. Define Decision Variables ---
# Create binary variables for each player (0 = not selected, 1 = selected)
# Using the player indices as the keys for the variable dictionary.
player_vars = pulp.LpVariable.dicts("Player", player_indices, cat="Binary")

# +++ START: PuLP Variables and Constraints for Game Rule +++
# Create a binary variable for each unique game
is_game_used_vars = pulp.LpVariable.dicts("IsGameUsed", unique_game_ids, cat="Binary")

# Link player selection to game usage:
# If a player is selected, their game must be marked as used.
for p_idx in player_indices:
    game_id_for_player = player_to_game_map[p_idx]
    # If player_vars[p_idx] is 1, then is_game_used_vars[game_id_for_player] must be >= 1 (so, 1)
    prob += (
        is_game_used_vars[game_id_for_player] >= player_vars[p_idx],
        f"Link_Player_{p_idx}_to_Game_{game_id_for_player}",
    )

# Constraint: At least 2 different games must be used
prob += (
    pulp.lpSum(is_game_used_vars[gid] for gid in unique_game_ids) >= 2,
    "At_Least_Two_Games",
)
# +++ END: PuLP Variables and Constraints for Game Rule +++

# --- 4. Define the Objective Function ---
# Maximize the sum of (Projection * Selection_Variable) for all players
prob += (
    pulp.lpSum(players_dict[i]["Projection"] * player_vars[i] for i in player_indices),
    "Total_Projection",
)

# --- 5. Define the Constraints ---
# Constraint 1: Salary Cap <= $50,000
prob += (
    pulp.lpSum(players_dict[i]["Salary"] * player_vars[i] for i in player_indices)
    <= 50000,
    "Salary_Cap",
)

# Constraint 2: Exactly 8 players must be selected
prob += pulp.lpSum(player_vars[i] for i in player_indices) == 8, "Total_Players"

# Constraint 3: Positional Requirements (DraftKings Classic NBA)
# We need >= 1 player eligible for each specific base position (PG, SG, SF, PF, C)
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

# We also need enough players to fill the G (PG/SG), F (SF/PF), and UTIL slots.
# The easiest way to express this with the >=1 constraints above is to ensure we have
# at least 3 players *eligible* for G slots and 3 players *eligible* for F slots.
# The total of 8 players automatically covers the UTIL slot logic.
# If a player is PG/SG, they count towards 'is_G'. If SF/PF, they count towards 'is_F'.
prob += (
    pulp.lpSum(players_dict[i]["is_G"] * player_vars[i] for i in player_indices) >= 4,
    "Min_Guard_Slots",
)  # Covers PG, SG, G
# Changed from >= 3 to >= 4 after an SG/SF player was used at SF and "optimal" 8 players couldn't fill G spot
prob += (
    pulp.lpSum(players_dict[i]["is_F"] * player_vars[i] for i in player_indices) >= 3,
    "Min_Forward_Slots",
)  # Covers SF, PF, F

# +++ START: MAX 2 C-ONLY PLAYERS CONSTRAINT +++
prob += (
    pulp.lpSum(players_dict[i]["is_C_only"] * player_vars[i] for i in player_indices)
    <= 2,
    "Max_Two_C_Only_Players",
)
# +++ END: MAX 2 C-ONLY PLAYERS CONSTRAINT +++

# --- 6. Solve the Problem ---
print("Solving optimization problem...")
# You might see some output from the solver here.
prob.solve(pulp.PULP_CBC_CMD(msg=0))  # Use this to suppress solver messages
# prob.solve()
print("Solver finished.")

# --- 7. Display the Results ---
print(f"\nOptimization Status: {pulp.LpStatus[prob.status]}\n")

if pulp.LpStatus[prob.status] == "Optimal":
    total_proj = pulp.value(prob.objective)
    total_salary = sum(
        players_dict[i]["Salary"] * player_vars[i].varValue
        for i in player_indices
        if player_vars[i].varValue > 0.5
    )

    # --- Extract Selected Players ---
    selected_players_list = []
    for i in player_indices:
        # Check if the variable value is close to 1
        if player_vars[i].varValue > 0.5:
            # Retrieve the full player info row and make a copy
            player_info = final_players.loc[i].copy()
            selected_players_list.append(player_info)

    selected_player_indices_final = [
        i for i in player_indices if player_vars[i].varValue > 0.5
    ]

    games_represented = set()
    print("\nSelected Players and their Games:")
    for p_idx in selected_player_indices_final:
        player_name = players_dict[p_idx]["Name"]
        player_game_id = player_to_game_map[p_idx]
        games_represented.add(player_game_id)
        print(f"  - {player_name} (Game: {player_game_id})")

    # Create a DataFrame of the selected lineup
    selected_lineup_df = pd.DataFrame(selected_players_list)
    if selected_lineup_df.empty:
        print("Error: No players selected despite Optimal status.")
        # Handle error or exit
    else:

        def get_start_time_minutes(game_info):
            """Parses game info string to get start time in minutes past midnight ET."""
            # Regex to find time like HH:MMPM ET or HH:MM ET. Assumes ET if not specified.
            # It's basic, a robust solution would handle timezones properly with pytz.
            match = re.search(
                r"(\d{1,2}:\d{2})\s*(AM|PM)?", str(game_info), re.IGNORECASE
            )
            if not match:
                print(
                    f"Warning: Could not parse time from Game Info: {game_info}. Assigning default late time."
                )
                return 24 * 60  # Assign a very late time (end of day)

            time_str = match.group(1)
            am_pm = match.group(2)

            time_format = "%I:%M%p" if am_pm else "%H:%M"
            full_time_str = f"{time_str}{am_pm if am_pm else ''}"

            try:
                # We only need the time for sorting within the same day's slate
                time_obj = datetime.strptime(full_time_str, time_format).time()
                minutes_past_midnight = time_obj.hour * 60 + time_obj.minute
                return minutes_past_midnight
            except ValueError:
                print(
                    f"Warning: Could not parse time format: {full_time_str}. Assigning default late time."
                )
                return 24 * 60

        # Add a sortable start time column to the selected players DataFrame
        # Note: This assumes all games are on the same date, relative time is sufficient for sorting.
        selected_lineup_df["start_minutes"] = selected_lineup_df["Game Info"].apply(
            get_start_time_minutes
        )

        # --- Assign Players to DK Slots ---
        dk_slots = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
        assigned_lineup = {
            slot: None for slot in dk_slots
        }  # Dictionary to hold assigned player info
        remaining_players = (
            selected_lineup_df.copy()
        )  # Work with a copy, preserving original index
        assigned_indices = (
            set()
        )  # Keep track of assigned player indices (from remaining_players)

        # Calculate positional flexibility count (number of base positions eligible for)
        selected_lineup_df["flexibility_count"] = (
            selected_lineup_df["is_PG"]
            + selected_lineup_df["is_SG"]
            + selected_lineup_df["is_SF"]
            + selected_lineup_df["is_PF"]
            + selected_lineup_df["is_C"]
        )

        # 1. Assign Mandatory Slots using "Most Constrained First" logic

        # List of all fundamental mandatory slots that need to be filled.
        # This also serves as a tie-breaking preferred order if multiple slots are equally constrained.
        # For example, C might be prioritized over SF if both have only 1 candidate.
        # We found ["C", "SF", "PF", "SG", "PG"] worked well in our trace.
        preferred_mandatory_slots_order = ["C", "SF", "PF", "SG", "PG"]

        unfilled_mandatory_slots = preferred_mandatory_slots_order[
            :
        ]  # Make a copy to modify

        # Loop 5 times to fill the 5 mandatory slots
        for iteration_num in range(len(preferred_mandatory_slots_order)):
            if not unfilled_mandatory_slots:  # Should not happen if loop runs 5 times
                print("Warning: All mandatory slots filled before 5 iterations.")
                break

            most_constrained_slot = None
            min_eligible_count = float("inf")

            # Determine eligibility counts for all currently unfilled mandatory slots
            slot_eligibility_candidates = {}  # Stores {slot: [player_df_rows]}

            # First pass: find the minimum number of eligible players for any slot
            for slot_to_check in unfilled_mandatory_slots:
                eligible_for_this_slot = selected_lineup_df[
                    (
                        ~selected_lineup_df.index.isin(assigned_indices)
                    )  # Player not already assigned
                    & (
                        selected_lineup_df[f"is_{slot_to_check}"] == 1
                    )  # Player is eligible for this slot
                ]
                slot_eligibility_candidates[slot_to_check] = eligible_for_this_slot

                if len(eligible_for_this_slot) < min_eligible_count:
                    min_eligible_count = len(eligible_for_this_slot)

            # Second pass: find the 'most_constrained_slot' respecting preferred_order for tie-breaking
            # Iterate through preferred_mandatory_slots_order to ensure tie-breaking priority
            for slot_in_preferred_order in preferred_mandatory_slots_order:
                if (
                    slot_in_preferred_order in unfilled_mandatory_slots
                ):  # Only consider if still needs filling
                    if (
                        len(slot_eligibility_candidates[slot_in_preferred_order])
                        == min_eligible_count
                    ):
                        most_constrained_slot = slot_in_preferred_order
                        break  # Found the highest priority slot among those with min_eligible_count

            if most_constrained_slot is None or min_eligible_count == 0:
                print(
                    f"CRITICAL ERROR on iteration {iteration_num + 1}: Cannot find an assignable player for any remaining mandatory slot, or {most_constrained_slot} has no candidates. Halting mandatory assignment."
                )
                # This indicates a problem, possibly meaning the selected 8 players can't fill the 5 mandatory slots distinctly.
                # Or a logic error in this new assignment block.
                # For now, we'll break; in a more robust system, you might have a fallback.
                break

            # Now, get the players eligible for this 'most_constrained_slot'
            players_for_chosen_slot = slot_eligibility_candidates[most_constrained_slot]

            # Sort these players by start_minutes (earliest first)
            # .copy() is important here to avoid SettingWithCopyWarning when adding 'start_minutes' if not already present for sorting,
            # though 'start_minutes' should already be on selected_lineup_df. Good practice.
            # Sort these players: 1st by start_minutes (earliest), 2nd by flexibility_count (ascending - less flexible first)
            sorted_players_for_slot = players_for_chosen_slot.copy().sort_values(
                by=["start_minutes", "flexibility_count"], ascending=[True, True]
            )

            player_to_assign = sorted_players_for_slot.iloc[0]

            assigned_lineup[most_constrained_slot] = player_to_assign.to_dict()
            assigned_indices.add(
                player_to_assign.name
            )  # .name is the index of the player in selected_lineup_df
            unfilled_mandatory_slots.remove(most_constrained_slot)

            print(
                f"Assigned {player_to_assign['Name']} to {most_constrained_slot} (Iteration {iteration_num + 1}, Candidates: {min_eligible_count})"
            )

        # After this loop, check if all 5 mandatory slots were filled
        if len(unfilled_mandatory_slots) > 0:
            print(
                f"Warning: Could not fill all mandatory slots. Unfilled: {unfilled_mandatory_slots}"
            )
        else:
            print("Successfully assigned all 5 mandatory slots.")

        # The rest of your script (assigning G, F, UTIL) will then use the updated 'assigned_indices'
        # and 'assigned_lineup'. The 'remaining_players' DataFrame for flex slots will be derived from
        # 'selected_lineup_df' and 'assigned_indices' as before.

        # 2. Assign Flexible Slots (G, F, UTIL) - Revised Logic for Late Swap UTIL Preference
        flexible_slots = ["G", "F", "UTIL"]
        # Get unassigned players (should be exactly 3)
        unassigned_players_df = remaining_players[
            ~remaining_players.index.isin(assigned_indices)
        ]

        if len(unassigned_players_df) != 3:
            print(
                f"\nError: Expected 3 players remaining for flexible slots, found {len(unassigned_players_df)}. Cannot perform refined G/F/UTIL assignment."
            )
            # As a fallback, might try assigning remaining players arbitrarily or stop.
            # Printing remaining players might help debug.
            # print("Remaining players for G/F/UTIL:")
            # print(unassigned_players_df[['Name', 'Position', 'is_G', 'is_F', 'start_minutes']])
        else:
            # Sort by LATEST start time first
            sorted_unassigned = unassigned_players_df.sort_values(
                by="start_minutes", ascending=False
            )
            LatestPlayer = sorted_unassigned.iloc[0]
            MiddlePlayer = sorted_unassigned.iloc[1]
            EarliestPlayer = sorted_unassigned.iloc[2]

            # Check eligibility flags for convenience
            lp_is_g, lp_is_f = LatestPlayer["is_G"] == 1, LatestPlayer["is_F"] == 1
            mp_is_g, mp_is_f = MiddlePlayer["is_G"] == 1, MiddlePlayer["is_F"] == 1
            ep_is_g, ep_is_f = EarliestPlayer["is_G"] == 1, EarliestPlayer["is_F"] == 1

            # --- Check Scenario 1: Can the latest player go to UTIL? ---
            # This requires the Middle and Earliest players to be able to fill G and F between them.
            can_middle_earliest_fill_gf = (mp_is_g and ep_is_f) or (mp_is_f and ep_is_g)

            if can_middle_earliest_fill_gf:
                print("Attempting to assign latest starting flex player to UTIL...")
                assigned_lineup["UTIL"] = LatestPlayer.to_dict()
                assigned_indices.add(LatestPlayer.name)

                # Now assign Middle and Earliest to G and F.
                # Prioritize assigning Middle (later start) to F if possible.
                if mp_is_f and ep_is_g:  # Middle can be F, Earliest must be G
                    assigned_lineup["F"] = MiddlePlayer.to_dict()
                    assigned_indices.add(MiddlePlayer.name)
                    assigned_lineup["G"] = EarliestPlayer.to_dict()
                    assigned_indices.add(EarliestPlayer.name)
                    print("  Success: Latest->UTIL, Middle->F, Earliest->G")
                elif mp_is_g and ep_is_f:  # Middle can be G, Earliest must be F
                    assigned_lineup["G"] = MiddlePlayer.to_dict()
                    assigned_indices.add(MiddlePlayer.name)
                    assigned_lineup["F"] = EarliestPlayer.to_dict()
                    assigned_indices.add(EarliestPlayer.name)
                    print("  Success: Latest->UTIL, Middle->G, Earliest->F")
                else:
                    # This case should not happen if can_middle_earliest_fill_gf was True
                    # Indicates a potential logic flaw or unexpected data state.
                    print(
                        "  Error: Logic contradiction assigning G/F after placing UTIL. Reverting strategy."
                    )
                    # Reset assignments for flexible slots to be safe before fallback
                    assigned_indices.difference_update(
                        {LatestPlayer.name, MiddlePlayer.name, EarliestPlayer.name}
                    )
                    (
                        assigned_lineup["UTIL"],
                        assigned_lineup["G"],
                        assigned_lineup["F"],
                    ) = None, None, None
                    can_middle_earliest_fill_gf = False  # Force fallback

            # --- Scenario 2: Latest player MUST fill G or F (or fallback from Scenario 1 error) ---
            if not can_middle_earliest_fill_gf:
                # Use the previous logic: Assign Latest->Middle->Earliest priority to G, then F, then UTIL
                print(
                    "Assigning flex slots: Latest available preferred for G, then F..."
                )
                temp_assigned_indices_fallback = (
                    set()
                )  # Track assignments within this block

                # Assign G
                g_assigned = False
                for player in [LatestPlayer, MiddlePlayer, EarliestPlayer]:
                    if player["is_G"] == 1:
                        assigned_lineup["G"] = player.to_dict()
                        assigned_indices.add(player.name)
                        temp_assigned_indices_fallback.add(player.name)
                        print(f"  Assigned G: {player['Name']}")
                        g_assigned = True
                        break
                if not g_assigned:
                    print("  Error assigning G slot in fallback.")

                # Assign F
                f_assigned = False
                for player in [LatestPlayer, MiddlePlayer, EarliestPlayer]:
                    # Check if player is NOT already assigned AND is F-eligible
                    if (
                        player.name not in temp_assigned_indices_fallback
                        and player["is_F"] == 1
                    ):
                        assigned_lineup["F"] = player.to_dict()
                        assigned_indices.add(player.name)
                        temp_assigned_indices_fallback.add(player.name)
                        print(f"  Assigned F: {player['Name']}")
                        f_assigned = True
                        break
                if not f_assigned:
                    print("  Error assigning F slot in fallback.")

                # Assign UTIL (the single remaining player)
                util_assigned = False
                for player in [LatestPlayer, MiddlePlayer, EarliestPlayer]:
                    if player.name not in temp_assigned_indices_fallback:
                        assigned_lineup["UTIL"] = player.to_dict()
                        assigned_indices.add(player.name)
                        # No need to add to temp_assigned_indices_fallback, it's the last one
                        print(f"  Assigned UTIL: {player['Name']}")
                        util_assigned = True
                        break
                if not util_assigned:
                    print("  Error assigning UTIL slot in fallback.")

        # --- End of Flexible Slot Assignment ---

        # --- Print Formatted Lineup ---
        print("\nOptimal Lineup (Formatted for DraftKings):")
        print(f"  Total Projection: {total_proj:.2f}")
        print(f"  Total Salary: ${int(total_salary):,}")

        # --- Define Header and Width ---
        # Adjusted width: Slot(5)+1+Name(25)+1+ID(10)+1+Pos(10)+1+Sal(8)+1+Proj(7)+1+Time(10) = 81
        header_width = 81
        print("-" * header_width)
        print(
            f"{'Slot':<5} {'Name':<25} {'ID':<10} {'Position':<10} {'Salary':<8} {'Proj':<7} {'Time'}"
        )  # Changed last column name
        print("-" * header_width)

        final_order = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
        # import re # Ensure re is imported

        for slot in final_order:
            player = assigned_lineup[slot]  # This is now a dictionary or None
            if player:
                # Extract time from Game Info for cleaner display
                game_info_str = player.get(
                    "Game Info", ""
                )  # Get the full string safely
                time_str_display = "N/A"  # Default if no time found

                # Regex to find HH:MM and optional AM/PM
                match = re.search(
                    r"(\d{1,2}:\d{2})\s*(AM|PM)?", str(game_info_str), re.IGNORECASE
                )
                if match:
                    time_part = match.group(1)
                    am_pm_part = (
                        match.group(2) or ""
                    )  # Add AM/PM if it exists, otherwise empty string
                    time_str_display = (
                        f"{time_part}{am_pm_part}"  # Combine HH:MM and AM/PM
                    )

                # Print the line using the extracted time string
                print(
                    f"{slot:<5} {player['Name']:<25} {player['ID']:<10} {player['Position']:<10} ${player['Salary']:<7,} {player['Projection']:<6.2f} {time_str_display:<10}"
                )  # Use extracted time
            else:
                # Adjusted placeholder width
                print(
                    f"{slot:<5} {'- ERROR ASSIGNING -':<{header_width - 5 - 1}}"
                )  # Fill remaining space

        print("-" * header_width)

        # ... (End of the 'if Optimal' block and the formatted printing) ...

        # +++ START OF CODE TO GENERATE DK UPLOAD FILE +++
        print("\n--- Preparing to Generate DraftKings Upload File ---")

        # This code section runs only if an optimal lineup was found,
        # which is already ensured by the outer 'if pulp.LpStatus[prob.status] == "Optimal":' condition.

        # 1. Prepare player strings for the lineup (e.g., "Name (ID)")
        formatted_optimal_lineup = {}
        all_slots_properly_formatted = True
        # 'dk_slots' is defined earlier in the script (around line 321)
        # Example: dk_slots = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]

        for slot in dk_slots:  # Using the pre-defined dk_slots
            player_data = assigned_lineup.get(slot)
            if player_data and "Name" in player_data and "ID" in player_data:
                formatted_optimal_lineup[slot] = (
                    f"{player_data['Name']} ({player_data['ID']})"
                )
            else:
                print(
                    f"Error: Player data (Name or ID) missing for slot '{slot}' in optimal lineup. Cannot generate upload file."
                )
                all_slots_properly_formatted = False
                break

        if all_slots_properly_formatted:
            try:
                # Define the required metadata columns we need to extract
                required_meta_cols = [
                    "Entry ID",
                    "Contest Name",
                    "Contest ID",
                    "Entry Fee",
                ]

                # 2. Load entry metadata from the original DKEntries.csv using csv.reader
                print(f"Loading entry metadata from: {dk_filepath} using csv.reader")

                entries_data_for_df = []
                # required_meta_cols is already defined globally or accessible from outer scope
                # For clarity, ensure it's: required_meta_cols = ['Entry ID', 'Contest Name', 'Contest ID', 'Entry Fee']
                required_meta_cols_set = set(required_meta_cols)
                col_to_index_map = {}

                try:
                    with open(dk_filepath, "r", encoding="utf-8", newline="") as f:
                        reader = csv.reader(f)

                        try:
                            header_row_raw = next(reader)
                            header_row = [h.strip() for h in header_row_raw]
                        except StopIteration:
                            print(
                                f"ERROR: DKEntries.csv ({dk_filepath}) is empty or has no header row."
                            )
                            raise ValueError("Empty or no header in DKEntries.csv")

                        for i, h_name in enumerate(header_row):
                            if h_name in required_meta_cols_set:
                                col_to_index_map[h_name] = i

                        if len(col_to_index_map) != len(required_meta_cols_set):
                            missing = required_meta_cols_set - set(
                                col_to_index_map.keys()
                            )
                            print(
                                f"ERROR: The DKEntries.csv ({dk_filepath}) header is missing required metadata columns: {missing}."
                            )
                            raise KeyError(
                                f"Missing columns for entry metadata from header: {missing}"
                            )

                        # Refined loop to process data rows:
                        for row_num, row_fields_raw in enumerate(reader, start=2):
                            row_fields = [field.strip() for field in row_fields_raw]
                            entry_id_index = col_to_index_map.get("Entry ID")

                            if entry_id_index is None:
                                print(
                                    "Critical Error: 'Entry ID' index mapping missing during row processing."
                                )
                                continue

                            if len(row_fields) <= entry_id_index:
                                # print(f"Skipping line {row_num}: not enough fields for Entry ID (actual fields: {len(row_fields)}, expected index: {entry_id_index}).")
                                continue

                            entry_id_str = row_fields[entry_id_index]

                            if not entry_id_str:
                                # print(f"Skipping line {row_num}: Entry ID field is empty string.")
                                continue

                            numeric_id_value = pd.to_numeric(
                                entry_id_str, errors="coerce"
                            )

                            if pd.isna(numeric_id_value):
                                # print(f"Skipping line {row_num}: Entry ID '{entry_id_str}' is not a valid number (became NaN).")
                                continue

                            current_entry_meta = {}
                            all_meta_fields_present_for_row = True
                            for col_name, col_idx in col_to_index_map.items():
                                if len(row_fields) > col_idx:
                                    current_entry_meta[col_name] = row_fields[col_idx]
                                else:
                                    all_meta_fields_present_for_row = False
                                    break

                            if all_meta_fields_present_for_row:
                                entries_data_for_df.append(current_entry_meta)

                except FileNotFoundError:
                    print(
                        f"Error: Original DK Entries file not found at '{dk_filepath}'. Cannot generate upload file."
                    )
                    raise
                except KeyError as e_key_header_missing:
                    print(
                        f"Halting due to missing required columns in CSV header: {e_key_header_missing}"
                    )
                    # If this happens, we want to ensure we don't proceed as if all_slots_properly_formatted is still true for this block
                    # The 'if not all_slots_properly_formatted' check after this try-except block might be too late.
                    # Let's re-raise or explicitly set a flag to prevent further processing in this 'if all_slots_properly_formatted:'
                    raise  # Re-raise to be caught by the outer try-except if necessary or to stop here.
                except ValueError as e_val_empty_csv:
                    print(
                        f"Error processing CSV file ({dk_filepath}): {e_val_empty_csv}"
                    )
                    raise  # Re-raise

                # This 'if' block should now be at the same indentation level as the 'try' block above it
                # It processes the results from the 'try' block.
                if not entries_data_for_df:  # Check if any valid entries were collected
                    print(
                        f"No valid entries with numeric 'Entry ID' found in {dk_filepath}. Upload file not generated."
                    )
                else:
                    entries_to_fill_df = pd.DataFrame(entries_data_for_df)
                    # Ensure required_meta_cols is defined here or inherited; it was used above
                    # If it wasn't defined in this scope, add:
                    # required_meta_cols = ['Entry ID', 'Contest Name', 'Contest ID', 'Entry Fee']
                    entries_to_fill_df = entries_to_fill_df[required_meta_cols]
                    num_valid_entries = len(entries_to_fill_df)

                    print(
                        f"Found {num_valid_entries} entries to populate in the new upload file."
                    )

                    # 3. Construct the DataFrame for the new CSV
                    data_for_final_df_rows = []
                    for index, entry_row_series in entries_to_fill_df.iterrows():
                        current_row_list = [
                            entry_row_series["Entry ID"],
                            entry_row_series["Contest Name"],
                            entry_row_series["Contest ID"],
                            entry_row_series["Entry Fee"],
                        ]
                        for slot_key in dk_slots:
                            current_row_list.append(
                                formatted_optimal_lineup.get(slot_key, "")
                            )
                        data_for_final_df_rows.append(current_row_list)

                    upload_df_column_headers = [
                        "Entry ID",
                        "Contest Name",
                        "Contest ID",
                        "Entry Fee",
                    ] + dk_slots

                    upload_df = pd.DataFrame(
                        data_for_final_df_rows, columns=upload_df_column_headers
                    )

                    # 4. Write to a new CSV file
                    from datetime import datetime

                    timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    output_filename = f"dk_nba_initial_lineups_{timestamp_str}.csv"

                    # Define output directory for generated lineups
                    if (
                        "EXPORT_LINEUPS_DIRECTORY" in globals()
                        and EXPORT_LINEUPS_DIRECTORY
                    ):
                        output_dir = EXPORT_LINEUPS_DIRECTORY
                        # Create the directory if it doesn't exist
                        if not os.path.isdir(output_dir):
                            try:
                                os.makedirs(
                                    output_dir, exist_ok=True
                                )  # exist_ok=True prevents error if dir already exists
                                print(f"Ensured output directory exists: {output_dir}")
                            except OSError as e:
                                print(
                                    f"ERROR: Could not create output directory {output_dir}: {e}"
                                )
                                print("Defaulting to current directory for output.")
                                output_dir = (
                                    "."  # Default to current directory on error
                                )
                    else:
                        # Fallback if EXPORT_LINEUPS_DIRECTORY is not defined
                        print(
                            "Warning: EXPORT_LINEUPS_DIRECTORY not defined. Defaulting to current directory for output."
                        )
                        output_dir = "."

                    output_filepath = os.path.join(output_dir, output_filename)

                    upload_df.to_csv(output_filepath, index=False, encoding="utf-8-sig")
                    print("\nSuccessfully generated DraftKings upload file:")
                    print(f"  >> {output_filepath}")
                    print(
                        "This file contains your optimized lineup applied to all detected entries."
                    )

            # Catch exceptions from this broader try block
            except FileNotFoundError:
                # This catch might be redundant if the inner one re-raises, but good for safety.
                print(
                    f"Error: Original DK Entries file '{dk_filepath}' confirmed not found (outer catch). Upload file generation failed."
                )
            except (
                KeyError
            ) as e_key_general:  # Catches re-raised KeyError from header issues
                print(
                    f"A key error occurred (likely missing CSV headers or player data issues): {e_key_general}. File not generated."
                )
            except (
                ValueError
            ) as e_val_general:  # Catches re-raised ValueError from empty CSV
                print(
                    f"A value error occurred (likely empty CSV after header): {e_val_general}. File not generated."
                )
            except Exception as e_gen_unexpected:
                print(
                    f"An unexpected error occurred while generating the upload file: {e_gen_unexpected}"
                )
                import traceback

                traceback.print_exc()
        else:
            # This 'else' corresponds to 'if all_slots_properly_formatted:' at the beginning
            print(
                "Upload file not generated due to initial issues formatting player data for the lineup."
            )

        # +++ END OF CODE TO GENERATE DK UPLOAD FILE +++

elif pulp.LpStatus[prob.status] == "Infeasible":
    # Indent these lines:
    print("Problem is Infeasible: No solution exists that satisfies all constraints.")
    print("Possible Causes:")
    print("- Salary cap too low for available high-projection players.")
    print(
        "- Not enough players available at required positions within the salary constraints."
    )
    print(
        "- Conflicting constraints or errors in input data (e.g., player positions, salaries)."
    )

else:
    # Indent these lines:
    print(f"Optimal solution not found. Status: {pulp.LpStatus[prob.status]}")
    print("Check constraints, data integrity, and solver output for more details.")

# This line should be OUTDENTED, back at the main level
print("\n--- Optimization Complete ---")
# --- End of Script ---
