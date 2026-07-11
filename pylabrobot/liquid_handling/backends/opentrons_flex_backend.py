"""Opentrons **Flex** backend for PyLabRobot (LOCAL addition — not upstream).

PLR ships only `OpentronsOT2Backend`; there is no Flex support. The Flex runs the same robot-server
HTTP API as the OT-2 (port 31950), so we drive it with a *maintenance run* and stream one command
per PLR op via `POST /runs/{id}/commands?waitUntilComplete=true`:

    POST /runs                          -> runId
    POST /runs/{id}/commands  loadPipette / loadLabware / pickUpTip / aspirate / dispense / dropTip
    POST /runs/{id}/actions   {actionType: "stop"}      (E-STOP / teardown)

Differences from the OT-2 that this backend encodes: robotType "Flex", `flex_*` pipette names,
named deck slots ("A1".."D3") instead of integer slots, and an explicit trash bin.

The HTTP transport is INJECTED (`request`), exactly like labverify's run_api.py, so the backend is
unit-testable with a fake robot and no hardware. The default transport uses httpx.

NOT YET RUN AGAINST A REAL FLEX — calibration, deck config, and the exact `Opentrons-Version`
header should be confirmed on first physical bring-up. Kept deliberately close to the verified
labverify Run-API shape to minimise that risk.
"""

from typing import Any, Callable, Dict, List, Optional, Union

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Deck
from pylabrobot.resources.tip_rack import TipRack

# request(method, path, *, json=None, files=None) -> parsed JSON dict
RequestFn = Callable[..., Dict[str, Any]]

# Flex pipette load name -> max volume (µL). Mirrors OT-2 backend's pipette_name2volume table.
FLEX_PIPETTES: Dict[str, int] = {
  "flex_1channel_50": 50,
  "flex_1channel_1000": 1000,
  "flex_8channel_50": 50,
  "flex_8channel_1000": 1000,
  "flex_96channel_1000": 1000,
}


class OpentronsFlexError(RuntimeError):
  """A Flex Run-API call failed or the robot rejected a command."""


class OpentronsFlexBackend(LiquidHandlerBackend):
  """Drive an Opentrons Flex through the robot-server HTTP API via a live maintenance run.

  Args:
    host: robot IP / mDNS name (Opentrons App > Settings > Network).
    port: HTTP API port (31950).
    left_pipette / right_pipette: Flex pipette load names to mount, e.g. "flex_1channel_1000".
      At least one is required.
    request: injected transport ``(method, path, *, json=None, files=None) -> dict``. Defaults to
      httpx; inject a fake in tests.
    api_version: value for the required ``Opentrons-Version`` header.
  """

  def __init__(
    self,
    host: str,
    port: int = 31950,
    *,
    left_pipette: Optional[str] = None,
    right_pipette: Optional[str] = None,
    request: Optional[RequestFn] = None,
    api_version: str = "3",
  ):
    super().__init__()
    if not left_pipette and not right_pipette:
      raise ValueError("mount at least one pipette (left_pipette= and/or right_pipette=)")
    for name in (left_pipette, right_pipette):
      if name and name not in FLEX_PIPETTES:
        raise ValueError(f"unknown Flex pipette {name!r}; one of {sorted(FLEX_PIPETTES)}")
    self.host = host
    self.port = port
    self._base = f"http://{host}:{port}"
    self._request = request or self._default_request
    self._headers = {"Opentrons-Version": api_version}
    self._mounts: Dict[str, Optional[str]] = {"left": left_pipette, "right": right_pipette}

    self._run_id: Optional[str] = None
    self._pipette_ids: Dict[str, str] = {}  # mount -> pipetteId returned by loadPipette
    self._labware_ids: Dict[str, str] = {}  # plr resource.name -> Flex labwareId
    self._tip_pipette: Optional[str] = None  # mount currently holding a tip

  def serialize(self) -> dict:
    return {**super().serialize(), "host": self.host, "port": self.port, "mounts": self._mounts}

  @property
  def num_channels(self) -> int:
    return len([m for m in self._mounts.values() if m])

  # --- lifecycle -----------------------------------------------------------------------------

  async def setup(self):
    """Create a maintenance run and load the mounted pipettes."""
    self._run_id = self._dig(self._post("/runs", {"data": {}}), "run create")
    self._pipette_ids = {}
    for mount, name in self._mounts.items():
      if not name:
        continue
      res = self._command("loadPipette", {"pipetteName": name, "mount": mount})
      pid = ((res.get("data") or {}).get("result") or {}).get("pipetteId")
      if not pid:
        raise OpentronsFlexError(f"loadPipette({name}) returned no pipetteId: {res!r}")
      self._pipette_ids[mount] = pid

  async def stop(self):
    """Stop the run so the robot is released (also serves as E-STOP for an in-flight run)."""
    if self._run_id:
      try:
        self._post(f"/runs/{self._run_id}/actions", {"data": {"actionType": "stop"}})
      except OpentronsFlexError:
        pass
    self._run_id = None
    self._pipette_ids = {}
    self._labware_ids = {}
    self._tip_pipette = None

  # --- liquid handling verbs (one PLR op -> one Flex command) ---------------------------------

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    op = ops[0]
    tip_rack = op.resource.parent
    assert isinstance(tip_rack, TipRack), "a tip's parent must be a TipRack"
    labware_id = self._ensure_labware(tip_rack)
    mount = self._mount_for(op.tip.maximal_volume)
    self._command("pickUpTip", {
      "pipetteId": self._pipette_ids[mount],
      "labwareId": labware_id,
      "wellName": op.resource.name.split("_")[-1],
    })
    self._tip_pipette = mount

  async def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    op = ops[0]
    mount = self._tip_pipette or self._mount_for(op.tip.maximal_volume)
    # Drop into the Flex trash bin addressable area (default deck fixture on the Flex).
    self._command("moveToAddressableAreaForDropTip",
                  {"pipetteId": self._pipette_ids[mount], "addressableAreaName": "movableTrashA3"})
    self._command("dropTipInPlace", {"pipetteId": self._pipette_ids[mount]})
    self._tip_pipette = None

  async def aspirate(self, ops: List[SingleChannelAspiration], use_channels: List[int]):
    op = ops[0]
    mount = self._tip_pipette or self._mount_for(op.volume)
    labware_id = self._ensure_labware(op.resource.parent)
    self._command("aspirate", {
      "pipetteId": self._pipette_ids[mount],
      "labwareId": labware_id,
      "wellName": op.resource.name.split("_")[-1],
      "volume": op.volume,
      "flowRate": getattr(op, "flow_rate", None) or 150,
    })

  async def dispense(self, ops: List[SingleChannelDispense], use_channels: List[int]):
    op = ops[0]
    mount = self._tip_pipette or self._mount_for(op.volume)
    labware_id = self._ensure_labware(op.resource.parent)
    self._command("dispense", {
      "pipetteId": self._pipette_ids[mount],
      "labwareId": labware_id,
      "wellName": op.resource.name.split("_")[-1],
      "volume": op.volume,
      "flowRate": getattr(op, "flow_rate", None) or 150,
    })

  # --- unsupported on a single/8-channel Flex (mirror OT-2: raise rather than pretend) --------

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("96-head not implemented for the Flex backend yet")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("96-head not implemented for the Flex backend yet")

  async def aspirate96(self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]):
    raise NotImplementedError("96-head not implemented for the Flex backend yet")

  async def dispense96(self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]):
    raise NotImplementedError("96-head not implemented for the Flex backend yet")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("Flex gripper resource moves not implemented yet")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("Flex gripper resource moves not implemented yet")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("Flex gripper resource moves not implemented yet")

  def can_pick_up_tip(self, channel_idx: int, tip) -> bool:
    """A tip is pickable if any mounted pipette's capacity covers it."""
    caps = [FLEX_PIPETTES[n] for n in self._mounts.values() if n]
    return any(tip.maximal_volume <= c + 1e-6 for c in caps)

  # --- helpers -------------------------------------------------------------------------------

  def _mount_for(self, volume: float) -> str:
    """Pick the mounted pipette whose capacity fits ``volume`` (smallest that fits, else largest)."""
    candidates = [(m, FLEX_PIPETTES[n]) for m, n in self._mounts.items() if n]
    fitting = sorted([(m, v) for m, v in candidates if v + 1e-6 >= volume], key=lambda mv: mv[1])
    if fitting:
      return fitting[0][0]
    return max(candidates, key=lambda mv: mv[1])[0]  # nothing fits — let the engine's own check flag it

  def _ensure_labware(self, resource) -> str:
    """Load a PLR resource onto the Flex JIT via a loadLabware command; cache the labwareId.

    Uses the resource's Opentrons load name (``resource.model``) and its assigned deck slot.
    """
    if resource.name in self._labware_ids:
      return self._labware_ids[resource.name]
    load_name = getattr(resource, "model", None) or resource.name
    slot = self._slot_of(resource)
    res = self._command("loadLabware", {
      "location": {"slotName": slot},
      "loadName": load_name,
      "namespace": "opentrons",
      "version": 1,
    })
    lw_id = ((res.get("data") or {}).get("result") or {}).get("labwareId")
    if not lw_id:
      raise OpentronsFlexError(f"loadLabware({load_name}@{slot}) returned no labwareId: {res!r}")
    self._labware_ids[resource.name] = lw_id
    return lw_id

  def _slot_of(self, resource) -> str:
    """Find the Flex slot label for the resource by walking up to the FlexDeck."""
    from pylabrobot.resources.opentrons.flex_deck import FlexDeck

    node = resource
    while node is not None:
      parent = node.parent
      if isinstance(parent, FlexDeck):
        slot = parent.slot_for(node)
        if slot:
          return slot
      node = parent
    raise OpentronsFlexError(f"could not resolve a Flex deck slot for resource {resource.name!r}")

  def _command(self, command_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """POST one protocol-engine command and wait for it to complete."""
    if not self._run_id:
      raise OpentronsFlexError("backend not set up (no run); call setup() first")
    body = {"data": {"commandType": command_type, "params": params}}
    res = self._post(f"/runs/{self._run_id}/commands?waitUntilComplete=true", body)
    status = ((res.get("data") or {}).get("status"))
    if status == "failed":
      err = (res.get("data") or {}).get("error") or {}
      raise OpentronsFlexError(f"{command_type} failed: {err.get('detail', err)}")
    return res

  def _post(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
    return self._request("POST", path, json=json)

  @staticmethod
  def _dig(data: Dict[str, Any], what: str) -> str:
    ident = ((data or {}).get("data") or {}).get("id")
    if not ident:
      raise OpentronsFlexError(f"{what}: no id in response {data!r}")
    return ident

  def _default_request(self, method: str, path: str, *, json=None, files=None) -> Dict[str, Any]:
    import httpx

    try:
      with httpx.Client(timeout=60.0) as client:
        resp = client.request(method, self._base + path, json=json, files=files, headers=self._headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
      raise OpentronsFlexError(f"{method} {path} failed: {e}") from e
