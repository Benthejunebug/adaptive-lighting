"""Forced adaptation must not be suppressed by `skip_redundant_commands`.

Regression test for the `filter_by_state` / `force` interaction in
`AdaptiveSwitch.prepare_adaptation_data`:

    filter_by_state=self._skip_redundant_commands,

With `skip_redundant_commands` enabled the cached light state is used to drop
adaptation attributes that are already satisfied.  A *forced* adaptation
(explicit `adaptive_lighting.apply`, switch turn-on, sleep-mode change) must
still reach the light even when the recorded state already matches, otherwise
an explicit user request silently does nothing.

The lightener-style skipped group from `test_skipgroup_child_manual` is used as
the target so the test also covers the group path.

Only the HA test core is touched; no live Home Assistant is involved.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from homeassistant.components.light import ATTR_BRIGHTNESS

from .test_skipgroup_child_manual import GROUP, _setup

TARGET_BRIGHTNESS_PCT = 50
TARGET_BRIGHTNESS = round(255 * TARGET_BRIGHTNESS_PCT / 100)  # 128


def _mock_sun_light_settings(switch) -> None:
    """Pin the adaptation settings so the requested brightness is deterministic."""
    sun_light_settings_mock = Mock()
    sun_light_settings_mock.get_settings = Mock(
        return_value={
            "brightness_pct": TARGET_BRIGHTNESS_PCT,
            "color_temp_kelvin": 3448,
            "rgb_color": (255, 147, 41),
            "force_rgb_color": False,
        },
    )
    switch._sun_light_settings = sun_light_settings_mock


async def _prepare(switch, *, force: bool):
    """Prepare adaptation data for the group and return the first command."""
    data = await switch.prepare_adaptation_data(
        GROUP,
        transition=0,
        adapt_brightness=True,
        adapt_color=False,
        force=force,
        context=switch.create_context("test"),
    )
    assert data is not None
    return await data.next_service_call_data()


async def test_unforced_adaptation_is_suppressed_by_skip_redundant(
    hass,
) -> None:
    """Without force, a matching cached state still suppresses the command."""
    sw, _manager, _ents = await _setup(hass, skip_redundant_commands=True)
    _mock_sun_light_settings(sw)

    # Learn the requested brightness without the state filter.
    sw._skip_redundant_commands = False
    command = await _prepare(sw, force=False)
    assert command is not None
    assert command[ATTR_BRIGHTNESS] == TARGET_BRIGHTNESS

    # Make the recorded state match the requested value.
    state = hass.states.get(GROUP)
    hass.states.async_set(
        GROUP,
        state.state,
        {**state.attributes, ATTR_BRIGHTNESS: TARGET_BRIGHTNESS},
    )
    sw._skip_redundant_commands = True

    assert await _prepare(sw, force=False) is None, (
        "unforced adaptation with a matching cached state must be suppressed"
    )


async def test_forced_adaptation_bypasses_skip_redundant(hass) -> None:
    """With force, the command must be emitted despite a matching cached state."""
    sw, _manager, _ents = await _setup(hass, skip_redundant_commands=True)
    _mock_sun_light_settings(sw)

    # Learn the requested brightness without the state filter.
    sw._skip_redundant_commands = False
    command = await _prepare(sw, force=False)
    assert command is not None
    assert command[ATTR_BRIGHTNESS] == TARGET_BRIGHTNESS

    # Make the recorded state match the requested value.
    state = hass.states.get(GROUP)
    hass.states.async_set(
        GROUP,
        state.state,
        {**state.attributes, ATTR_BRIGHTNESS: TARGET_BRIGHTNESS},
    )
    sw._skip_redundant_commands = True

    forced = await _prepare(sw, force=True)
    assert forced is not None, (
        "forced adaptation was suppressed by skip_redundant_commands"
    )
    assert forced[ATTR_BRIGHTNESS] == TARGET_BRIGHTNESS


@pytest.mark.parametrize("skip_redundant", [False, True])
async def test_forced_adaptation_always_emits(hass, skip_redundant: bool) -> None:
    """A forced adaptation emits its command regardless of the setting."""
    sw, _manager, _ents = await _setup(
        hass, skip_redundant_commands=skip_redundant,
    )
    _mock_sun_light_settings(sw)

    state = hass.states.get(GROUP)
    hass.states.async_set(
        GROUP,
        state.state,
        {**state.attributes, ATTR_BRIGHTNESS: TARGET_BRIGHTNESS},
    )

    forced = await _prepare(sw, force=True)
    assert forced is not None
    assert forced[ATTR_BRIGHTNESS] == TARGET_BRIGHTNESS
