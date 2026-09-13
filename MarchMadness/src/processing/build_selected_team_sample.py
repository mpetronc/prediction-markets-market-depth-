from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import pandas as pd


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def normalize_team(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", "", "nan", "<na>"}:
        return False
    raise ValueError(f"Could not parse boolean value: {value!r}")


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def side_for_team(team: object, team_a: object, team_b: object) -> str:
    normalized = normalize_team(team)
    sides = {
        normalize_team(team_a): "a",
        normalize_team(team_b): "b",
    }
    if len(sides) != 2:
        raise ValueError(f"Team sides are not unique: {team_a!r}, {team_b!r}")
    try:
        return sides[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Could not map team {team!r} to {team_a!r} or {team_b!r}"
        ) from exc


def row_for_side(
    coverage: pd.DataFrame,
    platform: str,
    side: str,
    team_a: str,
    team_b: str,
) -> pd.Series:
    platform_rows = coverage.loc[coverage["platform"].eq(platform)].copy()
    platform_rows["side"] = platform_rows["team"].map(
        lambda team: side_for_team(team, team_a, team_b)
    )
    matching = platform_rows.loc[platform_rows["side"].eq(side)]
    if len(matching) != 1:
        raise ValueError(
            f"Expected one {platform} coverage row for side {side}; "
            f"found {len(matching)}"
        )
    return matching.iloc[0]


def build_selection(
    market_map: pd.DataFrame,
    coverage: pd.DataFrame,
) -> pd.DataFrame:
    required_market_columns = {
        "game_id",
        "polymarket_event_title",
        "polymarket_team_a",
        "polymarket_team_b",
    }
    required_coverage_columns = {
        "game_id",
        "platform",
        "team",
        "instrument_id",
        "bbo_eligible",
        "trade_eligible",
        "joint_eligible",
        "quality_flags",
    }
    missing_market = required_market_columns - set(market_map.columns)
    missing_coverage = required_coverage_columns - set(coverage.columns)
    if missing_market:
        raise ValueError(
            f"Market map is missing columns: {sorted(missing_market)}"
        )
    if missing_coverage:
        raise ValueError(
            f"Coverage file is missing columns: {sorted(missing_coverage)}"
        )
    if market_map["game_id"].duplicated().any():
        raise ValueError("Market map contains duplicate game IDs")

    records: list[dict[str, object]] = []
    for market in market_map.sort_values("game_id").itertuples(index=False):
        game_id = str(market.game_id)
        team_a = str(market.polymarket_team_a)
        team_b = str(market.polymarket_team_b)
        game_coverage = coverage.loc[coverage["game_id"].eq(game_id)].copy()
        if len(game_coverage) != 4:
            raise ValueError(
                f"{game_id}: expected four platform-team coverage rows; "
                f"found {len(game_coverage)}"
            )
        if set(game_coverage["platform"]) != {"kalshi", "polymarket"}:
            raise ValueError(
                f"{game_id}: expected Kalshi and Polymarket coverage rows"
            )

        kalshi_coverage = game_coverage.loc[
            game_coverage["platform"].eq("kalshi")
        ].copy()
        kalshi_coverage["side"] = kalshi_coverage["team"].map(
            lambda team: side_for_team(team, team_a, team_b)
        )
        kalshi_coverage["bbo_eligible_bool"] = kalshi_coverage[
            "bbo_eligible"
        ].map(as_bool)
        eligible_sides = kalshi_coverage.loc[
            kalshi_coverage["bbo_eligible_bool"], "side"
        ].tolist()

        if len(eligible_sides) == 2:
            selected_side = "b"
            selection_rule = "default_team_b"
            kalshi_bbo_status = "both_teams_usable"
        elif len(eligible_sides) == 1:
            selected_side = eligible_sides[0]
            kalshi_bbo_status = "one_team_usable"
            if selected_side == "b":
                selection_rule = "team_b_is_only_usable_kalshi_bbo"
            else:
                selection_rule = "override_to_only_usable_kalshi_bbo_team_a"
        elif len(eligible_sides) == 0:
            selected_side = "b"
            selection_rule = "team_b_trade_only_no_usable_kalshi_bbo"
            kalshi_bbo_status = "no_team_usable"
        else:
            raise ValueError(
                f"{game_id}: unexpected eligible-side count {len(eligible_sides)}"
            )

        selected_team = team_a if selected_side == "a" else team_b
        kalshi_row = row_for_side(
            game_coverage, "kalshi", selected_side, team_a, team_b
        )
        polymarket_row = row_for_side(
            game_coverage, "polymarket", selected_side, team_a, team_b
        )

        kalshi_bbo_eligible = as_bool(kalshi_row["bbo_eligible"])
        polymarket_bbo_eligible = as_bool(polymarket_row["bbo_eligible"])
        kalshi_trade_eligible = as_bool(kalshi_row["trade_eligible"])
        polymarket_trade_eligible = as_bool(polymarket_row["trade_eligible"])
        kalshi_joint_eligible = as_bool(kalshi_row["joint_eligible"])
        polymarket_joint_eligible = as_bool(polymarket_row["joint_eligible"])

        records.append(
            {
                "game_id": game_id,
                "game": str(market.polymarket_event_title),
                "team_a": team_a,
                "team_b": team_b,
                "default_team": team_b,
                "selected_side": selected_side,
                "selected_team": selected_team,
                "selection_rule": selection_rule,
                "kalshi_bbo_status": kalshi_bbo_status,
                "kalshi_instrument_id": str(kalshi_row["instrument_id"]),
                "polymarket_instrument_id": str(
                    polymarket_row["instrument_id"]
                ),
                "kalshi_bbo_eligible": kalshi_bbo_eligible,
                "polymarket_bbo_eligible": polymarket_bbo_eligible,
                "kalshi_trade_eligible": kalshi_trade_eligible,
                "polymarket_trade_eligible": polymarket_trade_eligible,
                "kalshi_joint_eligible": kalshi_joint_eligible,
                "polymarket_joint_eligible": polymarket_joint_eligible,
                "bbo_sample_eligible": (
                    kalshi_bbo_eligible and polymarket_bbo_eligible
                ),
                "trade_sample_eligible": (
                    kalshi_trade_eligible and polymarket_trade_eligible
                ),
                "joint_sample_eligible": (
                    kalshi_joint_eligible and polymarket_joint_eligible
                ),
                "kalshi_quality_flags": str(kalshi_row["quality_flags"]),
                "polymarket_quality_flags": str(
                    polymarket_row["quality_flags"]
                ),
            }
        )

    selection = pd.DataFrame(records)
    if len(selection) != 67:
        raise ValueError(
            f"Expected 67 selected-team rows; found {len(selection)}"
        )
    if selection["game_id"].duplicated().any():
        raise ValueError("Selected-team table contains duplicate game IDs")
    if int(selection["trade_sample_eligible"].sum()) != 67:
        raise ValueError("Expected all 67 selected teams to be trade eligible")
    if int(selection["bbo_sample_eligible"].sum()) != 64:
        raise ValueError("Expected 64 selected teams to be BBO eligible")
    if int(selection["joint_sample_eligible"].sum()) != 64:
        raise ValueError("Expected 64 selected teams to be jointly eligible")

    return selection


def build_selected_summary(
    selection: pd.DataFrame,
    instrument_summary: pd.DataFrame,
) -> pd.DataFrame:
    required = {"game_id", "platform", "instrument_id"}
    missing = required - set(instrument_summary.columns)
    if missing:
        raise ValueError(
            f"Instrument summary is missing columns: {sorted(missing)}"
        )
    if instrument_summary.duplicated(
        ["game_id", "platform", "instrument_id"]
    ).any():
        raise ValueError(
            "Instrument summary contains duplicate game-platform-instrument rows"
        )

    keys = []
    for row in selection.itertuples(index=False):
        common = {
            "game_id": row.game_id,
            "game": row.game,
            "selected_side": row.selected_side,
            "selected_team": row.selected_team,
            "selection_rule": row.selection_rule,
            "kalshi_bbo_status": row.kalshi_bbo_status,
            "bbo_sample_eligible": row.bbo_sample_eligible,
            "trade_sample_eligible": row.trade_sample_eligible,
            "joint_sample_eligible": row.joint_sample_eligible,
        }
        keys.append(
            {
                **common,
                "platform": "kalshi",
                "instrument_id": row.kalshi_instrument_id,
            }
        )
        keys.append(
            {
                **common,
                "platform": "polymarket",
                "instrument_id": row.polymarket_instrument_id,
            }
        )

    selected_keys = pd.DataFrame(keys)
    selected = selected_keys.merge(
        instrument_summary,
        on=["game_id", "platform", "instrument_id"],
        how="left",
        validate="one_to_one",
        indicator=True,
        suffixes=("", "_summary"),
    )
    unmatched = selected.loc[selected["_merge"].ne("both")]
    if not unmatched.empty:
        raise ValueError(
            "Selected instruments missing from instrument summary: "
            f"{unmatched[['game_id', 'platform', 'instrument_id']].to_dict('records')}"
        )
    selected = selected.drop(columns="_merge")
    if len(selected) != 134:
        raise ValueError(
            f"Expected 134 selected game-platform rows; found {len(selected)}"
        )
    if len(selected.loc[selected["bbo_sample_eligible"]]) != 128:
        raise ValueError("Expected 128 BBO-eligible selected summary rows")
    return selected.sort_values(["game_id", "platform"]).reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=SCRIPT_PROJECT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    market_map_path = root / "data/matched_markets/MM_market_map.csv"
    coverage_path = root / "data/results/data_coverage_by_instrument.csv"
    instrument_summary_path = (
        root / "data/results/summary_metrics_by_instrument_platform.csv"
    )
    selection_output = root / "data/results/selected_team_by_game.csv"
    selected_summary_output = (
        root / "data/results/summary_metrics_selected_team.csv"
    )
    selected_bbo_output = (
        root / "data/results/summary_metrics_selected_team_bbo_joint.csv"
    )

    for path in [market_map_path, coverage_path, instrument_summary_path]:
        require_file(path)

    market_map = pd.read_csv(market_map_path, dtype="string")
    coverage = pd.read_csv(coverage_path, dtype="string")
    instrument_summary = pd.read_csv(
        instrument_summary_path,
        dtype={
            "game_id": "string",
            "platform": "string",
            "team": "string",
            "instrument_id": "string",
        },
    )

    selection = build_selection(market_map, coverage)
    selected_summary = build_selected_summary(selection, instrument_summary)
    selected_bbo = selected_summary.loc[
        selected_summary["bbo_sample_eligible"]
        & selected_summary["joint_sample_eligible"]
    ].copy()

    selection_output.parent.mkdir(parents=True, exist_ok=True)
    selection.to_csv(selection_output, index=False)
    selected_summary.to_csv(selected_summary_output, index=False)
    selected_bbo.to_csv(selected_bbo_output, index=False)

    print(f"Selected games: {len(selection):,}")
    print(
        "Kalshi BBO coverage: "
        f"{selection['kalshi_bbo_status'].value_counts().to_dict()}"
    )
    print(
        "Trade-eligible games: "
        f"{int(selection['trade_sample_eligible'].sum()):,}"
    )
    print(
        "BBO/joint-eligible games: "
        f"{int(selection['joint_sample_eligible'].sum()):,}"
    )
    print(f"Selected game-platform rows: {len(selected_summary):,}")
    print(f"BBO/joint game-platform rows: {len(selected_bbo):,}")

    overrides = selection.loc[
        selection["selection_rule"].eq(
            "override_to_only_usable_kalshi_bbo_team_a"
        ),
        ["game_id", "selected_team"],
    ]
    print("Team-B overrides:")
    print(overrides.to_string(index=False))

    excluded = selection.loc[
        ~selection["bbo_sample_eligible"],
        ["game_id", "selected_team"],
    ]
    print("BBO/joint-excluded games retained for trade analysis:")
    print(excluded.to_string(index=False))

    print(f"Saved: {selection_output}")
    print(f"Saved: {selected_summary_output}")
    print(f"Saved: {selected_bbo_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
