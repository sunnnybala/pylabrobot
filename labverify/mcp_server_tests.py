"""Tests for the MCP server wiring (tool registration + dispatch), no stdio needed."""

import asyncio

from labverify.mcp_server import build_server
from labverify.session import Session


def _chatterbox():
  from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend

  return LiquidHandlerChatterboxBackend()


def _session(tmp_path):
  return Session(
    real_backend=_chatterbox(),
    audit_path=str(tmp_path / "audit.jsonl"),
    dry_backend=_chatterbox(),
  )


def test_server_registers_expected_tools(tmp_path):
  mcp = build_server(_session(tmp_path))
  names = {t.name for t in asyncio.run(mcp.list_tools())}
  assert {"pick_up_tips", "drop_tips", "aspirate", "dispense"} <= names
  assert {"describe_deck", "get_state", "confirm_run"} <= names


def test_tool_dispatch_drives_session(tmp_path):
  # Calling tools through the server should drive the underlying session end-to-end.
  async def run():
    session = _session(tmp_path)
    mcp = build_server(session)

    # Invoke tools the way an MCP client would, via call_tool(name, args).
    await mcp.call_tool("pick_up_tips", {"resource": "tips_01", "range": "A1:C1"})
    await mcp.call_tool(
      "aspirate", {"resource": "plate_01", "range": "A1:C1", "vols": [50.0, 50.0, 50.0]}
    )
    state = session.get_state()
    assert state["recorded"] == 2
    assert state["mode"] == "dry_run"

    await mcp.call_tool("confirm_run", {})
    assert session.get_state()["mode"] == "committed"
    ok, bad = session.audit.verify_chain()
    assert ok is True, f"chain broke at {bad}"
    await session.stop()

  asyncio.run(run())
