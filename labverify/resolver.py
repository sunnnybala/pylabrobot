"""Resolve recorded tool-call JSON into PyLabRobot verb arguments, against a given deck.

Rev-3 decision: we record the agent's tool-call JSON (stable selectors: resource NAME +
well/tip RANGE + volumes), never live PLR objects. This resolver turns that JSON into the
positional/keyword args for a `LiquidHandler` verb, resolved against whichever deck it is
handed. It runs twice with identical inputs: once on the dry-run deck, once on the real deck
at confirm time. Because resource names are unique and stable across two decks built from the
same config, name-based resolution is the safe bridge between the two engines.
"""

from typing import Any, Dict, List, Tuple

# verb -> whether it carries a volume list
_LIQUID_VERBS = {"aspirate", "dispense"}
_TIP_VERBS = {"pick_up_tips", "drop_tips"}
_KNOWN = _LIQUID_VERBS | _TIP_VERBS


class ResolveError(RuntimeError):
  """Raised when JSON args cannot be resolved to real resources on the deck."""


def resolve_call(deck: Any, verb: str, args: Dict[str, Any]) -> Tuple[List[Any], Dict[str, Any]]:
  """Return (positional_args, kwargs) for ``LiquidHandler.<verb>``, resolved on ``deck``.

  Args JSON shape:
    tip verbs:    {"resource": "tips_01", "range": "A1:C1"}
    liquid verbs: {"resource": "plate_01", "range": "A1:C1", "vols": [100.0, 50.0, 200.0]}
  """
  if verb not in _KNOWN:
    raise ResolveError(f"resolver does not handle verb {verb!r}")
  name = args.get("resource")
  rng = args.get("range")
  if not name or not rng:
    raise ResolveError(f"{verb} requires 'resource' and 'range' (got {args!r})")

  try:
    resource = deck.get_resource(name)
  except Exception as e:  # PLR raises if the name is not on the deck
    raise ResolveError(f"resource {name!r} not found on deck: {e}") from e

  try:
    targets = resource[rng]  # PLR supports "A1:C1" range indexing
  except Exception as e:
    raise ResolveError(f"bad range {rng!r} on {name!r}: {e}") from e

  if verb in _LIQUID_VERBS:
    vols = args.get("vols")
    if vols is None:
      raise ResolveError(f"{verb} requires 'vols'")
    return [targets], {"vols": vols}
  return [targets], {}
