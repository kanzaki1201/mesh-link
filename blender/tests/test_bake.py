import importlib
import sys
import types

import pytest


@pytest.fixture
def bake(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bpy', types.ModuleType('bpy'))
    module = importlib.import_module('mesh_link.bake')
    yield module
    sys.modules.pop('mesh_link.bake', None)


def material_with_labels(*labels):
    shader = types.SimpleNamespace(type='BSDF_PRINCIPLED', inputs={})
    surface = types.SimpleNamespace(is_linked=True,
                                    links=[types.SimpleNamespace(from_node=shader)])
    output = types.SimpleNamespace(type='OUTPUT_MATERIAL', is_active_output=True,
                                   inputs={'Surface': surface})
    groups = [types.SimpleNamespace(type='GROUP', node_tree=types.SimpleNamespace(
        name='Mesh Link Channel'), name=f'Channel {index}', label=label,
        inputs={'Color': types.SimpleNamespace(is_linked=False)})
        for index, label in enumerate(labels)]
    return types.SimpleNamespace(name='Paint', use_nodes=True,
                                 node_tree=types.SimpleNamespace(nodes=[output, *groups]))


@pytest.mark.parametrize('labels, node', [(('Bad',), 'Channel 0'),
                                         (('mask', 'mask'), 'Channel 1')])
def test_channel_label_error_names_material_and_node(bake, labels, node):
    with pytest.raises(ValueError) as info:
        bake.channels(material_with_labels(*labels))
    assert 'Paint' in str(info.value) and node in str(info.value)


def test_valid_unlinked_channel_has_no_bake(bake):
    assert bake.channels(material_with_labels('mask_2')) == []


def test_channel_bakes_with_unlinked_surface(bake):
    material = material_with_labels('mask')
    material.node_tree.nodes[0].inputs['Surface'].is_linked = False
    material.node_tree.nodes[1].inputs['Color'] = types.SimpleNamespace(
        is_linked=True, links=[types.SimpleNamespace(from_socket='source')])
    assert bake.channels(material)[0][:3] == ('x_mask', 'EMIT', 'source')


def test_material_without_nodes_has_no_channels(bake):
    material = material_with_labels('mask')
    material.use_nodes = False
    assert bake.channels(material) == []


def test_repeated_material_bakes_once(bake, monkeypatch):
    material = types.SimpleNamespace(name='Paint', as_pointer=lambda: 1)
    slots = [types.SimpleNamespace(material=material, name='Paint') for _ in range(2)]
    obj = types.SimpleNamespace(material_slots=slots)
    calls = []

    def bake_slot(_obj, _material, _size):
        calls.append(1)
        return {'color': b'png'}

    monkeypatch.setattr(bake, '_bake_slot', bake_slot)
    result = bake._bake_object('mesh', obj, 64)
    assert calls == [1]
    assert [slot[3] for slot in result] == [{'color': b'png'}] * 2


def test_mode_failure_restores_settings_without_object_operators(bake):
    names = ('target', 'normal_space', 'normal_r', 'normal_g', 'normal_b',
             'use_selected_to_active', 'use_clear')
    settings = types.SimpleNamespace(**{name: name for name in names})
    scene = types.SimpleNamespace(render=types.SimpleNamespace(bake=settings, engine='EEVEE'),
                                  cycles=types.SimpleNamespace(samples=8))
    active = types.SimpleNamespace(mode='EDIT')
    objects = types.SimpleNamespace(active=active)
    bake.bpy.context = types.SimpleNamespace(scene=scene,
                                              view_layer=types.SimpleNamespace(objects=objects),
                                              selected_objects=(active,))

    def fail_mode(*, mode):
        raise RuntimeError('mode switch failed')

    def forbid_selection(*, action):
        raise AssertionError('selection operator called after mode failure')

    bake.bpy.ops = types.SimpleNamespace(object=types.SimpleNamespace(
        mode_set=fail_mode, select_all=forbid_selection))
    with pytest.raises(RuntimeError, match='mode switch failed'):
        bake.bake_objects([], 64)
    assert scene.render.engine == 'EEVEE' and scene.cycles.samples == 8
    assert all(getattr(settings, name) == name for name in names)
    assert objects.active is active
