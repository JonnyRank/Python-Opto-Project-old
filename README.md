# NFL DFS Optimizers

Python optimizers that build DraftKings NFL lineups from a projections CSV. Each script
models the contest as a mixed-integer linear program with [PuLP](https://coin-or.github.io/pulp/)
and maximizes total projected points subject to the salary cap and DraftKings roster rules.

| Script | Format | Roster | Lineups | Solver |
| --- | --- | --- | --- | --- |
| `NFL-Multi-Opto-v2.0.py` | Classic | QB, 2 RB, 3 WR, TE, FLEX, DST (9) | Many | CBC |
| `NFL-Single-Opto.py` | Classic | QB, 2 RB, 3 WR, TE, FLEX, DST (9) | One | CBC |
| `NFL-SD-Multi-Opto-v1.0.py` | Showdown (Captain Mode) | 1 CPT + 5 FLEX (6) | Many | HiGHS |

(The repo also contains two older NBA scripts. They are configured by editing constants at
the top of the file rather than from the command line and are not covered here.)

## Setup

```bash
python -m venv venv
venv/Scripts/python.exe -m pip install -r requirements.txt
```

Requirements: `pandas`, `pulp`, `highspy`. The Showdown optimizer needs `highspy`; the two
Classic optimizers use the CBC solver that ships with PuLP.

## Running

All three scripts take the path to a projections CSV as the first argument.

```bash
# Single best Classic lineup
python NFL-Single-Opto.py "C:\path\to\projections.csv" -e

# 20 Classic lineups, at least 2 players different between any two, QB stacked with a WR/TE,
# no DST opposite one of your own offensive players
python NFL-Multi-Opto-v2.0.py "C:\path\to\projections.csv" -n 20 -u 2 -s -ndo -e

# 20 Showdown lineups with a locked Captain and a little salary left on the table
python NFL-SD-Multi-Opto-v1.0.py "C:\path\to\showdown.csv" -n 20 -u 2 -l "Drake Maye:CPT" -ms 49800 -e

# 20 Classic lineups built for upside instead of median points
python NFL-Multi-Opto-v2.0.py "C:\path\to\projections.csv" -n 20 -u 2 -c -e

# 20 Showdown lineups on a 50/50 blend of projection and ceiling
python NFL-SD-Multi-Opto-v1.0.py "C:\path\to\showdown.csv" -n 20 -u 2 -pj -e
```

Lineups print to the terminal as a formatted table with total projection, ownership, ceiling,
and salary. Nothing is written to disk unless you pass `-e`.

## Options

### `NFL-Multi-Opto-v2.0.py` (Classic, multi-lineup)

| Flag | Meaning |
| --- | --- |
| `filepath` | Path to the projections CSV (required) |
| `-n`, `--num-lineups` | Number of lineups to generate (default: 1) |
| `-u`, `--min-uniques` | Minimum players that must differ between any two lineups (default: 1) |
| `-e`, `--export` | Write the lineups to a timestamped CSV |
| `-l`, `--lock` | Player names to force into every lineup, e.g. `-l "Josh Allen" "Puka Nacua"` |
| `-x`, `--exclude` | Player names to keep out of every lineup |
| `-s`, `--stack [N]` | Require the QB to be paired with at least N WR/TE from his own team (N defaults to 1 when the flag is given without a number) |
| `-srb`, `--stack-rb` | Require the QB to be paired with an RB from his own team |
| `-te`, `--max-te` | Cap the number of TEs, e.g. `-te 1` to keep a TE out of the FLEX |
| `-ndo`, `--no-dst-opp` | Never roster a DST alongside a QB/RB/WR/TE from the opposing team |
| `-c`, `--ceiling` | Optimize on ceiling instead of projection |
| `-pj`, `--projceiling` | Optimize on a 50/50 blend of projection and ceiling |
| `-sf`, `--small-field` | Show small-field ownership instead of large-field (display/export only) |

Name matching for `-l` and `-x` is case-insensitive. A name that isn't in the projections file
prints a warning and is skipped rather than failing the run.

### `NFL-Single-Opto.py` (Classic, one lineup)

Takes only `filepath` and `-e`, `--export`. It applies the same salary cap, roster, and
two-games constraints as the multi-lineup version, without locks, stacking, or exclusions.

### `NFL-SD-Multi-Opto-v1.0.py` (Showdown, multi-lineup)

| Flag | Meaning |
| --- | --- |
| `filepath` | Path to the Showdown projections CSV (required) |
| `-n`, `--num-lineups` | Number of lineups to generate (default: 1) |
| `-u`, `--min-uniques` | Minimum roster spots that must differ between any two lineups (default: 1) |
| `-e`, `--export` | Write the lineups to a timestamped CSV |
| `-l`, `--lock` | Players to force into every lineup |
| `-x`, `--exclude` | Players to keep out of every lineup |
| `-ms`, `--max-salary` | Cap total lineup salary below $50,000 (values above the cap are clamped) |
| `-c`, `--ceiling` | Optimize on ceiling instead of projection |
| `-pj`, `--projceiling` | Optimize on a 50/50 blend of projection and ceiling |

Showdown notes:

* The roster is 1 Captain + 5 FLEX. The Captain scores 1.5x points and costs 1.5x salary, and
  a player can fill the Captain slot or a FLEX slot but never both.
* Every lineup must include at least one player from each of the two teams.
* `-l` and `-x` accept an optional slot suffix: `-l "Drake Maye:CPT"` locks him at Captain,
  `-x "Sam Darnold:CPT"` bans him from Captain but still allows him in the FLEX. Without a
  suffix the lock or exclusion applies to both slots.
* `-u` counts uniqueness by roster spot, so the same six players with a different Captain
  counts as two uniques.
* `-s`, `-srb`, `-te`, and `-ndo` don't apply to Showdown.
* Under any optimization target the Captain contributes its Captain-slot value — the file's
  `CPT Proj` / `CPT Ceiling` when present, otherwise 1.5x the FLEX value.

## Optimization target

Both multi-lineup NFL scripts maximize **projection** by default. Two mutually exclusive
flags swap in a different scoring target; everything else (salary cap, roster rules, locks,
stacks, diversity) is unchanged.

| Flag | What the solver maximizes |
| --- | --- |
| *(none)* | `Projection` — the median-points lineup, same as always |
| `-c`, `--ceiling` | `Ceiling` — the highest-upside lineup, for GPPs |
| `-pj`, `--projceiling` | `0.5 x Projection + 0.5 x Ceiling` — upside without abandoning floor |

* The run prints `Optimizing on: <target>` after loading, and every lineup still prints its
  projection, ownership, and ceiling totals. A `--projceiling` run also prints its blend score,
  since that number matches neither of the printed totals.
* Ceiling-weighted targets need real ceiling data. If the `Ceiling` column is missing or all
  zeros, the run stops with an explanatory error rather than quietly returning an arbitrary
  salary-feasible lineup. Projection-only runs are unaffected — a missing `Ceiling` column
  just displays as `0.00`.
* A *partly* populated `Ceiling` column still runs, but says so. Blank or unparseable ceilings
  become `0.00`, and the load prints how many players that affected. Under `--ceiling` those
  players can't be rostered unless locked; under `--projceiling` they're scored on projection
  alone. Either way a warning names them, so a quietly shrunken player pool never passes
  unnoticed.
* Exports tag the filename with the target (`..._ceiling_<timestamp>.csv`,
  `..._projceiling_<timestamp>.csv`) so files from different targets don't get mixed up. The
  columns inside the file are unchanged.

## Projections CSV format

### Classic

Required columns: `ID`, `Player`, `Position`, `Team`, `Opp`, `Salary`, `Proj`. `Own` and
`Ceiling` are optional and default to `0.00` (`Ceiling` is required only for `--ceiling` /
`--projceiling`).

`NFL-Multi-Opto-v2.0.py` resolves headers through an alias table instead of taking them
literally, so a projections source that renames its columns loads without hand-editing the CSV:

| Internal column | Accepted headers |
| --- | --- |
| `ID` | `ID`, `id`, `DK ID`, `Player ID` |
| `Player` | `Player`, `Name`, `Player Name` |
| `Position` | `Position`, `DK Pos`, `Pos` |
| `Team` | `Team`, `Tm` |
| `Opp` | `Opp`, `Opponent` |
| `Salary` | `Salary`, `DK Salary` |
| `Projection` | `Projection`, `Proj`, `DK Proj` |
| `Ceiling` | `Ceiling`, `DK Ceiling` |
| `Ownership` | `Large Field`, `Ownership`, `Own` — or, under `-sf`, `Small Field` only |

* Matching ignores case and punctuation, so `id` and `ID` are the same header. The first
  accepted header actually present wins, so two source columns can never collapse onto one
  internal name.
* A header matching nothing in the table falls back to fuzzy matching (85% similarity), and
  every fuzzy resolution is printed. Near-miss decoys (`DK Value`, `DK Floor`) and the
  ownership column you did *not* ask for are excluded from that fallback, so a wrong guess
  can't quietly swap in the wrong numbers.
* The fuzzy pass ignores alias spellings shorter than six characters. `difflib`'s ratio is
  `2M/T` over the combined length, so an 85% cutoff gets weaker the shorter the target: against
  `Own`, any four-letter header containing that run (`Down`, `Town`) scores `0.857` and would
  clear it. Short names are exact-match only, which costs nothing — a header close enough to
  `Tm` or `Opp` to be worth guessing at already hits as an exact alias.
* Only the default (large-field) request accepts the unlabeled legacy `Own` / `Ownership`
  headers, since on the files that carried one it was the only ownership column there was.
  `-sf` is an explicit request for the other measure, so it takes a column that actually says
  `Small Field` or shows `0.00%` with a note — it never falls back to an unlabeled column that
  may hold large-field numbers.
* A required column that stays unresolved raises an error naming it and listing the headers
  the file actually contained — the run never proceeds on a mis-mapped column.
* `NFL-Single-Opto.py` still expects the literal legacy headers.

* `Salary` may be formatted (`$6,000`) and ownership may carry a `%` — both are cleaned on load.
* `Opp` may be written `@BUF` or `BUF`; the `@` is stripped when pairing teams into games.
* Rows missing `ID`, `Salary`, `Proj`, or `Position` are dropped before optimizing.
* Every lineup is required to use players from at least two different games.
* A missing `Ceiling` column defaults to zero and displays as `0.00`.

### Showdown

Expected columns: `Player`, `Pos`, `Team`, `Salary`, `Proj`, plus the optional
`Ceiling`, `Total Own`, `CPT Own`, `CPT Salary`, `CPT Proj`, and `CPT Ceiling`.

* The file must contain exactly two teams — filter it down to the single game first, or
  loading fails with an explanatory error.
* `CPT Salary`, `CPT Proj`, and `CPT Ceiling` are used when present; otherwise the standard
  1.5x multiplier is applied to the FLEX values. This matters for `--ceiling`: if your source
  publishes Captain values that aren't exactly 1.5x, supplying `CPT Ceiling` keeps the Captain
  scaled the same way under every target.
* Ownership is slot-aware: the Captain contributes its `CPT Own` and each FLEX contributes
  `Total Own - CPT Own`, so the printed total is true product ownership for the exact lineup.
* Missing `Ceiling` / `Total Own` / `CPT Own` columns default to zero and display as `0.00`.

## Exports

`-e` writes a timestamped file (`nfl_classic_multi_lineups_<timestamp>.csv`,
`nfl_showdown_multi_lineups_<timestamp>.csv`, with `_ceiling` / `_projceiling` inserted before
the timestamp when one of those targets is used) to the directory set by the `EXPORT_DIR`
constant near the top of each script — currently `G:\My Drive\Documents\NFL-DFS\csv-exports`.
Change that constant to export somewhere else.

Each lineup is written as three blocks, all sharing a `Lineup_ID`:

1. **One row per roster spot**, in slot order.
2. **A `TOTAL` row** with the lineup's salary, projection, ownership, and ceiling.
3. **A DraftKings upload row** — the same lineup laid out horizontally, holding only the
   players' `Name + ID` values in slot order, ready to paste into a DraftKings entries file.

```
1,CPT,Drake Maye,QB,NE,15000,28.95,3.63,49.2
1,FLEX,Jaxon Smith-Njigba,WR,SEA,10600,17.9,12.92,30.4
...
1,TOTAL,,,,50000,97.65,78.51,165.9
1,Drake Maye (43782098),Jaxon Smith-Njigba (43782034),...
```

### The upload row and DKEntries.csv

The `Name + ID` values are read from `C:\Users\jrank\Downloads\DKEntries.csv` (the
`DK_ENTRIES_PATH` constant). That file is jagged: contest entries come first and the player
pool section starts at row 8 with its own header, listing `Position`, `Name + ID`, `Name`,
`ID`, `Roster Position`, `Salary`, `Game Info`, and `TeamAbbrev`.

* Showdown players appear twice — once at `CPT` and once at `FLEX` — with **different IDs**,
  so the Captain row and FLEX rows are looked up by slot and get the correct ID for each.
* Names are matched case-insensitively, ignoring punctuation and generational suffixes, so
  `AJ Brown` in your projections still finds `A.J. Brown` in the DraftKings file. Defenses
  fall back to matching on team abbreviation, since DraftKings names them by nickname.
* **If the file isn't there, the upload row is simply skipped** and the export continues from
  the `TOTAL` row to the next lineup. The same happens for an individual lineup if any of its
  players can't be matched — the script prints which player it couldn't resolve.

## When fewer lineups come back than you asked for

Each solved lineup adds a constraint forbidding it from reappearing, so the pool shrinks as
the run goes on. If the first lineup can't be built at all, the constraints are contradictory —
usually conflicting locks, too many exclusions, or a `-ms` value that's too low. If the run
stops partway through, the slate simply has no more lineups that satisfy your `-u` setting;
lower `-u` or loosen the stacking flags.
