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
    module = importlib.import_module('unity_link.handlers')
    yield module
    del sys.modules['unity_link.handlers']


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
    assert first.data['unity_link_geometry_id'] == second.data['unity_link_geometry_id']


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
    assert link.messages[0][0]['link_id'] == second['unity_link_id']


def test_duplicate_ids_and_initially_hidden(setup):
    sync, link, view, first, second = setup(shared=False)
    first['unity_link_id'] = second['unity_link_id'] = 'a' * 32
    first.data['unity_link_geometry_id'] = second.data['unity_link_geometry_id'] = 'b' * 32
    second.visible = False
    sync.tick(view)
    assert first['unity_link_id'] != second['unity_link_id']
    assert first.data['unity_link_geometry_id'] != second.data['unity_link_geometry_id']
    assert kinds(link) == ['mesh_full']
    link.messages.clear()
    second.visible = True
    sync.tick(view)
    assert kinds(link) == ['mesh_full']


def test_overflow_and_failed_send_retry_full(setup):
    sync, link, view, first, second = setup()
    sync.tick(view)
    link.messages.clear()
    link.overflow.add(second['unity_link_id'])
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
    first['unity_link_id'] = 'z' * 32
    sync.tick(view)
    assert all(character in '0123456789abcdef' for character in first['unity_link_id'])

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
    link.overflow.add(first['unity_link_id'])
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
