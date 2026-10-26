"""Tests for the hash-chained audit log."""

import json

import pytest

from labverify.audit import GENESIS_HASH, AuditError, AuditLog


def _fixed_clock():
  """Deterministic monotonically-increasing timestamps for stable tests."""
  state = {"n": 0}

  def clock() -> str:
    state["n"] += 1
    return f"2026-01-01T00:00:{state['n']:02d}+00:00"

  return clock


def test_append_writes_record_and_fields(tmp_path):
  log = AuditLog(str(tmp_path / "a.jsonl"), clock=_fixed_clock())
  rec = log.append("aspirate", "simulated", {"well": "A1", "volume": 50.0})
  assert rec["index"] == 0
  assert rec["tag"] == "simulated"
  assert rec["event"] == "aspirate"
  assert rec["payload"] == {"well": "A1", "volume": 50.0}
  assert rec["prev_hash"] == GENESIS_HASH
  assert len(rec["entry_hash"]) == 64


def test_chain_links_prev_to_entry(tmp_path):
  log = AuditLog(str(tmp_path / "a.jsonl"), clock=_fixed_clock())
  r0 = log.append("pick_up_tips", "simulated", {"tips": ["A1"]})
  r1 = log.append("aspirate", "simulated", {"well": "A1", "volume": 50.0})
  r2 = log.append("dispense", "executed", {"well": "B1", "volume": 50.0})
  assert r1["prev_hash"] == r0["entry_hash"]
  assert r2["prev_hash"] == r1["entry_hash"]
  assert (r0["index"], r1["index"], r2["index"]) == (0, 1, 2)


def test_verify_chain_passes_on_good_log(tmp_path):
  log = AuditLog(str(tmp_path / "a.jsonl"), clock=_fixed_clock())
  for i in range(5):
    log.append("dispense", "executed", {"well": f"A{i}", "volume": 10.0})
  ok, bad = log.verify_chain()
  assert ok is True
  assert bad is None


def test_verify_chain_detects_tamper(tmp_path):
  path = tmp_path / "a.jsonl"
  log = AuditLog(str(path), clock=_fixed_clock())
  log.append("aspirate", "executed", {"well": "A1", "volume": 50.0})
  log.append("dispense", "executed", {"well": "B1", "volume": 50.0})
  log.append("dispense", "executed", {"well": "C1", "volume": 50.0})

  # Tamper with the middle record's payload, leaving its hash intact.
  lines = path.read_text(encoding="utf-8").splitlines()
  rec = json.loads(lines[1])
  rec["payload"]["volume"] = 9999.0
  lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
  path.write_text("\n".join(lines) + "\n", encoding="utf-8")

  ok, bad = AuditLog(str(path)).verify_chain()
  assert ok is False
  assert bad == 1


def test_reopen_continues_chain(tmp_path):
  path = tmp_path / "a.jsonl"
  first = AuditLog(str(path), clock=_fixed_clock())
  r0 = first.append("pick_up_tips", "simulated", {"tips": ["A1"]})

  # New instance on the same file must continue the chain, not restart it.
  second = AuditLog(str(path), clock=_fixed_clock())
  r1 = second.append("aspirate", "executed", {"well": "A1", "volume": 50.0})
  assert r1["index"] == 1
  assert r1["prev_hash"] == r0["entry_hash"]
  ok, _ = second.verify_chain()
  assert ok is True


def test_invalid_tag_rejected(tmp_path):
  log = AuditLog(str(tmp_path / "a.jsonl"))
  with pytest.raises(ValueError):
    log.append("aspirate", "pretend", {"well": "A1"})


def test_torn_trailing_line_is_tolerated_and_healed(tmp_path):
  path = tmp_path / "a.jsonl"
  log = AuditLog(str(path), clock=_fixed_clock())
  log.append("aspirate", "executed", {"v": 1})
  log.append("dispense", "executed", {"v": 2})

  # Simulate a crash mid-write: a partial final record with no trailing newline.
  with open(path, "a", encoding="utf-8") as f:
    f.write('{"index":2,"ts":"partial')

  # Reopen: the torn line is skipped, the two good records survive, chain verifies.
  reopened = AuditLog(str(path))
  assert len(reopened.records()) == 2
  ok, bad = reopened.verify_chain()
  assert ok is True, f"chain broke at {bad}"

  # A fresh append heals framing (starts on a new line) and continues the chain.
  rec = reopened.append("dispense", "executed", {"v": 3})
  assert rec["index"] == 2
  assert len(reopened.records()) == 3
  ok, bad = reopened.verify_chain()
  assert ok is True, f"chain broke at {bad}"


def test_append_fail_closed_on_unwritable_path(tmp_path):
  # Path under a file (not a dir) -> open() raises -> must surface as AuditError.
  not_a_dir = tmp_path / "afile"
  not_a_dir.write_text("x", encoding="utf-8")
  log = AuditLog(str(not_a_dir / "nested.jsonl"))
  with pytest.raises(AuditError):
    log.append("aspirate", "executed", {"well": "A1"})
