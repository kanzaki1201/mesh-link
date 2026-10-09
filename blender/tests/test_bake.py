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
FIXED_WITH_ALPHA = FIXED + ('Alpha',)
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


def material_with_channels(*custom, standard=(), fixed=(), link_custom=(), alpha=None):
    """Fake material: a Principled BSDF on Surface, plus an Output node when custom or fixed is given.

    alpha is None for a node with five fixed inputs, or True or False for a sixth, Alpha input.
    """
    shader = types.SimpleNamespace(type='BSDF_PRINCIPLED', bl_idname='ShaderNodeBsdfPrincipled',
                                   inputs={name: linked(name) for name in standard})
    surface = linked(None)
    surface.links[0].from_node = shader
    output = types.SimpleNamespace(type='OUTPUT_MATERIAL', bl_idname='ShaderNodeOutputMaterial',
                                   is_active_output=True, inputs={'Surface': surface})
    nodes = [output, shader]
    if custom or fixed or alpha is not None:
        inputs = [socket(name, name if name in fixed else None) for name in FIXED]
        if alpha is not None:
            inputs.append(socket('Alpha', 'Alpha' if alpha else None))
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


def test_principled_inputs_give_no_channels_without_node(bake):
    material = material_with_channels(standard=('Metallic', 'Base Color'))
    assert bake.channels(material) == []


def test_first_output_node_gives_channels(bake):
    material = material_with_channels(fixed=('Color',))
    material.node_tree.nodes.append(output_node(material_with_channels(fixed=('Normal',))))
    assert keys(bake, material) == ['color']


def test_no_active_material_output_gives_no_channels(bake):
    material = material_with_channels(fixed=('Color',))
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
    material = material_with_channels(fixed=('Metallic', 'Roughness'))
    node = output_node(material)
    assert [entry[:3] for entry in bake.channels(material)] == [
        ('metalness', 'EMIT', 'Metallic'), ('roughness', 'EMIT', 'Roughness')]
    node.inputs[4].is_linked = False
    assert keys(bake, material) == ['metalness']
    node.inputs[3].is_linked = False
    assert bake.channels(material) == []


@pytest.mark.parametrize('flag', ['is_muted', 'is_valid'])
def test_muted_or_invalid_links_are_unlinked(bake, flag):
    material = material_with_channels('Mask', fixed=('Roughness',), link_custom=('Mask',))
    node = output_node(material)
    for broken, expected in ((node.inputs[5], ['roughness']), (node.inputs[4], ['x_mask'])):
        setattr(broken.links[0], flag, flag == 'is_muted')
        assert keys(bake, material) == expected
        setattr(broken.links[0], flag, flag == 'is_valid')


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


@pytest.mark.parametrize('alpha', [True, False, None])
def test_alpha_is_not_a_channel_key(bake, alpha):
    material = material_with_channels(fixed=('Color',), alpha=alpha)
    assert keys(bake, material) == ['color']
    assert bake.channels(material_with_channels(alpha=True)) == []


def test_alpha_source_belongs_to_color_only_when_both_are_linked(bake):
    material = material_with_channels(fixed=('Color', 'Roughness'), alpha=True)
    assert bake.alpha_source(material) == 'Alpha'
    assert bake.channel_sources(material) == {'color': ['Color', 'Alpha'], 'roughness': ['Roughness']}
    unlinked = material_with_channels(fixed=('Color',), alpha=False)
    assert bake.alpha_source(unlinked) is None
    assert bake.channel_sources(unlinked) == {'color': ['Color']}
    no_color = material_with_channels(fixed=('Roughness',), alpha=True)
    assert bake.channel_sources(no_color) == {'roughness': ['Roughness']}


def test_node_without_alpha_input_has_no_alpha_source_and_valid_custom_inputs(bake):
    material = material_with_channels('Mask', fixed=('Color',), link_custom=('Mask',))
    assert bake.alpha_source(material) is None
    assert keys(bake, material) == ['color', 'x_mask']
    assert bake.fixed_count(FIXED) == 5 and bake.fixed_count(FIXED_WITH_ALPHA) == 6
    assert bake.fixed_count(FIXED + ('Mask',)) == 5


def test_custom_inputs_start_after_alpha(bake):
    material = material_with_channels('Mask', fixed=('Color',), link_custom=('Mask',), alpha=True)
    assert keys(bake, material) == ['color', 'x_mask']
    del output_node(material).inputs[3]
    with pytest.raises(ValueError, match='input 4 must be "Metallic"'):
        bake.channels(material)


# -- output node setup


class FakeSocket(types.SimpleNamespace):
    def as_pointer(self):
        return id(self)


class FakeInputs(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next(item for item in self if item.name == key)
        return super().__getitem__(key)


class FakeNodes(list):
    def new(self, idname):
        node = types.SimpleNamespace(
            bl_idname=idname, type='CUSTOM', name=idname,
            location=types.SimpleNamespace(x=0, y=0), width=140,
            inputs=FakeInputs(FakeSocket(name=name, identifier=name, is_linked=False, links=[])
                              for name in FIXED_WITH_ALPHA))
        self.append(node)
        return node


class FakeLinks(list):
    def new(self, source, target):
        link = types.SimpleNamespace(from_socket=source, to_socket=target, is_valid=True,
                                     is_muted=False)
        target.is_linked = True
        target.links = [link]
        self.append(link)
        return link


def setup_material(**principled):
    shader = types.SimpleNamespace(
        type='BSDF_PRINCIPLED', bl_idname='ShaderNodeBsdfPrincipled', name='Principled BSDF',
        location=types.SimpleNamespace(x=10, y=20), width=240,
        inputs={name: linked(value) for name, value in principled.items()})
    nodes = FakeNodes([shader])
    material = types.SimpleNamespace(name='Paint', use_nodes=True,
                                     node_tree=types.SimpleNamespace(nodes=nodes, links=FakeLinks()))
    return material, shader


def test_setup_links_principled_inputs_and_places_node_near_it(bake):
    material, shader = setup_material(**{'Base Color': 'a', 'Emission Color': 'b', 'Alpha': 'c'})
    other = types.SimpleNamespace(type='BSDF_PRINCIPLED', bl_idname='ShaderNodeBsdfPrincipled',
                                  inputs={'Base Color': linked('late')})
    material.node_tree.nodes.append(other)
    assert bake.ensure_output_node(material)
    node = next(item for item in material.node_tree.nodes if item.bl_idname == 'MeshLinkOutputNode')
    assert [(link.from_socket, link.to_socket.name) for link in material.node_tree.links] == [
        ('a', 'Color'), ('b', 'Emission'), ('c', 'Alpha')]
    assert abs(node.location[0] - shader.location.x) < 400
    assert abs(node.location[1] - shader.location.y) < 400
    assert not bake.ensure_output_node(material)
    assert len(material.node_tree.links) == 3


def test_setup_skips_muted_links_and_needs_principled_and_nodes(bake):
    material, shader = setup_material(**{'Base Color': 'a', 'Normal': 'n'})
    shader.inputs['Normal'].links[0].is_muted = True
    assert bake.ensure_output_node(material)
    assert [link.to_socket.name for link in material.node_tree.links] == ['Color']
    bare, shader = setup_material()
    bare.node_tree.nodes.remove(shader)
    assert not bake.ensure_output_node(bare)
    assert not any(node.bl_idname == 'MeshLinkOutputNode' for node in bare.node_tree.nodes)
    bare.use_nodes = False
    assert not bake.ensure_output_node(bare) and not bake.ensure_output_node(None)


def test_setup_runs_before_name_checks_and_channels_stays_read_only(bake):
    material, _ = setup_material(**{'Base Color': 'a'})
    assert bake.channels(material) == [] and not any(
        node.bl_idname == 'MeshLinkOutputNode' for node in material.node_tree.nodes)
    bake.bpy.context = types.SimpleNamespace(
        scene=object(), view_layer=types.SimpleNamespace(update=lambda: None))
    obj = types.SimpleNamespace(material_slots=[types.SimpleNamespace(material=material)])
    bake._validate_channels([('mesh', obj)], None)
    assert any(node.bl_idname == 'MeshLinkOutputNode' for node in material.node_tree.nodes)


# -- channel signatures


class FakeImage:
    def __init__(self, name):
        self.name = name


class Graph:
    """Fake shader tree: image -> mix -> Mesh Link Output, with real link bookkeeping."""

    def __init__(self):
        self.nodes, self.links = [], []

    def as_pointer(self):
        return id(self)

    def node(self, name, idname, inputs=(), outputs=('Color',), type_='CUSTOM', **settings):
        node = types.SimpleNamespace(name=name, bl_idname=idname, type=type_, **settings)
        node.as_pointer = lambda: id(node)
        node.inputs = [self.socket(node, ident, value) for ident, value in inputs]
        node.outputs = [self.socket(node, ident, None) for ident in outputs]
        self.nodes.append(node)
        return node

    @staticmethod
    def socket(node, identifier, value):
        result = FakeSocket(node=node, identifier=identifier, name=identifier, is_linked=False,
                            links=[], default_value=value)
        return result

    def link(self, source, target):
        link = types.SimpleNamespace(from_socket=source, to_socket=target, from_node=source.node,
                                     is_valid=True, is_muted=False)
        target.is_linked = True
        target.links = [link]
        self.links.append(link)
        return link


def graph_material(alpha=False):
    tree = Graph()
    image = tree.node('Image', 'ShaderNodeTexImage', outputs=('Color', 'Alpha'), type_='TEX_IMAGE',
                      image=FakeImage('paint.png'))
    mix = tree.node('Mix', 'ShaderNodeMix', inputs=(('A', (0.5, 0.5, 0.5, 1.0)), ('B', (1.0, 0.0, 0.0, 1.0))),
                    outputs=('Result',), type_='MIX')
    tree.link(image.outputs[0], mix.inputs[0])
    node = tree.node('Mesh Link Output', 'MeshLinkOutputNode',
                     inputs=[(name, None) for name in FIXED_WITH_ALPHA], type_='CUSTOM')
    output = tree.node('Material Output', 'ShaderNodeOutputMaterial', inputs=(('Surface', None),),
                       type_='OUTPUT_MATERIAL', is_active_output=True)
    tree.link(mix.outputs[0], node.inputs[0])
    if alpha:
        tree.link(image.outputs[1], node.inputs[5])
    material = types.SimpleNamespace(name='Paint', use_nodes=True, node_tree=tree)
    return material, mix, image, node, output


def test_signature_is_stable_and_follows_values(bake):
    material, mix, image, *_ = graph_material()
    first = bake.signatures(material)
    assert first.keys() == {'color'}
    assert bake.signatures(material) == first
    mix.inputs[1].default_value = (0.0, 1.0, 0.0, 1.0)
    changed = bake.signatures(material)
    assert changed != first
    mix.inputs[1].default_value = (1.0, 0.0, 0.0, 1.0)
    assert bake.signatures(material) == first
    image.image = FakeImage('other.png')
    assert bake.signatures(material) != first


def test_signature_follows_links_and_ignores_unrelated_nodes(bake):
    material, mix, image, node, _ = graph_material()
    first = bake.signatures(material)
    material.node_tree.node('Unrelated', 'ShaderNodeRGB', outputs=('Color',))
    assert bake.signatures(material) == first
    link = material.node_tree.links[0]
    link.is_muted = True
    mix.inputs[0].is_linked = False
    assert bake.signatures(material) != first
    link.is_muted = False
    mix.inputs[0].is_linked = True
    assert bake.signatures(material) == first
    material.node_tree.links[0] = types.SimpleNamespace(
        from_socket=image.outputs[1], to_socket=mix.inputs[0], from_node=image,
        is_valid=True, is_muted=False)
    mix.inputs[0].links = [material.node_tree.links[0]]
    assert bake.signatures(material) != first


def test_color_signature_includes_alpha_graph(bake):
    material, _, image, node, _ = graph_material(alpha=True)
    first = bake.signatures(material)
    other = material.node_tree.node('Other', 'ShaderNodeValue', outputs=('Value',))
    material.node_tree.links.remove(material.node_tree.links[-1])
    material.node_tree.link(other.outputs[0], node.inputs[5])
    assert bake.signatures(material) != first
    plain, *_ = graph_material()
    assert bake.signatures(plain) != first


def test_signature_walks_groups_at_any_depth(bake):
    material, mix, image, node, _ = graph_material()
    inner = Graph()
    group_input = inner.node('Group Input', 'NodeGroupInput', outputs=('Color',), type_='GROUP_INPUT')
    group_output = inner.node('Group Output', 'NodeGroupOutput', inputs=(('Color', None),),
                              type_='GROUP_OUTPUT', is_active_output=True)
    invert = inner.node('Invert', 'ShaderNodeInvert', inputs=(('Fac', 1.0), ('Color', None)),
                        outputs=('Color',), type_='INVERT')
    inner.link(group_input.outputs[0], invert.inputs[1])
    inner.link(invert.outputs[0], group_output.inputs[0])
    group = material.node_tree.node('Group', 'ShaderNodeGroup', inputs=(('Color', None),),
                                    outputs=('Color',), type_='GROUP', node_tree=inner)
    material.node_tree.links.remove(material.node_tree.links[-1])
    material.node_tree.link(image.outputs[0], group.inputs[0])
    material.node_tree.link(group.outputs[0], node.inputs[0])
    first = bake.signatures(material)
    invert.inputs[0].default_value = 0.25
    assert bake.signatures(material) != first
    assert bake.source_images(material.node_tree, node.inputs[0].links[0].from_socket) == {image.image}
