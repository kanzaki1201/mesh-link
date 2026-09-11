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
            EditorGUILayout.LabelField($"Stored Materials ({scene.MaterialStore.Count})", EditorStyles.boldLabel);
            if (GUILayout.Button("Clear Stored Materials"))
                Session.ClearStoredMaterials(scene);
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
            }
        }
    }
}
