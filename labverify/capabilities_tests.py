"""Tests for the per-backend capability table."""

import pytest

from labverify.capabilities import (
  ALL_VERBS,
  UnknownBackendError,
  is_supported,
  supported_verbs,
)


# Stand-ins named exactly like the real backend classes, so we exercise the name-based
# lookup without importing hardware-dependent modules.
class NimbusBackend:
  pass


class LiquidHandlerChatterboxBackend:
  pass


class STARBackend:  # not in the table on purpose
  pass


class _MyNimbus(NimbusBackend):  # subclass should inherit via MRO
  pass


def test_nimbus_is_single_channel_only():
  verbs = supported_verbs(NimbusBackend())
  assert "aspirate" in verbs
  assert "dispense" in verbs
  # 96-head and resource moves are NOT supported on Nimbus.
  assert "aspirate96" not in verbs
  assert "move_plate" not in verbs
  assert "pick_up_resource" not in verbs


def test_chatterbox_supports_everything():
  assert supported_verbs(LiquidHandlerChatterboxBackend()) == set(ALL_VERBS)


def test_subclass_inherits_capability():
  verbs = supported_verbs(_MyNimbus())
  assert "aspirate" in verbs
  assert "aspirate96" not in verbs


def test_unknown_backend_refuses_to_guess():
  with pytest.raises(UnknownBackendError):
    supported_verbs(STARBackend())


def test_is_supported_helper():
  assert is_supported(NimbusBackend(), "aspirate") is True
  assert is_supported(NimbusBackend(), "aspirate96") is False
