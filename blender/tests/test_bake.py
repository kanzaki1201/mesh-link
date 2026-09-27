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
