import ast, sys

filepath = 'custom_components/adaptive_lighting/switch.py'
with open(filepath, 'r') as f:
    content = f.read()

# Step 1: Patch _is_light_group
old_is_light_group = '''def _is_light_group(state: State) -> bool:
    return "entity_id" in state.attributes and not state.attributes.get(
        "is_hue_group",
        False,
    )'''

new_is_light_group = '''def _is_light_group(state: State) -> bool:
    is_group = "entity_id" in state.attributes
    if not is_group:
        return False
    skip = state.attributes.get("is_hue_group", False) or state.attributes.get(
        "adaptive_lighting_skip_group", False
    )
    return is_group and not skip'''

content = content.replace(old_is_light_group, new_is_light_group)
print("Step 1: _is_light_group patched")

# Step 2: Patch _expand_light_groups
old_expand = '''def _expand_light_groups(
    hass: HomeAssistant,
    lights: list[str],
) -> list[str]:
    all_lights: set[str] = set()
    manager = hass.data[DOMAIN][ATTR_ADAPTIVE_LIGHTING_MANAGER]
    for light in lights:
        state = hass.states.get(light)
        if state is None:
            _LOGGER.debug("State of %s is None", light)
            all_lights.add(light)
        elif _is_light_group(state):
            group = state.attributes["entity_id"]
            manager.lights.discard(light)
            all_lights.update(group)
            _LOGGER.debug("Expanded %s to %s", light, group)
        else:
            all_lights.add(light)
    return sorted(all_lights)'''

new_expand = '''def _expand_light_groups(
    hass: HomeAssistant,
    lights: list[str],
) -> list[str]:
    all_lights: set[str] = set()
    manager = hass.data[DOMAIN][ATTR_ADAPTIVE_LIGHTING_MANAGER]
    for light in lights:
        state = hass.states.get(light)
        if state is None:
            _LOGGER.debug("State of %s is None", light)
            all_lights.add(light)
        elif _is_light_group(state):
            group = state.attributes["entity_id"]
            manager.lights.discard(light)
            all_lights.update(group)
            _LOGGER.debug("Expanded %s to %s", light, group)
        elif state.attributes.get("adaptive_lighting_skip_group"):
            # Skipped group: expand to children for manual-control tracking,
            # but record mapping so adaptation can redirect to the group entity.
            children = state.attributes.get("entity_id", [])
            all_lights.update(children)
            for child in children:
                manager._skipped_group_children[child] = light
            _LOGGER.debug("Expanded skipped group %s to %s for tracking", light, children)
        else:
            all_lights.add(light)
    return sorted(all_lights)'''

content = content.replace(old_expand, new_expand)
print("Step 2: _expand_light_groups patched")

# Step 3: Add _skipped_group_children init
content = content.replace(
    "        self.lights: set[str] = set()\n",
    "        self.lights: set[str] = set()\n        # child entity_id -> parent group entity_id (for skipped groups)\n        self._skipped_group_children: dict[str, str] = {}\n"
)
print("Step 3: _skipped_group_children init added")

# Step 4: Patch _adapt_light to redirect to group entity
insert_marker = '''        if context is None:
            context = self._lock_context.'''

if insert_marker not in content:
    print("ERROR: Could not find insertion point in _adapt_light")
    sys.exit(1)

new_adapt_logic = '''        if context is None:
            context = self._lock_context

        # If this light is a child of a skipped group, redirect adaptation to the group entity.
        # This preserves the group's internal brightness curves (e.g. Lightener).
        if light in self.manager._skipped_group_children:
            group_entity = self.manager._skipped_group_children[light]
            # Avoid redundant group adaptations in the same cycle
            if hasattr(self.manager, '_last_adapted_group') and self.manager._last_adapted_group == group_entity:
                _LOGGER.debug("Skipping redundant adaptation for %s (group %s already adapted)", light, group_entity)
                return
            _LOGGER.debug("Redirecting adaptation of %s to skipped group %s", light, group_entity)
            light = group_entity
            self.manager._last_adapted_group = group_entity

        else:
            self.manager._last_adapted_group = None
'''

content = content.replace(insert_marker, new_adapt_logic)
print("Step 4: _adapt_light patched to redirect to group entity")

# Step 5: Reset _last_adapted_group at the start of each adaptation cycle
old_adapt_all = '''    async def adapt_all_lights(
        self,
        data: AdaptationData,
        transition: int | None,
        force: bool,
        context: Context | None = None,
    ) -> None:'''

if old_adapt_all in content:
    new_adapt_all = '''    async def adapt_all_lights(
        self,
        data: AdaptationData,
        transition: int | None,
        force: bool,
        context: Context | None = None,
    ) -> None:
        # Reset skipped-group adaptation dedup for this cycle
        self.manager._last_adapted_group = None'''
    content = content.replace(old_adapt_all, new_adapt_all)
    print("Step 5: adapt_all_lights reset added")
else:
    print("Step 5: Could not find adapt_all_lights")

# Verify syntax
try:
    ast.parse(content)
    print("\n✅ Syntax OK")
except SyntaxError as e:
    print(f"\n❌ SyntaxError at line {e.lineno}: {e.msg}")
    sys.exit(1)

with open(filepath, 'w') as f:
    f.write(content)

print("Patch written to file")
