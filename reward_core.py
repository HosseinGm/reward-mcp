"""Reward data + scoring core — the single source of truth for both the MCP
server (server.py) and the Claude Code CLI (cli.py).

This is a SEPARATE repository from alborz scm. It is allowed to *use* the
alborz-ado package as a library (PAT auth + low-level ADO/time-log fetch) — that
is the "use alborz scm tools" part — but it adds nothing to that repo and is
versioned on its own.

Point ALBORZ_PATH at a local checkout of the alborz-ado MCP (defaults to
E:\\Claude\\AlborzMCP). Run with a Python that has `mcp`, `httpx`, `python-dotenv`
(the alborz-ado venv already does).
"""
from __future__ import annotations

import asyncio
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

# Use the alborz-ado package as a library (auth + ADO/time-log fetch helpers).
# Override the location with ALBORZ_PATH if the checkout lives elsewhere.
ALBORZ_PATH = os.environ.get("ALBORZ_PATH", r"E:\Claude\AlborzMCP")
if ALBORZ_PATH not in sys.path:
    sys.path.insert(0, ALBORZ_PATH)

from ado_mcp import config, timelog            # noqa: E402
from ado_mcp.ado_client import AdoClient        # noqa: E402

# --- tunables ----------------------------------------------------------------
WEIGHTS = {"hours": 0.35, "items": 0.20, "emergencies": 0.25, "deep": 0.20}
EMERGENCY_VALUE = "بله"          # SG.VSTS.Deployment.IsEmergency (string): بله=yes
RESOLVED_STATES = ["Resolved", "TestPassed", "Merged", "ReadyToMerge",
                   "Accepted", "Released", "Closed"]
DEEP_HOURS = 30                  # Q7: a PBI with > this much of the person's time
LONG_DAY_MIN = 480               # Q3: > 8h
LOW_DAY_MIN = 240                # Q4: < 4h on a team working day
DEFAULT_PROJECT = "3GModel"


def resolve_window(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    latest = date.fromisoformat(date_to) if date_to else today
    earliest = date.fromisoformat(date_from) if date_from else latest - timedelta(days=9)
    if earliest > latest:
        raise ValueError(f"date_from {earliest} is after date_to {latest}")
    return earliest, latest


def _in(ids) -> str:
    return ",".join(str(i) for i in ids)


def _parse_date(s: str | None) -> date | None:
    """Parse an ADO datetime string (e.g. '2024-10-05T08:32:17.4Z') to a date."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


async def _wiql_ids(ado, project: str, wiql: str) -> set[int]:
    res = await ado.post(f"{project}/_apis/wit/wiql", {"query": wiql})
    return {w["id"] for w in res.get("workItems", [])}


async def gather(team: str, date_from: str | None, date_to: str | None,
                 project_hint: str = DEFAULT_PROJECT) -> dict:
    s = config.load()
    ado = AdoClient(s)
    try:
        earliest, latest = resolve_window(date_from, date_to)
        start_iso, end_iso = timelog.iso_range(earliest, latest)
        p, t, mem, ids = await timelog._resolve_team(ado, team, project_hint)
        project = p["name"]

        client = timelog.timelog_client(s)
        try:
            recs, report = await asyncio.gather(
                timelog.fetch_records(client, ids, start_iso, end_iso),
                timelog.fetch_daily(client, ids, start_iso, end_iso),
            )
        finally:
            await client.aclose()

        pivot = timelog.daily_pivot_minutes(report)  # {uid_lower: {date: minutes}}

        # per-member per-PBI rollup (time is logged on tasks; record carries PBI_Id)
        members: dict[str, dict] = {
            m["id"].lower(): {"name": m["name"], "user_id": m["id"], "pbis": {}}
            for m in mem}
        for e in recs:
            mm = members.get(str(e.get("user_id", "")).lower())
            if mm is None:
                continue
            pbi = e.get("PBI_Id")
            key = pbi if pbi is not None else f"task:{e.get('taskId')}"
            it = mm["pbis"].get(key)
            if it is None:
                it = mm["pbis"][key] = {"pbi_id": pbi,
                                        "pbi_title": e.get("PBI_Title"),
                                        "minutes": 0}
            it["minutes"] += timelog.rec_minutes(e)
        for uidl, mm in members.items():
            mm["daily"] = pivot.get(uidl, {})

        # team working days = non-weekend days at least HALF the members logged
        # on. Requiring a quorum drops holidays / near-empty days (which a couple
        # of people sometimes work) so they don't read as everyone-else's gap.
        present: dict[date, int] = {}
        for per in pivot.values():
            for d, mn in per.items():
                if mn > 0:
                    present[d] = present.get(d, 0) + 1
        quorum = max(1, math.ceil(len(mem) / 2))
        working_days = {d for d, c in present.items()
                        if c >= quorum and d.weekday() not in timelog.WEEKEND}

        # union of PBIs anyone logged time on
        union = sorted({it["pbi_id"] for mm in members.values()
                        for it in mm["pbis"].values() if it["pbi_id"] is not None})

        # Owner of each PBI = the member who logged the most time on it in the
        # window. Bugs are attributed only to the owner so a PBI worked by several
        # people contributes its bugs once, not once per person.
        pbi_owner: dict[int, str] = {}
        _owner_min: dict[int, int] = {}
        for uidl, mm in members.items():
            for it in mm["pbis"].values():
                pid = it["pbi_id"]
                if pid is None:
                    continue
                if it["minutes"] > _owner_min.get(pid, -1):
                    _owner_min[pid] = it["minutes"]
                    pbi_owner[pid] = uidl

        emergency_set: set[int] = set()
        resolved_set: set[int] = set()
        bugs_by_pbi: dict[int, int] = {}
        if union:
            emergency_set = await _wiql_ids(
                ado, project,
                f"SELECT [System.Id] FROM WorkItems WHERE [System.Id] IN ({_in(union)}) "
                f"AND [SG.VSTS.Deployment.IsEmergency] = '{EMERGENCY_VALUE}'")
            states = ",".join(f"'{st}'" for st in RESOLVED_STATES)
            resolved_set = await _wiql_ids(
                ado, project,
                f"SELECT [System.Id] FROM WorkItems WHERE [System.Id] IN ({_in(union)}) "
                f"AND [System.State] IN ({states})")
            if resolved_set:
                bug_ids = await _wiql_ids(
                    ado, project,
                    "SELECT [System.Id] FROM WorkItems WHERE "
                    f"[System.WorkItemType] = 'Bug' AND [System.Parent] IN "
                    f"({_in(sorted(resolved_set))})")
                if bug_ids:
                    items = await ado.work_items_batch(
                        sorted(bug_ids), ["System.Parent", "System.CreatedDate"])
                    # Count each child Bug once, only if it was CREATED inside the
                    # window. A bug has a single System.Parent, so tallying by
                    # parent PBI de-dupes; single-owner attribution is done in
                    # metrics() via pbi_owner.
                    for it in items:
                        f = it.get("fields", {})
                        parent = f.get("System.Parent")
                        if parent is None:
                            continue
                        created = _parse_date(f.get("System.CreatedDate"))
                        if created is None or not (earliest <= created <= latest):
                            continue
                        bugs_by_pbi[parent] = bugs_by_pbi.get(parent, 0) + 1

        return {"project": project, "team": t["name"],
                "window": {"from": earliest.isoformat(), "to": latest.isoformat()},
                "members": members, "working_days": working_days,
                "emergency_set": emergency_set, "resolved_set": resolved_set,
                "bugs_by_pbi": bugs_by_pbi, "pbi_owner": pbi_owner}
    finally:
        await ado.aclose()


def metrics(data: dict) -> list[dict]:
    """The 8 raw parameters per member. NO scoring/ranking — just the data,
    sorted by hours so it's easy to read."""
    weekend = timelog.WEEKEND
    pbi_owner = data["pbi_owner"]
    rows: list[dict] = []
    for mm in data["members"].values():
        uidl = mm["user_id"].lower()
        daily = mm["daily"]                       # {date: minutes}
        items = list(mm["pbis"].values())
        pbi_ids = {it["pbi_id"] for it in items if it["pbi_id"] is not None}
        total_min = sum(daily.values())
        wknd = [d for d, mn in daily.items() if d.weekday() in weekend and mn > 0]
        rows.append({
            "name": mm["name"],
            "q1_hours": round(total_min / 60, 1),
            "q2_nonworking_days": len(wknd),
            "q2_nonworking_hours": round(sum(daily[d] for d in wknd) / 60, 1),
            "q3_long_days": sum(1 for mn in daily.values() if mn > LONG_DAY_MIN),
            "q4_low_days": sum(1 for d in data["working_days"]
                               if daily.get(d, 0) < LOW_DAY_MIN),
            "q5_emergencies": sum(1 for pid in pbi_ids
                                  if pid in data["emergency_set"]),
            "q6_items": len(pbi_ids),
            "q7_deep_items": sum(1 for it in items
                                 if it["minutes"] > DEEP_HOURS * 60),
            "q8_bugs": sum(data["bugs_by_pbi"].get(pid, 0) for pid in pbi_ids
                           if pid in data["resolved_set"]
                           and pbi_owner.get(pid) == uidl),
        })
    rows.sort(key=lambda r: -r["q1_hours"])
    return rows


def add_scores(rows: list[dict]) -> None:
    """Optional pure-output score/rank/priority/flags — only once weights are
    decided. Mutates rows in place and re-sorts by score."""
    def topmax(key: str) -> float:
        return max((r[key] for r in rows), default=0) or 1

    mx = {k: topmax(v) for k, v in
          {"hours": "q1_hours", "items": "q6_items",
           "emergencies": "q5_emergencies", "deep": "q7_deep_items"}.items()}
    for r in rows:
        r["score"] = round(100 * (
            WEIGHTS["hours"] * r["q1_hours"] / mx["hours"]
            + WEIGHTS["items"] * r["q6_items"] / mx["items"]
            + WEIGHTS["emergencies"] * r["q5_emergencies"] / mx["emergencies"]
            + WEIGHTS["deep"] * r["q7_deep_items"] / mx["deep"]), 1)
        flags = []
        if r["q2_nonworking_days"] > 0 or r["q3_long_days"] >= 3:
            flags.append("burnout")
        if r["q4_low_days"] >= 3:
            flags.append("reliability")
        if r["q8_bugs"] > 0:
            flags.append("quality")
        r["flags"] = flags

    rows.sort(key=lambda r: -r["score"])
    n = len(rows)
    high = max(1, math.ceil(n / 3))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["priority"] = "High" if i < high else ("Med" if i < 2 * high else "Low")


def build_report(rows: list[dict], data: dict, scored: bool) -> dict:
    """Assemble the final JSON-able report from metric rows."""
    out = {"team": data["team"], "project": data["project"],
           "window": data["window"],
           "team_total_hours": round(sum(r["q1_hours"] for r in rows), 1),
           "members": rows}
    if scored:
        out["weights"] = WEIGHTS
    return out


async def report(team: str, date_from: str | None, date_to: str | None,
                 score: bool = False, project_hint: str = DEFAULT_PROJECT) -> dict:
    """One-call entry point used by both server.py and cli.py."""
    data = await gather(team, date_from, date_to, project_hint)
    rows = metrics(data)
    if score:
        add_scores(rows)
    return build_report(rows, data, score)
