using System;
using System.Buffers.Binary;
using System.IO;
using System.Text;
using NUnit.Framework;
using UnityEngine;

namespace Malloc.MeshLink.Tests
{
    public sealed class MeshLinkGeometryTests
    {
        [Test]
        public void FrameParsingUsesBigEndianSizes()
        {
            const string json = "{\"type\":\"ping\",\"binary_size\":3}";
            var binary = new byte[] { 4, 5, 6 };
            var encoded = MeshLinkSession.EncodeFrame(json, binary);

            Assert.That(BinaryPrimitives.ReadUInt32BigEndian(
                encoded.AsSpan(0, 4)), Is.EqualTo(Encoding.UTF8.GetByteCount(json)));
            Assert.That(BinaryPrimitives.ReadUInt32BigEndian(
                encoded.AsSpan(4, 4)), Is.EqualTo(3));

            var parsed = MeshLinkSession.ReadFrame(new MemoryStream(encoded));
            Assert.That(Encoding.UTF8.GetString(parsed.Json), Is.EqualTo(json));
            Assert.That(parsed.Binary, Is.EqualTo(binary));
        }

        [Test]
        public void FrameParsingRejectsOversizedJson()
        {
            var prefix = new byte[8];
            BinaryPrimitives.WriteUInt32BigEndian(
                prefix.AsSpan(0, 4),
                (uint)MeshLinkSession.JsonLimit + 1u);

            Assert.Throws<InvalidDataException>(() =>
                MeshLinkSession.ReadFrame(new MemoryStream(prefix)));
        }

        [Test]
        public void CoordinateAndMatrixConversionNegateZ()
        {
            Assert.That(
                MeshLinkSession.ConvertPosition(new Vector3(1f, 2f, 3f)),
                Is.EqualTo(new Vector3(1f, 2f, -3f)));

            var values = Identity();
            values[12] = 4f;
            values[13] = 5f;
            values[14] = 6f;
            var matrix = MeshLinkSession.ConvertMatrix(values);

            Assert.That((Vector3)matrix.GetColumn(3),
                Is.EqualTo(new Vector3(4f, 5f, -6f)));
        }

        [Test]
        public void TriangleAndQuadConversionReverseWinding()
        {
            var faces = new[]
            {
                0, 1, 2, -1,
                0, 1, 2, 3
            };

            Assert.That(
                MeshLinkSession.TriangulateFaces(faces, 4),
                Is.EqualTo(new[]
                {
                    0, 2, 1,
                    0, 2, 1,
                    0, 3, 2
                }));
        }

        [Test]
        public void IndexSelectionChangesAbove65535Vertices()
        {
            Assert.That(MeshLinkSession.ShouldUseUInt32(65535), Is.False);
            Assert.That(MeshLinkSession.ShouldUseUInt32(65536), Is.True);
        }

        [Test]
        public void DeltaApplicationChangesOnlyNamedVertices()
        {
            var vertices = new[]
            {
                Vector3.zero,
                Vector3.one,
                Vector3.right
            };
            var replacement = new Vector3(7f, 8f, 9f);

            MeshLinkSession.ApplyPositionDelta(
                vertices, new[] { 1 }, new[] { replacement });

            Assert.That(vertices[0], Is.EqualTo(Vector3.zero));
            Assert.That(vertices[1], Is.EqualTo(replacement));
            Assert.That(vertices[2], Is.EqualTo(Vector3.right));
        }

        [Test]
        public void ObjectStateHeadersPreserveAbsentFields()
        {
            var state = MeshLinkSession.ObjectStateDto.Initial("Original");
            state = MeshLinkSession.PrepareObjectState(
                state,
                "{\"type\":\"mesh_full\",\"name\":\"Full\",\"visible\":false}")
                .State;
            state = MeshLinkSession.PrepareObjectState(
                state,
                "{\"type\":\"mesh_instance\",\"name\":\"Instance\"}")
                .State;
            var moved = Identity();
            moved[12] = 3f;
            var deltaJson =
                "{\"type\":\"mesh_delta\",\"visible\":true," +
                "\"world_matrix\":" + FloatArrayJson(moved) + "}";
            var update = MeshLinkSession.PrepareObjectState(state, deltaJson);

            Assert.That(update.State.name, Is.EqualTo("Instance"));
            Assert.That(update.State.visible, Is.True);
            Assert.That(update.Transform.Position, Is.EqualTo(new Vector3(3f, 0f, 0f)));
        }

        [Test]
        public void ShearedStateKeepsLastValidValues()
        {
            var state = MeshLinkSession.ObjectStateDto.Initial("Original");
            var matrix = Identity();
            matrix[4] = 0.25f;
            var json = "{\"name\":\"Changed\",\"world_matrix\":" +
                FloatArrayJson(matrix) + "}";

            var update = MeshLinkSession.PrepareObjectState(state, json);

            Assert.That(update.IsSheared, Is.True);
            Assert.That(update.State.name, Is.EqualTo("Original"));
        }

        [Test]
        public void FaceMaterialGroupReadsNamesAndLittleEndianIndicesAtZeroOffset()
        {
            var group = MeshLinkFaceMaterials.Read(MaterialGroupJson(), Int32Bytes(1, 0), 2);
            Assert.That(group.Names, Is.EqualTo(new[] { "Body", "Detail" }));
            Assert.That(group.SlotCount, Is.EqualTo(2));
            Assert.That(group.Indices, Is.EqualTo(new[] { 1, 0 }));
        }

        [Test]
        public void AbsentFaceMaterialGroupUsesOneImplicitSlot()
        {
            var group = MeshLinkFaceMaterials.Read("{}", Array.Empty<byte>(), 2);
            Assert.That(group.Names, Is.Null);
            Assert.That(group.SlotCount, Is.EqualTo(1));
            Assert.That(group.Indices, Is.EqualTo(new[] { 0, 0 }));
        }

        [TestCase("{\"material_names\":[\"Body\"]}")]
        [TestCase("{\"face_material_offset\":0}")]
        public void IncompleteFaceMaterialGroupIsRejected(string json)
        {
            Assert.Throws<InvalidDataException>(() =>
                MeshLinkFaceMaterials.Read(json, Int32Bytes(0), 1));
        }

        [TestCase(-1)]
        [TestCase(2)]
        [TestCase(int.MaxValue)]
        public void FaceMaterialIndexOutsideSlotRangeIsRejected(int index)
        {
            Assert.Throws<InvalidDataException>(() =>
                MeshLinkFaceMaterials.Read(MaterialGroupJson(), Int32Bytes(index), 1));
        }

        [Test]
        public void EmptyMaterialNamesWithFacesAreRejected()
        {
            Assert.Throws<InvalidDataException>(() => MeshLinkFaceMaterials.Read(
                "{\"material_names\":[],\"face_material_offset\":0}", Int32Bytes(0), 1));
        }

        [TestCase(-1L)]
        [TestCase(1L)]
        [TestCase(long.MaxValue)]
        public void FaceMaterialPayloadRangeIsChecked(long offset)
        {
            Assert.Throws<InvalidDataException>(() =>
                MeshLinkFaceMaterials.Read(MaterialGroupJson(offset), Int32Bytes(0), 1));
        }

        [Test]
        public void FaceMaterialPayloadMustContainEveryFaceIndex()
        {
            Assert.Throws<InvalidDataException>(() =>
                MeshLinkFaceMaterials.Read(MaterialGroupJson(), Int32Bytes(0), 2));
        }

        [Test]
        public void TwoMaterialSlotsReceiveTheirOwnTriangles()
        {
            var faces = new[] { 0, 1, 2, -1, 0, 2, 3, -1 };
            var mesh = new Mesh();
            try
            {
                WriteMaterialMesh(mesh, MaterialGroupJson(), Int32Bytes(1, 0), faces);
                Assert.That(mesh.subMeshCount, Is.EqualTo(2));
                Assert.That(mesh.GetTriangles(0), Is.EqualTo(new[] { 0, 3, 2 }));
                Assert.That(mesh.GetTriangles(1), Is.EqualTo(new[] { 0, 2, 1 }));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void QuadSplitKeepsBothTrianglesInItsMaterialSlot()
        {
            var faces = new[] { 0, 1, 2, 3, 0, 1, 2, -1 };
            var mesh = new Mesh();
            try
            {
                WriteMaterialMesh(mesh, MaterialGroupJson(), Int32Bytes(1, 0), faces);
                Assert.That(mesh.GetTriangles(0), Is.EqualTo(new[] { 0, 2, 1 }));
                Assert.That(mesh.GetTriangles(1), Is.EqualTo(new[] { 0, 2, 1, 0, 3, 2 }));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void MaterialSlotWithZeroFacesKeepsAnEmptySubmesh()
        {
            var mesh = new Mesh();
            try
            {
                WriteMaterialMesh(mesh, MaterialGroupJson(), Int32Bytes(1), new[] { 0, 1, 2, -1 });
                Assert.That(mesh.subMeshCount, Is.EqualTo(2));
                Assert.That(mesh.GetTriangles(0), Is.Empty);
                Assert.That(mesh.GetTriangles(1), Is.EqualTo(new[] { 0, 2, 1 }));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void UvSeamSplitKeepsMaterialIndicesPerSourceFace()
        {
            var faces = new[] { 0, 1, 2, -1, 0, 2, 3, -1 };
            var binary = new byte[80];
            var coordinates = new[] { 0f, 0f, 1f, 0f, 0f, 1f, 0.5f, 0.5f, 1f, 1f };
            for (var index = 0; index < coordinates.Length; index++)
                BinaryPrimitives.WriteInt32LittleEndian(binary.AsSpan(index * 4, 4),
                    BitConverter.SingleToInt32Bits(coordinates[index]));
            Int32Bytes(0, 1, 2, -1, 3, 2, 4, -1).CopyTo(binary, 40);
            Int32Bytes(1, 0).CopyTo(binary, 72);
            var json = MaterialGroupJson(72).TrimEnd('}') +
                ",\"texcoord_count\":5,\"texcoord_offset\":0,\"texcoord_format\":\"float32x2\",\"face_uv_offset\":40}";
            var mesh = new Mesh();
            try
            {
                WriteMaterialMesh(mesh, json, binary, faces);
                Assert.That(mesh.vertexCount, Is.EqualTo(5));
                Assert.That(mesh.vertices[0], Is.EqualTo(mesh.vertices[3]));
                Assert.That(mesh.uv[0], Is.Not.EqualTo(mesh.uv[3]));
                Assert.That(mesh.subMeshCount, Is.EqualTo(2));
                Assert.That(mesh.GetTriangles(0), Is.EqualTo(new[] { 3, 4, 2 }));
                Assert.That(mesh.GetTriangles(1), Is.EqualTo(new[] { 0, 2, 1 }));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        private static void WriteMaterialMesh(Mesh mesh, string json, byte[] binary, int[] faces)
        {
            var positions = new[] { Vector3.zero, Vector3.right, Vector3.up, Vector3.forward };
            var data = MeshLinkSession.ReadMeshData(json, binary, positions, faces);
            data.SourceFaces = faces;
            data.FaceMaterials = MeshLinkFaceMaterials.Read(json, binary, faces.Length / 4);
            MeshLinkSession.WriteMesh(mesh, data);
        }

        private static string MaterialGroupJson(long offset = 0)
        {
            return "{\"material_names\":[\"Body\",\"Detail\"],\"face_material_offset\":" + offset + "}";
        }

        private static byte[] Int32Bytes(params int[] values)
        {
            var bytes = new byte[values.Length * 4];
            for (var index = 0; index < values.Length; index++)
                BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(index * 4, 4), values[index]);
            return bytes;
        }

        private static float[] Identity()
        {
            return new[]
            {
                1f, 0f, 0f, 0f,
                0f, 1f, 0f, 0f,
                0f, 0f, 1f, 0f,
                0f, 0f, 0f, 1f
            };
        }

        private static string FloatArrayJson(float[] values)
        {
            return "[" + string.Join(",", Array.ConvertAll(
                values,
                value => value.ToString(
                    "R", System.Globalization.CultureInfo.InvariantCulture))) + "]";
        }
    }
}
