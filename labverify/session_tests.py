"""End-to-end engine tests on two chatterbox LiquidHandlers.

The "real" backend here is a second chatterbox standing in for hardware we don't have yet.
This exercises the full rev-3 loop: dry-run simulate + record, then confirm_run re-resolves
by name on a fresh second deck and executes, all audited and hash-chained.
"""

import asyncio

import pytest

from labverify.session import Session, SessionError


def _chatterbox():
  from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend

  return LiquidHandlerChatterboxBackend()


def _fixed_clock():
  state = {"n": 0}

  def clock() -> str:
    state["n"] += 1
    return f"2026-01-01T00:00:{state['n']:02d}+00:00"

  return clock


def _new_session(tmp_path):
  return Session(
    real_backend=_chatterbox(),
    audit_path=str(tmp_path / "audit.jsonl"),
    dry_backend=_chatterbox(),
    clock=_fixed_clock(),
  )


# The protocol an agent would issue: pick up 3 tips, move liquid A->D, drop tips.
_PROTOCOL = [
  ("pick_up_tips", {"resource": "tips_01", "range": "A1:C1"}),
  ("aspirate", {"resource": "plate_01", "range": "A1:C1", "vols": [100.0, 50.0, 200.0]}),
  ("dispense", {"resource": "plate_01", "range": "D1:F1", "vols": [100.0, 50.0, 200.0]}),
  ("drop_tips", {"resource": "tips_01", "range": "A1:C1"}),
]


def test_full_dry_run_then_confirm(tmp_path):
  async def run():
    s = _new_session(tmp_path)
    await s.start()
    for verb, args in _PROTOCOL:
      await s.simulate(verb, args)

    # Before confirm: everything simulated, nothing executed.
    recs = s.audit.records()
    sims = [r for r in recs if r["tag"] == "simulated"]
    execs = [r for r in recs if r["tag"] == "executed"]
    assert len(sims) == 4
    assert len(execs) == 0
    assert s.get_state()["mode"] == "dry_run"
    assert s.get_state()["recorded"] == 4

    result = await s.confirm_run()
    assert result["executed"] == 4

    # After confirm: 4 executed records, chain intact.
    recs = s.audit.records()
    assert len([r for r in recs if r["tag"] == "executed"]) == 4
    ok, bad = s.audit.verify_chain()
    assert ok is True, f"chain broke at {bad}"
    assert s.get_state()["mode"] == "committed"
    await s.stop()

  asyncio.run(run())


def test_double_confirm_is_rejected(tmp_path):
  async def run():
    s = _new_session(tmp_path)
    await s.start()
    await s.simulate(*_PROTOCOL[0])
    await s.confirm_run()
    with pytest.raises(SessionError):
      await s.confirm_run()
    await s.stop()

  asyncio.run(run())


def test_simulate_after_confirm_is_rejected(tmp_path):
  async def run():
    s = _new_session(tmp_path)
    await s.start()
    await s.simulate(*_PROTOCOL[0])
    await s.confirm_run()
    with pytest.raises(SessionError):
      await s.simulate(*_PROTOCOL[1])
    await s.stop()

  asyncio.run(run())


def test_confirm_with_no_ops_is_noop(tmp_path):
  async def run():
    s = _new_session(tmp_path)
    await s.start()
    result = await s.confirm_run()
    assert result["executed"] == 0
    ok, _ = s.audit.verify_chain()
    assert ok is True
    await s.stop()

  asyncio.run(run())


def test_concurrent_confirm_runs_execute_only_once(tmp_path):
  # Two concurrent confirm_run() calls must not both replay on hardware.
  async def run():
    s = _new_session(tmp_path)
    await s.start()
    await s.simulate(*_PROTOCOL[0])
    results = await asyncio.gather(s.confirm_run(), s.confirm_run(), return_exceptions=True)
    successes = [r for r in results if isinstance(r, dict)]
    errors = [r for r in results if isinstance(r, SessionError)]
    assert len(successes) == 1, results
    assert len(errors) == 1, results
    # Exactly ONE executed record, not two.
    execs = [r for r in s.audit.records() if r["tag"] == "executed"]
    assert len(execs) == 1
    await s.stop()

  asyncio.run(run())


def _raising_real_backend():
  from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend

  class RaisingChatterbox(LiquidHandlerChatterboxBackend):
    async def aspirate(self, *a, **k):
      raise RuntimeError("simulated hardware fault")

  return RaisingChatterbox()


def test_failed_confirm_reports_failed_state_and_audits(tmp_path):
  async def run():
    s = Session(
      real_backend=_raising_real_backend(),  # aspirate blows up on the "real" backend
      audit_path=str(tmp_path / "audit.jsonl"),
      dry_backend=_chatterbox(),
      clock=_fixed_clock(),
    )
    await s.start()
    for verb, args in _PROTOCOL:
      await s.simulate(verb, args)

    with pytest.raises(SessionError):
      await s.confirm_run()

    state = s.get_state()
    assert state["mode"] == "failed"  # not "dry_run" — hardware partially moved
    assert state["executed"] == 1  # pick_up_tips ran before aspirate failed

    recs = s.audit.records()
    assert any(r["event"] == "run_failed" for r in recs)
    # The failed op has an "executing" intent record but NO "executed" confirmation.
    assert any(r["tag"] == "executing" and r["event"] == "aspirate" for r in recs)
    assert not any(r["tag"] == "executed" and r["event"] == "aspirate" for r in recs)
    ok, bad = s.audit.verify_chain()
    assert ok is True, f"chain broke at {bad}"
    await s.stop()

  asyncio.run(run())


def test_tip_tracking_is_restored_on_stop(tmp_path):
  async def run():
    from pylabrobot.resources import does_tip_tracking, set_tip_tracking

    set_tip_tracking(enabled=False)  # known prior state
    s = _new_session(tmp_path)
    await s.start()
    assert does_tip_tracking() is True  # session turned it on
    await s.stop()
    assert does_tip_tracking() is False  # restored to prior

  asyncio.run(run())
