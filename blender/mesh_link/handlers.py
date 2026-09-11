from uuid import uuid4

import bpy
import numpy as np
from bpy.app.handlers import persistent

from .encode import encode_mesh_delta, encode_mesh_full, to_link_matrix, topology_equal
from .session import Session

_session = None
_sync = None
_endpoint = ""


def _array(collection, attribute, width=1, dtype=np.int32):
    values = np.empty(len(collection) * width, dtype=dtype)
    collection.foreach_get(attribute, values)
    return values.reshape((-1, width)) if width > 1 else values


def _snapshot(obj):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    mesh = obj.data
    if len(mesh.vertices) > 2_000_000:
        raise ValueError('exceeds 2,000,000 vertices')
    vertices = obj.active_shape_key.data if obj.active_shape_key else mesh.vertices
    return dict(
        positions=_array(vertices, 'co', 3, np.float32),
        corner_verts=_array(mesh.loops, 'vertex_index'),
        loop_starts=_array(mesh.polygons, 'loop_start'),
        loop_totals=_array(mesh.polygons, 'loop_total'),
        uv_corners_or_None=_uv_corners(obj),
        material_names=[slot.name for slot in obj.material_slots],
        face_material=_array(mesh.polygons, 'material_index'),
        smooth_shading=bool(_array(mesh.polygons, 'use_smooth', dtype=bool).any()),
    )


def _uv_corners(obj):
    uv = obj.data.uv_layers.active
    if uv is None:
        return None
    if obj.mode != 'EDIT':
        return _array(uv.data, 'uv', 2, np.float32)
    import bmesh
    # Mesh UV collections are empty while their edit BMesh owns the data.
    mesh = bmesh.from_edit_mesh(obj.data)
    layer = mesh.loops.layers.uv.get(uv.name)
    return np.array([loop[layer].uv[:] for face in mesh.faces for loop in face.loops],
                    dtype=np.float32).reshape((-1, 2))


def _same_attributes(old, new):
    same_topology = topology_equal(
        old['positions'], new['positions'], old['corner_verts'], new['corner_verts'],
        old['loop_starts'], new['loop_starts'], old['loop_totals'], new['loop_totals'])
    fields = ('uv_corners_or_None', 'material_names', 'face_material', 'smooth_shading')
    return same_topology and all(np.array_equal(old[key], new[key]) for key in fields)


def _state(obj, view_layer):
    return dict(name=obj.name, visible=obj.visible_get(view_layer=view_layer),
                world_matrix=to_link_matrix(obj.matrix_world))


def _assign_id(block, key, seen):
    value = block.get(key)
    pointer = block.as_pointer()
    if (not isinstance(value, str) or len(value) != 32
            or any(char not in '0123456789abcdef' for char in value)
            or seen.get(value, pointer) != pointer):
        value = uuid4().hex
        block[key] = value
    seen[value] = pointer
    return value


class SceneSync:
    def __init__(self, session):
        self.session = session
        self.sent = {}
        self.geometry = {}
        self.dirty = set()
        self.force = set()
        self.pointers = set()

    def objects(self, view_layer):
        objects = [obj for obj in view_layer.objects if obj is not None and obj.type == 'MESH']
        objects.sort(key=lambda obj: obj.as_pointer() not in self.pointers)
        ids, geometry_ids, result = {}, {}, {}
        for obj in objects:
            mesh_id = _assign_id(obj, 'mesh_link_id', ids)
            _assign_id(obj.data, 'mesh_link_geometry_id', geometry_ids)
            result[mesh_id] = obj
        self.pointers = {obj.as_pointer() for obj in objects}
        return result

    def _delete(self, objects):
        for mesh_id in self.sent.keys() - objects.keys():
            if self.session.send(dict(type='object_delete', link_id=mesh_id, live_sync=True)):
                del self.sent[mesh_id]
                self.force.discard(mesh_id)

    def _groups(self, objects, view_layer):
        groups = {}
        shared = 'mesh_instance' in self.session.capabilities
        for mesh_id, obj in objects.items():
            if mesh_id not in self.sent and not obj.visible_get(view_layer=view_layer):
                continue
            geometry_id = obj.data['mesh_link_geometry_id'] if shared else mesh_id
            groups.setdefault(geometry_id, []).append((mesh_id, obj))
        return groups

    def tick(self, view_layer):
        objects = self.objects(view_layer)
        self._delete(objects)
        self.force.update(self.session.transport.take_overflow())
        for geometry_id, members in self._groups(objects, view_layer).items():
            self._group(geometry_id, members, view_layer)
        self.force.update(self.session.transport.take_overflow())
        used = {value[0] for value in self.sent.values()}
        self.geometry = {key: value for key, value in self.geometry.items() if key in used}

    def _group(self, geometry_id, members, view_layer):
        members.sort(key=lambda item: (not item[1].visible_get(view_layer=view_layer),
                                       item[0] not in self.sent))
        source = _geometry_source(members, view_layer, self.dirty)
        if source.visible_get(view_layer=view_layer):
            members.sort(key=lambda item: item[1] != source)
        mesh_id, obj = members[0]
        state = _state(obj, view_layer)
        if not state['visible']:
            for mid, member in members:
                self._object_message(mid, geometry_id, _state(member, view_layer))
            return
        old = self.geometry.get(geometry_id)
        force = any(mid in self.force for mid, _ in members)
        dirty = any(ob.as_pointer() in self.dirty for _, ob in members)
        if old is None or dirty or force:
            try:
                data = _snapshot(obj)
                if not self._geometry_message(mesh_id, geometry_id, obj, state, old, data, force):
                    return
            except ValueError as exc:
                raise ValueError(f'{obj.name}: {exc}') from exc
        else:
            self._object_message(mesh_id, geometry_id, state)
        for mesh_id, obj in members[1:]:
            self._object_message(mesh_id, geometry_id, _state(obj, view_layer))
        self.dirty.difference_update(ob.as_pointer() for _, ob in members)
        self.force.difference_update(mid for mid, _ in members)

    def _geometry_message(self, mesh_id, geometry_id, obj, state, old, data, force):
        attributes_equal = old is not None and _same_attributes(old, data)
        previous = self.sent.get(mesh_id)
        if attributes_equal and np.array_equal(old['positions'], data['positions']) and not force:
            return self._object_message(mesh_id, geometry_id, state)
        delta = (attributes_equal and not force and previous == (geometry_id, state)
                 and 'mesh_delta_receive' in self.session.capabilities)
        if delta:
            frame = encode_mesh_delta(mesh_id=mesh_id, old_positions=old['positions'],
                                      new_positions=data['positions'])
        else:
            frame = encode_mesh_full(mesh_id=mesh_id, geometry_id=geometry_id,
                                     name=obj.name, world_matrix=obj.matrix_world, **data)
            frame[0]['visible'] = state['visible']
        if not self.session.send(*frame):
            self.force.add(mesh_id)
            return False
        self.geometry[geometry_id] = data
        self.sent[mesh_id] = (geometry_id, state)
        self.force.discard(mesh_id)
        return True

    def _object_message(self, mesh_id, geometry_id, state):
        previous = self.sent.get(mesh_id)
        if previous == (geometry_id, state):
            return True
        if previous is None or previous[0] != geometry_id:
            header = dict(type='mesh_instance', mesh_id=mesh_id, geometry_id=geometry_id,
                          live_sync=True, **state)
        else:
            header = dict(type='object_state', link_id=mesh_id, live_sync=True, **state)
        if not self.session.send(header):
            return False
        self.sent[mesh_id] = (geometry_id, state)
        return True


def _geometry_source(members, view_layer, dirty):
    active = view_layer.objects.active
    for _, obj in members:
        if obj.mode == 'EDIT':
            return obj
    candidates = [obj for _, obj in members if obj.as_pointer() in dirty]
    candidates = candidates or [obj for _, obj in members]
    return next((obj for obj in candidates if obj == active), candidates[0])


def running():
    return _session is not None and _session.running


def status():
    return _session.status if _session else 'Disconnected'


def connect(context):
    global _session, _sync, _endpoint
    disconnect()
    host, port = context.scene.mesh_link_host, context.scene.mesh_link_port
    _endpoint = f'{host}:{port}'
    tokens = context.preferences.addons[__package__].preferences.pair_tokens
    entry = tokens.get(_endpoint)
    _session = Session(host, port, entry.token if entry else '')
    _sync = SceneSync(_session)
    _session.connect()


def disconnect():
    global _sync
    if _session:
        _session.close()
    _sync = None


def _save_token():
    tokens = bpy.context.preferences.addons[__package__].preferences.pair_tokens
    entry = tokens.get(_endpoint)
    if _session.pair_token and (entry is None or entry.token != _session.pair_token):
        if entry is None:
            entry = tokens.add()
            entry.name = _endpoint
        entry.token = _session.pair_token
        bpy.ops.wm.save_userpref()


@persistent
def depsgraph_update_post(scene, depsgraph):
    if not running():
        return
    updated = {update.id.original for update in depsgraph.updates if update.is_updated_geometry}
    material_changed = any(isinstance(update.id, bpy.types.Material) for update in depsgraph.updates)
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if material_changed or obj in updated or obj.data in updated or obj.data.shape_keys in updated:
            _sync.dirty.add(obj.as_pointer())


@persistent
def undo_post(_scene):
    if running():
        _sync.dirty.update(obj.as_pointer() for obj in bpy.context.view_layer.objects
                           if obj is not None and obj.type == 'MESH')


@persistent
def redo_post(scene):
    undo_post(scene)


def drain():
    if running():
        try:
            ready = _session.ready
            _session.drain()
            _save_token()
            if _session.ready:
                if not ready:
                    _sync.force.update(_sync.sent)
                _sync.tick(bpy.context.view_layer)
        except Exception as exc:
            _session.close(str(exc))
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    return 1 / 30


def register():
    for name in ('depsgraph_update_post', 'undo_post', 'redo_post'):
        getattr(bpy.app.handlers, name).append(globals()[name])
    bpy.app.timers.register(drain, first_interval=1 / 30, persistent=True)


def unregister():
    disconnect()
    if bpy.app.timers.is_registered(drain):
        bpy.app.timers.unregister(drain)
    for name in ('depsgraph_update_post', 'undo_post', 'redo_post'):
        getattr(bpy.app.handlers, name).remove(globals()[name])
