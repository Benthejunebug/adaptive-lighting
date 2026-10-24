"""Isolated regression probe: per-child manual control under a skipped (Lightener) group.

Runs the REAL Adaptive Lighting integration code (whichever tree is first on
PYTHONPATH as `custom_components/adaptive_lighting`) inside the
pytest-homeassistant-custom-component HA test core. Nothing here talks to the
production Home Assistant on magsubun2.

Topology mirrors production (`Adapt Lighteneer` -> light.lightener_all):
  * light.light_1/2/3   : real template light entities (stand-ins for cync bulbs)
  * light.lightener_all : real template light entity, carrying
        adaptive_lighting_skip_group=True + is_hue_group=True and NO `entity_id`
        attribute, registry-linked to a `lightener` config entry whose
        data.entities lists the three children (same discovery path as prod).
  * The group does NOT fan out to children (no Lightener runtime); assertions
    are on which light.turn_on/turn_off calls AL itself issues.
  * A fake `_entities` list with translate_brightness() is attached to the group
    entity object, imitating LightenerLight._entities, so a repair can reuse the
    group's per-child curves.

Option set mirrors the live entry 01JVX4T4GSFC85MAGY095MMDHY options read from
.storage on 2026-10-23 (intercept, multi_light_intercept, take_over_control,
adapt_only_on_bare_turn_on, detect_non_ha_changes=True, autoreset 3600,
separate_turn_on_commands=False, skip_redundant_commands=False).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.setup import async_setup_component
from tests.common import MockConfigEntry

from homeassistant.components.adaptive_lighting import const as al_const
from homeassistant.components.adaptive_lighting import switch as al_switch

DOMAIN = al_const.DOMAIN
GROUP = "light.lightener_all"
C1, C2, C3 = "light.light_1", "light.light_2", "light.light_3"
CHILDREN = [C1, C2, C3]
OUT = os.environ.get("PROBE_OUT")

# Fake Lightener curves (group brightness -> child brightness).
CURVES = {
    C1: lambda b: b,
    C2: lambda b: max(1, b // 2),
    C3: lambda b: min(255, b * 2),
}


class _FakeControlled:
    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id

    def translate_brightness(self, brightness: int) -> int:
        return CURVES[self.entity_id](int(brightness))


def _record(name: str, data: dict[str, Any]) -> None:
    print(f"\nPROBE {name}: {json.dumps(data, sort_keys=True, default=str)}")
    if OUT:
        with open(OUT, "a") as fh:
            fh.write(json.dumps({"probe": name, **data}, default=str) + "\n")


async def _setup(hass: HomeAssistant, **extra_options):
    lights_cfg = [
        {
            "name": name,
            "unique_id": name,
            "turn_on": None,
            "turn_off": None,
            "set_level": None,
            "set_temperature": None,
            "set_hs": None,
        }
        for name in ("light_1", "light_2", "light_3", "lightener_all")
    ]
    assert await async_setup_component(
        hass, "template", {"template": {"light": lights_cfg}}
    )
    await hass.async_block_till_done()
    ents = {e.entity_id: e for e in async_get_platforms(hass, "template")[0].entities.values()}
    for eid, ent in ents.items():
        await ent.async_turn_on()
        ent._attr_brightness = 255
        if hasattr(ent, "_brightness"):
            ent._brightness = 255
        ent._attr_color_temp_kelvin = 4000
        ent.async_write_ha_state()

    # Lightener-like group: skip flag, no `entity_id` attr, config-entry members.
    lightener_entry = MockConfigEntry(
        domain="lightener",
        title="Lightener All",
        data={"entities": {c: {"brightness": {}} for c in CHILDREN}},
    )
    lightener_entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_update_entity(GROUP, config_entry_id=lightener_entry.entry_id)
    group = ents[GROUP]
    group._attr_extra_state_attributes = {
        "adaptive_lighting_skip_group": True,
        "is_hue_group": True,
    }
    group._entities = [_FakeControlled(c) for c in CHILDREN]
    group.async_write_ha_state()
    st = hass.states.get(GROUP)
    assert st.attributes.get("adaptive_lighting_skip_group") is True
    assert "entity_id" not in st.attributes

    options = {
        "name": "Adapt Lighteneer",
        "lights": [GROUP],
        "intercept": True,
        "multi_light_intercept": True,
        "take_over_control": True,
        "adapt_only_on_bare_turn_on": True,
        "detect_non_ha_changes": True,
        "autoreset_control_seconds": 3600,
        "separate_turn_on_commands": False,
        "skip_redundant_commands": False,
        "only_once": False,
        "interval": 90,
        "initial_transition": 0,
        "transition": 0,
    }
    if hasattr(al_const, "CONF_EXPAND_LIGHT_GROUPS"):
        options["expand_light_groups"] = True
    options["take_over_control_mode"] = "pause_all"  # live value
    options.update(extra_options)
    entry = MockConfigEntry(domain=DOMAIN, data=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sw = hass.data[DOMAIN][entry.entry_id]["switch"]
    return sw, sw.manager, ents


def _calls_recorder(hass: HomeAssistant) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _listener(event) -> None:
        if event.data.get("domain") != "light":
            return
        sd = dict(event.data.get("service_data") or {})
        eids = sd.pop("entity_id", None)
        if isinstance(eids, str):
            eids = [eids]
        calls.append(
            {
                "service": event.data.get("service"),
                "entity_id": sorted(eids or []),
                "keys": sorted(sd),
                "brightness": sd.get("brightness"),
                "ours": al_switch.is_our_context(event.context),
            }
        )

    hass.bus.async_listen(EVENT_CALL_SERVICE, _listener)
    return calls


def _attrs(hass: HomeAssistant, sw) -> dict[str, Any]:
    a = hass.states.get(sw.entity_id).attributes
    return {
        k: a.get(k)
        for k in (
            "manual_control",
            "manual_control_brightness",
            "manual_control_color",
            "autoreset_time_remaining",
        )
    }


def _mc(manager, light) -> str:
    return str(manager.manual_control.get(light, al_const.LightControlAttributes.NONE)) if hasattr(al_const, "LightControlAttributes") else str(manager.manual_control.get(light))


async def _interval(hass, sw) -> None:
    await sw._update_attrs_and_maybe_adapt_lights(
        context=sw.create_context("interval"), transition=0, force=False
    )
    await hass.async_block_till_done()


def _ours_targets(calls):
    return [c for c in calls if c["ours"]]


async def test_p1_mapping_and_owner_lookup(hass: HomeAssistant) -> None:
    sw, manager, _ = await _setup(hass)
    owners = {
        c: [s.entity_id for s in al_switch._switches_with_lights(hass, [c], expand_light_groups=False)]
        for c in CHILDREN
    }
    _record(
        "P1_mapping",
        {
            "switch.lights": sw.lights,
            "skipped_group_children": manager._skipped_group_children,
            "children_in_manager.lights": all(c in manager.lights for c in CHILDREN),
            "owner_switches_for_child": owners,
        },
    )
    assert manager._skipped_group_children == {c: GROUP for c in CHILDREN}
    assert all(owners[c] == [sw.entity_id] for c in CHILDREN), "child has no owning switch"


async def test_p2_child_brightness_manual_detected_and_reported(hass: HomeAssistant) -> None:
    sw, manager, _ = await _setup(hass)
    calls = _calls_recorder(hass)
    # Dashboard-style per-bulb dim on an already-on child (external context).
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": C1, "brightness": 40}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()
    attrs = _attrs(hass, sw)
    _record(
        "P2_child_brightness",
        {
            "manager.manual_control[C1]": _mc(manager, C1),
            "manager.manual_control[GROUP]": _mc(manager, GROUP),
            "switch_attrs": attrs,
            "light_calls": calls,
        },
    )
    assert "BRIGHTNESS" in _mc(manager, C1), "child brightness override NOT detected"
    assert C1 in attrs["manual_control"], "child override NOT reported in manual_control"
    badge_names = hass.states.get(sw.entity_id).attributes["manual_control_friendly_names"]
    assert badge_names == [hass.states.get(C1).attributes["friendly_name"]]
    assert C1 in attrs["manual_control_brightness"]
    assert C1 not in (attrs["manual_control_color"] or [])
    assert GROUP not in attrs["manual_control"], "group wrongly marked manual"


async def test_p3_child_color_manual_split(hass: HomeAssistant) -> None:
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": C2, "color_temp_kelvin": 6000}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()
    attrs = _attrs(hass, sw)
    _record(
        "P3_child_color",
        {"manager.manual_control[C2]": _mc(manager, C2), "switch_attrs": attrs},
    )
    assert "COLOR" in _mc(manager, C2) and "BRIGHTNESS" not in _mc(manager, C2)
    assert C2 in attrs["manual_control_color"]
    assert C2 not in attrs["manual_control_brightness"]


@pytest.mark.parametrize("mode", ["pause_all", "pause_changed"])
async def test_p4_interval_preserves_override_and_adapts_siblings(hass: HomeAssistant, mode: str) -> None:
    sw, manager, _ = await _setup(hass, take_over_control_mode=mode)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": C1, "brightness": 40}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()
    calls = _calls_recorder(hass)
    await _interval(hass, sw)
    ours = _ours_targets(calls)
    c1_brightness = hass.states.get(C1).attributes.get("brightness")
    _record(
        f"P4_interval_after_child_brightness_override[{mode}]",
        {
            "manual[C1]": _mc(manager, C1),
            "al_issued_calls": ours,
            "C1_brightness_after": c1_brightness,
        },
    )
    group_cmds = [c for c in ours if GROUP in c["entity_id"]]
    assert not group_cmds, (
        "AL commanded the Lightener group while a child has a brightness override; "
        "Lightener fans brightness out to every child, clobbering the override"
    )
    assert not [c for c in ours if C1 in c["entity_id"] and "brightness" in c["keys"]]
    group_b = round(255 * sw._settings["brightness_pct"] / 100)
    for sib in (C2, C3):
        sib_b = [c["brightness"] for c in ours if sib in c["entity_id"] and c["brightness"] is not None]
        assert sib_b, f"sibling {sib} brightness not adapted"
        # Brightness must go through the group's (Lightener) curve, not AL's raw value.
        assert sib_b[-1] == CURVES[sib](group_b), (
            f"{sib} got raw/unexpected brightness {sib_b[-1]}, curve wants {CURVES[sib](group_b)} (group {group_b})"
        )
    c1_cmds = [c for c in ours if C1 in c["entity_id"]]
    if mode == "pause_all":
        assert not c1_cmds, "pause_all: overridden child must not be adapted at all"
    else:
        assert c1_cmds and all("color_temp_kelvin" in c["keys"] and "brightness" not in c["keys"] for c in c1_cmds), (
            "pause_changed: overridden child's non-overridden axis (color) must still adapt, brightness must not"
        )


async def test_p5_child_external_off_not_relit(hass: HomeAssistant) -> None:
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": C3}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()
    calls = _calls_recorder(hass)
    await _interval(hass, sw)
    ours = _ours_targets(calls)
    attrs = _attrs(hass, sw)
    _record(
        "P5_child_off_then_interval",
        {"manual[C3]": _mc(manager, C3), "switch_attrs": attrs, "al_issued_calls": ours},
    )
    assert not [c for c in ours if GROUP in c["entity_id"] and c["service"] == "turn_on"], (
        "AL commanded the group on while a child was manually switched off; "
        "Lightener would turn that child back on"
    )
    assert not [c for c in ours if C3 in c["entity_id"] and c["service"] == "turn_on"]


async def test_p6_no_override_uses_group_path(hass: HomeAssistant) -> None:
    """Guard: with no child override, adaptation must stay a single group command."""
    sw, manager, _ = await _setup(hass)
    calls = _calls_recorder(hass)
    await _interval(hass, sw)
    ours = _ours_targets(calls)
    _record("P6_interval_no_override", {"al_issued_calls": ours})
    assert ours and all(c["entity_id"] == [GROUP] for c in ours)


async def test_p7_group_level_call_not_child_manual(hass: HomeAssistant) -> None:
    """Guard: a Lightener fan-out (same context as the group call) must not mark children."""
    sw, manager, _ = await _setup(hass)
    ctx = Context()
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": GROUP, "brightness": 100}, blocking=True, context=ctx
    )
    # Simulate Lightener's fan-out: child calls reuse the group's context.
    for c in CHILDREN:
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": c, "brightness": CURVES[c](100)}, blocking=True, context=ctx
        )
    await hass.async_block_till_done()
    attrs = _attrs(hass, sw)
    _record(
        "P7_group_call_fanout",
        {"manual": {e: _mc(manager, e) for e in [GROUP, *CHILDREN]}, "switch_attrs": attrs},
    )
    assert all("BRIGHTNESS" not in _mc(manager, c) for c in CHILDREN), (
        "group-level fan-out misread as per-child overrides"
    )


async def test_p8_child_back_on_bare_rejoins_group_path(hass: HomeAssistant) -> None:
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call("light", "turn_off", {"entity_id": C3}, blocking=True, context=Context())
    await hass.async_block_till_done()
    marked_after_off = _mc(manager, C3)
    await hass.services.async_call("light", "turn_on", {"entity_id": C3}, blocking=True, context=Context())
    await hass.async_block_till_done()
    calls = _calls_recorder(hass)
    await _interval(hass, sw)
    ours = _ours_targets(calls)
    _record("P8_child_off_then_bare_on", {"manual[C3]_after_off": marked_after_off,
            "manual[C3]_after_bare_on": _mc(manager, C3), "al_issued_calls": ours})
    assert "BRIGHTNESS" in marked_after_off, "external child off not recorded as override"
    assert "BRIGHTNESS" not in _mc(manager, C3) and "COLOR" not in _mc(manager, C3)
    assert ours and all(c["entity_id"] == [GROUP] for c in ours), "did not return to single group command"


@pytest.mark.parametrize("turn_on_lights", [False, True])
async def test_apply_clears_group_and_child_overrides(hass: HomeAssistant, turn_on_lights: bool) -> None:
    sw, manager, _ = await _setup(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": C1, "brightness": 120}, blocking=True, context=Context()
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": GROUP, "brightness": 140}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()
    assert manager.manual_control.get(C1)
    calls = _calls_recorder(hass)
    await hass.services.async_call(
        DOMAIN, "apply", {"entity_id": sw.entity_id, "lights": [GROUP], "turn_on_lights": turn_on_lights}, blocking=True
    )
    await hass.async_block_till_done()
    assert not _attrs(hass, sw)["manual_control"], "Apply did not clear manual overrides"
    ours = _ours_targets(calls)
    assert ours and all(c["entity_id"] == [GROUP] for c in ours), "Apply must restore group curves without duplicate child dispatch"
