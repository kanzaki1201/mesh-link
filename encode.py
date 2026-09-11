import numpy as np


def to_link_positions(positions):
    positions = np.asarray(positions, dtype='<f4')
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError('positions must have shape (vertex_count, 3)')
    if not np.isfinite(positions).all():
        raise ValueError('positions must be finite')
    result = positions[:, [0, 2, 1]].copy()
    result[:, 2] *= -1
    return result


def to_link_matrix(world_matrix):
    matrix = np.asarray(world_matrix, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError('world_matrix must be a finite 4x4 matrix')
    basis = np.array([[1, 0, 0, 0], [0, 0, 1, 0],
                      [0, -1, 0, 0], [0, 0, 0, 1]])
    return (basis @ matrix @ basis.T).ravel(order='F').tolist()


def _faces(corners, starts, totals):
    corners = np.asarray(corners, dtype='<i4')
    starts = np.asarray(starts, dtype=np.int64)
    totals = np.asarray(totals, dtype=np.int64)
    if corners.ndim != 1 or starts.ndim != 1 or starts.shape != totals.shape:
        raise ValueError('invalid face array shapes')
    result = np.full((len(starts), 4), -1, dtype='<i4')
    for face, (start, total) in enumerate(zip(starts, totals)):
        if total not in (3, 4):
            raise ValueError(f'face {face} has {total} corners; expected 3 or 4')
        if start < 0 or start + total > len(corners):
            raise ValueError(f'face {face} corners are out of range')
        result[face, :total] = corners[start:start + total]
    return result


def _material_group(name, material_names, face_material, face_count):
    if len(material_names) == 0:
        return [name], np.zeros(face_count, dtype='<i4')
    names = [slot or f'Slot {index + 1}' for index, slot in enumerate(material_names)]
    indices = np.asarray(face_material, dtype='<i4')
    if indices.shape != (face_count,):
        raise ValueError('face_material must have one index per face')
    if np.any(indices < 0) or np.any(indices >= len(names)):
        raise ValueError('face_material index is out of range')
    return names, indices


def _uv_group(uv_corners, corner_count, loop_starts, loop_totals):
    uv = np.asarray(uv_corners, dtype='<f4')
    if uv.shape != (corner_count, 2) or not np.isfinite(uv).all():
        raise ValueError('UV coordinates must be finite pairs per corner')
    uv = uv.copy()
    uv[:, 1] = 1 - uv[:, 1]
    texcoords, indices = np.unique(uv, axis=0, return_inverse=True)
    faces = _faces(indices, loop_starts, loop_totals)
    return texcoords.astype('<f4').tobytes(), faces.tobytes(), len(texcoords)


def encode_mesh_full(*, mesh_id, geometry_id, name, world_matrix,
                     smooth_shading, positions, corner_verts, loop_starts,
                     loop_totals, uv_corners_or_None, material_names, face_material):
    positions = to_link_positions(positions)
    faces = _faces(corner_verts, loop_starts, loop_totals)
    active = np.asarray(corner_verts)
    if np.any(active < 0) or np.any(active >= len(positions)):
        raise ValueError('face vertex index is out of range')
    names, materials = _material_group(name, material_names, face_material, len(faces))
    position_bytes = positions.tobytes()
    parts = [position_bytes, faces.tobytes(), materials.tobytes()]
    header = dict(type='mesh_full', mesh_id=mesh_id, geometry_id=geometry_id,
                  name=name, vertex_count=len(positions), face_count=len(faces),
                  coordinate_system='nomad_y_up', world_matrix=to_link_matrix(world_matrix),
                  smooth_shading=bool(smooth_shading), live_sync=True, replace_topology=True,
                  position_offset=0, position_format='float32x3',
                  face_offset=len(position_bytes), face_format='int32x4',
                  material_names=names, face_material_offset=len(position_bytes) + faces.nbytes)
    if uv_corners_or_None is not None:
        uv_bytes, uv_faces, count = _uv_group(
            uv_corners_or_None, len(corner_verts), loop_starts, loop_totals)
        offset = sum(map(len, parts))
        header.update(texcoord_count=count, texcoord_offset=offset,
                      texcoord_format='float32x2', face_uv_offset=offset + len(uv_bytes))
        parts.extend([uv_bytes, uv_faces])
    payload = b''.join(parts)
    header['binary_size'] = len(payload)
    return header, payload


def encode_mesh_delta(*, mesh_id, old_positions, new_positions):
    old = to_link_positions(old_positions)
    new = to_link_positions(new_positions)
    if old.shape != new.shape:
        raise ValueError('delta requires equal vertex counts')
    indices = np.flatnonzero(np.any(old != new, axis=1)).astype('<u4')
    payload = indices.tobytes() + new[indices].tobytes()
    header = dict(type='mesh_delta', mesh_id=mesh_id, count=len(indices),
                  vertex_count=len(new), binary_size=len(payload), live_sync=True,
                  index_offset=0, index_format='uint32', position_offset=indices.nbytes,
                  position_format='float32x3')
    return header, payload


def topology_equal(old_positions, new_positions, old_corner_verts, new_corner_verts,
                   old_loop_starts, new_loop_starts, old_loop_totals, new_loop_totals):
    pairs = ((old_corner_verts, new_corner_verts),
             (old_loop_starts, new_loop_starts), (old_loop_totals, new_loop_totals))
    return (np.shape(old_positions) == np.shape(new_positions)
            and all(np.array_equal(old, new) for old, new in pairs))
