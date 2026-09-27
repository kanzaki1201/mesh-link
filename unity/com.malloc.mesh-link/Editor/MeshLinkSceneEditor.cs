using System;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Malloc.MeshLink
{
    [CustomEditor(typeof(MeshLinkScene))]
    public sealed class MeshLinkSceneEditor : UnityEditor.Editor
    {
        private MeshLinkSession Session => MeshLinkSession.Instance;

        private void OnEnable()
        {
            Session.InspectorStateChanged += Repaint;
        }

        private void OnDisable()
        {
            Session.InspectorStateChanged -= Repaint;
        }

        public override void OnInspectorGUI()
        {
            serializedObject.Update();
            var scene = (MeshLinkScene)target;
            var snapshot = Session.GetSnapshot(scene);

            using (new EditorGUI.DisabledScope(snapshot.Enabled))
            {
                EditorGUILayout.PropertyField(serializedObject.FindProperty("host"));
                EditorGUILayout.PropertyField(serializedObject.FindProperty("port"));
                EditorGUILayout.PropertyField(serializedObject.FindProperty("listen"));
                var materialMap = serializedObject.FindProperty("materialMap");
                EditorGUILayout.PropertyField(materialMap);
                if (materialMap.objectReferenceValue == null &&
                    GUILayout.Button("Create Material Map"))
                {
                    var path = EditorUtility.SaveFilePanelInProject(
                        "Create Material Map", "MeshLinkMaterialMap", "asset",
                        "Choose where to save the Mesh Link material map.");
                    if (!string.IsNullOrEmpty(path))
                    {
                        var map = ScriptableObject.CreateInstance<MeshLinkMaterialMap>();
                        AssetDatabase.CreateAsset(map, path);
                        materialMap.objectReferenceValue = map;
                        serializedObject.ApplyModifiedProperties();
                    }
                    // The modal save panel breaks the current layout pass.
                    GUIUtility.ExitGUI();
                }
            }

            serializedObject.ApplyModifiedProperties();
            DrawSessionButton(scene, snapshot);
            EditorGUILayout.LabelField("Status", snapshot.Status);
            DrawSessionMessage(snapshot);
            DrawPairingButtons(scene, snapshot);

            if (snapshot.OwnsSession)
            {
                DrawMaterials(scene, snapshot.Rows);
            }

            DrawStoredMaterials(scene);
        }

        private void DrawStoredMaterials(MeshLinkScene scene)
        {
            EditorGUILayout.Space();
            var map = scene.MaterialMap;
            EditorGUILayout.LabelField(
                $"Stored Materials ({(map == null ? 0 : map.Entries.Count)})",
                EditorStyles.boldLabel);
            using (new EditorGUI.DisabledScope(map == null))
            {
                if (GUILayout.Button(new GUIContent(
                    "Clear Stored Materials",
                    "Removes every entry from this map. Every scene that uses the map loses them.")))
                    Session.ClearStoredMaterials(scene);
            }
        }

        private void DrawSessionButton(
            MeshLinkScene scene,
            MeshLinkSession.SessionSnapshot snapshot)
        {
            if (snapshot.OwnsSession && snapshot.Enabled)
            {
                if (GUILayout.Button("Disable Sync"))
                {
                    Session.Disable(scene);
                }

                return;
            }

            using (new EditorGUI.DisabledScope(snapshot.OtherOwner))
            {
                if (GUILayout.Button("Enable Sync"))
                {
                    Session.Enable(scene, scene.Host, scene.Port, scene.Listen);
                }
            }
        }

        private void DrawPairingButtons(
            MeshLinkScene scene,
            MeshLinkSession.SessionSnapshot snapshot)
        {
            if (!snapshot.PairingPending)
            {
                return;
            }

            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button("Accept"))
                {
                    Session.AcceptPairing(scene);
                }

                if (GUILayout.Button("Reject"))
                {
                    Session.RejectPairing(scene);
                }
            }
        }

        private static void DrawSessionMessage(
            MeshLinkSession.SessionSnapshot snapshot)
        {
            if (!string.IsNullOrEmpty(snapshot.Message))
            {
                EditorGUILayout.HelpBox(snapshot.Message, snapshot.MessageType);
            }
        }

        private void DrawMaterials(
            MeshLinkScene scene,
            MeshLinkSession.MaterialRowSnapshot[] rows)
        {
            EditorGUILayout.Space();
            EditorGUILayout.LabelField(
                $"Synced Object Materials ({Session.ObjectCount})",
                EditorStyles.boldLabel);

            foreach (var row in rows)
            {
                var shortId = row.MeshId.Length <= 8
                    ? row.MeshId
                    : row.MeshId.Substring(0, 8);
                var label = new GUIContent(
                    $"{row.Label} ({shortId})",
                    row.MeshId);

                EditorGUI.BeginChangeCheck();
                var material = (Material)EditorGUILayout.ObjectField(
                    label,
                    row.Material,
                    typeof(Material),
                    false);
                if (EditorGUI.EndChangeCheck())
                {
                    Session.SetMaterial(scene, row.MeshId, material, row.SlotIndex);
                }

                DrawTextureChannels(scene, row);
            }
        }

        private void DrawTextureChannels(MeshLinkScene scene,
            MeshLinkSession.MaterialRowSnapshot row)
        {
            foreach (var channel in Session.GetTextureChannels(row.MeshId, row.SlotIndex))
            {
                var material = row.Renderer.sharedMaterials[row.SlotIndex];
                var properties = MeshLinkTextures.GetTextureProperties(material);
                var resolved = Session.ResolveTextureProperty(material, channel, scene.MaterialMap);
                var options = new[] { "(unbound)" }.Concat(properties).ToArray();
                var selected = Array.IndexOf(properties, resolved.Property) + 1;
                using (new EditorGUI.DisabledScope(scene.MaterialMap == null || material == null))
                {
                    EditorGUI.indentLevel++;
                    EditorGUILayout.BeginHorizontal();
                    var next = EditorGUILayout.Popup(channel, selected, options);
                    var invert = MeshLinkTextures.IsLinear(channel)
                        ? EditorGUILayout.ToggleLeft("Invert", resolved.Invert, GUILayout.Width(70))
                        : false;
                    EditorGUILayout.EndHorizontal();
                    EditorGUI.indentLevel--;
                    if (next != selected || invert != resolved.Invert)
                        Session.SetTextureBinding(scene, row.MeshId, row.SlotIndex,
                            channel, next == 0 ? null : properties[next - 1], invert,
                            next != selected);
                }
            }
        }
    }
}
