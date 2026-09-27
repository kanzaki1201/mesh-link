import importlib.util
from pathlib import Path
import sys
import traceback

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


def main():
    bake = load_bake()
    mesh = bpy.data.meshes.new('Smoke Plane')
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
                     [], [(0, 1, 2, 3)])
    mesh.update()
    uv = mesh.uv_layers.new(name='UVMap')
    for loop, coord in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = coord
    obj = bpy.data.objects.new('Smoke Plane', mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    material = bpy.data.materials.new('Smoke Paint')
    material.use_nodes = True
    mesh.materials.append(material)
    tree = material.node_tree
    shader = tree.nodes.get('Principled BSDF')
    output = tree.nodes.get('Material Output')
    color = bpy.data.images.new('Smoke Color', 4, 4)
    color.generated_color = (0.8, 0.1, 0.2, 1)
    mask = bpy.data.images.new('Smoke Mask', 4, 4)
    mask.generated_color = (0.25, 0.25, 0.25, 1)
    color_node = tree.nodes.new('ShaderNodeTexImage')
    color_node.image = color
    tree.links.new(color_node.outputs['Color'], shader.inputs['Base Color'])
    channel_node = tree.nodes.new('ShaderNodeGroup')
    channel_node.node_tree = bake.ensure_channel_group()
    channel_node.label = 'mask'
    mask_node = tree.nodes.new('ShaderNodeTexImage')
    mask_node.image = mask
    tree.links.new(mask_node.outputs['Color'], channel_node.inputs['Color'])
    tree.nodes.active = color_node

    scene = bpy.context.scene
    old_engine = scene.render.engine
    old_samples = scene.cycles.samples
    old_bake = {name: getattr(scene.render.bake, name) for name in (
        'target', 'normal_space', 'use_selected_to_active', 'use_clear')}
    old_links = links(tree)
    old_nodes = len(tree.nodes)
    old_images = len(bpy.data.images)
    old_selection = tuple(bpy.context.selected_objects)
    bpy.ops.object.mode_set(mode='EDIT')
    result = bake.bake_objects([('smoke', obj)], 64)
    textures = result[0][3]
    assert textures.keys() == {'color', 'x_mask'}
    assert all(data.startswith(b'\x89PNG\r\n\x1a\n') for data in textures.values())
    assert scene.render.engine == old_engine
    assert scene.cycles.samples == old_samples
    assert all(getattr(scene.render.bake, name) == value for name, value in old_bake.items())
    assert obj.mode == 'EDIT'
    assert bpy.context.view_layer.objects.active == obj
    assert tuple(bpy.context.selected_objects) == old_selection
    assert links(tree) == old_links
    assert len(tree.nodes) == old_nodes
    assert tree.nodes.active == color_node
    assert len(bpy.data.images) == old_images


try:
    main()
except Exception:
    traceback.print_exc()
    sys.exit(1)
