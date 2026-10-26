"""Append-only, hash-chained audit log.

This is the compliance core of Labverify. Every operation the engine performs (simulated
during a dry run, or executed on real hardware) is appended as one JSON line whose hash is
chained to the previous entry. Any post-hoc edit to an earlier line breaks the chain, so
`verify_chain()` can detect tampering.

Threat model: tamper-EVIDENT, not tamper-proof. A process with write access to the file can
recompute the whole chain. What this guarantees is that an after-the-fact edit (the common
audit-fraud case) is detectable, and that we never move real hardware without a durable record.

Fail-closed: if a record cannot be durably written (disk full, unwritable path), `append`
raises `AuditError`. Callers MUST treat that as "do not execute" — we never run an operation
on hardware that we could not log.

Chain:
    entry_hash[i] = sha256( prev_hash[i] + canonical_json(core[i]) )
    prev_hash[0]  = GENESIS ("0" * 64)
    prev_hash[i]  = entry_hash[i-1]
where core = {index, ts, tag, event, payload}.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

GENESIS_HASH = "0" * 64


class AuditError(RuntimeError):
  """Raised when a record cannot be durably written. Callers must refuse to execute."""


def _utcnow_iso() -> str:
  return datetime.now(timezone.utc).isoformat()


def _canonical(obj: Any) -> str:
  """Deterministic JSON: sorted keys, no whitespace. Used for hashing."""
  return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev_hash: str, core: Dict[str, Any]) -> str:
  return hashlib.sha256((prev_hash + _canonical(core)).encode("utf-8")).hexdigest()


class AuditLog:
  """An append-only hash-chained JSONL audit log.

  Args:
    path: file path for the JSONL log. Created on first append if absent.
    clock: callable returning an ISO-8601 timestamp string. Injectable for deterministic
      tests; defaults to UTC now.
  """

  def __init__(self, path: str, clock: Optional[Callable[[], str]] = None):
    self.path = path
    self._clock = clock or _utcnow_iso
    self._needs_leading_newline = False
    self._last_hash, self._count = self._scan_tail()

  def _scan_tail(self) -> Tuple[str, int]:
    """Return (last_entry_hash, record_count) by scanning an existing file.

    A crash can leave a torn (partial) final line. We tolerate it: lines that don't parse are
    skipped, so a half-written trailing record is ignored rather than bricking startup. The
    next append simply continues from the last durably-written record.
    """
    if not os.path.exists(self.path):
      return GENESIS_HASH, 0
    last_hash = GENESIS_HASH
    count = 0
    had_final_newline = True
    with open(self.path, "r", encoding="utf-8") as f:
      for line in f:
        had_final_newline = line.endswith("\n")
        if not line.strip():
          continue
        try:
          rec = json.loads(line)
        except json.JSONDecodeError:
          continue  # torn/corrupt line (e.g. crash mid-write) — skip, do not crash
        last_hash = rec.get("entry_hash", last_hash)
        count += 1
    # If the file ends without a newline (torn final write), the next append must start on a
    # fresh line so it isn't concatenated onto the partial record.
    self._needs_leading_newline = not had_final_newline
    return last_hash, count

  def append(self, event: str, tag: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Append one record and return it. Fail-closed: raises AuditError if it cannot be written.

    Args:
      event: the operation name (e.g. "aspirate", "confirm_run", "run_failed").
      tag: provenance, one of "simulated" | "executed" | "system".
      payload: arbitrary JSON-serializable detail (resource names, volumes, error text...).
    """
    if tag not in ("simulated", "executing", "executed", "system"):
      raise ValueError(f"invalid tag: {tag!r}")
    core = {
      "index": self._count,
      "ts": self._clock(),
      "tag": tag,
      "event": event,
      "payload": payload,
    }
    entry_hash = _hash(self._last_hash, core)
    record = {**core, "prev_hash": self._last_hash, "entry_hash": entry_hash}
    line = _canonical(record) + "\n"
    if self._needs_leading_newline:
      line = "\n" + line  # heal framing after a torn trailing line
    try:
      # Append + flush + fsync so a crash can't lose an acknowledged record.
      with open(self.path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    except OSError as e:
      raise AuditError(f"could not write audit record (refusing to execute): {e}") from e
    self._needs_leading_newline = False
    self._last_hash = entry_hash
    self._count += 1
    return record

  def records(self) -> List[Dict[str, Any]]:
    """Return all records in order."""
    if not os.path.exists(self.path):
      return []
    out: List[Dict[str, Any]] = []
    with open(self.path, "r", encoding="utf-8") as f:
      for line in f:
        if not line.strip():
          continue
        try:
          out.append(json.loads(line))
        except json.JSONDecodeError:
          continue  # tolerate a torn trailing line (see _scan_tail)
    return out

  def verify_chain(self) -> Tuple[bool, Optional[int]]:
    """Recompute the chain. Returns (ok, first_bad_index).

    ok is False if any record's hash does not match its (prev_hash, core), or if prev_hash
    does not equal the previous record's entry_hash, or if indices are not contiguous.
    """
    prev = GENESIS_HASH
    for expected_index, rec in enumerate(self.records()):
      core = {k: rec.get(k) for k in ("index", "ts", "tag", "event", "payload")}
      if rec.get("index") != expected_index:
        return False, expected_index
      if rec.get("prev_hash") != prev:
        return False, expected_index
      if _hash(prev, core) != rec.get("entry_hash"):
        return False, expected_index
      prev = rec["entry_hash"]
    return True, None
