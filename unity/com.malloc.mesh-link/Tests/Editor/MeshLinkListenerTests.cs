using System;
using System.Buffers.Binary;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Malloc.MeshLink.Tests
{
    public sealed class MeshLinkListenerTests
    {
        private MeshLinkSession session;
        private Scene scene;
        private GameObject ownerObject;
        private MeshLinkScene owner;
        private int port;

        [SetUp]
        public void SetUp()
        {
            session = MeshLinkSession.Instance;
            session.StopForTests();
            scene = EditorSceneManager.NewPreviewScene();
            ownerObject = new GameObject("Mesh Link Listener Controller");
            SceneManager.MoveGameObjectToScene(ownerObject, scene);
            owner = ownerObject.AddComponent<MeshLinkScene>();
            port = FreePort();
            EditorPrefs.DeleteKey(MeshLinkSession.PairTokenKey("127.0.0.1", port));
        }

        [TearDown]
        public void TearDown()
        {
            session.StopForTests();
            EditorPrefs.DeleteKey(MeshLinkSession.PairTokenKey("127.0.0.1", port));
            if (ownerObject != null)
            {
                UnityEngine.Object.DestroyImmediate(ownerObject);
            }

            if (scene.IsValid())
            {
                EditorSceneManager.ClosePreviewScene(scene);
            }
        }

        [Test]
        public void ApprovalConfiguresClientAndAppliesCurrentRevisionOnly()
        {
            StartListening();
            using (var blender = new FakeBlender(port))
            {
                blender.Send(HelloJson(string.Empty));
                Assert.That(blender.ReceiveJson(session),
                    Does.Contain("\"type\":\"pairing_pending\""));
                WaitUntil(() => session.GetSnapshot(owner).PairingPending);
                Assert.That(session.Status, Is.EqualTo("Pairing: accept?"));

                session.AcceptPairing(owner);
                var helloJson = blender.ReceiveJson(session);
                var hello = JsonUtility.FromJson<HelloProbe>(helloJson);
                AssertHello(hello);
                Assert.That(hello.pair_token, Is.Not.Empty);
                Assert.That(helloJson, Does.Not.Contain("bridge_version"));
                Assert.That(helloJson, Does.Not.Contain("nomad_version"));
                Assert.That(helloJson, Does.Not.Contain("minimum_bridge_version"));
                Assert.That(EditorPrefs.GetString(
                    MeshLinkSession.PairTokenKey("127.0.0.1", port)),
                    Is.EqualTo(hello.pair_token));

                var initial = ReadConfig(blender);
                AssertInitialConfig(initial);
                Assert.That(session.Status, Is.EqualTo("Connected: Blender"));

                blender.Send(SetConfigJson(1, false, true, true));
                var applied = ReadConfig(blender);
                Assert.That(applied.revision, Is.EqualTo(2));
                Assert.That(applied.live_sync, Is.False);
                Assert.That(applied.sync_mode, Is.EqualTo("client"));
                Assert.That(applied.active_source, Is.EqualTo("client"));
                Assert.That(applied.sync_view, Is.True);
                Assert.That(applied.sync_objects, Is.True);
                Assert.That(applied.sync_materials, Is.True);
                Assert.That(applied.sync_lights, Is.False);
                Assert.That(applied.sync_cameras, Is.True);
                Assert.That(applied.sync_shading, Is.False);
                Assert.That(applied.sync_postprocess, Is.True);

                blender.Send(MeshFullJson("blocked", "blocked-geometry"),
                    MeshBinary(1f));
                PumpFor(0.2);
                Assert.That(session.ObjectCount, Is.Zero);

                blender.Send(SetConfigJson(1, true, false, false));
                var stale = ReadConfig(blender);
                Assert.That(JsonUtility.ToJson(stale),
                    Is.EqualTo(JsonUtility.ToJson(applied)));

                blender.Send("{\"type\":\"claim_sync\",\"source\":\"client\"}");
                var claimed = ReadConfig(blender);
                Assert.That(JsonUtility.ToJson(claimed),
                    Is.EqualTo(JsonUtility.ToJson(applied)));
                blender.Send("{\"type\":\"claim_sync\",\"source\":\"nomad\"}");
                blender.AssertNoFrame(session);
            }
        }

        [Test]
        public void KnownTokenTransfersFramesRejectsSecondClientAndStopsCleanly()
        {
            EditorPrefs.SetString(
                MeshLinkSession.PairTokenKey("127.0.0.1", port), "known-token");
            StartListening();
            using (var blender = new FakeBlender(port))
            {
                blender.Send(HelloJson("known-token"));
                var helloJson = blender.ReceiveJson(session);
                AssertHello(JsonUtility.FromJson<HelloProbe>(helloJson));
                Assert.That(helloJson, Does.Not.Contain("pair_token"));
                AssertInitialConfig(ReadConfig(blender));

                blender.Send(MeshFullJson("mesh-a", "geometry-a"), MeshBinary(1f));
                blender.Send(MeshFullJson("mesh-b", "geometry-b"), MeshBinary(2f));
                WaitUntil(() => session.ObjectCount == 2);
                blender.AssertNoFrame(session);

                blender.Send(MeshDeltaJson("mesh-a"), DeltaBinary(7f));
                WaitUntil(() => session.FindMesh("mesh-a").vertices[1].x == 7f);
                blender.Send("{\"type\":\"object_delete\",\"link_id\":\"mesh-b\",\"live_sync\":true}");
                WaitUntil(() => session.ObjectCount == 1);

                using (var second = new FakeBlender(port))
                {
                    var refusal = second.ReceiveJson(session);
                    Assert.That(refusal, Does.Contain("\"type\":\"error\""));
                    second.AssertClosed(session);
                }

                blender.Send("{\"type\":\"ping\"}");
                Assert.That(blender.ReceiveJson(session),
                    Does.Contain("\"type\":\"pong\""));
                blender.Send(
                    "{\"type\":\"error\",\"message\":\"Blender stopped.\"}");
                WaitUntil(() => !session.IsRunning);
                Assert.That(session.IsRunning, Is.False);
                Assert.That(session.Error, Is.EqualTo("Blender stopped."));
                Assert.That(session.ObjectCount, Is.Zero);
                Assert.That(scene.GetRootGameObjects().Any(
                    item => item.name == "Mesh Link Preview"), Is.False);
                blender.AssertClosed(session);
            }
        }

        [Test]
        public void SecondClientResetKeepsPairedSessionConnected()
        {
            EditorPrefs.SetString(
                MeshLinkSession.PairTokenKey("127.0.0.1", port), "known-token");
            StartListening();
            using (var blender = new FakeBlender(port))
            {
                blender.Send(HelloJson("known-token"));
                blender.ReceiveJson(session);
                AssertInitialConfig(ReadConfig(blender));

                using (var second = new TcpClient())
                {
                    second.Connect(IPAddress.Loopback, port);
                    second.LingerState = new LingerOption(true, 0);
                    second.Close();
                }

                PumpFor(0.2);
                blender.Send(MeshFullJson("mesh-a", "geometry-a"), MeshBinary(1f));
                WaitUntil(() => session.ObjectCount == 1);
                Assert.That(session.FindMesh("mesh-a").vertices[1].x, Is.EqualTo(1f));
                Assert.That(session.Status, Is.EqualTo("Connected: Blender"));
            }
        }

        [Test]
        public void RejectSendsErrorAndKeepsListening()
        {
            StartListening();
            using (var blender = new FakeBlender(port))
            {
                blender.Send(HelloJson(string.Empty));
                blender.ReceiveJson(session);
                WaitUntil(() => session.GetSnapshot(owner).PairingPending);
                session.RejectPairing(owner);
                Assert.That(blender.ReceiveJson(session),
                    Does.Contain("\"type\":\"error\""));
                blender.AssertClosed(session);
                WaitUntil(() => session.Status == $"Listening on 127.0.0.1:{port}");
                Assert.That(session.IsRunning, Is.True);
            }
        }

        private void StartListening()
        {
            session.Enable(owner, "127.0.0.1", port, true);
            WaitUntil(() => session.Status == $"Listening on 127.0.0.1:{port}");
        }

        private static void AssertHello(HelloProbe hello)
        {
            Assert.That(hello.type, Is.EqualTo("hello"));
            Assert.That(hello.protocol, Is.EqualTo(1));
            Assert.That(hello.client_name, Is.EqualTo("Unity"));
            Assert.That(hello.capabilities, Is.EqualTo(new[]
            {
                "scene_transfer",
                "scene_edits",
                "object_state",
                "session_config",
                "mesh_instance",
                "mesh_delta_receive",
                "mesh_attributes_receive"
            }));
        }

        private static void AssertInitialConfig(SessionConfigProbe config)
        {
            Assert.That(config.type, Is.EqualTo("session_config"));
            Assert.That(config.revision, Is.EqualTo(1));
            Assert.That(config.live_sync, Is.True);
            Assert.That(config.sync_mode, Is.EqualTo("client"));
            Assert.That(config.active_source, Is.EqualTo("client"));
            Assert.That(config.sync_objects, Is.True);
            Assert.That(config.sync_view, Is.False);
            Assert.That(config.sync_materials, Is.False);
            Assert.That(config.sync_lights, Is.False);
            Assert.That(config.sync_cameras, Is.False);
            Assert.That(config.sync_shading, Is.False);
            Assert.That(config.sync_postprocess, Is.False);
        }

        private SessionConfigProbe ReadConfig(FakeBlender blender)
        {
            return JsonUtility.FromJson<SessionConfigProbe>(
                blender.ReceiveJson(session));
        }

        private void WaitUntil(Func<bool> condition)
        {
            var deadline = DateTime.UtcNow.AddSeconds(5);
            while (!condition())
            {
                if (DateTime.UtcNow >= deadline)
                {
                    Assert.Fail("Timed out while waiting for listen mode.");
                }

                session.Pump();
                Thread.Sleep(10);
            }

            session.Pump();
        }

        private void PumpFor(double seconds)
        {
            var deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < deadline)
            {
                session.Pump();
                Thread.Sleep(10);
            }
        }

        private static int FreePort()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            var value = ((IPEndPoint)listener.LocalEndpoint).Port;
            listener.Stop();
            return value;
        }

        private static string HelloJson(string token)
        {
            return JsonUtility.ToJson(new ClientHelloWire
            {
                type = "hello",
                protocol = 1,
                pair_token = token,
                client_name = "Blender"
            });
        }

        private static string SetConfigJson(
            int baseRevision,
            bool liveSync,
            bool syncObjects,
            bool syncPostprocess)
        {
            return JsonUtility.ToJson(new SetConfigWire
            {
                type = "set_session_config",
                base_revision = baseRevision,
                live_sync = liveSync,
                sync_mode = "nomad",
                active_source = "nomad",
                sync_view = true,
                sync_objects = syncObjects,
                sync_materials = true,
                sync_lights = false,
                sync_cameras = true,
                sync_shading = false,
                sync_postprocess = syncPostprocess
            });
        }

        private static string MeshFullJson(string meshId, string geometryId)
        {
            return JsonUtility.ToJson(new MeshFullWire
            {
                type = "mesh_full",
                mesh_id = meshId,
                geometry_id = geometryId,
                name = meshId,
                vertex_count = 3,
                face_count = 1,
                binary_size = 52,
                coordinate_system = "nomad_y_up",
                world_matrix = Identity(),
                smooth_shading = true,
                live_sync = true,
                replace_topology = true,
                position_offset = 0,
                position_format = "float32x3",
                face_offset = 36,
                face_format = "int32x4"
            });
        }

        private static string MeshDeltaJson(string meshId)
        {
            return JsonUtility.ToJson(new MeshDeltaWire
            {
                type = "mesh_delta",
                mesh_id = meshId,
                count = 1,
                vertex_count = 3,
                binary_size = 16,
                live_sync = true,
                index_offset = 0,
                index_format = "uint32",
                position_offset = 4,
                position_format = "float32x3"
            });
        }

        private static byte[] MeshBinary(float scale)
        {
            var bytes = new byte[52];
            WriteVector(bytes, 0, 0f, 0f, 0f);
            WriteVector(bytes, 12, scale, 0f, 0f);
            WriteVector(bytes, 24, 0f, scale, 0f);
            BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(36, 4), 0);
            BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(40, 4), 1);
            BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(44, 4), 2);
            BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(48, 4), -1);
            return bytes;
        }

        private static byte[] DeltaBinary(float x)
        {
            var bytes = new byte[16];
            BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(0, 4), 1);
            WriteVector(bytes, 4, x, 0f, 0f);
            return bytes;
        }

        private static void WriteVector(
            byte[] bytes,
            int offset,
            float x,
            float y,
            float z)
        {
            WriteSingle(bytes, offset, x);
            WriteSingle(bytes, offset + 4, y);
            WriteSingle(bytes, offset + 8, z);
        }

        private static void WriteSingle(byte[] bytes, int offset, float value)
        {
            BinaryPrimitives.WriteInt32LittleEndian(
                bytes.AsSpan(offset, 4),
                BitConverter.SingleToInt32Bits(value));
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

        private sealed class FakeBlender : IDisposable
        {
            private readonly TcpClient client = new TcpClient();

            internal FakeBlender(int targetPort)
            {
                client.NoDelay = true;
                client.Connect(IPAddress.Loopback, targetPort);
            }

            internal void Send(string json, byte[] binary = null)
            {
                var frame = MeshLinkSession.EncodeFrame(
                    json, binary ?? Array.Empty<byte>());
                client.GetStream().Write(frame, 0, frame.Length);
            }

            internal string ReceiveJson(MeshLinkSession targetSession)
            {
                var deadline = DateTime.UtcNow.AddSeconds(5);
                while (!client.GetStream().DataAvailable)
                {
                    if (DateTime.UtcNow >= deadline)
                    {
                        Assert.Fail("Timed out while waiting for a listener frame.");
                    }

                    targetSession.Pump();
                    Thread.Sleep(10);
                }

                var frame = MeshLinkSession.ReadFrame(client.GetStream());
                return Encoding.UTF8.GetString(frame.Json);
            }

            internal void AssertNoFrame(MeshLinkSession targetSession)
            {
                var deadline = DateTime.UtcNow.AddMilliseconds(200);
                while (DateTime.UtcNow < deadline)
                {
                    targetSession.Pump();
                    Assert.That(client.GetStream().DataAvailable, Is.False);
                    Thread.Sleep(10);
                }
            }

            internal void AssertClosed(MeshLinkSession targetSession)
            {
                var deadline = DateTime.UtcNow.AddSeconds(5);
                while (!(client.Client.Poll(1000, SelectMode.SelectRead) &&
                    client.Client.Available == 0))
                {
                    if (DateTime.UtcNow >= deadline)
                    {
                        Assert.Fail("Timed out while waiting for the listener to close.");
                    }

                    targetSession.Pump();
                    Thread.Sleep(10);
                }
            }

            public void Dispose()
            {
                client.Close();
            }
        }

        [Serializable]
        private class TypeProbe
        {
            public string type;
        }

        [Serializable]
        private sealed class HelloProbe : TypeProbe
        {
            public int protocol;
            public string client_name;
            public string[] capabilities;
            public string pair_token;
        }

        [Serializable]
        private sealed class SessionConfigProbe : TypeProbe
        {
            public int revision;
            public bool live_sync;
            public string sync_mode;
            public string active_source;
            public bool sync_view;
            public bool sync_objects;
            public bool sync_materials;
            public bool sync_lights;
            public bool sync_cameras;
            public bool sync_shading;
            public bool sync_postprocess;
        }

        [Serializable]
        private sealed class ClientHelloWire
        {
            public string type;
            public int protocol;
            public string pair_token;
            public string client_name;
        }

        [Serializable]
        private sealed class SetConfigWire
        {
            public string type;
            public int base_revision;
            public bool live_sync;
            public string sync_mode;
            public string active_source;
            public bool sync_view;
            public bool sync_objects;
            public bool sync_materials;
            public bool sync_lights;
            public bool sync_cameras;
            public bool sync_shading;
            public bool sync_postprocess;
        }

        [Serializable]
        private sealed class MeshFullWire
        {
            public string type;
            public string mesh_id;
            public string geometry_id;
            public string name;
            public int vertex_count;
            public int face_count;
            public int binary_size;
            public string coordinate_system;
            public float[] world_matrix;
            public bool smooth_shading;
            public bool live_sync;
            public bool replace_topology;
            public int position_offset;
            public string position_format;
            public int face_offset;
            public string face_format;
        }

        [Serializable]
        private sealed class MeshDeltaWire
        {
            public string type;
            public string mesh_id;
            public int count;
            public int vertex_count;
            public int binary_size;
            public bool live_sync;
            public int index_offset;
            public string index_format;
            public int position_offset;
            public string position_format;
        }
    }
}
