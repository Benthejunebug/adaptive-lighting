"""Explicit child resync and immediate event regression coverage."""
from unittest.mock import AsyncMock, patch

from homeassistant.core import Context

from .test_skipgroup_child_manual import (
    C1, C2, CHILDREN, CURVES, DOMAIN, GROUP, _setup, _calls_recorder, _ours_targets,
)


async def test_child_apply_resets_only_child_and_preserves_curve(hass):
    sw, manager, _ = await _setup(hass)
    for child in (C1, C2):
        await hass.services.async_call("light", "turn_on", {"entity_id": child, "brightness": 111}, blocking=True, context=Context())
    await hass.async_block_till_done()
    assert manager.manual_control.get(C1)
    assert manager.manual_control.get(C2)
    calls = _calls_recorder(hass)
    await hass.services.async_call(DOMAIN, "apply", {"entity_id": sw.entity_id, "lights": [C1]}, blocking=True)
    await hass.async_block_till_done()
    assert not manager.manual_control.get(C1)
    assert manager.manual_control.get(C2)
    ours = _ours_targets(calls)
    assert ours and all(c["entity_id"] == [C1] for c in ours)
    expected = CURVES[C1](round(255 * sw._settings["brightness_pct"] / 100))
    assert any(c["brightness"] == expected for c in ours)


async def test_child_apply_without_other_overrides_does_not_touch_siblings(hass):
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": C1, "brightness": 111}, blocking=True, context=Context())
    await hass.async_block_till_done()
    calls = _calls_recorder(hass)
    await hass.services.async_call(DOMAIN, "apply", {"entity_id": sw.entity_id, "lights": [C1]}, blocking=True)
    await hass.async_block_till_done()
    assert not manager.manual_control.get(C1)
    ours = _ours_targets(calls)
    assert ours and all(c["entity_id"] == [C1] for c in ours)


async def test_child_bare_turn_on_reaches_immediate_adaptation(hass):
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": C1}, blocking=True, context=Context())
    await hass.async_block_till_done()
    with patch.object(sw, "_update_attrs_and_maybe_adapt_lights", new=AsyncMock()) as update:
        await hass.services.async_call("light", "turn_on", {"entity_id": C1}, blocking=True, context=Context())
        await hass.async_block_till_done()
        assert any(c.kwargs.get("lights") == [C1] for c in update.await_args_list)
