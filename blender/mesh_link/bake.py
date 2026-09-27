import os
import tempfile

import bpy

from .session import channel_key


def render_enabled(obj, view_layer):
    if not obj.visible_get(view_layer=view_layer) or obj.hide_render:
        return False

    def enabled(layer, parent_enabled=True):
        current = parent_enabled and not layer.exclude and not layer.collection.hide_render
        if current and obj.name in layer.collection.objects:
            return True
        return any(enabled(child, current) for child in layer.children)

    return enabled(view_layer.layer_collection)


def ensure_channel_group():
    group = bpy.data.node_groups.get("Mesh Link Channel")
    if group is None:
        group = bpy.data.node_groups.new("Mesh Link Channel", 'ShaderNodeTree')
        group.interface.new_socket(name="Color", in_out='INPUT',
                                   socket_type='NodeSocketColor')
    return group


def _extra_channels(material, output):
    result = []
    labels = set()
    for node in material.node_tree.nodes:
        if node.type != 'GROUP' or node.node_tree is None or node.node_tree.name != 'Mesh Link Channel':
            continue
        try:
            channel = channel_key(node.label)
        except ValueError as exc:
            raise ValueError(f"{material.name}: {node.name}: {exc}") from exc
        if channel in labels:
            raise ValueError(f"{material.name}: {node.name}: duplicate Mesh Link Channel label")
        labels.add(channel)
        socket = node.inputs.get('Color')
        if output is not None and socket is not None and socket.is_linked:
            result.append((channel, 'EMIT', socket.links[0].from_socket, output))
    return result


def channels(material):
    if material is None or not material.use_nodes or material.node_tree is None:
        return []
    tree = material.node_tree
    output = next((node for node in tree.nodes
                   if node.type == 'OUTPUT_MATERIAL' and node.is_active_output), None)
    extra = _extra_channels(material, output)
    if output is None or not output.inputs['Surface'].is_linked:
        return extra
    shader = output.inputs['Surface'].links[0].from_node
    result = []
    if shader.type == 'BSDF_PRINCIPLED':
        for input_name, channel, bake_type in (
                ('Normal', 'normal', 'NORMAL'),
                ('Base Color', 'color', 'EMIT'),
                ('Emission Color', 'emissive', 'EMIT')):
            socket = shader.inputs.get(input_name)
            if socket is not None and socket.is_linked:
                result.append((channel, bake_type, socket.links[0].from_socket, output))
    return result + extra


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
    original = surface.links[0].from_socket if surface.is_linked else None
    image = bpy.data.images.new("Mesh Link Bake", width=size, height=size,
                                alpha=True, float_buffer=False)
    target = None
    emission = None
    try:
        image.colorspace_settings.name = 'sRGB' if channel in {'color', 'emissive'} else 'Non-Color'
        target = tree.nodes.new('ShaderNodeTexImage')
        target.image = image
        tree.nodes.active = target
        if bake_type == 'EMIT':
            emission = tree.nodes.new('ShaderNodeEmission')
            tree.links.new(source, emission.inputs['Color'])
            tree.links.new(emission.outputs['Emission'], surface)
        if bpy.ops.object.bake(type=bake_type) != {'FINISHED'}:
            raise RuntimeError(f"{obj.name}: {material.name}: {channel} bake did not finish")
        return _png(image)
    finally:
        if emission is not None:
            tree.nodes.remove(emission)
            if original is not None:
                tree.links.new(original, surface)
        if target is not None:
            tree.nodes.remove(target)
        tree.nodes.active = active
        bpy.data.images.remove(image)


def _bake_slot(obj, material, size):
    sources = channels(material)
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


def _bake_object(mesh_id, obj, size):
    result = []
    original = [slot.material for slot in obj.material_slots]
    names = [slot.name for slot in obj.material_slots]
    sink = {}
    try:
        baked = {}
        for slot_index, material in enumerate(original):
            if material is not None and material.as_pointer() not in baked:
                _isolate_slot(obj, original, material, sink)
                baked[material.as_pointer()] = _bake_slot(obj, material, size)
            result.append((mesh_id, slot_index,
                           material.name if material else names[slot_index],
                           baked[material.as_pointer()] if material else {}))
    finally:
        for slot, material in zip(obj.material_slots, original):
            slot.material = material
        if 'material' in sink:
            bpy.data.materials.remove(sink['material'])
        if 'image' in sink:
            bpy.data.images.remove(sink['image'])
    return result


def _restore_context(scene, settings, engine, samples, selected, active, mode, object_mode):
    bake = scene.render.bake
    for name, value in settings.items():
        setattr(bake, name, value)
    scene.cycles.samples = samples
    scene.render.engine = engine
    if object_mode:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in selected:
            obj.select_set(True)
    bpy.context.view_layer.objects.active = active
    if active and mode != 'OBJECT' and object_mode:
        bpy.ops.object.mode_set(mode=mode)


def bake_objects(objects, size):
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    selected = tuple(bpy.context.selected_objects)
    mode = active.mode if active else 'OBJECT'
    bake = scene.render.bake
    settings = {name: getattr(bake, name) for name in (
        'target', 'normal_space', 'normal_r', 'normal_g', 'normal_b',
        'use_selected_to_active', 'use_clear')}
    engine = scene.render.engine
    samples = scene.cycles.samples
    result = []
    object_mode = mode == 'OBJECT'
    try:
        if active and active.mode != 'OBJECT':
            if bpy.ops.object.mode_set(mode='OBJECT') != {'FINISHED'}:
                raise RuntimeError("Could not enter Object Mode for texture bake")
            object_mode = True
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 1
        bake.target = 'IMAGE_TEXTURES'
        bake.normal_space = 'TANGENT'
        bake.normal_r, bake.normal_g, bake.normal_b = 'POS_X', 'POS_Y', 'POS_Z'
        bake.use_selected_to_active = False
        bake.use_clear = True
        bpy.ops.object.select_all(action='DESELECT')
        for mesh_id, obj in objects:
            if not render_enabled(obj, view_layer):
                continue
            obj.select_set(True)
            view_layer.objects.active = obj
            try:
                result.extend(_bake_object(mesh_id, obj, size))
            finally:
                obj.select_set(False)
    finally:
        _restore_context(scene, settings, engine, samples, selected, active, mode, object_mode)
    return result
