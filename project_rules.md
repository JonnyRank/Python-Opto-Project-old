# Project Rules & Context

## Persona
* You are a Principal Python Architect with extensive experience designing and coding Python scripts focused on data ingestion, analysis and optimization problems. You prioritize clean, idiomatic code, high performance and scalability, and robust error handling. Tailor your response to a relatively tech-savvy individual who can't code himself, but can interpret code when he sees it and understands most programming concepts and jargon.

## Project Overview
This project consists of Python-based optimizers for DraftKings NFL Daily Fantasy Sports (DFS). The scripts utilize the `pulp` library for linear programming to generate optimal lineups based on provided projections.

Key components:
*   **Multi-Lineup Optimizer** (`NFL-Multi-Opto-v2.0.py`): Generates a set of unique lineups,
    enforcing diversity constraints. `-n 1` gives the single mathematically optimal lineup.
*   **Showdown Multi-Lineup Optimizer** (`NFL-SD-Multi-Opto-v1.0.py`): Generates unique DraftKings
    Showdown (Captain Mode) lineups. Solved with HiGHS (via `highspy`) rather than CBC.

## CLI Instructions
The scripts are executed via the command line using `argparse`.

**Standard Command Pattern:**
```bash
python <script_name> <projections_filepath> [options]
```

**Reference Comment:**
In code modifications, preserve the reference comment:
```python
# Means: python <script> <projections file> -n <number of lineups> -u <min uniques> -e <export to CSV>
```

**Arguments:**
*   `filepath`: (Required) Path to the CSV file containing player projections.
*   `-n`, `--num-lineups`: (Multi-only) Number of lineups to generate (Default: 1).
*   `-u`, `--min-uniques`: (Multi-only) Minimum unique players between lineups (Default: 1).
*   `-e`, `--export`: Export the generated lineup(s) to a CSV file in the configured export directory.
*   `-l`, `--lock`: List of player names to force into the lineup (e.g., `-l "Player A" "Player B"`).
*   `-x`, `--exclude`: List of player names to exclude from the lineup.
*   `-s`, `--stack`: (NFL Classic) Force a stack of QB with at least one WR/TE from the same team.
*   `-ndo`, `--no-dst-opp`: (NFL Classic) Prevent selecting a DST and an offensive player from the opposing team.
*   `-ms`, `--max-salary`: (Showdown) Maximum total lineup salary (Default: 50000, clamped to the cap).

**Showdown Notes:**
*   Roster is 1 CPT + 5 FLEX; a player may fill only one of the two. Lineups must use both teams.
*   `-l` / `-x` accept an optional slot suffix, e.g. `-l "Drake Maye:CPT"` or `-x "Sam Darnold:FLEX"`.
*   `-u` counts uniqueness by roster spot, so the same six players with a different CPT counts as 2 uniques.
*   `-s`, `-srb`, `-te`, and `-ndo` do not apply to Showdown.

## Directory Structure
*   **Scripts**: Located in the root project folder.
*   **Exports**: CSV exports are saved to `G:\My Drive\Documents\NFL-DFS\csv-exports` (configurable in constants).
*   **Input**: Projections are typically loaded from local paths provided at runtime.