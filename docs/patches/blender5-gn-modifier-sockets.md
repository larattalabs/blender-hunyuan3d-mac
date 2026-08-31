# Blender 5.x geometry-node modifier sockets

Add-ons that set geometry-node modifier inputs the 4.x way fail on Blender 5.x with:

```
TypeError: bpy_struct[key] = val: id properties not supported for this type
```

The value moved:

```python
mod[socket_id] = value                              # Blender 4.x
mod.properties.inputs[socket_id]["value"] = value   # Blender 5.x
```

`mod.properties.inputs[socket_id]` is an `IDPropertyGroup` — `{'value': ..., 'type': ...,
'attribute_name': ''}` — so the value is a key inside it, not the item itself and not an attribute
(attribute assignment is read-only).

Compatibility shim, works on both:

```python
def _set_gn_socket(mod, key, value):
    try:
        mod.properties.inputs[key]["value"] = value
    except (AttributeError, KeyError, TypeError):
        mod[key] = value
```

Then rewrite the call sites, preserving trailing comments:

```python
import re
pat = re.compile(r'^(\s*)mod\[([^\]]+)\]\s*=\s*([^#\n]+?)\s*(#.*)?$', re.M)
src = pat.sub(lambda m: f"{m.group(1)}_set_gn_socket(mod, {m.group(2)}, {m.group(3)})"
                        + (f"  {m.group(4)}" if m.group(4) else ""), src)
```

Applied 2026-08-31 to Easy Tree 1.0.1 (33 sites, `operators.py`) and Modular Tree 5.5.2 (12 sites,
`python_classes/resources/node_groups.py`, where the variable is `modifier`). Both generate
correctly afterwards. Add-on updates overwrite this; report upstream instead of carrying it.
