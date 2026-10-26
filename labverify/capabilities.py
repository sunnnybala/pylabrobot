"""Per-backend capability table.

Rev-3 decision: we do NOT detect supported verbs by introspection. The verbs are
``@abstractmethod`` on the base, and concrete backends (e.g. Nimbus) "support" the abstract
contract by overriding the method and then `raise NotImplementedError` inside it
(nimbus_backend.py:2269-2288). So ``type(b).verb is not Base.verb`` reports those as supported
when they are not. We therefore keep an EXPLICIT table, keyed by backend class name, and treat
a runtime ``NotImplementedError`` as a backstop (the caller de-registers / refuses the verb).

Capability is always sourced from the REAL backend, never the chatterbox dry-run backend
(chatterbox implements 96-head and resource-move verbs that real backends like Nimbus do not).
"""

from typing import Any, FrozenSet, Set

# The single-channel core every liquid handler we target supports.
_SINGLE_CHANNEL: FrozenSet[str] = frozenset(
  {"pick_up_tips", "drop_tips", "aspirate", "dispense", "transfer"}
)

# 96-head / multi-probe verbs.
_HEAD96: FrozenSet[str] = frozenset(
  {"pick_up_tips96", "drop_tips96", "aspirate96", "dispense96", "stamp"}
)

# Integrated-gripper resource movement verbs.
_RESOURCE_MOVES: FrozenSet[str] = frozenset(
  {"pick_up_resource", "move_picked_up_resource", "drop_resource", "move_plate", "move_lid"}
)

ALL_VERBS: FrozenSet[str] = _SINGLE_CHANNEL | _HEAD96 | _RESOURCE_MOVES

# Explicit table keyed by backend class name. Matched against the backend's full MRO so
# subclasses inherit their parent's entry unless they declare their own.
CAPABILITY_TABLE = {
  # Nimbus: single-channel only. 96-head + resource moves raise NotImplementedError.
  "NimbusBackend": _SINGLE_CHANNEL,
  # Chatterbox simulates everything (dry-run only — never used as a capability source).
  "LiquidHandlerChatterboxBackend": ALL_VERBS,
}


class UnknownBackendError(RuntimeError):
  """Raised when a backend has no capability-table entry and we refuse to guess."""


def supported_verbs(backend: Any) -> Set[str]:
  """Return the set of verbs the given (real) backend supports.

  Looks up the backend's class and every ancestor by name, so subclasses inherit. Raises
  ``UnknownBackendError`` if no entry is found — we never guess a hardware capability surface.
  """
  for klass in type(backend).__mro__:
    entry = CAPABILITY_TABLE.get(klass.__name__)
    if entry is not None:
      return set(entry)
  raise UnknownBackendError(
    f"no capability entry for backend {type(backend).__name__!r}; "
    f"add it to CAPABILITY_TABLE before exposing tools"
  )


def is_supported(backend: Any, verb: str) -> bool:
  """True if ``verb`` is supported by ``backend``."""
  return verb in supported_verbs(backend)
