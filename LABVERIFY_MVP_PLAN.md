# Labverify MVP Plan (rev. 3, post second three-reviewer review)

## Rev 3 changes (cross-model consensus from Codex + cloud subagent + self)
1. **Replay records stable selectors, never PLR objects.** The Session records the agent's
   MCP tool-call JSON (resource NAMES, ranges, coords, enum strings). `confirm_run()` replays
   by re-running that JSON through the resolver against the REAL LH's own deck. Reusing dry-run
   objects is unsafe AND SILENT: `_assert_resources_exist` compares structurally not by identity
   (`resource.py:160-169`), and `aspirate`/`dispense` skip that check entirely
   (`liquid_handler.py:927-942 / 1122-1137`) — a foreign object would mutate the wrong tracker
   with no error. Stable-name re-resolution is reliable (deck names are unique + stable).
2. **Capability = explicit per-backend table, sourced from the REAL backend.** Verbs are
   `@abstractmethod` in the base and Nimbus overrides them just to `raise NotImplementedError`
   (`nimbus_backend.py:2269-2288`), so override-vs-inherited introspection MISCLASSIFIES. Maintain
   a capability registry per backend class; catch `NotImplementedError` at dry-run as a backstop.
   Never source capability from chatterbox (it implements ops Nimbus can't, `chatterbox.py:201-226`).
3. **Dry run must enable tracking AND load lab state to be meaningful.** Enable
   `set_tip_tracking(True)` + `set_volume_tracking(True)` on the dry-run LH (these are
   process-global, `tip_tracker.py:13-14` — they affect both LHs). Deck-config JSON is layout
   ONLY; starting tip inventory / well volumes need `load_all_state` (`resource.py:853-889`).
   The session config therefore needs a state file, not just a deck layout.
4. **Two independent backend instances.** Each LH gets its OWN backend; never share one (it holds
   mutable `_deck`/`_head` refs overwritten on setup, `backend.py:56-65`). Two decks from one JSON
   are confirmed fully independent (fresh trackers/_resources, no singletons).
5. **Two logs, two purposes.** Replay log = verb-level tool-call JSON (for re-execution). Audit
   log = per-op records captured from ACTUAL real execution (compliance truth; `transfer` expands
   to aspirate + N dispenses, `liquid_handler.py:1320-1334`).
6. **Real-backend lifecycle defined.** `LiquidHandler.setup()` is one-shot and raises on re-entry
   (`liquid_handler.py:161-162`). Build the real LH fresh per `confirm_run`; double-confirm guard
   and mid-replay failure must leave a clean, re-runnable state (no half-set-up real LH).


## Goal
Ship a **vendor-agnostic** agent-driven control layer over PyLabRobot (PLR). First product:
an **MCP server** so any Claude (Claude Code / Desktop) can operate a liquid handler in
natural language, with a server-side safety gate and a tamper-evident audit log. Web UI is
phase 2 on the same engine.

## Vendor scope (corrected after review)
The engine is **backend-agnostic** — it works with any PLR backend: Hamilton STAR / Vantage /
Nimbus, Tecan EVO, Opentrons OT-2, chatterbox. The verb surface is **detected per connected
backend at runtime** (capability map), so each instrument exposes exactly the verbs it can
run. MVP is **hardware-verified on the Nimbus** (the instrument we physically own); other
backends are supported via PLR and validated against chatterbox until hardware is available.

## What already exists (reuse, do NOT rebuild)
- `liquid_handling/liquid_handler.py` — `LiquidHandler`, ~30 async verbs.
- `backends/hamilton/nimbus_backend.py` — real Nimbus driver over TCP (ours, works).
- PLR ships STAR / Vantage / Tecan EVO / Opentrons OT-2 backends too (cross-vendor for free).
- `backends/chatterbox.py` — no-hardware backend, prints ops (dry-run target).
- `backends/serializing_backend.py` — `serialize(...)` helpers for per-op JSON (audit source).
- `visualizer/visualizer.py` — websocket + http live deck view (phase-2 UI base).
- `resources/.../Deck` — serialize()/deserialize() to JSON exist.

## Locked design decisions
1. Scope: MCP server first; web UI phase 2 on the same engine.
2. Vendor-agnostic engine. Verb surface = capability-detected per connected backend
   (a backend that raises `NotImplementedError` for an op does not get that tool).
3. Safety: **record-then-commit with TWO LiquidHandler instances** (review P1 fix).
   A dry-run LH (chatterbox backend) and a real LH (e.g. Nimbus), each set up fresh from the
   SAME deck config. Agent verbs run on the dry-run LH (recorded, audited `simulated`).
   `confirm_run()` replays the recorded **verb-level intent** on the fresh real LH (audited
   `executed`). NEVER share one LiquidHandler across dry-run and real — PLR commits tip/volume/
   deck state on the LH itself (liquid_handler.py:540-544 / 1030-1036 / 1232-1238), so a shared
   instance corrupts the real run.
4. Deck: loaded from config JSON, but the loader must produce the **backend-matched deck type**
   (NimbusDeck for Nimbus, STARLetDeck for STAR, OT-2 deck for OT-2). Agent gets read-only
   `describe_deck`; never invents geometry.
5. Code layout: new top-level `labverify/` package in this repo, imports `pylabrobot`.

## Critical realities found in review (must be designed for)
- **State lives in LiquidHandler, not the backend.** Verbs commit tracker/deck state on the LH
  regardless of backend → two-instance design is mandatory.
- **Args are objects, not JSON.** Verbs take `TipSpot`/`Well`/`Coordinate`/`GripDirection`/lists,
  not primitives. A **resolver layer** is required: name→`deck.get_resource()`, `"A1:H1"`→well
  list, `[x,y,z]`→`Coordinate`, enum-string→enum. This is first-class work, not a footnote.
- **Capabilities differ per backend.** Nimbus implements only single-channel pick/drop/aspirate/
  dispense; 96-head + resource-moves raise `NotImplementedError` (nimbus_backend.py:2269-2288).
  Capability detection drives which tools are exposed.
- `transfer` expands to aspirate + N dispenses internally → record/replay unit is the verb.

## Tech stack
- Python 3.10+ (repo `>=3.9`, but `nimbus_backend.py` uses `X | Y` unions). Async throughout.
- MCP: official `mcp` SDK / FastMCP, **stdio** transport. (`pip install mcp` — not yet present.)
- Schemas/validation: pydantic v2 (already 2.12.5) for resolved tool args + audit records.
- Audit: stdlib `hashlib` + append-only JSONL (hash-chained). Tamper-EVIDENT (state threat
  model: detects post-hoc edits, not write-time tampering by the running process). No DB.
- Backends: `chatterbox` (dry-run) + any real PLR backend (Nimbus verified, others supported).
- Deck config: JSON via PLR deserialize, coerced to the backend-matched deck subclass.
- Tests: pytest + pytest-timeout, `*_tests.py`. Chatterbox = fake HW in CI.
- Lint/type: ruff (line 100, indent 2) + mypy, matching repo.
- Packaging/distribution: setuptools. Console entry `labverify-mcp` + `.mcp.json` snippet.
- Visualizer: existing PLR Visualizer (phase-2 web UI base; keep OUT of the MCP process for now
  — it spawns its own asyncio loop in a thread, viz lines 481-483).

## Package layout (new code only)
```
labverify/
  __init__.py
  session.py        # Session: holds dry-run LH + real LH (lazy), mode, recorded verbs, deck
                    #   config. record-then-commit + confirm_run() (replays verbs on real LH).
  capabilities.py   # per-backend supported-verb map / detection -> which MCP tools to expose.
  resolver.py       # JSON -> PLR objects: name lookup, "A1:H1" ranges, Coordinate, enums.
  audit.py          # AuditLog: append-only hash-chained JSONL; verify_chain();
                    #   refuse-to-execute if write fails. Uses SerializingBackend serialize().
  deck_loader.py    # load_deck(path, backend) -> backend-matched Deck subclass; clear errors.
  mcp_server.py     # FastMCP: registers capability-filtered verb tools + describe_deck,
                    #   get_state, confirm_run. stdio entry point labverify-mcp.
  *_tests.py        # pytest, chatterbox-backed.
```

## Data flow
```
Claude Code --MCP/stdio--> mcp_server (tools filtered by REAL-backend capability table)
   verb tool(JSON) -> record raw tool-call JSON (stable selectors)
                   -> resolver(JSON->objects on DRY-RUN deck) -> dry-run LH (chatterbox,
                      tracking ON, lab state loaded) -> replay-log(simulated)
   confirm_run() -> build fresh real LH (own backend) + load same deck/state
                 -> for each recorded tool-call JSON: resolver(JSON->objects on REAL deck)
                    -> real LH verb -> per-op audit captured from execution (executed)
   describe_deck / get_state -> read-only introspection
Replay log = verb-level JSON.  Audit log = per-op, hash-chained JSONL, from real execution.
```

## Test plan (all new code; target 100%)
- session: dry-run LH and real LH are SEPARATE; dry run does not mutate real LH state
  (CRITICAL regression guard for the P1); confirm replays verbs on fresh real LH; double
  confirm_run guard (CRITICAL); empty-recording no-op; HW raises mid-replay -> catch + audit
  run_failed + structured error (CRITICAL).
- capabilities: Nimbus exposes single-channel set only; a STAR-like backend exposes 96-head;
  unsupported verb is NOT registered as a tool.
- resolver: name lookup, "A1:H1" range, bad name -> clear error, [x,y,z] -> Coordinate, enum str.
- audit: hash links prev; verify_chain detects tamper; simulated vs executed tag; disk write
  fails -> refuse to execute (CRITICAL).
- deck_loader: valid JSON -> correct backend deck subclass; wrong deck type for backend ->
  clear error; missing/malformed file.
- mcp_server: describe_deck w/ no deck -> clear error; get_state; bad args -> structured error.
- E2E: dry->confirm->execute via two chatterbox LHs; MCP stdio round-trip.

## Failure modes
| Path | Failure | Test | Handling | Silent? |
|------|---------|------|----------|---------|
| confirm_run replay | real backend raises mid-run | yes (CRIT) | catch + run_failed audit + error | no |
| shared state | dry run corrupts real LH | yes (CRIT) | two-instance design prevents | no |
| AuditLog | disk write fails | yes (CRIT) | refuse to execute | no |
| session | double confirm_run | yes (CRIT) | guard + reject | no |
| capabilities | verb unsupported by backend | yes | tool not exposed | no |
| resolver | bad resource name / range | yes | structured error to agent | no |
| deck_loader | deck type != backend | yes | clear error | no |

## NOT in scope
Web UI (phase 2); multi-instrument orchestration/scheduler; non-liquid-handler devices;
agent loop / LLM hosting (client brings Claude); auth / multi-tenant; full ELN; SiLA2;
real-hardware validation of non-Nimbus backends (supported via PLR, chatterbox-validated only).

## Review status
Round 1 (rev 1->2): three reviewers. Consensus P1 (single-LH state corruption) fixed by
two-instance design; folded in JSON->object resolver + vendor-agnostic capability surface.

Round 2 (rev 2->3): three reviewers again (Codex 20-min deep pass + cloud subagent + self).
Consensus P1s, all now addressed in the rev-3 changes block above:
- Replay must record stable selectors and re-resolve on the real deck (reusing dry-run objects
  is SILENTLY unsafe: == is structural, aspirate/dispense skip the existence check).
- Capability detection by introspection is unworkable; use an explicit per-backend table.
- Dry run is hollow unless tracking is enabled AND lab state (tips/volumes) is loaded.
Plus P2s: two-log audit model, separate backend instances, real-LH setup/retry lifecycle.
Quality: rev 2 ~6/10 -> rev 3 ~9/10.
