using System;
using System.Buffers.Binary;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
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
        private MeshLinkMaterialMap textureMap;
        private Material textureMaterialA;
        private Material textureMaterialB;
        private Material textureMaterialC;
        private Texture2D baseTexture;

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
            if (textureMaterialA != null) UnityEngine.Object.DestroyImmediate(textureMaterialA);
            if (textureMaterialB != null) UnityEngine.Object.DestroyImmediate(textureMaterialB);
            if (textureMaterialC != null) UnityEngine.Object.DestroyImmediate(textureMaterialC);
            if (baseTexture != null) UnityEngine.Object.DestroyImmediate(baseTexture);
            if (textureMap != null) UnityEngine.Object.DestroyImmediate(textureMap);
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

        [Test]
        public void TextureBlobCacheDropsOnlyDereferencedIdsAndStopDestroysDecodes()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                baseTexture = new Texture2D(2, 2);
                textureMaterialA.SetTexture("_MainTex", baseTexture);
                var png = Png(Color.red);
                var id = TextureId(png);
                var unused = Png(Color.blue);
                var unusedId = TextureId(unused);
                blender.Send(TextureJson(id, png), png);
                blender.Send(TextureJson(id, png), png);
                blender.Send(TextureJson(unusedId, unused), unused);
                WaitUntil(() => session.TextureBlobCount == 2);

                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}"));
                WaitUntil(() => session.DecodedTextureCount == 1);
                var texture = BlockTexture(0, "_MainTex");
                blender.Send(TextureJson(id, png), png);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}"));
                PumpFor(0.2);
                Assert.That(BlockTexture(0, "_MainTex"), Is.SameAs(texture));
                Assert.That(texture, Is.Not.Null);
                Assert.That(texture.mipmapCount, Is.GreaterThan(1));
                Assert.That(texture.hideFlags, Is.EqualTo(HideFlags.DontSave));
                Assert.That(texture.wrapMode, Is.EqualTo(TextureWrapMode.Repeat));
                Assert.That(texture.filterMode, Is.EqualTo(FilterMode.Bilinear));

                blender.Send(MaterialJson("\"color\":{}"));
                WaitUntil(() => session.TextureBlobCount == 1);
                Assert.That(BlockTexture(0, "_MainTex"), Is.Null);
                Assert.That(textureMaterialA.GetTexture("_MainTex"), Is.SameAs(baseTexture));
                Assert.That(session.DecodedTextureCount, Is.Zero);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + unusedId + "\"}"));
                WaitUntil(() => session.DecodedTextureCount == 1);
                texture = BlockTexture(0, "_MainTex");
                session.StopForTests();
                Assert.That(texture == null, Is.True);
                Assert.That(session.TextureBlobCount, Is.Zero);
                Assert.That(session.DecodedTextureCount, Is.Zero);
                Assert.That(session.GetTextureChannels("mesh-a", 0), Is.Empty);
            }
        }

        [Test]
        public void MaterialChannelsKeepClearRequestAndUseSlotZeroByDefault()
        {
            StartPairedTexturePreview(out var blender, true);
            using (blender)
            {
                var png = Png(Color.green);
                var id = TextureId(png);
                blender.Send(TextureJson(id, png), png);
                WaitUntil(() => session.TextureBlobCount == 1);
                blender.Send(MaterialJson("\"roughness\":{},\"x_Bad\":{}", false));
                PumpFor(0.1);
                Assert.That(session.GetTextureChannels("mesh-a", 0), Is.Empty);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}", false));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                Assert.That(BlockTexture(1, "_MainTex"), Is.Null);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}", true, 1));
                WaitUntil(() => session.GetTextureChannels("mesh-a", 1).Length == 1);
                Assert.That(BlockTexture(1, "_MainTex"), Is.Null);
                session.SetMaterial(owner, "mesh-a", textureMaterialA, 1);
                Assert.That(BlockTexture(1, "_MainTex"), Is.SameAs(BlockTexture(0, "_MainTex")));

                blender.Send(MaterialJson("\"normal\":{}", false));
                WaitUntil(() => session.GetTextureChannels("mesh-a", 0).Length == 2);
                Assert.That(BlockTexture(0, "_MainTex"), Is.Not.Null);

                var yellow = Png(Color.yellow);
                var missing = TextureId(yellow);
                var previous = BlockTexture(0, "_MainTex");
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + missing + "\"}", false));
                Assert.That(blender.ReceiveJson(session),
                    Does.Contain("\"type\":\"request_texture\"").And.Contain(missing));
                Assert.That(BlockTexture(0, "_MainTex"), Is.Not.Null);
                blender.Send(TextureJson(missing, yellow), yellow);
                WaitUntil(() => BlockTexture(0, "_MainTex") != previous);

                blender.Send(MaterialJson("\"color\":{}", false));
                WaitUntil(() => BlockTexture(0, "_MainTex") == null);

                blender.Send(MaterialJson("\"color\":{}", true, 2));
                WaitUntil(() => session.Status.Contains("slot_index is out of range"));
                blender.Send(MaterialJson("\"color\":{}", true, 1, "unknown"));
                WaitUntil(() => session.Status.Contains("unknown mesh_id"));
                Assert.That(BlockTexture(0, "_MainTex"), Is.Null);
            }
        }

        [Test]
        public void TextureBindingsUseColorSpaceAndReapplyAfterMaterialChange()
        {
            StartPairedTexturePreview(out var blender, true);
            using (blender)
            {
                var shader = Shader.Find("Standard");
                Assert.That(shader, Is.Not.Null);
                textureMaterialB = new Material(shader);
                textureMap = ScriptableObject.CreateInstance<MeshLinkMaterialMap>();
                owner.MaterialMap = textureMap;
                session.SetMaterial(owner, "mesh-a", textureMaterialB, 1);
                var png = Png(Color.magenta);
                var id = TextureId(png);
                blender.Send(TextureJson(id, png), png);
                WaitUntil(() => session.TextureBlobCount == 1);
                var channels = "\"color\":{\"texture_id\":\"" + id + "\"}," +
                    "\"emissive\":{\"texture_id\":\"" + id + "\"}," +
                    "\"normal\":{\"texture_id\":\"" + id + "\"}," +
                    "\"x_mask\":{\"texture_id\":\"" + id + "\"}";
                blender.Send(MaterialJson(channels, true, 1));
                WaitUntil(() => session.DecodedTextureCount == 2);
                var color = BlockTexture(1, "_MainTex");
                var emissive = BlockTexture(1, "_EmissionMap");
                var normal = BlockTexture(1, "_BumpMap");
                var original = textureMaterialB.GetTexture("_MainTex");
                Assert.That(color, Is.SameAs(emissive));
                Assert.That(normal, Is.Not.SameAs(color));
                Assert.That(color.isDataSRGB, Is.True);
                Assert.That(normal.isDataSRGB, Is.False);
                Assert.That(BlockTexture(0, "_MainTex"), Is.Null);
                Assert.That(textureMaterialB.GetTexture("_MainTex"), Is.SameAs(original));
                Assert.That(session.ResolveTextureProperty(textureMaterialB, "x_mask", textureMap), Is.Null);

                Assert.That(session.SetTextureBinding(owner, "mesh-a", 1, "x_mask", "_DetailMask"), Is.True);
                Assert.That(textureMap.TextureBindings.Single().property, Is.EqualTo("_DetailMask"));
                Assert.That(new SerializedObject(textureMap).FindProperty("textureBindings").arraySize,
                    Is.EqualTo(1));
                Assert.That(BlockTexture(1, "_DetailMask"), Is.SameAs(normal));
                textureMaterialC = new Material(shader);
                var nextOriginal = textureMaterialC.GetTexture("_MainTex");
                session.SetMaterial(owner, "mesh-a", textureMaterialC, 1);
                Assert.That(BlockTexture(1, "_MainTex"), Is.SameAs(color));
                Assert.That(BlockTexture(1, "_DetailMask"), Is.Null);
                Assert.That(textureMaterialC.GetTexture("_MainTex"), Is.SameAs(nextOriginal));
            }
        }

        [Test]
        public void BadTextureDecodeKeepsListenerConnectedAndLiveMaterialNeedsFlag()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                var png = Png(Color.cyan);
                var id = TextureId(png);
                blender.Send(TextureJson(id, png), png);
                WaitUntil(() => session.TextureBlobCount == 1);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}", false, 0,
                    "mesh-a", true));
                PumpFor(0.2);
                Assert.That(BlockTexture(0, "_MainTex"), Is.Null);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                var current = BlockTexture(0, "_MainTex");

                var invalid = new byte[] { 1, 2, 3, 4 };
                var invalidId = TextureId(invalid);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + invalidId + "\"}"));
                Assert.That(blender.ReceiveJson(session), Does.Contain(invalidId));
                blender.Send(TextureJson(invalidId, invalid), invalid);
                WaitUntil(() => session.Status.Contains("PNG decode failed"));
                Assert.That(session.IsRunning, Is.True);
                Assert.That(BlockTexture(0, "_MainTex"), Is.SameAs(current));
                blender.Send(SetConfigJson(1, true, true, false));
                Assert.That(ReadConfig(blender).sync_materials, Is.True);
                var nextPng = Png(Color.blue);
                var nextId = TextureId(nextPng);
                blender.Send(TextureJson(nextId, nextPng), nextPng);
                WaitUntil(() => session.TextureBlobCount == 3);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + nextId + "\"}",
                    true, 0, "mesh-a", true));
                WaitUntil(() => BlockTexture(0, "_MainTex") != current);
            }
        }

        [Test]
        public void DefaultTexturePropertiesFollowShaderOrder()
        {
            var cases = new[]
            {
                new[] { "[MainTexture] _AlbedoMap(\"Albedo\", 2D) = \"white\" {} _MainTex(\"Main\", 2D) = \"white\" {}", "color", "_AlbedoMap" },
                new[] { "_BaseMap(\"Base\", 2D) = \"white\" {}", "color", "_BaseMap" },
                new[] { "_BumpMap(\"Bump\", 2D) = \"bump\" {} _NormalMap(\"Normal\", 2D) = \"bump\" {}", "normal", "_BumpMap" },
                new[] { "_NormalMap(\"Normal\", 2D) = \"bump\" {}", "normal", "_NormalMap" }
            };
            foreach (var item in cases)
            {
                var source = "Shader \"Hidden/MeshLinkTextureOrder\" { Properties { " +
                    item[0] + " } SubShader { Pass {} } }";
                var shader = ShaderUtil.CreateShaderAsset(source);
                Assert.That(shader, Is.Not.Null);
                try
                {
                    var material = new Material(shader);
                    try
                    {
                        var textures = new MeshLinkTextures(_ => { }, _ => { }, () => { });
                        Assert.That(textures.ResolveProperty(material, item[1], null),
                            Is.EqualTo(item[2]));
                    }
                    finally { UnityEngine.Object.DestroyImmediate(material); }
                }
                finally { UnityEngine.Object.DestroyImmediate(shader); }
            }
        }

        [Test]
        public void SecondBakeReplacesPreviewWhenMaterialTextureIsEmpty()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                Assert.That(textureMaterialA.GetTexture("_MainTex"), Is.Null);
                var first = Png(Color.red);
                var second = Png(Color.blue);
                blender.Send(TextureJson(TextureId(first), first), first);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + TextureId(first) + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                var previous = BlockTexture(0, "_MainTex");
                blender.Send(TextureJson(TextureId(second), second), second);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + TextureId(second) + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != previous);
                Assert.That(session.IsRunning, Is.True);
            }
        }

        [Test]
        public void ResentDroppedBlobIsUsedWithoutRequest()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                var png = Png(Color.red);
                var id = TextureId(png);
                blender.Send(TextureJson(id, png), png);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                blender.Send(MaterialJson("\"color\":{}"));
                WaitUntil(() => session.TextureBlobCount == 0);
                blender.Send(TextureJson(id, png), png);
                WaitUntil(() => session.TextureBlobCount == 1);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                blender.AssertNoFrame(session);
            }
        }

        [Test]
        public void BindingReappliesEveryObjectUsingMaterial()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                textureMap = ScriptableObject.CreateInstance<MeshLinkMaterialMap>();
                owner.MaterialMap = textureMap;
                textureMaterialB = new Material(Shader.Find("Standard"));
                session.SetMaterial(owner, "mesh-a", textureMaterialB);
                blender.Send(MeshFullJson("mesh-b", "geometry-b"), MeshBinary(1f));
                WaitUntil(() => session.ObjectCount == 2);
                session.SetMaterial(owner, "mesh-b", textureMaterialB);
                var png = Png(Color.red);
                var id = TextureId(png);
                blender.Send(TextureJson(id, png), png);
                blender.Send(MaterialJson("\"x_mask\":{\"texture_id\":\"" + id + "\"}"));
                blender.Send(MaterialJson("\"x_mask\":{\"texture_id\":\"" + id + "\"}",
                    true, 0, "mesh-b"));
                WaitUntil(() => session.GetTextureChannels("mesh-b", 0).Length == 1);
                Assert.That(BlockTexture("mesh-b", 0, "_DetailMask"), Is.Null);
                Assert.That(session.SetTextureBinding(owner, "mesh-a", 0, "x_mask", "_DetailMask"), Is.True);
                Assert.That(BlockTexture("mesh-b", 0, "_DetailMask"), Is.Not.Null);
            }
        }

        [Test]
        public void UndoMaterialChangeReappliesRestoredProperty()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                var png = Png(Color.red);
                var id = TextureId(png);
                blender.Send(TextureJson(id, png), png);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + id + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                var shader = ShaderUtil.CreateShaderAsset(
                    "Shader \"Hidden/MeshLinkUndo\" { Properties { _BaseMap(\"Base\", 2D) = \"white\" {} } SubShader { Pass {} } }");
                try
                {
                    textureMaterialB = new Material(shader);
                    Undo.FlushUndoRecordObjects();
                    Undo.IncrementCurrentGroup();
                    session.SetMaterial(owner, "mesh-a", textureMaterialB);
                    Undo.FlushUndoRecordObjects();
                    Assert.That(BlockTexture(0, "_BaseMap"), Is.Not.Null);
                    Undo.PerformUndo();
                    Assert.That(session.FindRenderer("mesh-a").sharedMaterial, Is.SameAs(textureMaterialA));
                    Assert.That(BlockTexture(0, "_MainTex"), Is.Not.Null);
                    Assert.That(BlockTexture(0, "_BaseMap"), Is.Null);
                }
                finally { UnityEngine.Object.DestroyImmediate(shader); }
            }
        }

        [Test]
        public void HugeSlotIndexSkipsMaterialAndKeepsListenerConnected()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                blender.Send(MaterialJson("\"color\":{}")
                    .Replace("\"slot_index\":0", "\"slot_index\":99999999999"));
                WaitUntil(() => session.Status.Contains("slot_index is out of range"));
                Assert.That(session.IsRunning, Is.True);
                blender.Send(MaterialJson("\"color\":{}"));
                WaitUntil(() => session.GetTextureChannels("mesh-a", 0).Length == 1);
            }
        }

        [Test]
        public void MalformedSecondChannelLeavesFirstChannelUnchanged()
        {
            StartPairedTexturePreview(out var blender);
            using (blender)
            {
                var first = Png(Color.red);
                var second = Png(Color.blue);
                blender.Send(TextureJson(TextureId(first), first), first);
                blender.Send(TextureJson(TextureId(second), second), second);
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + TextureId(first) + "\"}"));
                WaitUntil(() => BlockTexture(0, "_MainTex") != null);
                var previous = BlockTexture(0, "_MainTex");
                blender.Send(MaterialJson("\"color\":{\"texture_id\":\"" + TextureId(second) +
                    "\"},\"normal\":[]"));
                WaitUntil(() => session.Status.Contains("Skipped material"));
                session.SetMaterial(owner, "mesh-a", textureMaterialA);
                Assert.That(BlockTexture(0, "_MainTex"), Is.SameAs(previous));
                Assert.That(session.IsRunning, Is.True);
                blender.Send(MaterialJson("\"color\":null   "));
                WaitUntil(() => BlockTexture(0, "_MainTex") == null);
            }
        }

        private void StartPairedTexturePreview(out FakeBlender blender, bool twoSlots = false)
        {
            EditorPrefs.SetString(MeshLinkSession.PairTokenKey("127.0.0.1", port), "known-token");
            StartListening();
            blender = new FakeBlender(port);
            blender.Send(HelloJson("known-token"));
            AssertHello(JsonUtility.FromJson<HelloProbe>(blender.ReceiveJson(session)));
            AssertInitialConfig(ReadConfig(blender));
            var shader = Shader.Find("Unlit/Texture");
            Assert.That(shader, Is.Not.Null);
            textureMaterialA = new Material(shader);
            if (twoSlots)
            {
                var bytes = new byte[56];
                MeshBinary(1f).CopyTo(bytes, 0);
                BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(52, 4), 1);
                var json = MeshFullJson("mesh-a", "geometry-a")
                    .Replace("\"binary_size\":52", "\"binary_size\":56").TrimEnd('}') +
                    ",\"material_names\":[\"Body\",\"Detail\"],\"face_material_offset\":52}";
                blender.Send(json, bytes);
            }
            else blender.Send(MeshFullJson("mesh-a", "geometry-a"), MeshBinary(1f));
            WaitUntil(() => session.ObjectCount == 1);
            session.SetMaterial(owner, "mesh-a", textureMaterialA);
        }

        private Texture2D BlockTexture(int slot, string property)
        {
            return BlockTexture("mesh-a", slot, property);
        }

        private Texture2D BlockTexture(string meshId, int slot, string property)
        {
            var block = new MaterialPropertyBlock();
            session.FindRenderer(meshId).GetPropertyBlock(block, slot);
            return block.GetTexture(property) as Texture2D;
        }

        private static string MaterialJson(string channels, bool includeSlot = true,
            int slot = 0, string meshId = "mesh-a", bool live = false)
        {
            return "{\"type\":\"material\",\"mesh_id\":\"" + meshId +
                "\",\"live_sync\":" + (live ? "true" : "false") +
                (includeSlot ? ",\"slot_index\":" + slot : string.Empty) +
                ",\"material\":{\"textures\":{" + channels + "}}}";
        }

        private static string TextureJson(string id, byte[] bytes)
        {
            return "{\"type\":\"texture\",\"texture_id\":\"" + id +
                "\",\"name\":\"preview.png\",\"binary_size\":" + bytes.Length + "}";
        }

        private static string TextureId(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes))
                    .Replace("-", string.Empty).ToLowerInvariant();
        }

        private static byte[] Png(Color color)
        {
            var texture = new Texture2D(2, 2);
            texture.SetPixels(new[] { color, color, color, color });
            texture.Apply();
            var bytes = ImageConversion.EncodeToPNG(texture);
            UnityEngine.Object.DestroyImmediate(texture);
            return bytes;
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
                "mesh_attributes_receive",
                "material",
                "texture"
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
