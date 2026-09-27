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


def test_repeated_material_bakes_once_without_changing_polygons(bake, monkeypatch):
    material = types.SimpleNamespace(name='Paint', as_pointer=lambda: 1)
    slots = [types.SimpleNamespace(material=material) for _ in range(2)]
    polygons = [types.SimpleNamespace(material_index=index) for index in (0, 1)]
    obj = types.SimpleNamespace(material_slots=slots,
                                data=types.SimpleNamespace(polygons=polygons))
    calls = []

    def bake_slot(_obj, _material, _size):
        calls.append(1)
        return {'color': b'png'}

    monkeypatch.setattr(bake, '_bake_slot', bake_slot)
    result = bake._bake_object('mesh', obj, 64)
    assert calls == [1]
    assert [slot[3] for slot in result] == [{'color': b'png'}] * 2
    assert [polygon.material_index for polygon in polygons] == [0, 1]
