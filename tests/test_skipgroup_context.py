"""Regression probe: context attribution for parent_id-carrying contexts.

Covers the gap where `is_our_context()` only inspected `context.id`, so a
context whose `parent_id` is an Adaptive Lighting context (as produced when
HA/Lightener fans a call out with a child context) was attributed to the
user and marked as a manual override.

Also covers `_off_to_on_state_event_is_from_turn_on()`, which compared only
`context.id` for strict equality and therefore failed to recognise an
"off -> on" state event that carries the tracked turn-on context as its
parent.

Nothing here talks to the production Home Assistant on magsubun2.
"""

from __future__ import annotations

from homeassistant.components.adaptive_lighting import const as al_const
from homeassistant.components.adaptive_lighting import switch as al_switch
from homeassistant.core import Context, HomeAssistant

from .test_skipgroup_child_manual import (
    C1,
    C2,
    CHILDREN,
    GROUP,
    _attrs,
    _mc,
    _setup,
)


def _al_context(sw) -> Context:
    """Create a context that Adaptive Lighting itself owns."""
    return sw.create_context("probe")


async def test_a_al_parent_context_is_not_manual(hass: HomeAssistant) -> None:
    """A turn-on whose context has an AL parent_id is ours, not a user override."""
    sw, manager, _ = await _setup(hass)
    al_ctx = _al_context(sw)
    assert al_switch.is_our_context(al_ctx), "probe context is not recognised as AL's"

    # A child context (id is random, parent is the AL context) - this is what
    # HA/Lightener produce when a call is fanned out while AL is adapting.
    child_ctx = Context(parent_id=al_ctx.id)
    assert not al_switch.is_our_context_id(child_ctx.id), "child id must not look like AL's"

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": C1, "brightness": 40},
        blocking=True,
        context=child_ctx,
    )
    await hass.async_block_till_done()

    assert "BRIGHTNESS" not in _mc(manager, C1), (
        "context with an AL parent_id was misread as a user override"
    )
    assert C1 not in (_attrs(hass, sw)["manual_control"] or [])


async def test_b_unrelated_user_context_still_manual(hass: HomeAssistant) -> None:
    """An unrelated user context must still be detected as a manual override."""
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": C1, "brightness": 40},
        blocking=True,
        context=Context(),
    )
    await hass.async_block_till_done()

    assert "BRIGHTNESS" in _mc(manager, C1), "user override NOT detected"
    assert C1 in (_attrs(hass, sw)["manual_control"] or [])


async def test_c_off_to_on_with_parent_of_tracked_turn_on(hass: HomeAssistant) -> None:
    """An "off -> on" event carrying the tracked turn-on as parent is ours."""
    _, manager, _ = await _setup(hass)
    turn_on_ctx = Context()
    manager.turn_on_event[C1] = _fake_event(turn_on_ctx)

    # (1) strict equality still counts.
    same = _fake_event(Context(id=turn_on_ctx.id))
    assert manager._off_to_on_state_event_is_from_turn_on(C1, same) is True

    # (2) the tracked turn-on context is the parent of the state event.
    child_of_turn_on = _fake_event(Context(id="child-ctx", parent_id=turn_on_ctx.id))
    assert manager._off_to_on_state_event_is_from_turn_on(C1, child_of_turn_on) is True, (
        "off -> on event whose parent matches the tracked turn_on was not "
        "recognised as coming from that turn_on"
    )

    # (3) the state event id is the parent of the tracked turn-on.
    turn_on_child = _fake_event(Context(id="parent-ctx"))
    manager.turn_on_event[C1] = _fake_event(Context(id="child-of-parent", parent_id="parent-ctx"))
    assert manager._off_to_on_state_event_is_from_turn_on(C1, turn_on_child) is True

    # (4) an unrelated event is not from the tracked turn_on.
    unrelated = _fake_event(Context(id="unrelated"))
    assert manager._off_to_on_state_event_is_from_turn_on(C1, unrelated) is False


def _fake_event(context: Context):
    """Build a minimal stand-in event carrying only a context."""
    from homeassistant.core import Event

    return Event("state_changed", {"entity_id": C1, "new_state": None, "old_state": None}, context=context)


async def test_d_al_parent_context_on_group_child_not_manual(hass: HomeAssistant) -> None:
    """Same as (a) but for a child of a skipped (Lightener) group."""
    sw, manager, _ = await _setup(hass)
    al_ctx = _al_context(sw)
    child_ctx = Context(parent_id=al_ctx.id)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": C2, "color_temp_kelvin": 6000},
        blocking=True,
        context=child_ctx,
    )
    await hass.async_block_till_done()

    assert "COLOR" not in _mc(manager, C2), (
        "context with an AL parent_id was misread as a user override on a "
        "skipped-group child"
    )
    assert C2 not in (_attrs(hass, sw)["manual_control"] or [])


async def test_e_group_fanout_child_context_not_manual(hass: HomeAssistant) -> None:
    """Lightener fan-out that reuses the group context (same id) is not manual."""
    _, manager, _ = await _setup(hass)
    ctx = Context()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": GROUP, "brightness": 100},
        blocking=True,
        context=ctx,
    )
    for c in CHILDREN:
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": c, "brightness": 100},
            blocking=True,
            context=ctx,
        )
    await hass.async_block_till_done()
    assert all("BRIGHTNESS" not in _mc(manager, c) for c in CHILDREN)


async def test_f_user_context_with_unrelated_parent_still_manual(hass: HomeAssistant) -> None:
    """A user context with a non-AL parent is still a user override."""
    _, manager, _ = await _setup(hass)
    user_parent = Context()
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": C1, "brightness": 40},
        blocking=True,
        context=Context(parent_id=user_parent.id),
    )
    await hass.async_block_till_done()
    assert "BRIGHTNESS" in _mc(manager, C1), "user override NOT detected"


async def test_g_al_context_helpers_unit(hass: HomeAssistant) -> None:
    """Unit-level checks of the two context helpers."""
    al_ctx = Context(id="20260101:al:hash:probe:0000")
    assert al_switch.is_our_context(al_ctx) is True
    assert al_switch.is_our_context(Context(parent_id=al_ctx.id)) is True
    assert al_switch.is_our_context(Context(id="random", parent_id=al_ctx.id), "skipped") is False
    assert al_switch.is_our_context(Context(id="random")) is False
    assert al_switch.is_our_context(None) is False
    assert al_const.DOMAIN == "adaptive_lighting"
