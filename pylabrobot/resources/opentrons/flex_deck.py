from typing import Dict, List, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource import Resource

# Opentrons Flex deck: a 3-wide (columns 1-3) x 4-tall (rows A-D) grid of cutouts.
# Absolute cutout origins in mm, taken from opentrons_shared_data deck def "ot3_standard" (schema 5).
# Rows run A (back) -> D (front); columns 1 (left) -> 3 (right).
FLEX_CUTOUTS: Dict[str, Coordinate] = {
  "D1": Coordinate(0.0, 0.0, 0.0),
  "D2": Coordinate(164.0, 0.0, 0.0),
  "D3": Coordinate(328.0, 0.0, 0.0),
  "C1": Coordinate(0.0, 107.0, 0.0),
  "C2": Coordinate(164.0, 107.0, 0.0),
  "C3": Coordinate(328.0, 107.0, 0.0),
  "B1": Coordinate(0.0, 214.0, 0.0),
  "B2": Coordinate(164.0, 214.0, 0.0),
  "B3": Coordinate(328.0, 214.0, 0.0),
  "A1": Coordinate(0.0, 321.0, 0.0),
  "A2": Coordinate(164.0, 321.0, 0.0),
  "A3": Coordinate(328.0, 321.0, 0.0),
}

# Staging-area column (column 4) sits to the right of column 3, offset in +x.
_STAGING_DX = 164.0
FLEX_STAGING: Dict[str, Coordinate] = {
  f"{row}4": Coordinate(FLEX_CUTOUTS[f"{row}3"].x + _STAGING_DX, FLEX_CUTOUTS[f"{row}3"].y, 0.0)
  for row in "ABCD"
}

FLEX_SLOTS: List[str] = list(FLEX_CUTOUTS.keys())


class FlexDeck(Deck):
  """The Opentrons Flex deck (12 addressable slots A1-D3 + optional staging column A4-D4).

  PLR has no Flex support upstream; this is a local addition so the Flex can be driven through the
  same LiquidHandler abstraction as every other backend. Slots are addressed by the Flex's own
  labels ("A1".."D3"), not OT-2 integer slots.
  """

  def __init__(
    self,
    size_x: float = 854.0,
    size_y: float = 471.0,
    size_z: float = 900.0,
    origin: Coordinate = Coordinate(0, 0, 0),
    with_staging: bool = False,
    name: str = "deck",
  ):
    super().__init__(size_x=size_x, size_y=size_y, size_z=size_z, origin=origin, name=name)
    self.slot_locations: Dict[str, Coordinate] = dict(FLEX_CUTOUTS)
    if with_staging:
      self.slot_locations.update(FLEX_STAGING)

  def assign_child_at_slot(self, resource: Resource, slot: str):
    """Assign a resource to a named Flex slot (e.g. "D1")."""
    if slot not in self.slot_locations:
      raise ValueError(f"slot must be one of {sorted(self.slot_locations)}, got {slot!r}")
    super().assign_child_resource(resource, location=self.slot_locations[slot])

  def slot_for(self, resource: Resource) -> Optional[str]:
    """Return the Flex slot label a resource sits on, or None."""
    for slot, loc in self.slot_locations.items():
      if resource.location == loc:
        return slot
    return None
