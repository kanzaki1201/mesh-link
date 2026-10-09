import os
import tempfile

import bpy

from .session import channel_key

baking = False

# (Mesh Link Output input, Principled BSDF input, channel, bake type)
FIXED_INPUTS = (
    ('Color', 'Base Color', 'color', 'EMIT'),
    ('Emission', 'Emission Color', 'emissive', 'EMIT'),
    ('Normal', 'Normal', 'normal', 'NORMAL'),
    ('Metallic', 'Metallic', 'metalness', 'EMIT'),
    ('Roughness', 'Roughness', 'roughness', 'EMIT'),
)


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


def _custom_channels(material, node, output):
    inputs = list(node.inputs)
    for position, (name, *_) in enumerate(FIXED_INPUTS):
        if position >= len(inputs) or inputs[position].name != name:
            raise ValueError(f'{material.name}: Mesh Link Output input {position + 1} must be '
                             f'"{name}". Rename it back, or add a new Mesh Link Output node.')
    result = []
    names = {}
    for socket in inputs[len(FIXED_INPUTS):]:
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
        link = _active_link(inputs[position])
        if output is not None and link is not None:
            result.append((channel, bake_type, link.from_socket, output))
    return result


def channels(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    nodes = material.node_tree.nodes
    output = next((node for node in nodes
                   if node.type == 'OUTPUT_MATERIAL' and node.is_active_output), None)
    custom = next((node for node in nodes if node.bl_idname == 'MeshLinkOutputNode'), None)
    if custom is not None:
        extra = _custom_channels(material, custom, output)
        return _standard_channels(list(custom.inputs), output) + extra
    shader = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)
    if shader is None:
        return []
    inputs = [shader.inputs.get(principled) for _, principled, _, _ in FIXED_INPUTS]
    return _standard_channels(inputs, output)


def _linked_source(tree, socket, stack):
    link = _active_link(socket)
    return [(tree, link.from_socket, stack)] if link is not None else []


def _source_inputs(tree, socket, stack):
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
        return _linked_source(inner, entry, stack + ((node, tree),))
    if node.type == 'GROUP_INPUT':
        if not stack:
            return []
        group, parent = stack[-1]
        entry = next((item for item in group.inputs
                      if item.identifier == socket.identifier), None)
        return _linked_source(parent, entry, stack[:-1])
    return [state for entry in node.inputs for state in _linked_source(tree, entry, stack)]


def source_images(tree, source):
    """Find images upstream of a channel source socket."""
    images = set()
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
        if node.type == 'TEX_IMAGE':
            if node.image is not None:
                images.add(node.image)
        else:
            pending.extend(_source_inputs(tree, socket, stack))
    return images


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


def _bake_channel(obj, material, channel, bake_type, source, output, size):
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
        image.colorspace_settings.name = 'sRGB' if channel in {'color', 'emissive'} else 'Non-Color'
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
            raise RuntimeError(f"{obj.name}: {material.name}: {channel} bake did not finish")
        return _png(image)
    finally:
        if shader is not None:
            tree.nodes.remove(shader)
            if original is not None:
                tree.links.new(original, surface)
        if target is not None:
            tree.nodes.remove(target)
        tree.nodes.active = active
        bpy.data.images.remove(image)


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
    # Setting nodes.active to the bake target clears the active texture flag on every other node.
    shown = {node.name: node.show_texture for node in nodes}
    try:
        uv.active_render = True
        return {channel: _bake_channel(obj, material, channel, bake_type, source, output, size)
                for channel, bake_type, source, output in sources}
    finally:
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


def bake_objects(objects, size, selected=None):
    global baking
    baking = True
    try:
        return _bake_objects(objects, size, selected)
    finally:
        try:
            bpy.context.view_layer.update()
        finally:
            baking = False


def _validate_channels(objects, channel_filter):
    for _, obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            if channel_filter is None or (material and material.as_pointer() in channel_filter):
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
