"""The record-then-commit engine.

Rev-3 design + post-review hardening:
  - TWO LiquidHandler instances, each on its own independent deck. Dry-run LH = chatterbox;
    real LH = whatever real backend is passed in.
  - Agent tool calls are recorded as JSON (stable selectors), simulated on the dry-run LH,
    audited "simulated". `confirm_run()` builds a FRESH real LH and re-resolves each recorded
    call by name on the real deck before executing. Dry-run objects never cross over.
  - Verb surface gated by the REAL backend's capability table.
  - SAFETY INVARIANT: every real op is preceded by a durable "executing" audit record (written
    fail-closed). So a crash leaves an "executing" with no "executed" — detectable, never silent.
  - CONCURRENCY: all state transitions run under an asyncio.Lock, so concurrent MCP tool calls
    cannot double-execute or interleave the recorded protocol.
  - Tip tracking is process-global in PLR; we save the prior value on start and restore on stop.

    agent --simulate(verb,json)--> [dry-run LH] --audit:simulated   (recorded)
    agent --confirm_run()-------->  [fresh real LH] per op: audit:executing -> run -> audit:executed
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from labverify import capabilities
from labverify import deck_loader
from labverify.audit import AuditLog
from labverify.resolver import resolve_call


class SessionError(RuntimeError):
  """Raised on invalid session transitions (unsupported verb, double confirm, etc.)."""


class Session:
  """One agent session over a pair of liquid handlers.

  Args:
    real_backend: the real PyLabRobot backend (capability source + execution target at confirm).
    audit_path: path for the hash-chained audit log.
    make_lh: factory ``backend -> LiquidHandler`` (defaults to the demo deck builder). MUST build
      a deck that matches the backend (e.g. a NimbusDeck for a NimbusBackend).
    dry_backend: backend for the dry run (defaults to a fresh chatterbox).
    clock: injectable clock for deterministic audit timestamps in tests.
  """

  def __init__(
    self,
    real_backend: Any,
    audit_path: str,
    make_lh: Optional[Callable[[Any], Any]] = None,
    dry_backend: Any = None,
    clock: Optional[Callable[[], str]] = None,
  ):
    self._real_backend = real_backend
    self._dry_backend = dry_backend
    self._make_lh = make_lh or deck_loader.make_lh
    self.audit = AuditLog(audit_path, clock=clock)
    self._lock = asyncio.Lock()
    self._dry_lh: Any = None
    self._real_lh: Any = None
    self._recorded: List[Tuple[str, Dict[str, Any]]] = []
    self._supported: Optional[set] = None
    self._confirm_started = False
    self._committed = False
    self._failed = False
    self._executed = 0
    self._prior_tip_tracking: Optional[bool] = None

  # --- read-only helpers (no state mutation, no lock needed) ---

  def capability_set(self) -> set:
    """Verbs the REAL backend supports, computed synchronously (no setup needed)."""
    return capabilities.supported_verbs(self._real_backend)

  def describe_deck(self) -> Dict[str, Any]:
    """Read-only view of the dry-run deck: every resource name in the tree."""
    if self._dry_lh is None:
      raise SessionError("session not started")

    def names(resource: Any) -> List[str]:
      out = [resource.name]
      for child in resource.children:
        out.extend(names(child))
      return out

    return {"resources": names(self._dry_lh.deck)}

  def get_state(self) -> Dict[str, Any]:
    if self._failed:
      mode = "failed"
    elif self._committed:
      mode = "committed"
    else:
      mode = "dry_run"
    return {
      "mode": mode,
      "recorded": len(self._recorded),
      "executed": self._executed,
      "supported_verbs": sorted(self._supported) if self._supported else [],
    }

  # --- lifecycle ---

  async def ensure_started(self) -> None:
    """Idempotently start the session (safe to call from each MCP tool, race-safe)."""
    async with self._lock:
      await self._start_locked()

  async def start(self) -> None:
    async with self._lock:
      await self._start_locked()

  async def _start_locked(self) -> None:
    """Set up the dry-run LH (tracking on) and compute the supported verb surface.

    Caller MUST hold self._lock. Idempotent.
    """
    if self._dry_lh is not None:
      return
    from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
    from pylabrobot.resources import does_tip_tracking, set_tip_tracking

    if self._dry_backend is None:
      self._dry_backend = LiquidHandlerChatterboxBackend()
    # Tip tracking must be ON for the dry run to validate tip presence. It is process-global in
    # PLR, so we save the prior value and restore it in stop().
    self._prior_tip_tracking = does_tip_tracking()
    set_tip_tracking(enabled=True)

    self._supported = capabilities.supported_verbs(self._real_backend)
    self._dry_lh = self._make_lh(self._dry_backend)
    await self._dry_lh.setup()
    deck_loader.populate(self._dry_lh)
    self.audit.append("session_start", "system", {"supported_verbs": sorted(self._supported)})

  async def simulate(self, verb: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run one verb on the dry-run LH, record it, audit it as simulated."""
    async with self._lock:
      if self._confirm_started:
        raise SessionError("session already confirmed; cannot simulate further")
      if self._supported is None:
        raise SessionError("session not started")
      if verb not in self._supported:
        raise SessionError(
          f"verb {verb!r} not supported by {type(self._real_backend).__name__}"
        )
      pos, kw = resolve_call(self._dry_lh.deck, verb, args)
      try:
        await getattr(self._dry_lh, verb)(*pos, **kw)
      except Exception as e:
        # A rejected simulation is itself audit-worthy ("the agent tried X, it was rejected").
        self.audit.append("simulate_failed", "system", {"verb": verb, "args": args, "error": str(e)})
        raise
      self.audit.append(verb, "simulated", args)
      self._recorded.append((verb, args))
      return {"recorded": len(self._recorded)}

  async def confirm_run(self) -> Dict[str, Any]:
    """Build a fresh real LH and replay the recorded calls on it, re-resolved by name.

    One-shot. Each real op is preceded by a durable "executing" audit record, so a crash can
    never leave a hardware action with no trace.
    """
    async with self._lock:
      if self._confirm_started:
        raise SessionError("confirm_run already attempted; start a new session to retry")
      self._confirm_started = True  # set BEFORE any await, under the lock

      if not self._recorded:
        self.audit.append("confirm_run", "system", {"ops": 0, "note": "nothing to run"})
        self._committed = True
        return {"executed": 0}

      self._real_lh = self._make_lh(self._real_backend)
      await self._real_lh.setup()
      deck_loader.populate(self._real_lh)
      self.audit.append("confirm_run", "system", {"ops": len(self._recorded)})

      for verb, args in self._recorded:
        # Intent first, fail-closed: if we can't durably log the intent, AuditError is raised
        # here and the op below never runs.
        self.audit.append(verb, "executing", args)
        try:
          pos, kw = resolve_call(self._real_lh.deck, verb, args)
          await getattr(self._real_lh, verb)(*pos, **kw)
        except Exception as e:
          self._failed = True
          self.audit.append(
            "run_failed", "system", {"verb": verb, "args": args, "error": str(e)}
          )
          raise SessionError(f"run failed at {verb} (after {self._executed} ops): {e}") from e
        self.audit.append(verb, "executed", args)
        self._executed += 1

      self._committed = True
      return {"executed": self._executed}

  async def stop(self) -> None:
    if self._dry_lh is not None:
      await self._dry_lh.stop()
    if self._real_lh is not None:
      await self._real_lh.stop()
    if self._prior_tip_tracking is not None:
      from pylabrobot.resources import set_tip_tracking

      set_tip_tracking(enabled=self._prior_tip_tracking)
      self._prior_tip_tracking = None
