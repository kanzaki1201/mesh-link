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


def material_with_channels(*names, standard=()):
    shader = types.SimpleNamespace(type='BSDF_PRINCIPLED', inputs={
        name: types.SimpleNamespace(is_linked=True, links=[types.SimpleNamespace(
            from_socket=name, is_valid=True, is_muted=False)])
        for name in standard})
    surface = types.SimpleNamespace(is_linked=True,
                                    links=[types.SimpleNamespace(
                                        from_node=shader, is_valid=True, is_muted=False)])
    output = types.SimpleNamespace(type='OUTPUT_MATERIAL', is_active_output=True,
                                   inputs={'Surface': surface})
    groups = [types.SimpleNamespace(bl_idname='MeshLinkChannelNode',
        name=f'Channel {index}', channel=name,
        inputs={'Color': types.SimpleNamespace(is_linked=False)})
        for index, name in enumerate(names)]
    output.bl_idname = 'ShaderNodeOutputMaterial'
    return types.SimpleNamespace(name='Paint', use_nodes=True,
                                 node_tree=types.SimpleNamespace(nodes=[output, *groups]))


@pytest.mark.parametrize('names, message', [
    (('',), 'Paint: a Mesh Link Channel node has no name. Type a name on the node.'),
    (('Mask!',), 'Paint: Mesh Link Channel "Mask!": use letters, digits, spaces, '
                  'hyphens, or underscores (letters are lowercased)'),
    (('Shadow Mask', 'shadow--mask'),
     'Paint: Mesh Link Channel "Shadow Mask" and Mesh Link Channel "shadow--mask": '
     'use different Mesh Link Channel names; both clean to x_shadow_mask'),
])
def test_channel_name_errors(bake, names, message):
    with pytest.raises(ValueError) as info:
        bake.channels(material_with_channels(*names))
    assert str(info.value) == message


def test_channel_without_color_input_stops_bake(bake):
    material = material_with_channels('Shadow Mask')
    material.node_tree.nodes[1].inputs.clear()
    with pytest.raises(ValueError, match='Paint: Mesh Link Channel "Shadow Mask" has no Color input'):
        bake.channels(material)


def test_valid_unlinked_channel_has_no_bake(bake):
    assert bake.channels(material_with_channels('mask_2')) == []


def test_channel_bakes_with_unlinked_surface(bake):
    material = material_with_channels('mask')
    material.node_tree.nodes[0].inputs['Surface'].is_linked = False
    material.node_tree.nodes[1].inputs['Color'] = types.SimpleNamespace(
        is_linked=True, links=[types.SimpleNamespace(
            from_socket='source', is_valid=True, is_muted=False)])
    assert bake.channels(material)[0][:3] == ('x_mask', 'EMIT', 'source')


def test_material_without_nodes_has_no_channels(bake):
    material = material_with_channels('mask')
    material.use_nodes = False
    assert bake.channels(material) == []


def test_metalness_and_roughness_only_when_linked(bake):
    material = material_with_channels(standard=('Metallic', 'Roughness'))
    assert [entry[:3] for entry in bake.channels(material)] == [
        ('metalness', 'EMIT', 'Metallic'), ('roughness', 'EMIT', 'Roughness')]
    material.node_tree.nodes[0].inputs['Surface'].links[0].from_node.inputs[
        'Roughness'].is_linked = False
    assert [entry[0] for entry in bake.channels(material)] == ['metalness']
    material.node_tree.nodes[0].inputs['Surface'].links[0].from_node.inputs[
        'Metallic'].is_linked = False
    assert bake.channels(material) == []


@pytest.mark.parametrize('flag', ['is_muted', 'is_valid'])
def test_muted_or_invalid_links_are_unlinked(bake, flag):
    material = material_with_channels('Mask', standard=('Metallic',))
    channel = material.node_tree.nodes[1].inputs['Color']
    channel.is_linked = True
    channel.links = [types.SimpleNamespace(
        from_socket='mask', is_valid=True, is_muted=False)]
    surface = material.node_tree.nodes[0].inputs['Surface']
    metallic = surface.links[0].from_node.inputs['Metallic']
    for socket, expected in ((channel, ['metalness']), (metallic, ['x_mask']),
                             (surface, ['x_mask'])):
        link = socket.links[0]
        setattr(link, flag, flag == 'is_muted')
        assert [entry[0] for entry in bake.channels(material)] == expected
        setattr(link, flag, flag == 'is_valid')


@pytest.mark.parametrize('missing_color', [False, True])
def test_channel_errors_before_first_bake(bake, monkeypatch, missing_color):
    good = material_with_channels('Mask')
    bad = material_with_channels('Mask' if missing_color else 'Mask!')
    if missing_color:
        bad.node_tree.nodes[1].inputs.clear()
    objects = [('good', types.SimpleNamespace(material_slots=[types.SimpleNamespace(material=good)])),
               ('bad', types.SimpleNamespace(material_slots=[types.SimpleNamespace(material=bad)]))]
    calls = []
    monkeypatch.setattr(bake, 'render_enabled', lambda _obj, _layer: True)
    monkeypatch.setattr(bake, '_bake_object', lambda *_args: calls.append(1))
    bake.bpy.context = types.SimpleNamespace(
        scene=object(), view_layer=types.SimpleNamespace(update=lambda: None))
    with pytest.raises(ValueError, match='has no Color input' if missing_color else '"Mask!"'):
        bake.bake_objects(objects, 64)
    assert calls == []


def test_repeated_material_bakes_once(bake, monkeypatch):
    material = types.SimpleNamespace(name='Paint', as_pointer=lambda: 1)
    slots = [types.SimpleNamespace(material=material, name='Paint') for _ in range(2)]
    obj = types.SimpleNamespace(material_slots=slots)
    calls = []

    def bake_slot(_obj, _material, _size):
        calls.append(1)
        return {'color': b'png'}

    monkeypatch.setattr(bake, '_bake_slot', bake_slot)
    monkeypatch.setattr(bake, 'channels', lambda _material: [('color',)])
    result = bake._bake_object('mesh', obj, 64)
    assert calls == [1]
    assert [slot[3] for slot in result] == [{'color': b'png'}] * 2


def test_mode_failure_restores_settings_without_object_operators(bake):
    names = ('target', 'normal_space', 'normal_r', 'normal_g', 'normal_b',
             'use_selected_to_active', 'use_clear')
    settings = types.SimpleNamespace(**{name: name for name in names})
    scene = types.SimpleNamespace(render=types.SimpleNamespace(bake=settings, engine='EEVEE'),
                              cycles=types.SimpleNamespace(samples=8, device='GPU'))
    active = types.SimpleNamespace(mode='EDIT')
    objects = types.SimpleNamespace(active=active)
    bake.bpy.context = types.SimpleNamespace(scene=scene,
                                              view_layer=types.SimpleNamespace(objects=objects,
                                                                               update=lambda: None),
                                              selected_objects=(active,))

    def fail_mode(*_args, mode):
        raise RuntimeError('mode switch failed')

    def forbid_selection(*, action):
        raise AssertionError('selection operator called after mode failure')

    bake.bpy.ops = types.SimpleNamespace(object=types.SimpleNamespace(
        mode_set=fail_mode, select_all=forbid_selection))
    with pytest.raises(RuntimeError, match='mode switch failed'):
        bake.bake_objects([], 64)
    assert scene.render.engine == 'EEVEE' and scene.cycles.samples == 8
    assert scene.cycles.device == 'GPU'
    assert all(getattr(settings, name) == name for name in names)
    assert objects.active is active
