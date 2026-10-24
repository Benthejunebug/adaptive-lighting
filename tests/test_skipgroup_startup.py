"""Regression probe: Lightener children not discovered at AL setup time.

Scenario (the real production startup race):

  * Home Assistant starts. Adaptive Lighting is set up BEFORE the Lightener
    integration has created its group entity, so `hass.states.get("light.lightener_all")`
    is None at AL setup time.
  * The entity registry already contains `light.lightener_all`, linked to the
    `lightener` config entry whose `data["entities"]` lists the children.
  * Later the Lightener group entity appears in the state machine.
  * A user then dims one of the children by hand (dashboard / physical).

Expected: AL notices the manual change on the child and reports it
(`manual_control`, `manual_control_friendly_names`) WITHOUT reloading AL.

Everything runs inside the pytest-homeassistant-custom-component HA test core.
Nothing here talks to the production Home Assistant on magsubun2.
"""

from __future__ import annotations

import json
import os
from typing import Any

from homeassistant.const import EVENT_CALL_SERVICE, STATE_ON
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


def _record(name: str, data: dict[str, Any]) -> None:
    print(f"\nPROBE {name}: {json.dumps(data, sort_keys=True, default=str)}")
    if OUT:
        with open(OUT, "a") as fh:
            fh.write(json.dumps({"probe": name, **data}, default=str) + "\n")


async def _setup_startup_gap(hass: HomeAssistant):
    """Set up AL while the Lightener group state does not exist yet."""
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
        for name in ("light_1", "light_2", "light_3")
    ]
    assert await async_setup_component(
        hass, "template", {"template": {"light": lights_cfg}}
    )
    await hass.async_block_till_done()
    ents = {
        e.entity_id: e
        for e in async_get_platforms(hass, "template")[0].entities.values()
    }
    for eid, ent in ents.items():
        await ent.async_turn_on()
        ent._attr_brightness = 255
        if hasattr(ent, "_brightness"):
            ent._brightness = 255
        ent._attr_color_temp_kelvin = 4000
        ent.async_write_ha_state()

    # The Lightener config entry exists, and the registry already links the
    # (not yet existing) group entity to it.
    lightener_entry = MockConfigEntry(
        domain="lightener",
        title="Lightener All",
        data={"entities": {c: {"brightness": {}} for c in CHILDREN}},
    )
    lightener_entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create(
        "light",
        "lightener",
        "lightener_all",
        config_entry=lightener_entry,
        suggested_object_id="lightener_all",
    )
    assert reg.async_get(GROUP) is not None, "registry entry for the group missing"

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
    options["take_over_control_mode"] = "pause_all"
    entry = MockConfigEntry(domain=DOMAIN, data=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sw = hass.data[DOMAIN][entry.entry_id]["switch"]
    return sw, sw.manager, ents


def _group_state_appears(hass: HomeAssistant) -> None:
    """Simulate the Lightener group entity showing up after AL setup."""
    hass.states.async_set(
        GROUP,
        STATE_ON,
        {
            "friendly_name": "Lightener All",
            "adaptive_lighting_skip_group": True,
            "is_hue_group": True,
        },
    )


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
    return str(
        manager.manual_control.get(light, al_switch.LightControlAttributes.NONE)
    )


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
                "brightness": sd.get("brightness"),
                "ours": al_switch.is_our_context(event.context),
            }
        )

    hass.bus.async_listen(EVENT_CALL_SERVICE, _listener)
    return calls


async def test_s1_startup_gap_children_discovered_after_state_appears(
    hass: HomeAssistant,
) -> None:
    """Children must be discovered once the group state appears."""
    sw, manager, _ = await _setup_startup_gap(hass)

    # At AL setup time the group state did not exist.
    assert hass.states.get(GROUP) is None
    _record(
        "S1_at_setup",
        {
            "group_state": hass.states.get(GROUP),
            "skipped_group_children": dict(manager._skipped_group_children),
            "manager_lights": sorted(manager.lights),
            "switch_lights": list(sw.lights),
        },
    )

    _group_state_appears(hass)
    await hass.async_block_till_done()

    # Force the same lookup AL performs on every service/state event.
    al_switch._switches_with_lights(hass, [GROUP], expand_light_groups=False)
    await hass.async_block_till_done()

    _record(
        "S1_after_state_appears",
        {
            "skipped_group_children": dict(manager._skipped_group_children),
            "manager_lights": sorted(manager.lights),
            "switch_lights": list(sw.lights),
        },
    )
    assert manager._skipped_group_children == {c: GROUP for c in CHILDREN}, (
        "Lightener children were never discovered after the group state appeared"
    )


async def test_s2_manual_child_change_detected_without_reload(
    hass: HomeAssistant,
) -> None:
    """A manual child dim must be reported, no AL reload."""
    sw, manager, _ = await _setup_startup_gap(hass)
    assert hass.states.get(GROUP) is None

    _group_state_appears(hass)
    await hass.async_block_till_done()

    calls = _calls_recorder(hass)
    # Dashboard-style per-bulb dim on an already-on child (external context).
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": C1, "brightness": 40}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()

    attrs = _attrs(hass, sw)
    _record(
        "S2_manual_child_change",
        {
            "skipped_group_children": dict(manager._skipped_group_children),
            "manager_lights": sorted(manager.lights),
            "manager.manual_control[C1]": _mc(manager, C1),
            "manager.manual_control[GROUP]": _mc(manager, GROUP),
            "switch_attrs": attrs,
            "friendly_names": hass.states.get(sw.entity_id).attributes.get(
                "manual_control_friendly_names"
            ),
            "light_calls": calls,
        },
    )

    assert "BRIGHTNESS" in _mc(manager, C1), "child brightness override NOT detected"
    assert C1 in (attrs["manual_control"] or []), "child override NOT reported"
    assert C1 in (attrs["manual_control_brightness"] or [])
    badge_names = hass.states.get(sw.entity_id).attributes["manual_control_friendly_names"]
    assert badge_names == [hass.states.get(C1).attributes["friendly_name"]]
    assert GROUP not in (attrs["manual_control"] or []), "group wrongly marked manual"


async def test_s3_group_stays_the_adaptation_target(hass: HomeAssistant) -> None:
    """The group, not its children, must remain AL's adaptation target."""
    sw, manager, _ = await _setup_startup_gap(hass)
    assert hass.states.get(GROUP) is None

    _group_state_appears(hass)
    await hass.async_block_till_done()

    assert sw.lights == [GROUP], "children leaked into the switch's adaptation targets"
    assert manager._skipped_group_children == {c: GROUP for c in CHILDREN}


async def test_s4_non_lightener_config_entry_is_not_guessed(
    hass: HomeAssistant,
) -> None:
    """A missing entity owned by another domain must not be expanded."""
    ent_reg = er.async_get(hass)
    other_entry = MockConfigEntry(
        domain="some_other_domain",
        title="Not Lightener",
        data={"entities": {C1: {"brightness": {}}}},
    )
    other_entry.add_to_hass(hass)
    ent_reg.async_get_or_create(
        "light",
        "some_other_domain",
        "not_lightener",
        config_entry=other_entry,
        suggested_object_id="not_lightener",
    )

    children = al_switch._lightener_children_from_registry(
        hass, "light.not_lightener"
    )
    _record("S4_non_lightener", {"children": children})
    assert children == [], "expanded a group owned by a non-lightener config entry"


async def test_s5_missing_entity_without_registry_entry(hass: HomeAssistant) -> None:
    """A completely unknown entity yields no children (no crash)."""
    children = al_switch._lightener_children_from_registry(hass, "light.does_not_exist")
    _record("S5_unknown_entity", {"children": children})
    assert children == []


async def test_s6_all_children_detected_independently(
    hass: HomeAssistant,
) -> None:
    """Every child, not just the first-touched one, must be tracked."""
    sw, manager, _ = await _setup_startup_gap(hass)
    _group_state_appears(hass)
    await hass.async_block_till_done()

    for child, brightness in ((C1, 40), (C2, 55), (C3, 70)):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": child, "brightness": brightness},
            blocking=True,
            context=Context(),
        )
        await hass.async_block_till_done()

    attrs = _attrs(hass, sw)
    _record(
        "S6_all_children",
        {
            "manual": {c: _mc(manager, c) for c in CHILDREN},
            "switch_attrs": attrs,
        },
    )
    for child in CHILDREN:
        assert "BRIGHTNESS" in _mc(manager, child), f"{child} override NOT detected"
    assert sorted(attrs["manual_control"]) == sorted(CHILDREN)
