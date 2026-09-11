using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace Malloc.MeshLink
{
    internal sealed class MeshLinkFaceMaterials
    {
        private MeshLinkFaceMaterials(string[] names, int[] indices)
        {
            Names = names;
            Indices = indices;
        }

        internal string[] Names { get; }
        internal int[] Indices { get; }
        internal int SlotCount => Names?.Length ?? 1;

        internal static MeshLinkFaceMaterials Read(string json, byte[] binary, int faceCount)
        {
            var first = new Header();
            var second = new Header { material_names = new[] { "absent" }, face_material_offset = -1 };
            JsonUtility.FromJsonOverwrite(json, first);
            JsonUtility.FromJsonOverwrite(json, second);
            var hasNames = first.material_names != null || second.material_names == null;
            var hasOffset = first.face_material_offset == second.face_material_offset;
            if (hasNames != hasOffset)
                throw new InvalidDataException("The face material group is incomplete.");
            if (!hasNames)
                return new MeshLinkFaceMaterials(null, new int[faceCount]);
            if (first.material_names == null || (faceCount > 0 && first.material_names.Length == 0))
                throw new InvalidDataException("The face material names are empty.");

            MeshLinkSession.ValidateRange(first.face_material_offset, faceCount, 4, binary.Length);
            var indices = new int[faceCount];
            for (var face = 0; face < faceCount; face++)
            {
                var offset = checked((int)(first.face_material_offset + face * 4L));
                var slot = BinaryPrimitives.ReadInt32LittleEndian(binary.AsSpan(offset, 4));
                if (slot < 0 || slot >= first.material_names.Length)
                    throw new InvalidDataException("A face material index is out of range.");
                indices[face] = slot;
            }
            return new MeshLinkFaceMaterials(first.material_names, indices);
        }

        internal void ValidateReplacement(MeshLinkFaceMaterials current, bool replaceTopology)
        {
            if (current == null || replaceTopology) return;
            var sameNames = Names == null ? current.Names == null
                : current.Names != null && Names.SequenceEqual(current.Names);
            if (!sameNames || !Indices.SequenceEqual(current.Indices))
                throw new InvalidDataException("The full mesh cannot replace the face material group without replace_topology.");
        }

        internal void WriteSubmeshes(Mesh mesh, int[] faces, int[] triangles)
        {
            var slots = Enumerable.Range(0, SlotCount).Select(_ => new List<int>()).ToArray();
            var triangleOffset = 0;
            for (var face = 0; face < Indices.Length; face++)
            {
                var count = faces[face * 4 + 3] == -1 ? 3 : 6;
                for (var corner = 0; corner < count; corner++)
                    slots[Indices[face]].Add(triangles[triangleOffset++]);
            }
            mesh.subMeshCount = SlotCount;
            for (var slot = 0; slot < SlotCount; slot++)
                mesh.SetTriangles(slots[slot], slot);
        }

        [Serializable]
        internal class Header
        {
            public string[] material_names;
            public long face_material_offset;
        }
    }
}
