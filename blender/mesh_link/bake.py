from contextlib import contextmanager
import hashlib
import os
import tempfile

import bpy
import numpy as np

from .session import channel_key

baking = False

# (Mesh Link Output input, Principled BSDF input, channel, bake type)
# Alpha has no channel of its own: it bakes into the alpha of the color map.
FIXED_INPUTS = (
    ('Color', 'Base Color', 'color', 'EMIT'),
    ('Emission', 'Emission Color', 'emissive', 'EMIT'),
    ('Normal', 'Normal', 'normal', 'NORMAL'),
    ('Metallic', 'Metallic', 'metalness', 'EMIT'),
    ('Roughness', 'Roughness', 'roughness', 'EMIT'),
    ('Alpha', 'Alpha', None, 'EMIT'),
)
ALPHA = len(FIXED_INPUTS) - 1


def render_enabled(obj, view_layer):
    if not obj.visible_get(view_layer=view_layer) or obj.hide_render:
        return False

    def enabled(layer, parent_enabled=True):
        current = parent_enabled and not layer.exclude and not layer.collection.hide_render
        if current and obj.name in layer.collection.objects:
            return True
        return any(enabled(child, current) for child in layer.children)

    return enabled(view_layer.layer_collection)


def _active_link(socket):
    if socket is None or not socket.is_linked:
        return None
    return next((link for link in socket.links if link.is_valid and not link.is_muted), None)


def fixed_count(names):
    """Count the fixed inputs of a node. A node from before the Alpha input has one fewer."""
    return len(FIXED_INPUTS) if list(names)[ALPHA:ALPHA + 1] == ['Alpha'] else ALPHA


def _custom_channels(material, node, output):
    inputs = list(node.inputs)
    fixed = fixed_count([socket.name for socket in inputs])
    for position, (name, *_) in enumerate(FIXED_INPUTS[:fixed]):
        if position >= len(inputs) or inputs[position].name != name:
            raise ValueError(f'{material.name}: Mesh Link Output input {position + 1} must be '
                             f'"{name}". Rename it back, or add a new Mesh Link Output node.')
    result = []
    names = {}
    for socket in inputs[fixed:]:
        try:
            channel = channel_key(socket.name)
        except ValueError as exc:
            raise ValueError(f'{material.name}: Mesh Link Output input "{socket.name}": '
                             f'{exc}. Rename it in the Node tab of the sidebar.') from exc
        if channel in names:
            raise ValueError(f'{material.name}: Mesh Link Output inputs "{names[channel]}" and '
                             f'"{socket.name}": use different names; both clean to {channel}')
        names[channel] = socket.name
        link = _active_link(socket)
        if output is not None and link is not None:
            result.append((channel, 'EMIT', link.from_socket, output))
    return result


def _standard_channels(inputs, output):
    result = []
    for position, (_, _, channel, bake_type) in enumerate(FIXED_INPUTS):
        link = _active_link(inputs[position]) if channel is not None else None
        if output is not None and link is not None:
            result.append((channel, bake_type, link.from_socket, output))
    return result


def _output_nodes(material):
    """Return the active Material Output and the first Mesh Link Output node of a material."""
    if material is None or not material.use_nodes or material.node_tree is None:
        return None, None
    nodes = material.node_tree.nodes
    output = next((node for node in nodes
                   if node.type == 'OUTPUT_MATERIAL' and node.is_active_output), None)
    custom = next((node for node in nodes if node.bl_idname == 'MeshLinkOutputNode'), None)
    return output, custom


def channels(material):
    """List (channel, bake type, source socket, Material Output) from the Mesh Link Output node."""
    output, custom = _output_nodes(material)
    if custom is None:
        return []
    extra = _custom_channels(material, custom, output)
    return _standard_channels(list(custom.inputs), output) + extra


def alpha_source(material):
    """Return the socket linked to the Alpha input of the Mesh Link Output node, or None."""
    output, custom = _output_nodes(material)
    if output is None or custom is None:
        return None
    inputs = list(custom.inputs)
    if fixed_count([socket.name for socket in inputs]) <= ALPHA:
        return None
    link = _active_link(inputs[ALPHA])
    return link.from_socket if link is not None else None


def channel_sources(material):
    """Map each channel to the sockets that feed it. The color channel includes its alpha source."""
    result = {entry[0]: [entry[2]] for entry in channels(material)}
    alpha = alpha_source(material)
    if alpha is not None and 'color' in result:
        result['color'].append(alpha)
    return result


def ensure_output_node(material):
    """Add a Mesh Link Output node linked from the first Principled BSDF when a material has none."""
    if material is None or not material.use_nodes or material.node_tree is None:
        return False
    tree = material.node_tree
    if any(node.bl_idname == 'MeshLinkOutputNode' for node in tree.nodes):
        return False
    shader = next((node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
    if shader is None:
        return False
    node = tree.nodes.new('MeshLinkOutputNode')
    node.location = (shader.location.x + shader.width + 40, shader.location.y + 160)
    for name, principled, *_ in FIXED_INPUTS:
        link = _active_link(shader.inputs.get(principled))
        if link is not None:
            tree.links.new(link.from_socket, node.inputs[name])
    return True


def _linked(tree, socket, cache):
    """Find the active link into a socket. A cache turns each tree into one pass over its links."""
    if socket is None or not socket.is_linked:
        return None
    key = ('links', tree.as_pointer())
    if key not in cache:
        cache[key] = {link.to_socket.as_pointer(): link for link in tree.links
                      if link.is_valid and not link.is_muted}
    return cache[key].get(socket.as_pointer())


def _linked_source(tree, socket, stack, cache):
    link = _linked(tree, socket, cache)
    return [(tree, link.from_socket, stack)] if link is not None else []


def _source_inputs(tree, socket, stack, cache):
    node = socket.node
    if node.type == 'GROUP':
        inner = node.node_tree
        if inner is None:
            return []
        output = next((item for item in inner.nodes
                       if item.type == 'GROUP_OUTPUT' and item.is_active_output), None)
        if output is None:
            return []
        entry = next((item for item in output.inputs
                      if item.identifier == socket.identifier), None)
        return _linked_source(inner, entry, stack + ((node, tree),), cache)
    if node.type == 'GROUP_INPUT':
        if not stack:
            return []
        group, parent = stack[-1]
        entry = next((item for item in group.inputs
                      if item.identifier == socket.identifier), None)
        return _linked_source(parent, entry, stack[:-1], cache)
    return [state for entry in node.inputs
            for state in _linked_source(tree, entry, stack, cache)]


def _walk(tree, source, through_images, cache):
    """Yield (tree, socket, stack) for each socket upstream of a channel source, once each."""
    pending = [(tree, source, ())]
    seen = set()
    while pending:
        tree, socket, stack = pending.pop()
        node = socket.node
        key = (tree.as_pointer(), node.as_pointer(), socket.identifier, len(stack),
               tuple(group.as_pointer() for group, _ in stack))
        if key in seen:
            continue
        seen.add(key)
        yield tree, socket, stack
        if through_images or node.type != 'TEX_IMAGE':
            pending.extend(_source_inputs(tree, socket, stack, cache))


def source_images(tree, source):
    """Find images upstream of a channel source socket."""
    return {socket.node.image for _, socket, _ in _walk(tree, source, False, {})
            if socket.node.type == 'TEX_IMAGE' and socket.node.image is not None}


_PLAIN = {'ENUM', 'BOOLEAN', 'INT', 'FLOAT', 'STRING'}
_EDITOR_ONLY = {'name', 'label', 'location', 'width', 'width_hidden', 'height', 'dimensions',
                'select', 'hide', 'show_options', 'show_preview', 'show_texture',
                'use_custom_color', 'color'}
_setting_names = {}


def _plain(value):
    """Turn a socket or node value into something with a stable repr."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    name = getattr(value, 'name', None)
    if isinstance(name, str):
        return name
    try:
        return tuple(_plain(item) for item in value)
    except TypeError:
        return repr(value)


def _plain_settings(node):
    rna = getattr(node, 'bl_rna', None)
    if rna is None:
        return ()
    if node.bl_idname not in _setting_names:
        _setting_names[node.bl_idname] = tuple(
            prop.identifier for prop in rna.properties
            if prop.type in _PLAIN and prop.identifier not in _EDITOR_ONLY
            and not prop.identifier.startswith('bl_'))
    return _setting_names[node.bl_idname]


def _node_settings(node):
    settings = [(name, _plain(getattr(node, name))) for name in _plain_settings(node)]
    settings.extend((name, _plain(getattr(node, name, None))) for name in ('image', 'node_tree'))
    ramp = getattr(node, 'color_ramp', None)
    if ramp is not None:
        settings.append(('color_ramp', (ramp.interpolation, tuple(
            (element.position, _plain(element.color)) for element in ramp.elements))))
    return tuple(settings)


def _node_record(tree, node, cache):
    key = ('node', node.as_pointer())
    if key not in cache:
        inputs = []
        for socket in node.inputs:
            link = _linked(tree, socket, cache)
            inputs.append((socket.identifier,
                           (link.from_node.name, link.from_socket.identifier) if link is not None
                           else _plain(getattr(socket, 'default_value', None))))
        record = repr((node.bl_idname, _node_settings(node), tuple(inputs)))
        cache[key] = hashlib.sha256(record.encode()).hexdigest()[:16]
    return cache[key]


def signature(tree, sources, cache=None):
    """Hash the upstream graph of channel sources: nodes, links, input values, and settings."""
    cache = {} if cache is None else cache
    records = set()
    for source in sources:
        for node_tree, socket, stack in _walk(tree, source, True, cache):
            node = socket.node
            path = tuple(group.name for group, _ in stack)
            records.add(f'{path}/{node.name}/{socket.identifier}')
            records.add(f'{path}/{node.name}={_node_record(node_tree, node, cache)}')
    return hashlib.sha256('\n'.join(sorted(records)).encode()).hexdigest()


def signatures(material):
    """Hash the upstream graph of every channel of a material."""
    cache = {}
    return {key: signature(material.node_tree, sources, cache)
            for key, sources in channel_sources(material).items()}


def _png(image):
    fd, path = tempfile.mkstemp(suffix='.png', dir=bpy.app.tempdir)
    os.close(fd)
    try:
        image.filepath_raw = path
        image.file_format = 'PNG'
        image.save()
        with open(path, 'rb') as stream:
            return stream.read()
    finally:
        os.unlink(path)


@contextmanager
def _bake_image(obj, material, label, bake_type, source, output, size, color_space):
    """Bake a source socket into a new image and yield it. Restore the node tree afterwards."""
    tree = material.node_tree
    active = tree.nodes.active
    surface = output.inputs['Surface']
    link = _active_link(surface)
    original = link.from_socket if link is not None else None
    image = bpy.data.images.new("Mesh Link Bake", width=size, height=size,
                                alpha=True, float_buffer=False)
    target = None
    shader = None
    try:
        image.colorspace_settings.name = color_space
        target = tree.nodes.new('ShaderNodeTexImage')
        target.image = image
        tree.nodes.active = target
        if bake_type == 'EMIT':
            shader = tree.nodes.new('ShaderNodeEmission')
            tree.links.new(source, shader.inputs['Color'])
            tree.links.new(shader.outputs['Emission'], surface)
        else:
            shader = tree.nodes.new('ShaderNodeBsdfDiffuse')
            tree.links.new(source, shader.inputs['Normal'])
            tree.links.new(shader.outputs['BSDF'], surface)
        if bpy.ops.object.bake('EXEC_DEFAULT', False, type=bake_type) != {'FINISHED'}:
            raise RuntimeError(f"{obj.name}: {material.name}: {label} bake did not finish")
        yield image
    finally:
        if shader is not None:
            tree.nodes.remove(shader)
            if original is not None:
                tree.links.new(original, surface)
        if target is not None:
            tree.nodes.remove(target)
        tree.nodes.active = active
        bpy.data.images.remove(image)


def _pixels(image):
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    return values


def _bake_channel(obj, material, channel, bake_type, source, output, size, alpha=None):
    mask = None
    if alpha is not None:
        with _bake_image(obj, material, 'alpha', 'EMIT', alpha, output, size,
                         'Non-Color') as image:
            mask = _pixels(image)[0::4]
    color_space = 'sRGB' if channel in {'color', 'emissive'} else 'Non-Color'
    with _bake_image(obj, material, channel, bake_type, source, output, size,
                     color_space) as image:
        if mask is not None:
            values = _pixels(image)
            values[3::4] = mask
            image.pixels.foreach_set(values)
        return _png(image)


def _paint_system_channels(material):
    groups = getattr(getattr(material, 'ps_mat_data', None), 'groups', ())
    return [channel for group in groups for channel in group.channels]


def _bake_slot(obj, material, size, selected=None):
    sources = [entry for entry in channels(material)
               if selected is None or entry[0] in selected]
    if not sources:
        return {}
    if obj.data.uv_layers.active is None:
        raise ValueError(f"{obj.name}: no active UV layer")
    uv = obj.data.uv_layers.active
    render_uv = next((layer.name for layer in obj.data.uv_layers if layer.active_render), None)
    nodes = material.node_tree.nodes
    active = nodes.active
    paint_slot = material.paint_active_slot
    alpha = alpha_source(material)
    # Setting nodes.active to the bake target clears the active texture flag on every other node.
    shown = {node.name: node.show_texture for node in nodes}
    # A Paint System channel preview drops the output transform, so the Normal socket carries colour.
    previewed = [channel for channel in _paint_system_channels(material)
                 if channel.disable_output_transform]
    try:
        for channel in previewed:
            channel.disable_output_transform = False
        uv.active_render = True
        return {channel: _bake_channel(obj, material, channel, bake_type, source, output, size,
                                       alpha if channel == 'color' else None)
                for channel, bake_type, source, output in sources}
    finally:
        for channel in previewed:
            channel.disable_output_transform = True
        material.paint_active_slot = paint_slot
        nodes.active = active
        for node in nodes:
            node.show_texture = shown.get(node.name, False)
        if render_uv is not None:
            obj.data.uv_layers[render_uv].active_render = True


def _isolate_slot(obj, original, material, sink):
    if not any(other != material for other in original):
        return
    if 'material' not in sink:
        sink['image'] = bpy.data.images.new("Mesh Link Sink", width=8, height=8,
                                            alpha=True, float_buffer=False)
        sink['image'].colorspace_settings.name = 'Non-Color'
        sink['material'] = bpy.data.materials.new("Mesh Link Sink")
        sink['material'].use_nodes = True
        node = sink['material'].node_tree.nodes.new('ShaderNodeTexImage')
        node.image = sink['image']
        sink['material'].node_tree.nodes.active = node
    for index, slot in enumerate(obj.material_slots):
        slot.material = material if original[index] == material else sink['material']


def _bake_material(obj, original, material, size, selected, sink):
    _isolate_slot(obj, original, material, sink)
    if selected is None:
        return _bake_slot(obj, material, size)
    return _bake_slot(obj, material, size, selected)


def _restore_slots(obj, original, sink):
    for slot, material in zip(obj.material_slots, original):
        slot.material = material
    if 'material' in sink:
        bpy.data.materials.remove(sink['material'])
    if 'image' in sink:
        bpy.data.images.remove(sink['image'])


def _bake_object(mesh_id, obj, size, selected=None):
    result = []
    original = [slot.material for slot in obj.material_slots]
    names = [slot.name for slot in obj.material_slots]
    sink = {}
    try:
        baked = {}
        for slot_index, material in enumerate(original):
            pointer = material.as_pointer() if material else None
            if selected is not None and pointer not in selected:
                continue
            if material is not None and pointer not in baked:
                keys = selected[pointer] if selected is not None else None
                baked[pointer] = _bake_material(obj, original, material, size, keys, sink)
            result.append((mesh_id, slot_index,
                           material.name if material else names[slot_index],
                           baked[pointer] if material else {},
                           {entry[0] for entry in channels(material)}))
    finally:
        _restore_slots(obj, original, sink)
    return result


def _clear_selection(mode):
    if mode == 'TEXTURE_PAINT':
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
    else:
        bpy.ops.object.select_all('EXEC_DEFAULT', False, action='DESELECT')


def _restore_context(scene, settings, engine, samples, device, selected, active, mode, object_mode):
    bake = scene.render.bake
    for name, value in settings.items():
        setattr(bake, name, value)
    scene.cycles.samples = samples
    scene.cycles.device = device
    scene.render.engine = engine
    if object_mode:
        _clear_selection(mode)
        for obj in selected:
            obj.select_set(True)
    bpy.context.view_layer.objects.active = active
    if active and mode not in {'OBJECT', 'TEXTURE_PAINT'} and object_mode:
        bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode=mode)


@contextmanager
def _ignoring_updates():
    """Keep the bake's own edits out of texture dirt. One view layer update reports them first."""
    global baking
    baking = True
    try:
        yield
    finally:
        try:
            bpy.context.view_layer.update()
        finally:
            baking = False


def bake_objects(objects, size, selected=None):
    with _ignoring_updates():
        return _bake_objects(objects, size, selected)


def ensure_outputs(objects):
    """Run the output node setup for every material of the objects."""
    materials = {slot.material.as_pointer(): slot.material for _, obj in objects
                 for slot in obj.material_slots if slot.material is not None}
    with _ignoring_updates():
        for material in materials.values():
            ensure_output_node(material)


def _validate_channels(objects, channel_filter):
    for _, obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            if channel_filter is None or (material and material.as_pointer() in channel_filter):
                ensure_output_node(material)
                channels(material)


def _bake_objects(objects, size, channel_filter):
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    visible = [(mesh_id, obj) for mesh_id, obj in objects if render_enabled(obj, view_layer)]
    _validate_channels(visible, channel_filter)
    active = view_layer.objects.active
    selected = tuple(bpy.context.selected_objects)
    mode = active.mode if active else 'OBJECT'
    bake = scene.render.bake
    settings = {name: getattr(bake, name) for name in (
        'target', 'normal_space', 'normal_r', 'normal_g', 'normal_b',
        'use_selected_to_active', 'use_clear')}
    engine = scene.render.engine
    samples = scene.cycles.samples
    device = scene.cycles.device
    result = []
    object_mode = mode in {'OBJECT', 'TEXTURE_PAINT'}
    try:
        if active and not object_mode:
            if bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='OBJECT') != {'FINISHED'}:
                raise RuntimeError("Could not enter Object Mode for texture bake")
            object_mode = True
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 1
        scene.cycles.device = 'CPU'
        bake.target = 'IMAGE_TEXTURES'
        bake.normal_space = 'TANGENT'
        bake.normal_r, bake.normal_g, bake.normal_b = 'POS_X', 'POS_Y', 'POS_Z'
        bake.use_selected_to_active = False
        bake.use_clear = True
        _clear_selection(mode)
        for mesh_id, obj in visible:
            obj.select_set(True)
            view_layer.objects.active = obj
            try:
                result.extend(_bake_object(mesh_id, obj, size, channel_filter))
            finally:
                obj.select_set(False)
    finally:
        _restore_context(scene, settings, engine, samples, device, selected, active, mode, object_mode)
    return result
