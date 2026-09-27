import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import traceback
import types
import zlib

import bpy


def load_bake():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('mesh_link.bake', root / 'mesh_link' / 'bake.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def links(tree):
    return {(link.from_node.name, link.from_socket.name,
             link.to_node.name, link.to_socket.name) for link in tree.links}


def solid_png(rgb):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data)))

    row = b'\0' + bytes((*rgb, 255)) * 4
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 4, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * 4))
            + chunk(b'IEND', b''))


def load_png(data, name, color_space='sRGB'):
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as stream:
        stream.write(data)
        path = Path(stream.name)
    try:
        image = bpy.data.images.load(str(path))
        image.name = name
        image.colorspace_settings.name = color_space
        image.pack()
        return image
    finally:
        path.unlink()


def center(image):
    width, height = image.size
    index = ((height // 2) * width + width // 2) * 4
    return tuple(image.pixels[index:index + 3])


def assert_color(data, source):
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    image = load_png(data, 'Smoke Result')
    try:
        actual = center(image)
        expected = center(source)
        assert all(abs(a - b) <= 2 / 255 for a, b in zip(actual, expected)), (actual, expected)
    finally:
        bpy.data.images.remove(image)


def assert_linear(data, value, name):
    image = load_png(data, name, 'Non-Color')
    try:
        assert all(abs(actual - value) <= 2 / 255 for actual in center(image))
    finally:
        bpy.data.images.remove(image)


def make_object(name, quads):
    vertices = []
    faces = []
    for left, right in quads:
        index = len(vertices)
        vertices.extend(((left, -1, 0), (right, -1, 0),
                         (right, 1, 0), (left, 1, 0)))
        faces.append(tuple(range(index, index + 4)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv = mesh.uv_layers.new(name='UVMap')
    for polygon in mesh.polygons:
        for index, coord in zip(polygon.loop_indices, ((0, 0), (1, 0), (1, 1), (0, 1))):
            uv.data[index].uv = coord
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def make_material(name, image):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    shader = tree.nodes.get('Principled BSDF')
    color_node = tree.nodes.new('ShaderNodeTexImage')
    color_node.image = image
    tree.links.new(color_node.outputs['Color'], shader.inputs['Base Color'])
    tree.nodes.active = color_node
    return material


def assert_bakes(result, color_a, red, blue):
    textures = {(mesh_id, slot): channels for mesh_id, slot, _, channels in result}
    assert not any(mesh_id == 'hidden' for mesh_id, *_ in result)
    assert textures[('a', 0)].keys() == {
        'normal', 'color', 'metalness', 'roughness', 'x_shadow_mask'}
    assert textures[('a', 1)] == {}
    assert textures[('unlinked', 0)].keys() == {'x_shadow_mask'}
    assert all(data.startswith(b'\x89PNG\r\n\x1a\n')
               for channels in textures.values() for data in channels.values())
    assert_color(textures[('a', 0)]['color'], color_a)
    assert_color(textures[('b', 0)]['color'], red)
    assert_color(textures[('b', 1)]['color'], blue)
    assert_linear(textures[('a', 0)]['x_shadow_mask'], 0.5, 'Smoke Mask Result')
    assert_linear(textures[('a', 0)]['metalness'], 0.25, 'Smoke Metallic Result')
    assert_linear(textures[('a', 0)]['roughness'], 0.75, 'Smoke Roughness Result')


def main():
    bake = load_bake()
    from mesh_link import channel_node
    channel_node.register()
    obj_a = make_object('Smoke Plane', [(-1, 1)])
    obj_b = make_object('Smoke Overlap', [(-2, 0), (0, 2)])
    obj_unlinked = make_object('Smoke Unlinked', [(-1, 1)])
    obj_hidden = make_object('Smoke Hidden', [(-1, 1)])
    color_a = load_png(solid_png((204, 26, 51)), 'Smoke Color')
    red = load_png(solid_png((255, 0, 0)), 'Smoke Red')
    blue = load_png(solid_png((0, 0, 255)), 'Smoke Blue')
    material_a = make_material('Smoke Paint', color_a)
    material_red = make_material('Smoke Red Paint', red)
    material_blue = make_material('Smoke Blue Paint', blue)
    material_unlinked = bpy.data.materials.new('Smoke Unlinked Paint')
    material_unlinked.use_nodes = True
    unlinked_tree = material_unlinked.node_tree
    for link in list(unlinked_tree.links):
        unlinked_tree.links.remove(link)
    unlinked_channel = unlinked_tree.nodes.new('MeshLinkChannelNode')
    unlinked_channel.channel = 'Shadow Mask'
    unlinked_color = unlinked_tree.nodes.new('ShaderNodeRGB')
    unlinked_color.outputs['Color'].default_value = (0.5, 0.5, 0.5, 1)
    unlinked_tree.links.new(unlinked_color.outputs['Color'], unlinked_channel.inputs['Color'])
    obj_a.data.materials.append(material_a)
    obj_a.data.materials.append(None)
    obj_b.data.materials.append(material_red)
    obj_b.data.materials.append(material_blue)
    obj_unlinked.data.materials.append(material_unlinked)
    obj_hidden.data.materials.append(material_a)
    obj_hidden.hide_set(True)
    obj_b.data.polygons[1].material_index = 1
    tree = material_a.node_tree
    custom_channel = tree.nodes.new('MeshLinkChannelNode')
    custom_channel.channel = 'Shadow Mask'
    mask_node = tree.nodes.new('ShaderNodeRGB')
    mask_node.outputs['Color'].default_value = (0.5, 0.5, 0.5, 1)
    tree.links.new(mask_node.outputs['Color'], custom_channel.inputs['Color'])
    shader = tree.nodes.get('Principled BSDF')
    metallic = tree.nodes.new('ShaderNodeValue')
    metallic.outputs['Value'].default_value = 0.25
    tree.links.new(metallic.outputs['Value'], shader.inputs['Metallic'])
    roughness = tree.nodes.new('ShaderNodeValue')
    roughness.outputs['Value'].default_value = 0.75
    tree.links.new(roughness.outputs['Value'], shader.inputs['Roughness'])
    normal_node = tree.nodes.new('ShaderNodeNewGeometry')
    tree.links.new(normal_node.outputs['Normal'],
                   tree.nodes.get('Principled BSDF').inputs['Normal'])
    second_uv = obj_a.data.uv_layers.new(name='Other UV')
    second_uv.active_render = True
    obj_a.data.uv_layers.active_index = 0
    material_a.paint_active_slot = 0

    obj_a.select_set(True)
    bpy.context.view_layer.objects.active = obj_a
    scene = bpy.context.scene
    old_engine = scene.render.engine
    old_samples = scene.cycles.samples
    old_bake = {name: getattr(scene.render.bake, name) for name in (
        'target', 'normal_space', 'normal_r', 'normal_g', 'normal_b',
        'use_selected_to_active', 'use_clear')}
    materials = (material_a, material_red, material_blue, material_unlinked)
    old_trees = [(material, links(material.node_tree), len(material.node_tree.nodes),
                  material.node_tree.nodes.active) for material in materials]
    old_indices = [[polygon.material_index for polygon in obj.data.polygons]
                   for obj in (obj_a, obj_b)]
    old_images = len(bpy.data.images)
    old_materials = len(bpy.data.materials)
    old_selection = tuple(bpy.context.selected_objects)
    objects = (obj_a, obj_b, obj_unlinked)
    old_slots = [[slot.material for slot in obj.material_slots] for obj in objects]
    old_paint = {material.name: material.paint_active_slot for material in materials}
    bpy.ops.object.mode_set(mode='EDIT')
    result = bake.bake_objects([('a', obj_a), ('b', obj_b),
                                ('unlinked', obj_unlinked), ('hidden', obj_hidden)], 64)
    assert_bakes(result, color_a, red, blue)

    def assert_restored():
        assert scene.render.engine == old_engine
        assert scene.cycles.samples == old_samples
        assert all(getattr(scene.render.bake, name) == value for name, value in old_bake.items())
        assert obj_a.mode == 'EDIT'
        assert bpy.context.view_layer.objects.active == obj_a
        assert tuple(bpy.context.selected_objects) == old_selection
        assert obj_a.data.uv_layers['Other UV'].active_render
        assert [[slot.material for slot in obj.material_slots] for obj in objects] == old_slots
        assert all(material.paint_active_slot == old_paint[material.name]
                   for material in materials)
        for material, old_links, old_nodes, old_active in old_trees:
            tree = material.node_tree
            assert links(tree) == old_links, material.name
            assert len(tree.nodes) == old_nodes, material.name
            assert tree.nodes.active == old_active, material.name
        assert [[polygon.material_index for polygon in obj.data.polygons]
                for obj in (obj_a, obj_b)] == old_indices
        assert len(bpy.data.images) == old_images
        assert len(bpy.data.materials) == old_materials

    assert_restored()

    original_channel = bake._bake_channel
    calls = []

    def fail_second(*args):
        current = scene.render.bake
        assert (current.normal_r, current.normal_g, current.normal_b) == ('POS_X', 'POS_Y', 'POS_Z')
        assert obj_a.data.uv_layers.active.active_render
        assert obj_a.material_slots[1].material is not None
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('forced mid-bake failure')
        return original_channel(*args)

    bake._bake_channel = fail_second
    try:
        try:
            bake.bake_objects([('a', obj_a)], 64)
        except RuntimeError as exc:
            assert str(exc) == 'forced mid-bake failure'
        else:
            raise AssertionError('second channel did not fail')
    finally:
        bake._bake_channel = original_channel
    assert_restored()

    from mesh_link import handlers

    obj_flag = make_object('Smoke Active Texture', [(-1, 1)])
    material_flag = make_material('Smoke Active Paint', red)
    obj_flag.data.materials.append(material_flag)
    flag_nodes = material_flag.node_tree.nodes
    shader = flag_nodes.get('Principled BSDF')
    flag_image = next(node for node in flag_nodes if node.type == 'TEX_IMAGE')
    flag_nodes.active = flag_image
    flag_nodes.active = shader
    assert flag_image.show_texture

    obj_collection = make_object('Smoke Collection Hidden', [(-1, 1)])
    obj_collection.data.materials.append(material_flag)
    bpy.context.collection.objects.unlink(obj_collection)
    parent = bpy.data.collections.new('Smoke Render Disabled')
    child = bpy.data.collections.new('Smoke Child')
    bpy.context.scene.collection.children.link(parent)
    parent.children.link(child)
    child.objects.link(obj_collection)
    parent.hide_render = True

    sent = []
    handlers._sync = types.SimpleNamespace(
        objects=lambda _view: {'flag': obj_flag, 'collection': obj_collection},
        sent={'flag', 'collection'})

    def send_bakes(slots):
        sent.extend(slots)
        return sum(len(channels) for _, _, _, channels in slots)

    handlers._session = types.SimpleNamespace(
        running=True, ready=True, capabilities={'material', 'texture'},
        send_bakes=send_bakes, status='Connected')
    preferences = types.SimpleNamespace(addons={
        'mesh_link': types.SimpleNamespace(preferences=types.SimpleNamespace(texture_size='64'))})
    context = types.SimpleNamespace(view_layer=bpy.context.view_layer, preferences=preferences)
    handlers.bake_and_send(context)
    assert [mesh_id for mesh_id, *_ in sent] == ['flag']
    assert handlers._session.status == 'Sent 1 textures, skipped 1 hidden objects'
    assert flag_nodes.active == shader
    assert flag_image.show_texture

    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    obj_flag.select_set(True)
    bpy.context.view_layer.objects.active = obj_flag
    scene.render.engine = 'CYCLES'
    scene.render.bake.target = 'IMAGE_TEXTURES'
    assert bpy.ops.object.bake(type='EMIT') == {'FINISHED'}


try:
    main()
except Exception:
    traceback.print_exc()
    sys.exit(1)
