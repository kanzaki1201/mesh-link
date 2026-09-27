import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import traceback
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


def load_png(data, name):
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as stream:
        stream.write(data)
        path = Path(stream.name)
    try:
        image = bpy.data.images.load(str(path))
        image.name = name
        image.colorspace_settings.name = 'sRGB'
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


def main():
    bake = load_bake()
    obj_a = make_object('Smoke Plane', [(-1, 1)])
    obj_b = make_object('Smoke Overlap', [(-2, 0), (0, 2)])
    color_a = load_png(solid_png((204, 26, 51)), 'Smoke Color')
    mask = load_png(solid_png((64, 64, 64)), 'Smoke Mask')
    red = load_png(solid_png((255, 0, 0)), 'Smoke Red')
    blue = load_png(solid_png((0, 0, 255)), 'Smoke Blue')
    material_a = make_material('Smoke Paint', color_a)
    material_red = make_material('Smoke Red Paint', red)
    material_blue = make_material('Smoke Blue Paint', blue)
    obj_a.data.materials.append(material_a)
    obj_b.data.materials.append(material_red)
    obj_b.data.materials.append(material_blue)
    obj_b.data.polygons[1].material_index = 1
    tree = material_a.node_tree
    channel_node = tree.nodes.new('ShaderNodeGroup')
    channel_node.node_tree = bake.ensure_channel_group()
    channel_node.label = 'mask'
    mask_node = tree.nodes.new('ShaderNodeTexImage')
    mask_node.image = mask
    tree.links.new(mask_node.outputs['Color'], channel_node.inputs['Color'])

    obj_a.select_set(True)
    bpy.context.view_layer.objects.active = obj_a
    scene = bpy.context.scene
    old_engine = scene.render.engine
    old_samples = scene.cycles.samples
    old_bake = {name: getattr(scene.render.bake, name) for name in (
        'target', 'normal_space', 'use_selected_to_active', 'use_clear')}
    materials = (material_a, material_red, material_blue)
    old_trees = [(material, links(material.node_tree), len(material.node_tree.nodes),
                  material.node_tree.nodes.active) for material in materials]
    old_indices = [[polygon.material_index for polygon in obj.data.polygons]
                   for obj in (obj_a, obj_b)]
    old_images = len(bpy.data.images)
    old_selection = tuple(bpy.context.selected_objects)
    bpy.ops.object.mode_set(mode='EDIT')
    result = bake.bake_objects([('a', obj_a), ('b', obj_b)], 64)
    textures = {(mesh_id, slot): channels for mesh_id, slot, _, channels in result}
    assert textures[('a', 0)].keys() == {'color', 'x_mask'}
    assert all(data.startswith(b'\x89PNG\r\n\x1a\n')
               for channels in textures.values() for data in channels.values())
    assert_color(textures[('a', 0)]['color'], color_a)
    assert_color(textures[('b', 0)]['color'], red)
    assert_color(textures[('b', 1)]['color'], blue)
    assert scene.render.engine == old_engine
    assert scene.cycles.samples == old_samples
    assert all(getattr(scene.render.bake, name) == value for name, value in old_bake.items())
    assert obj_a.mode == 'EDIT'
    assert bpy.context.view_layer.objects.active == obj_a
    assert tuple(bpy.context.selected_objects) == old_selection
    for material, old_links, old_nodes, old_active in old_trees:
        tree = material.node_tree
        assert links(tree) == old_links, material.name
        assert len(tree.nodes) == old_nodes, material.name
        assert tree.nodes.active == old_active, material.name
    assert [[polygon.material_index for polygon in obj.data.polygons]
            for obj in (obj_a, obj_b)] == old_indices
    assert len(bpy.data.images) == old_images


try:
    main()
except Exception:
    traceback.print_exc()
    sys.exit(1)
