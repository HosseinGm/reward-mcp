# reward-tools

A small **standalone** MCP server that produces a per-person reward/contribution
breakdown for a team over an **exact date range** (8 parameters per member).

It is its **own repository, separate from alborz scm**. It is allowed to *use*
the alborz-ado tooling: it imports the alborz-ado package as a library for PAT
auth + the low-level ADO / time-log fetch. It does not modify that repo and adds
nothing to it.

## Why this exists

A Claude **chat** connector has no shell to run a script, and the alborz-ado
connector only offers `team_time_logged` (rolling "last N days"), which cannot do
a fixed `from → to` window. This MCP fills that gap with one exact tool.

## Layout

| file | purpose |
|---|---|
| `reward_core.py` | the logic (single source of truth): fetch → 8 metrics → optional scoring |
| `server.py` | MCP server: tool `reward_report` + prompt `reward_suggest` |
| `cli.py` | command-line wrapper (used by the Claude Code `/reward-suggest` skill) |
| `requirements.txt` | `mcp`, `httpx`, `python-dotenv` |

## Dependency on alborz-ado (library use only)

`reward_core.py` imports `ado_mcp` for auth + fetch. Point it at a local checkout
with the `ALBORZ_PATH` env var (default `E:\Claude\AlborzMCP`). The ADO PAT and
endpoints are read from that checkout's `.env` automatically — no secrets are
stored here.

You can run it with the alborz-ado venv (which already has all deps):
`E:\Claude\AlborzMCP\.venv\Scripts\python.exe`, or make your own venv from
`requirements.txt` (you still need a local alborz-ado checkout for `ado_mcp`).

## Use in Claude Desktop (chat)

Add to `%APPDATA%\Claude\claude_desktop_config.json`, next to `alborz-ado`:

```json
{
  "mcpServers": {
    "reward-tools": {
      "command": "E:\\Claude\\AlborzMCP\\.venv\\Scripts\\python.exe",
      "args": ["E:\\Claude\\PMTools\\reward-mcp\\server.py"]
    }
  }
}
```

Fully quit and reopen Claude Desktop. Then use the **/reward_suggest** slash
command, or ask: *"Use reward_report for team SLS from 2026-04-21 to 2026-05-21."*

## Use from the command line / Claude Code

```
& "E:\Claude\AlborzMCP\.venv\Scripts\python.exe" "E:\Claude\PMTools\reward-mcp\cli.py" --team SLS --from 2026-04-21 --to 2026-05-21
```

Add `--score` to include ranking once weights are decided (tune `WEIGHTS` and the
thresholds at the top of `reward_core.py`).

## Notes

- Needs the corporate network/VPN (hits the alborz scm ADO + time-log API).
- Only Thu/Fri count as non-working (Iranian public holidays aren't flagged).
- Q8 counts all-time child bugs of resolved PBIs; a PBI worked by several people
  contributes its bugs to each.
