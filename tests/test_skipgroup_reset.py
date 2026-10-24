"""Regression tests: a group reset must also clear its skipped-group children.

`AdaptiveLightingManager.reset()` only cleared the entities it was handed.
Every caller that resets a skipped group (Lightener) passes the *group*
entity only, so the per-child manual-control flags of that group's children
survived the reset. The group then kept being adapted child-by-child instead
of through the single group command.

These tests use the same topology as `test_skipgroup_child_manual.py`:
  * light.light_1/2/3   : template lights (stand-ins for the cync bulbs)
  * light.lightener_all : template light with `adaptive_lighting_skip_group`
                          -> children are mapped to it in
                          `manager._skipped_group_children`.

Nothing here talks to the production Home Assistant on magsubun2.
"""

from __future__ import annotations

import pytest
from homeassistant.core import Context, HomeAssistant

from .test_skipgroup_child_manual import (
    C1,
    C2,
    CHILDREN,
    DOMAIN,
    GROUP,
    _attrs,
    _calls_recorder,
    _interval,
    _mc,
    _ours_targets,
    _setup,
)


async def _override_child(
    hass: HomeAssistant, child: str, brightness: int = 40
) -> None:
    """Simulate a user dimming one bulb of the group (external context)."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": child, "brightness": brightness},
        blocking=True,
        context=Context(),
    )
    await hass.async_block_till_done()


async def test_r1_reset_group_clears_child_flags(hass: HomeAssistant) -> None:
    """`manager.reset(GROUP)` must clear the flags of the group's children."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)
    await _override_child(hass, C2, brightness=60)
    assert "BRIGHTNESS" in _mc(manager, C1)
    assert "BRIGHTNESS" in _mc(manager, C2)

    manager.reset(GROUP)

    for child in CHILDREN:
        assert "BRIGHTNESS" not in _mc(
            manager, child
        ), f"reset({GROUP}) left the child override of {child} in place"
        assert "COLOR" not in _mc(manager, child)
    assert not _attrs(hass, sw)[
        "manual_control"
    ], "group reset did not clear the reported manual-control state"


async def test_r2_reset_group_leaves_unrelated_lights_alone(
    hass: HomeAssistant,
) -> None:
    """Only the children of *that* group may be cleared by a group reset."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)

    # A light that is not a child of this group.
    other = "light.not_in_group"
    manager.manual_control[other] = manager.get_manual_control_attributes(C1)
    assert "BRIGHTNESS" in _mc(manager, other)

    manager.reset(GROUP)

    assert "BRIGHTNESS" in _mc(
        manager, other
    ), "group reset cleared the manual state of an unrelated light"
    assert "BRIGHTNESS" not in _mc(manager, C1)


async def test_r3_reset_child_does_not_touch_group_or_siblings(
    hass: HomeAssistant,
) -> None:
    """Resetting a single child must not clear the group or its siblings."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)
    await _override_child(hass, C2, brightness=60)
    manager.manual_control[GROUP] = manager.get_manual_control_attributes(C1)

    manager.reset(C1)

    assert "BRIGHTNESS" not in _mc(manager, C1)
    assert "BRIGHTNESS" in _mc(manager, C2), "sibling override was cleared"
    assert "BRIGHTNESS" in _mc(manager, GROUP), "group override was cleared"


async def test_r4_reset_group_without_manual_control_keeps_flags(
    hass: HomeAssistant,
) -> None:
    """`reset_manual_control=False` must not clear any manual-control flag."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)

    manager.reset(GROUP, reset_manual_control=False)

    assert "BRIGHTNESS" in _mc(
        manager, C1
    ), "reset_manual_control=False cleared the child override"
    assert "BRIGHTNESS" in _mc(manager, C1)


async def test_r5_switch_toggle_off_clears_child_overrides(hass: HomeAssistant) -> None:
    """Turning the adaptive-lighting switch off must clear the child flags."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)
    assert "BRIGHTNESS" in _mc(manager, C1)

    await sw.async_turn_off()
    await hass.async_block_till_done()

    assert "BRIGHTNESS" not in _mc(
        manager, C1
    ), "switch turn_off left the child override in place"
    assert "BRIGHTNESS" not in _mc(manager, GROUP)


@pytest.mark.parametrize("reset_on_sleep", [True, False])
async def test_r6_sleep_mode_change_clears_child_overrides(
    hass: HomeAssistant,
    reset_on_sleep: bool,
) -> None:
    """The sleep-mode change honours `reset_manual_control_on_sleep_mode_change`."""
    sw, manager, _ = await _setup(
        hass,
        reset_manual_control_on_sleep_mode_change=reset_on_sleep,
    )
    await _override_child(hass, C1)
    assert "BRIGHTNESS" in _mc(manager, C1)

    sleep_entity = sw.sleep_mode_switch.entity_id
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": sleep_entity},
        blocking=True,
        context=Context(),
    )
    await hass.async_block_till_done()

    if reset_on_sleep:
        assert "BRIGHTNESS" not in _mc(
            manager, C1
        ), "sleep-mode change did not clear the child override"
    else:
        assert "BRIGHTNESS" in _mc(manager, C1), (
            "sleep-mode change cleared the child override although "
            "reset_manual_control_on_sleep_mode_change is False"
        )


async def test_r7_group_off_then_on_returns_to_single_group_command(
    hass: HomeAssistant,
) -> None:
    """Parent group OFF -> ON clears the child overrides and restores the group path."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)
    assert "BRIGHTNESS" in _mc(manager, C1)

    await hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": GROUP},
        blocking=True,
        context=Context(),
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": GROUP},
        blocking=True,
        context=Context(),
    )
    await hass.async_block_till_done()

    for child in CHILDREN:
        assert "BRIGHTNESS" not in _mc(
            manager, child
        ), f"group off->on left the child override of {child} in place"

    calls = _calls_recorder(hass)
    await _interval(hass, sw)
    ours = _ours_targets(calls)
    assert ours, "AL issued no command after the group came back on"
    assert all(
        c["entity_id"] == [GROUP] for c in ours
    ), "adaptation did not return to the single group command"


async def test_r8_apply_still_clears_group_and_children(hass: HomeAssistant) -> None:
    """Guard: the Apply service keeps clearing the group and its children."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)

    calls = _calls_recorder(hass)
    await hass.services.async_call(
        DOMAIN,
        "apply",
        {"entity_id": sw.entity_id, "lights": [GROUP], "turn_on_lights": True},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert not _attrs(hass, sw)["manual_control"], "Apply did not clear the overrides"
    ours = _ours_targets(calls)
    assert ours and all(
        c["entity_id"] == [GROUP] for c in ours
    ), "Apply must restore the group curves without duplicate child dispatch"


async def test_r9_badge_attribute_updates_after_group_reset(
    hass: HomeAssistant,
) -> None:
    """Guard: the `manual_control_friendly_names` badge is cleared by a group reset."""
    sw, manager, _ = await _setup(hass)
    await _override_child(hass, C1)
    badge = hass.states.get(sw.entity_id).attributes["manual_control_friendly_names"]
    assert badge == [hass.states.get(C1).attributes["friendly_name"]]

    manager.reset(GROUP)
    await hass.async_block_till_done()

    badge = hass.states.get(sw.entity_id).attributes["manual_control_friendly_names"]
    assert badge == [], "the manual-control badge was not cleared by the group reset"
