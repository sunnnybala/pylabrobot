"""Offline tests for the local Opentrons Flex backend — a fake robot, no hardware, no httpx."""

import unittest

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.opentrons_flex_backend import (
  FLEX_PIPETTES,
  OpentronsFlexBackend,
  OpentronsFlexError,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.opentrons import FlexDeck
from pylabrobot.resources.opentrons.tip_racks import opentrons_96_tiprack_1000ul
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb


class FakeFlex:
  """A fake Flex robot-server: records commands, returns plausible ids/status."""

  def __init__(self):
    self.calls = []          # (method, path, json)
    self.commands = []       # commandType strings, in order
    self._n = 0

  def request(self, method, path, *, json=None, files=None):
    self.calls.append((method, path, json))
    if path == "/runs":
      return {"data": {"id": "run-1"}}
    if path.endswith("/actions"):
      return {"data": {"id": "act-1"}}
    if "/commands" in path:
      ct = json["data"]["commandType"]
      self.commands.append(ct)
      self._n += 1
      result = {}
      if ct == "loadPipette":
        result = {"pipetteId": f"pip-{json['data']['params']['mount']}"}
      elif ct == "loadLabware":
        result = {"labwareId": f"lw-{self._n}"}
      return {"data": {"id": f"cmd-{self._n}", "status": "succeeded", "result": result}}
    return {"data": {"id": "x"}}


def _deck():
  deck = FlexDeck()
  tips = opentrons_96_tiprack_1000ul(name="tips")
  plate = Cor_96_wellplate_360ul_Fb(name="plate")
  deck.assign_child_at_slot(tips, "C1")
  deck.assign_child_at_slot(plate, "D1")
  return deck, tips, plate


class FlexBackendTests(unittest.IsolatedAsyncioTestCase):
  async def test_requires_a_pipette(self):
    with self.assertRaises(ValueError):
      OpentronsFlexBackend("127.0.0.1")

  async def test_rejects_unknown_pipette(self):
    with self.assertRaises(ValueError):
      OpentronsFlexBackend("127.0.0.1", left_pipette="p1000_single_gen2")

  async def test_setup_creates_run_and_loads_pipette(self):
    fake = FakeFlex()
    be = OpentronsFlexBackend("127.0.0.1", left_pipette="flex_1channel_1000", request=fake.request)
    await be.setup()
    self.assertEqual(be._run_id, "run-1")
    self.assertEqual(be._pipette_ids["left"], "pip-left")
    self.assertIn("loadPipette", fake.commands)
    self.assertEqual(be.num_channels, 1)

  async def test_transfer_emits_expected_command_stream(self):
    fake = FakeFlex()
    deck, tips, plate = _deck()
    be = OpentronsFlexBackend("127.0.0.1", left_pipette="flex_1channel_1000", request=fake.request)
    lh = LiquidHandler(backend=be, deck=deck)
    await lh.setup()

    await lh.pick_up_tips(tips["A1"])
    await lh.aspirate(plate["A1"], vols=[100])
    await lh.dispense(plate["B1"], vols=[100])
    await lh.drop_tips(tips["A1"])

    # The Flex must receive a coherent, ordered command stream.
    self.assertEqual(
      [c for c in fake.commands if c in
       ("pickUpTip", "aspirate", "dispense", "dropTipInPlace")],
      ["pickUpTip", "aspirate", "dispense", "dropTipInPlace"],
    )
    # Labware is loaded JIT exactly once per resource.
    self.assertEqual(fake.commands.count("loadLabware"), 2)  # tip rack + plate

  async def test_stop_sends_stop_action(self):
    fake = FakeFlex()
    be = OpentronsFlexBackend("127.0.0.1", left_pipette="flex_1channel_1000", request=fake.request)
    await be.setup()
    await be.stop()
    self.assertTrue(any(p.endswith("/actions") for _, p, _ in fake.calls))
    self.assertIsNone(be._run_id)

  async def test_command_failure_raises(self):
    fake = FakeFlex()

    def failing(method, path, *, json=None, files=None):
      if "/commands" in path and json["data"]["commandType"] == "aspirate":
        return {"data": {"id": "c", "status": "failed", "error": {"detail": "tip not attached"}}}
      return fake.request(method, path, json=json, files=files)

    deck, tips, plate = _deck()
    be = OpentronsFlexBackend("127.0.0.1", left_pipette="flex_1channel_1000", request=failing)
    lh = LiquidHandler(backend=be, deck=deck)
    await lh.setup()
    await lh.pick_up_tips(tips["A1"])  # front-end requires a tip before aspirate
    with self.assertRaises(OpentronsFlexError):
      await lh.aspirate(plate["A1"], vols=[100])

  async def test_mount_selection_picks_smallest_fitting(self):
    be = OpentronsFlexBackend(
      "127.0.0.1", left_pipette="flex_1channel_50", right_pipette="flex_1channel_1000",
      request=FakeFlex().request,
    )
    self.assertEqual(be._mount_for(30), "left")    # 50 µL fits
    self.assertEqual(be._mount_for(500), "right")  # only 1000 µL fits
    self.assertEqual(be.num_channels, 2)


class FlexDeckTests(unittest.TestCase):
  def test_twelve_slots_and_geometry(self):
    deck = FlexDeck()
    self.assertEqual(len(deck.slot_locations), 12)
    self.assertEqual(deck.slot_locations["D1"], Coordinate(0.0, 0.0, 0.0))
    self.assertEqual(deck.slot_locations["A3"], Coordinate(328.0, 321.0, 0.0))

  def test_staging_column_optional(self):
    self.assertEqual(len(FlexDeck(with_staging=True).slot_locations), 16)

  def test_bad_slot_rejected(self):
    deck = FlexDeck()
    tips = opentrons_96_tiprack_1000ul(name="tips")
    with self.assertRaises(ValueError):
      deck.assign_child_at_slot(tips, "E9")

  def test_slot_for_roundtrip(self):
    deck = FlexDeck()
    plate = Cor_96_wellplate_360ul_Fb(name="plate")
    deck.assign_child_at_slot(plate, "B2")
    self.assertEqual(deck.slot_for(plate), "B2")


if __name__ == "__main__":
  unittest.main()
