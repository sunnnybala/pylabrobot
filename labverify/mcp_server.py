"""FastMCP stdio server exposing a Labverify Session to any MCP client (Claude Code/Desktop).

Tools are gated twice: by what the ENGINE implements (`_ENGINE_VERBS`) and by what the REAL
backend supports (the capability table). So a Nimbus session never exposes 96-head tools.

All tools are async and run inside FastMCP's event loop; each calls `session.ensure_started()`
first, so the dry-run LH is set up lazily in that same loop (no cross-loop hand-off).

Run: `labverify-mcp` (see build_server / main). Default backend is chatterbox (demo, no
hardware). Production swaps in a real backend, e.g. NimbusBackend.
"""

from typing import Any, List

from mcp.server.fastmcp import FastMCP

from labverify.session import Session

# Verbs the engine's resolver can currently handle. Intersected with backend capabilities.
_ENGINE_VERBS = {"pick_up_tips", "drop_tips", "aspirate", "dispense"}


def build_server(session: Session, name: str = "labverify") -> FastMCP:
  """Build a FastMCP server wiring the given Session. Registers only verbs supported by both
  the engine and the real backend, plus describe_deck / get_state / confirm_run."""
  mcp = FastMCP(name)
  exposable = _ENGINE_VERBS & session.capability_set()

  if "pick_up_tips" in exposable:

    async def pick_up_tips(resource: str, range: str) -> dict:
      """Pick up tips. resource=tip-rack name, range e.g. 'A1:C1'. Simulated until confirm_run."""
      await session.ensure_started()
      return await session.simulate("pick_up_tips", {"resource": resource, "range": range})

    mcp.tool(name="pick_up_tips")(pick_up_tips)

  if "drop_tips" in exposable:

    async def drop_tips(resource: str, range: str) -> dict:
      """Drop tips. resource=tip-rack name, range e.g. 'A1:C1'. Simulated until confirm_run."""
      await session.ensure_started()
      return await session.simulate("drop_tips", {"resource": resource, "range": range})

    mcp.tool(name="drop_tips")(drop_tips)

  if "aspirate" in exposable:

    async def aspirate(resource: str, range: str, vols: List[float]) -> dict:
      """Aspirate. resource=plate name, range e.g. 'A1:C1', vols=uL per channel. Simulated."""
      await session.ensure_started()
      return await session.simulate(
        "aspirate", {"resource": resource, "range": range, "vols": vols}
      )

    mcp.tool(name="aspirate")(aspirate)

  if "dispense" in exposable:

    async def dispense(resource: str, range: str, vols: List[float]) -> dict:
      """Dispense. resource=plate name, range e.g. 'D1:F1', vols=uL per channel. Simulated."""
      await session.ensure_started()
      return await session.simulate(
        "dispense", {"resource": resource, "range": range, "vols": vols}
      )

    mcp.tool(name="dispense")(dispense)

  async def describe_deck() -> dict:
    """List every resource (tip racks, plates) on the deck, by name."""
    await session.ensure_started()
    return session.describe_deck()

  async def get_state() -> dict:
    """Session state: dry_run vs committed, recorded op count, supported verbs."""
    return session.get_state()

  async def confirm_run() -> dict:
    """Execute the recorded protocol on the REAL backend. One-shot. Until called, nothing runs."""
    await session.ensure_started()
    return await session.confirm_run()

  mcp.tool(name="describe_deck")(describe_deck)
  mcp.tool(name="get_state")(get_state)
  mcp.tool(name="confirm_run")(confirm_run)
  return mcp


def main() -> None:
  """Console entry point (`labverify-mcp`). Demo mode: chatterbox real backend, no hardware.

  Going to hardware is NOT just swapping the backend: each backend needs a matching deck. The
  demo `make_lh` builds a STARLetDeck, which a NimbusBackend rejects at setup. For Nimbus, pass
  a `make_lh` that builds a NimbusDeck, e.g.:

      from pylabrobot.liquid_handling.backends.hamilton.nimbus_backend import NimbusBackend
      from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
      from pylabrobot.liquid_handling import LiquidHandler
      def nimbus_make_lh(backend):
        return LiquidHandler(backend=backend, deck=NimbusDeck())
      session = Session(
        real_backend=NimbusBackend(host="192.168.100.100", port=2000),
        audit_path="labverify_audit.jsonl",
        make_lh=nimbus_make_lh,   # and a matching populate() for Nimbus labware
      )
  """
  from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend

  session = Session(
    real_backend=LiquidHandlerChatterboxBackend(),
    audit_path="labverify_audit.jsonl",
  )
  build_server(session).run(transport="stdio")


if __name__ == "__main__":
  main()
