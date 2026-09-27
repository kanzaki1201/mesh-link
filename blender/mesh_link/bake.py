import os
import tempfile

import bpy

from .session import channel_key


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
        return []
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


def _bake_channel(material, channel, bake_type, source, output, size):
    tree = material.node_tree
    active = tree.nodes.active
    surface = output.inputs['Surface']
    original = surface.links[0].from_socket
    image = bpy.data.images.new("Mesh Link Bake", width=size, height=size,
                                alpha=True, float_buffer=False)
    image.colorspace_settings.name = 'sRGB' if channel in {'color', 'emissive'} else 'Non-Color'
    target = None
    emission = None
    try:
        target = tree.nodes.new('ShaderNodeTexImage')
        target.image = image
        tree.nodes.active = target
        if bake_type == 'EMIT':
            emission = tree.nodes.new('ShaderNodeEmission')
            tree.links.new(source, emission.inputs['Color'])
            tree.links.new(emission.outputs['Emission'], surface)
        bpy.ops.object.bake(type=bake_type)
        return _png(image)
    finally:
        if emission is not None:
            tree.nodes.remove(emission)
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
    return {channel: _bake_channel(material, channel, bake_type, source, output, size)
            for channel, bake_type, source, output in sources}


def _bake_object(mesh_id, obj, size):
    result = []
    indices = [polygon.material_index for polygon in obj.data.polygons]
    try:
        for slot_index, slot in enumerate(obj.material_slots):
            for polygon in obj.data.polygons:
                polygon.material_index = slot_index
            obj.data.update()
            material = slot.material
            result.append((mesh_id, slot_index,
                           material.name if material else slot.name,
                           _bake_slot(obj, material, size)))
    finally:
        for polygon, index in zip(obj.data.polygons, indices):
            polygon.material_index = index
        obj.data.update()
    return result


def bake_objects(objects, size):
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    selected = tuple(bpy.context.selected_objects)
    mode = active.mode if active else 'OBJECT'
    bake = scene.render.bake
    settings = {name: getattr(bake, name) for name in (
        'target', 'normal_space', 'use_selected_to_active', 'use_clear')}
    engine = scene.render.engine
    samples = scene.cycles.samples
    result = []
    try:
        if active and active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 1
        bake.target = 'IMAGE_TEXTURES'
        bake.normal_space = 'TANGENT'
        bake.use_selected_to_active = False
        bake.use_clear = True
        bpy.ops.object.select_all(action='DESELECT')
        for mesh_id, obj in objects:
            obj.select_set(True)
            view_layer.objects.active = obj
            try:
                result.extend(_bake_object(mesh_id, obj, size))
            finally:
                obj.select_set(False)
    finally:
        for name, value in settings.items():
            setattr(bake, name, value)
        scene.cycles.samples = samples
        scene.render.engine = engine
        bpy.ops.object.select_all(action='DESELECT')
        for obj in selected:
            obj.select_set(True)
        view_layer.objects.active = active
        if active and mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=mode)
    return result
