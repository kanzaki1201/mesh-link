import numpy as np
import pytest

from encode import encode_mesh_delta, encode_mesh_full, to_link_matrix, to_link_positions, topology_equal


def mesh(**changes):
    args = dict(mesh_id='object', geometry_id='geometry', name='Triangle',
                world_matrix=np.eye(4), smooth_shading=True,
                positions=np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32),
                corner_verts=np.array([0, 1, 2]), loop_starts=np.array([0]),
                loop_totals=np.array([3]), uv_corners_or_None=None,
                material_names=['Skin'], face_material=np.array([0]))
    args.update(changes)
    return encode_mesh_full(**args)


def test_axis_conversion():
    source = np.array([[1, 2, 3], [-2, -4, 5]])
    np.testing.assert_array_equal(to_link_positions(source), [[1, 3, -2], [-2, 5, 4]])
    np.testing.assert_array_equal(source, [[1, 2, 3], [-2, -4, 5]])


def test_column_major_matrix_basis_conversion():
    matrix = np.array([[2, 0, 0, 10], [0, 3, 0, 20], [0, 0, 4, 30], [0, 0, 0, 1]])
    assert to_link_matrix(matrix) == [2, 0, 0, 0, 0, 4, 0, 0, 0, 0, 3, 0, 10, 30, -20, 1]
    matrix = np.array([[0, -2, 0, 10], [3, 0, 0, 20], [0, 0, 4, 30], [0, 0, 0, 1]])
    point = np.array([[2, 3, 4]])
    converted = np.array(to_link_matrix(matrix)).reshape(4, 4, order='F')
    expected = to_link_positions((matrix @ np.append(point[0], 1))[:3].reshape(1, 3))[0]
    np.testing.assert_allclose((converted @ np.append(to_link_positions(point)[0], 1))[:3], expected)


def test_required_fields_and_binary_size():
    header, payload = mesh()
    required = {'mesh_id', 'geometry_id', 'name', 'vertex_count', 'face_count', 'binary_size',
                'coordinate_system', 'world_matrix', 'smooth_shading', 'live_sync',
                'position_offset', 'face_offset'}
    assert required <= header.keys()
    assert header['binary_size'] == len(payload) == 56
    assert header['coordinate_system'] == 'nomad_y_up'
    assert header['live_sync'] is True and header['replace_topology'] is True
    assert not any(key.startswith(('texcoord', 'face_uv')) for key in header)


def test_triangle_and_quad_indices():
    header, payload = mesh(corner_verts=[0, 1, 2, 2, 1, 0, 2], loop_starts=[0, 3],
                           loop_totals=[3, 4], face_material=[0, 0])
    faces = np.frombuffer(payload, '<i4', count=8, offset=header['face_offset']).reshape(-1, 4)
    np.testing.assert_array_equal(faces, [[0, 1, 2, -1], [2, 1, 0, 2]])


def test_ngon_error_names_face_index():
    with pytest.raises(ValueError, match='face 1 has 5 corners'):
        mesh(corner_verts=[0, 1, 2, 0, 1, 2, 1, 0], loop_starts=[0, 3],
             loop_totals=[3, 5], face_material=[0, 0])


def test_uv_welding_indices_and_v_flip():
    header, payload = mesh(uv_corners_or_None=[[0.25, 0.75], [0.8, 0.1], [0.25, 0.75]])
    assert header['texcoord_format'] == 'float32x2'
    assert header['texcoord_count'] == 2
    uv = np.frombuffer(payload, '<f4', count=4, offset=header['texcoord_offset']).reshape(-1, 2)
    indices = np.frombuffer(payload, '<i4', count=4, offset=header['face_uv_offset'])
    assert indices[0] == indices[2] and indices[3] == -1
    np.testing.assert_allclose(uv[indices[:3]], [[0.25, 0.25], [0.8, 0.9], [0.25, 0.25]])
    assert header['binary_size'] == len(payload)


@pytest.mark.parametrize('names,indices,expected', [
    (['Skin', ''], [1], ['Skin', 'Slot 2']), ([], [8], ['Triangle'])])
def test_face_material_group(names, indices, expected):
    header, payload = mesh(material_names=np.array(names), face_material=indices)
    assert header['material_names'] == expected
    material = np.frombuffer(payload, '<i4', count=1, offset=header['face_material_offset'])
    assert material[0] == (indices[0] if names else 0)


def test_delta_diff_absolute_positions():
    old = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.float32)
    new = old.copy()
    new[1] = [6, 7, 8]
    header, payload = encode_mesh_delta(mesh_id='object', old_positions=old, new_positions=new)
    assert header['count'] == 1 and header['vertex_count'] == 2
    assert header['binary_size'] == len(payload) == 16 and header['live_sync'] is True
    assert np.frombuffer(payload, '<u4', count=1)[0] == 1
    np.testing.assert_array_equal(np.frombuffer(payload, '<f4', offset=header['position_offset']), [6, 8, -7])
    empty, data = encode_mesh_delta(mesh_id='object', old_positions=new, new_positions=new)
    assert empty['count'] == 0 and data == b''
    with pytest.raises(ValueError, match='equal vertex counts'):
        encode_mesh_delta(mesh_id='object', old_positions=old, new_positions=new[:1])


def test_topology_guard():
    args = [np.zeros((3, 3)), np.ones((3, 3)), [0, 1, 2], [0, 1, 2], [0], [0], [3], [3]]
    assert topology_equal(*args)
    for index, value in [(1, np.zeros((4, 3))), (3, [0, 2, 1]), (5, [1]), (7, [4])]:
        changed = args.copy()
        changed[index] = value
        assert not topology_equal(*changed)


@pytest.mark.parametrize('changes', [
    {'positions': [[float('nan'), 0, 0]]},
    {'corner_verts': [0, 1, 9]},
    {'face_material': [1]},
    {'uv_corners_or_None': [[0, 0]]},
])
def test_invalid_mesh_arrays(changes):
    with pytest.raises(ValueError):
        mesh(**changes)
