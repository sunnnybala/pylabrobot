# Labverify

An agent-driven control + compliance layer over PyLabRobot. First product: an MCP server so
any Claude (Claude Code / Desktop) can operate a liquid handler in natural language, with a
server-side safety gate (record-then-commit) and a tamper-evident audit log.

See `../LABVERIFY_MVP_PLAN.md` (rev 3) for the architecture and the three review rounds behind it.

## Status
MVP engine complete and tested on a no-hardware backend. The "real" liquid handler is currently
simulated by a second chatterbox backend; swap it for `NimbusBackend` when hardware is available.

## Run the tests
```bash
python -m pytest labverify/ -q
```

## Run the MCP server (demo mode, no hardware)
```bash
python -m labverify.mcp_server
```

## Connect from Claude Code
Add to your `.mcp.json`:
```json
{
  "mcpServers": {
    "labverify": {
      "command": "python",
      "args": ["-m", "labverify.mcp_server"],
      "cwd": "/path/to/pylabrobot"
    }
  }
}
```

The agent sees tools: `pick_up_tips`, `drop_tips`, `aspirate`, `dispense` (only those the real
backend supports), plus `describe_deck`, `get_state`, `confirm_run`. Every action is simulated
on the dry-run handler and audited as `simulated`; nothing touches the real backend until the
agent calls `confirm_run`, which replays the recorded protocol and audits each op as `executed`.

## Going to hardware
Swapping the backend is NOT enough: each backend needs a matching deck. The demo `make_lh`
builds a `STARLetDeck`, which a `NimbusBackend` rejects at setup. For Nimbus, also pass a
`make_lh` that builds a `NimbusDeck` (see the docstring in `mcp_server.main()` for the snippet).
Once the deck matches the backend, `confirm_run` executes on the Nimbus; the dry run, capability
gating, and audit log are unchanged.

## Not yet (next slices)
- Config-driven decks + `load_all_state` for starting tip inventory / well volumes (so the dry
  run validates volumes, not just tip presence). Today the demo deck is fixed and volume
  tracking is off.
- Per-op audit captured from real execution (vs the current verb-level record).
- Real-backend connection lifecycle / retry on mid-run failure.
