# Adaptive Lighting: `adaptive_lighting_skip_group` Support

## What This Fork Does

This fork adds support for a new entity attribute `adaptive_lighting_skip_group` that tells Adaptive Lighting:

1. **Don't expand this group** — adapt the group entity directly instead of its children
2. **Still track the children** — if a child is manually controlled, stop adapting the GROUP (not just the child)

## The Problem It Solves

Lightener (and similar integrations) create virtual light groups with custom per-light brightness curves. When Adaptive Lighting expands these groups, it bypasses the curves and sets all children to the same brightness.

Before: AL expands the group → curves are bypassed → lights look wrong
After: AL adapts the group entity → Lightener distributes brightness via its curves → everyone is happy

## How It Works

1. Lightener sets `adaptive_lighting_skip_group: True` on its entities
2. AL's `_is_light_group` sees this and returns `False` (won't expand)
3. AL's `_expand_light_groups` keeps the group entity in the lights list
4. AL tracks the group's children in `_skipped_group_children`
5. When a child is manually controlled, AL marks the GROUP as manual_control instead

## Changes to AL (`switch.py`)

- `_is_light_group()`: Checks `adaptive_lighting_skip_group` in addition to `is_hue_group`
- `_expand_light_groups()`: New elif branch to handle skipped groups
- `AdaptiveLightingManager.__init__`: Added `_skipped_group_children` dict
- `update_manually_controlled_from_event()`: Marks parent group as manual_control when child is controlled

## Changes to Lightener (separate)

Lightener needs to set the attribute:
```python
self._attr_extra_state_attributes = {"adaptive_lighting_skip_group": True}
```

## Revertability

This is a clean fork with no breaking changes. To revert:
1. Switch back to upstream AL
2. Remove the attribute from Lightener (or leave it — upstream AL ignores unknown attributes)
