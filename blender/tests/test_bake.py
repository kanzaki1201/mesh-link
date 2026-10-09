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


FIXED = ('Color', 'Emission', 'Normal', 'Metallic', 'Roughness')
SIDEBAR = 'Rename it in the Node tab of the sidebar.'
LIMIT = 'use letters, digits, spaces, hyphens, or underscores (letters are lowercased)'


def linked(source):
    return types.SimpleNamespace(is_linked=True, links=[types.SimpleNamespace(
        from_socket=source, is_valid=True, is_muted=False)])


def socket(name, source=None):
    result = linked(source) if source is not None else types.SimpleNamespace(
        is_linked=False, links=[])
    result.name = name
    return result


def material_with_channels(*custom, standard=(), fixed=(), link_custom=()):
    """Fake material: a Principled BSDF on Surface, plus an Output node when custom or fixed is given."""
    shader = types.SimpleNamespace(type='BSDF_PRINCIPLED', bl_idname='ShaderNodeBsdfPrincipled',
                                   inputs={name: linked(name) for name in standard})
    surface = linked(None)
    surface.links[0].from_node = shader
    output = types.SimpleNamespace(type='OUTPUT_MATERIAL', bl_idname='ShaderNodeOutputMaterial',
                                   is_active_output=True, inputs={'Surface': surface})
    nodes = [output, shader]
    if custom or fixed:
        inputs = [socket(name, name if name in fixed else None) for name in FIXED]
        inputs += [socket(name, name if name in link_custom else None) for name in custom]
        nodes.insert(0, types.SimpleNamespace(type='CUSTOM', bl_idname='MeshLinkOutputNode',
                                              inputs=inputs))
    return types.SimpleNamespace(name='Paint', use_nodes=True,
                                 node_tree=types.SimpleNamespace(nodes=nodes))


def output_node(material):
    return material.node_tree.nodes[0]


def keys(bake, material):
    return [entry[0] for entry in bake.channels(material)]


@pytest.mark.parametrize('names, message', [
    (('',), f'Paint: Mesh Link Output input "": type a channel name. {SIDEBAR}'),
    (('Mask!',), f'Paint: Mesh Link Output input "Mask!": {LIMIT}. {SIDEBAR}'),
    (('Shadow Mask', 'shadow--mask'),
     'Paint: Mesh Link Output inputs "Shadow Mask" and "shadow--mask": '
     'use different names; both clean to x_shadow_mask'),
])
def test_custom_name_errors(bake, names, message):
    with pytest.raises(ValueError) as info:
        bake.channels(material_with_channels(*names))
    assert str(info.value) == message


@pytest.mark.parametrize('damage, position, name', [
    ('renamed', 2, 'Emission'), ('missing', 5, 'Roughness')])
def test_fixed_input_errors(bake, damage, position, name):
    material = material_with_channels('Mask')
    inputs = output_node(material).inputs
    if damage == 'renamed':
        inputs[1].name = 'Emit'
    else:
        del inputs[4:]
    with pytest.raises(ValueError) as info:
        bake.channels(material)
    assert str(info.value) == (f'Paint: Mesh Link Output input {position} must be "{name}". '
                               'Rename it back, or add a new Mesh Link Output node.')


def test_fixed_inputs_map_to_channels(bake):
    material = material_with_channels(fixed=FIXED)
    assert [entry[:3] for entry in bake.channels(material)] == [
        ('color', 'EMIT', 'Color'), ('emissive', 'EMIT', 'Emission'),
        ('normal', 'NORMAL', 'Normal'), ('metalness', 'EMIT', 'Metallic'),
        ('roughness', 'EMIT', 'Roughness')]


def test_custom_input_bakes_as_emission_with_cleaned_key(bake):
    material = material_with_channels('Shadow  Mask-2', link_custom=('Shadow  Mask-2',))
    assert bake.channels(material)[0][:3] == ('x_shadow_mask_2', 'EMIT', 'Shadow  Mask-2')


def test_output_node_replaces_principled_channels(bake):
    material = material_with_channels(standard=('Metallic', 'Base Color'), fixed=('Roughness',))
    assert keys(bake, material) == ['roughness']


def test_first_output_node_gives_channels(bake):
    material = material_with_channels(fixed=('Color',))
    material.node_tree.nodes.append(output_node(material_with_channels(fixed=('Normal',))))
    assert keys(bake, material) == ['color']


@pytest.mark.parametrize('surface', ['linked', 'unlinked', 'other node'])
def test_principled_channels_do_not_need_surface_from_it(bake, surface):
    material = material_with_channels(standard=('Base Color', 'Normal'))
    link_socket = material.node_tree.nodes[0].inputs['Surface']
    if surface == 'unlinked':
        link_socket.is_linked, link_socket.links = False, []
    if surface == 'other node':
        link_socket.links[0].from_node = types.SimpleNamespace(type='EMISSION')
    assert keys(bake, material) == ['color', 'normal']


def test_no_active_material_output_gives_no_channels(bake):
    material = material_with_channels(standard=('Base Color',))
    material.node_tree.nodes.pop(0)
    assert bake.channels(material) == []


def test_first_principled_gives_channels(bake):
    material = material_with_channels(standard=('Roughness',))
    material.node_tree.nodes.append(material_with_channels(standard=('Metallic',)).node_tree.nodes[1])
    assert keys(bake, material) == ['roughness']


def test_material_without_principled_or_output_node_has_no_channels(bake):
    material = material_with_channels()
    material.node_tree.nodes.pop(1)
    assert bake.channels(material) == []


def test_valid_unlinked_channel_has_no_bake(bake):
    assert bake.channels(material_with_channels('mask_2')) == []


def test_material_without_nodes_has_no_channels(bake):
    material = material_with_channels('mask', link_custom=('mask',))
    material.use_nodes = False
    assert bake.channels(material) == []
    material.use_nodes = True
    material.node_tree = None
    assert bake.channels(material) == []


def test_metalness_and_roughness_only_when_linked(bake):
    material = material_with_channels(standard=('Metallic', 'Roughness'))
    shader = material.node_tree.nodes[1]
    assert [entry[:3] for entry in bake.channels(material)] == [
        ('metalness', 'EMIT', 'Metallic'), ('roughness', 'EMIT', 'Roughness')]
    shader.inputs['Roughness'].is_linked = False
    assert keys(bake, material) == ['metalness']
    shader.inputs['Metallic'].is_linked = False
    assert bake.channels(material) == []


@pytest.mark.parametrize('flag', ['is_muted', 'is_valid'])
def test_muted_or_invalid_links_are_unlinked(bake, flag):
    material = material_with_channels('Mask', standard=('Metallic',), fixed=('Roughness',),
                                      link_custom=('Mask',))
    node = output_node(material)
    for broken, expected in ((node.inputs[5], ['roughness']), (node.inputs[4], ['x_mask'])):
        setattr(broken.links[0], flag, flag == 'is_muted')
        assert keys(bake, material) == expected
        setattr(broken.links[0], flag, flag == 'is_valid')
    fallback = material_with_channels(standard=('Metallic',))
    setattr(fallback.node_tree.nodes[1].inputs['Metallic'].links[0], flag, flag == 'is_muted')
    assert bake.channels(fallback) == []


@pytest.mark.parametrize('damage', ['name', 'fixed'])
def test_channel_errors_before_first_bake(bake, monkeypatch, damage):
    good = material_with_channels('Mask')
    bad = material_with_channels('Mask!' if damage == 'name' else 'Mask')
    if damage == 'fixed':
        del output_node(bad).inputs[1:]
    objects = [('good', types.SimpleNamespace(material_slots=[types.SimpleNamespace(material=good)])),
               ('bad', types.SimpleNamespace(material_slots=[types.SimpleNamespace(material=bad)]))]
    calls = []
    monkeypatch.setattr(bake, 'render_enabled', lambda _obj, _layer: True)
    monkeypatch.setattr(bake, '_bake_object', lambda *_args: calls.append(1))
    bake.bpy.context = types.SimpleNamespace(
        scene=object(), view_layer=types.SimpleNamespace(update=lambda: None))
    with pytest.raises(ValueError, match='must be "Emission"' if damage == 'fixed' else '"Mask!"'):
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
