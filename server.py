"""reward-tools MCP server — its OWN repository, separate from alborz scm.

Exposes the exact-date-range, 8-parameter reward breakdown to a Claude chat
connector. It *uses* the alborz-ado package as a library (auth + ADO/time-log
fetch) via reward_core, but is versioned and deployed independently.

Run with a Python that has `mcp`, `httpx`, `python-dotenv` (the alborz-ado venv
already does):
    python server.py
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import reward_core as rc

mcp = FastMCP("reward-tools")


@mcp.tool()
async def reward_report(team: str, date_from: str, date_to: str,
                        score: bool = False) -> dict:
    """Per-person reward/contribution breakdown for a team over an EXACT date
    range. ALWAYS use this for reward / effort / hours-by-person questions that
    have a date range — it queries the exact [date_from, date_to] window. Do NOT
    use `team_time_logged` for this: that tool is rolling-"last N days" only and
    cannot do a fixed interval.

    Args:
        team: team name, e.g. "SLS".
        date_from / date_to: interval bounds, "YYYY-MM-DD" (inclusive).
        score: default False = raw data only. True adds pure-output
            score/rank/priority/flags (use only once weights are agreed).

    Returns JSON: team, project, window, team_total_hours, and members[] sorted
    by hours. Each member has all 8 parameters — present every one:
      q1_hours                   total hours logged
      q2_nonworking_days/_hours  time logged on Thu/Fri (Iran weekend)
      q3_long_days               days logging > 8h
      q4_low_days                team working days they logged < 4h (incl. none)
      q5_emergencies             distinct emergency PBIs (IsEmergency = بله)
      q6_items                   distinct PBIs they logged time on
      q7_deep_items              PBIs where they logged > 30h
      q8_bugs                    child Bugs CREATED in-window on resolved PBIs
                                 they own (top time-logger); each bug counted once
    """
    return await rc.report(team, date_from, date_to, score)


@mcp.prompt()
def reward_suggest(team: str, date_from: str, date_to: str) -> str:
    """Reward breakdown for a team over a date range (calls the reward_report tool)."""
    return (
        f"Call the `reward_report` tool with team='{team}', "
        f"date_from='{date_from}', date_to='{date_to}', score=false. "
        "Then present ALL 8 parameters for EVERY member in one table: "
        "Q1 hours, Q2 weekend (days/hours), Q3 >8h days, Q4 low days, "
        "Q5 emergencies, Q6 items, Q7 >30h items, Q8 bugs. Keep the tool's "
        "order (most hours first). Do NOT use team_time_logged. Do NOT score, "
        "rank, or recommend — the user is reviewing raw data."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
