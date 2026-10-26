"""Build LiquidHandler instances with a deck layout.

MVP: a fixed demo deck (one tip rack + one 96-well plate on a STARLet deck) built
programmatically, so the engine runs end-to-end with no hardware and no config file. Each call
to `make_lh` returns a brand-new independent deck tree, which is exactly what the two-instance
design needs (dry-run LH and real LH must not share objects).

Later (config-driven): replace `make_lh` with a loader that deserializes a backend-matched deck
from JSON and `load_all_state` for starting tip inventory / well volumes. Resource NAMES must
stay identical across the two decks so the resolver can re-resolve by name at replay.
"""

from typing import Any

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  TIP_CAR_480_A00,
  Cor_96_wellplate_360ul_Fb,
  hamilton_96_tiprack_1000uL_filter,
)
from pylabrobot.resources.hamilton import STARLetDeck

# Stable resource names. The resolver bridges the two decks by these names, so they MUST match
# between the dry-run deck and the real deck.
TIPRACK_NAME = "tips_01"
PLATE_NAME = "plate_01"


def make_lh(backend: Any) -> LiquidHandler:
  """Return a LiquidHandler on a fresh STARLet deck for the given backend (not yet set up)."""
  return LiquidHandler(backend=backend, deck=STARLetDeck())


def populate(lh: LiquidHandler) -> None:
  """Assign the demo labware (tip rack + plate) onto the LiquidHandler's deck.

  Call AFTER ``await lh.setup()`` to mirror the PyLabRobot tutorial order.
  """
  tip_car = TIP_CAR_480_A00(name="tip carrier")
  tip_car[0] = hamilton_96_tiprack_1000uL_filter(name=TIPRACK_NAME, with_tips=True)
  lh.deck.assign_child_resource(tip_car, rails=3)

  plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
  plt_car[0] = Cor_96_wellplate_360ul_Fb(name=PLATE_NAME)
  lh.deck.assign_child_resource(plt_car, rails=15)
