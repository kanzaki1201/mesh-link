import copy
import importlib
import sys
import types

import numpy as np
import pytest


@pytest.fixture
def handlers(monkeypatch):
    bpy = types.ModuleType('bpy')
    app = types.ModuleType('bpy.app')
    callbacks = types.ModuleType('bpy.app.handlers')
    callbacks.persistent = lambda callback: callback
    app.handlers = callbacks
    bpy.app = app
    for name, value in [('bpy', bpy), ('bpy.app', app),
                        ('bpy.app.handlers', callbacks)]:
        monkeypatch.setitem(sys.modules, name, value)
    module = importlib.import_module('mesh_link.handlers')
    yield module
    del sys.modules['mesh_link.handlers']


class Block(dict):
    def as_pointer(self):
        return id(self)


class Object(Block):
    def __init__(self, name, mesh=None):
        super().__init__()
        self.name = name
        self.data = mesh if mesh is not None else Block()
        self.type = 'MESH'
        self.mode = 'OBJECT'
        self.visible = True
        self.matrix_world = np.eye(4)
        self.snapshot = dict(
            positions=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            corner_verts=np.array([0, 1, 2]), loop_starts=np.array([0]),
            loop_totals=np.array([3]), uv_corners_or_None=None,
            material_names=['Material'], face_material=np.array([0]), smooth_shading=True,
        )

    def visible_get(self, *, view_layer):
        return self.visible


class Objects(list):
    active = None


class Link:
    def __init__(self, capabilities):
        self.capabilities = set(capabilities)
        self.messages = []
        self.overflow = set()
        self.transport = self
        self.reject = False

    def send(self, header, payload=b''):
        if self.reject:
            return False
        self.messages.append((header, payload))
        return True

    def take_overflow(self):
        result, self.overflow = self.overflow, set()
        return result


@pytest.fixture
def setup(handlers, monkeypatch):
    monkeypatch.setattr(handlers, '_snapshot', lambda obj: copy.deepcopy(obj.snapshot))

    def create(capabilities=('mesh_instance', 'mesh_delta_receive'), shared=True):
        first = Object('First')
        second = Object('Second', first.data if shared else None)
        link = Link(capabilities)
        sync = handlers.SceneSync(link)
        view = types.SimpleNamespace(objects=Objects([first, second]))
        return sync, link, view, first, second

    return create


def kinds(link):
    return [header['type'] for header, _ in link.messages]


def test_initial_full_instance_and_unchanged(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    assert kinds(link) == ['mesh_full', 'mesh_instance']
    full, payload = link.messages[0]
    instance, _ = link.messages[1]
    assert full['geometry_id'] == instance['geometry_id']
    assert full['mesh_id'] != instance['mesh_id']
    assert full['binary_size'] == len(payload)
    link.messages.clear()
    sync.tick(view)
    assert not link.messages
    sync.dirty.add(first.as_pointer())
    sync.tick(view)
    assert not link.messages


def test_no_instance_capability_sends_independent_geometry(setup):
    sync, link, view, first, second = setup(capabilities=())
    sync.tick(view)
    assert kinds(link) == ['mesh_full', 'mesh_full']
    assert link.messages[0][0]['geometry_id'] != link.messages[1][0]['geometry_id']
    assert first.data['mesh_link_geometry_id'] == second.data['mesh_link_geometry_id']


@pytest.mark.parametrize('capabilities, expected', [
    (('mesh_instance', 'mesh_delta_receive'), 'mesh_delta'),
    (('mesh_instance',), 'mesh_full'),
])
def test_shared_geometry_update(setup, capabilities, expected):
    sync, link, view, first, second = setup(capabilities=capabilities)
    sync.tick(view)
    link.messages.clear()
    first.snapshot['positions'][1, 0] = 4
    second.snapshot = first.snapshot
    sync.dirty.add(second.as_pointer())
    sync.tick(view)
    assert kinds(link) == [expected]
    header, payload = link.messages[0]
    assert header['binary_size'] == len(payload)


@pytest.mark.parametrize('field, value', [
    ('uv_corners_or_None', np.array([[0, 0], [1, 0], [0, 1]])),
    ('material_names', ['Other']),
    ('corner_verts', np.array([2, 1, 0])),
    ('smooth_shading', False),
])
def test_attribute_change_forces_full(setup, field, value):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    first.snapshot[field] = value
    sync.dirty.add(first.as_pointer())
    sync.tick(view)
    assert kinds(link) == ['mesh_full']


def test_object_state_and_unlink(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    second.name = 'Renamed'
    second.visible = False
    second.matrix_world[0, 3] = 2
    sync.tick(view)
    assert kinds(link) == ['object_state']
    state = link.messages[0][0]
    assert state['name'] == 'Renamed' and state['visible'] is False
    assert state['world_matrix'][12] == 2
    link.messages.clear()
    view.objects.remove(second)
    sync.tick(view)
    assert kinds(link) == ['object_delete']
    assert link.messages[0][0]['link_id'] == second['mesh_link_id']


def test_duplicate_ids_and_initially_hidden(setup):
    sync, link, view, first, second = setup(shared=False)
    first['mesh_link_id'] = second['mesh_link_id'] = 'a' * 32
    first.data['mesh_link_geometry_id'] = second.data['mesh_link_geometry_id'] = 'b' * 32
    second.visible = False
    sync.tick(view)
    assert first['mesh_link_id'] != second['mesh_link_id']
    assert first.data['mesh_link_geometry_id'] != second.data['mesh_link_geometry_id']
    assert kinds(link) == ['mesh_full']
    link.messages.clear()
    second.visible = True
    sync.tick(view)
    assert kinds(link) == ['mesh_full']


def test_overflow_and_failed_send_retry_full(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    link.overflow.add(second['mesh_link_id'])
    sync.tick(view)
    assert kinds(link) == ['mesh_full']
    link.messages.clear()
    first.snapshot['positions'][1, 0] = 4
    sync.dirty.add(first.as_pointer())
    link.reject = True
    sync.tick(view)
    assert not link.messages
    link.reject = False
    sync.tick(view)
    assert kinds(link) == ['mesh_full']


class Collection(list):
    def foreach_get(self, attribute, values):
        values[:] = np.asarray([getattr(item, attribute) for item in self]).reshape(-1)


def test_snapshot_reads_active_shape_key_in_edit_mode(handlers):
    obj = Object('Shape')
    obj.mode = 'EDIT'
    updates = []
    obj.update_from_editmode = lambda: updates.append(True)
    obj.data.vertices = Collection([types.SimpleNamespace(co=[0, 0, 0])])
    obj.data.loops = Collection()
    obj.data.polygons = Collection()
    obj.data.uv_layers = types.SimpleNamespace(active=None)
    obj.active_shape_key = types.SimpleNamespace(
        data=Collection([types.SimpleNamespace(co=[1, 2, 3])]))
    obj.material_slots = []
    snapshot = handlers._snapshot(obj)
    assert updates == [True]
    np.testing.assert_array_equal(snapshot['positions'], [[1, 2, 3]])


def test_vertex_limit_error_names_object_once(handlers):
    obj = Object('Big')
    obj.data.vertices = range(2_000_001)
    sync = handlers.SceneSync(Link(()))
    view = types.SimpleNamespace(objects=Objects([obj]))
    with pytest.raises(ValueError) as info:
        sync.tick(view)
    text = str(info.value)
    assert text.count('Big') == 1
    assert 'exceeds 2,000,000 vertices' in text


def test_invalid_existing_uuid_is_replaced(setup):
    sync, link, view, first, second = setup()
    first['mesh_link_id'] = 'z' * 32
    sync.tick(view)
    assert all(character in '0123456789abcdef' for character in first['mesh_link_id'])

def test_instance_material_edit_is_sent(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    second.snapshot['material_names'] = ['Second material']
    sync.dirty.add(second.as_pointer())
    sync.tick(view)
    assert any(header.get('material_names') == ['Second material']
               for header, _ in link.messages)


def test_edit_uv_reads_bmesh_layer(handlers, monkeypatch):
    obj = Object('UV')
    obj.mode = 'EDIT'
    obj.data.uv_layers = types.SimpleNamespace(active=types.SimpleNamespace(name='UVMap'))
    layer = object()
    loops = [{layer: types.SimpleNamespace(uv=uv)} for uv in [(0, 0), (1, 0), (0, 1)]]
    mesh = types.SimpleNamespace(
        faces=[types.SimpleNamespace(loops=loops)],
        loops=types.SimpleNamespace(layers=types.SimpleNamespace(uv={'UVMap': layer})))
    monkeypatch.setitem(sys.modules, 'bmesh', types.SimpleNamespace(from_edit_mesh=lambda data: mesh))
    np.testing.assert_array_equal(handlers._uv_corners(obj), [[0, 0], [1, 0], [0, 1]])


def test_stale_view_layer_entry_sends_delete(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    view.objects[1] = None
    sync.tick(view)
    assert kinds(link) == ['object_delete']


def test_hidden_overflow_requires_full_when_visible(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    first.visible = second.visible = False
    link.overflow.add(first['mesh_link_id'])
    sync.tick(view)
    sync.tick(view)
    link.messages.clear()
    first.visible = True
    sync.tick(view)
    assert kinds(link) == ['mesh_full']


@pytest.mark.parametrize('mode', ['OBJECT', 'EDIT'])
def test_active_instance_geometry_is_sent(setup, mode):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    second.snapshot['positions'][1, 0] = 8
    second.mode = mode
    view.objects.active = second
    sync.dirty.add(second.as_pointer())
    sync.tick(view)
    assert kinds(link) == ['mesh_delta']
    header, payload = link.messages[0]
    assert header['vertex_count'] == 3
    assert np.frombuffer(payload, dtype='<f4', offset=4)[0] == 8


def test_hidden_geometry_waits_until_visible(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    first.visible = second.visible = False
    first.snapshot['positions'][1, 0] = 8
    sync.dirty.add(first.as_pointer())
    sync.tick(view)
    assert kinds(link) == ['object_state', 'object_state']
    assert first.as_pointer() in sync.dirty
    link.messages.clear()
    first.visible = True
    sync.tick(view)
    assert kinds(link) == ['mesh_full']


def test_bake_skips_hidden_sent_objects_and_counts_them(handlers, monkeypatch):
    from mesh_link import bake

    def obj(name, visible=True, hide_render=False):
        return types.SimpleNamespace(name=name, visible_get=lambda **_kw: visible,
                                     hide_render=hide_render, material_slots=[])

    def layer(names=(), children=(), exclude=False, hide_render=False):
        return types.SimpleNamespace(exclude=exclude, children=children,
                                     collection=types.SimpleNamespace(objects=names,
                                                                      hide_render=hide_render))

    visible, hidden, render_off = obj('Visible'), obj('Hidden', False), obj('Render off', hide_render=True)
    parent_off, excluded = obj('Parent off'), obj('Excluded')
    view_layer = types.SimpleNamespace(layer_collection=layer(
        ('Visible', 'Hidden', 'Render off'),
        (layer(children=(layer(('Parent off',)),), hide_render=True),
         layer(('Excluded',), exclude=True))))
    handlers._sync = types.SimpleNamespace(
        objects=lambda _view: {'a': visible, 'b': hidden, 'c': render_off,
                               'd': parent_off, 'e': excluded},
        sent={'a', 'b', 'c', 'd', 'e'})
    handlers._session = types.SimpleNamespace(
        running=True, ready=True, capabilities={'material', 'texture'},
        send_bakes=lambda _slots: 1, status='Connected')
    received = []
    monkeypatch.setattr(bake, 'bake_objects',
                        lambda objects, _size: received.extend(objects) or [])
    preferences = types.SimpleNamespace(addons={
        'mesh_link': types.SimpleNamespace(preferences=types.SimpleNamespace(texture_size='512'))})
    context = types.SimpleNamespace(view_layer=view_layer, preferences=preferences)
    handlers.bake_and_send(context)
    assert received == [('a', visible)]
    assert handlers._session.status == 'Sent 1 textures, skipped 4 hidden objects'


def test_auto_bake_waits_for_pause_and_keeps_link_on_error(handlers, monkeypatch):
    from mesh_link import bake

    material = types.SimpleNamespace(as_pointer=lambda: 7)
    obj = types.SimpleNamespace(material_slots=[types.SimpleNamespace(material=material)])
    scene = types.SimpleNamespace(mesh_link_auto_bake=True)
    window = types.SimpleNamespace(modal_operators=['stroke'])
    context = types.SimpleNamespace(
        scene=scene, view_layer=object(),
        window_manager=types.SimpleNamespace(windows=[window]),
        preferences=types.SimpleNamespace(addons={'mesh_link': types.SimpleNamespace(
            preferences=types.SimpleNamespace(texture_size='512'))}))
    session = types.SimpleNamespace(running=True, ready=True, status='Connected',
                                    capabilities={'material', 'texture'})
    handlers._session = session
    monkeypatch.setattr(handlers, '_sent_objects', lambda _view: [('mesh', obj)])
    monkeypatch.setattr(handlers, '_texture_filter', lambda *_args: {7: {'color'}})
    monkeypatch.setattr(bake, 'render_enabled', lambda *_args: True)
    prepared, stored = [], []
    monkeypatch.setattr(bake, 'ensure_outputs', prepared.append)
    monkeypatch.setattr(handlers, '_store_signatures', lambda *args: stored.append(args))
    calls = []

    def bake_objects(objects, size, selected):
        assert not handlers._dirty_images and not handlers._dirty_materials
        calls.append((objects, size, selected))
        return [('mesh', 0, 'Paint', {'color': b'png'}, {'color'})]

    monkeypatch.setattr(bake, 'bake_objects', bake_objects)
    session.send_bakes = lambda slots, auto: len(slots[0][3])
    monkeypatch.setattr(handlers.time, 'monotonic', lambda: 10.0)
    handlers._dirty_images.add(2)
    handlers._texture_changed_at = 9.0
    handlers._auto_bake(context)
    assert not calls
    handlers._texture_changed_at = 7.0
    handlers._auto_bake(context)
    assert not calls
    window.modal_operators.clear()
    handlers._auto_bake(context)
    assert calls == [([('mesh', obj)], 512, {7: {'color'}})]
    assert prepared == [[('mesh', obj)]]
    assert stored == [([('mesh', obj)], {7: {'color'}})]
    assert session.status == 'Auto baked 1 textures'
    handlers._dirty_materials.add(7)
    session.send_bakes = lambda _slots, auto: (_ for _ in ()).throw(ValueError('queue limit'))
    handlers._auto_bake(context)
    assert len(stored) == 1
    assert not scene.mesh_link_auto_bake
    assert session.status == 'queue limit' and session.running


def resolution_fixture(monkeypatch):
    from mesh_link import bake

    image = types.SimpleNamespace(as_pointer=lambda: 1)
    paint = types.SimpleNamespace(as_pointer=lambda: 2, node_tree='paint')
    settings = types.SimpleNamespace(as_pointer=lambda: 3, node_tree='settings')
    slots = [types.SimpleNamespace(material=material) for material in (paint, settings)]
    objects = [('mesh', types.SimpleNamespace(material_slots=slots))]
    state = {'color': 'c1', 'roughness': 'r1'}
    monkeypatch.setattr(bake, 'channel_sources', lambda material: {
        'color': [(material.node_tree, 'color')], 'roughness': [(material.node_tree, 'roughness')]})
    monkeypatch.setattr(bake, 'source_images',
                        lambda _tree, source: [image] if source == ('paint', 'color') else [])
    monkeypatch.setattr(bake, 'signatures', lambda _material: dict(state))
    return objects, state


def test_image_dirt_selects_reachable_channel_before_material_dirt(handlers, monkeypatch):
    objects, _ = resolution_fixture(monkeypatch)
    assert handlers._texture_filter(objects, {1}, {3}) == {2: {'color'}}


def test_material_dirt_selects_channels_with_changed_or_missing_signature(handlers, monkeypatch):
    objects, state = resolution_fixture(monkeypatch)
    assert handlers._texture_filter(objects, set(), {3}) == {3: {'color', 'roughness'}}
    handlers._signatures.update({(3, 'color'): 'c1', (3, 'roughness'): 'r1'})
    assert handlers._texture_filter(objects, set(), {3}) == {}
    state['roughness'] = 'r2'
    assert handlers._texture_filter(objects, set(), {3}) == {3: {'roughness'}}
    assert handlers._texture_filter(objects, set(), {2}) == {2: {'color', 'roughness'}}
    assert handlers._texture_filter(objects, set(), set()) == {}
    assert handlers._texture_filter(objects, {1}, {3}) == {2: {'color'}}


def test_signatures_are_stored_after_a_bake_and_cleared_on_disconnect(handlers, monkeypatch):
    objects, state = resolution_fixture(monkeypatch)
    handlers._store_signatures(objects, {3: {'color'}})
    assert handlers._signatures == {(3, 'color'): 'c1'}
    handlers._store_signatures(objects)
    assert handlers._signatures == {(pointer, key): value
                                    for pointer in (2, 3) for key, value in state.items()}
    handlers.disconnect()
    assert handlers._signatures == {}


def test_texture_dirt_ignores_bake_updates(handlers, monkeypatch):
    from mesh_link import bake

    class Image:
        original = property(lambda self: self)

        def as_pointer(self):
            return 1

    class Material:
        original = property(lambda self: self)

        def as_pointer(self):
            return 2

    handlers.bpy.types = types.SimpleNamespace(Image=Image, Material=Material)
    monkeypatch.setattr(handlers.time, 'monotonic', lambda: 5.0)
    scene = types.SimpleNamespace(mesh_link_auto_bake=True)
    updates = types.SimpleNamespace(updates=[types.SimpleNamespace(id=Image()),
                                             types.SimpleNamespace(id=Material())])
    handlers.clear_texture_dirt()
    monkeypatch.setattr(bake, 'baking', False)
    handlers._collect_texture_dirt(scene, updates)
    assert handlers._dirty_images == {1} and handlers._dirty_materials == {2}
    assert handlers._texture_changed_at == 5.0
    handlers.clear_texture_dirt()
    monkeypatch.setattr(bake, 'baking', True)
    handlers._collect_texture_dirt(scene, updates)
    assert not handlers._dirty_images and not handlers._dirty_materials
