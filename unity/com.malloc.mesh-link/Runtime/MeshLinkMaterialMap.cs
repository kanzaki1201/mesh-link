using System;
using System.Collections.Generic;
using UnityEngine;

namespace Malloc.MeshLink
{
    [Serializable]
    internal sealed class MaterialStoreEntry
    {
        public string meshId;
        public int slotIndex;
        public string objectName;
        public string slotName;
        public Material material;
    }

    public sealed class MeshLinkMaterialMap : ScriptableObject
    {
        [SerializeField] private List<MaterialStoreEntry> entries = new List<MaterialStoreEntry>();

        internal List<MaterialStoreEntry> Entries => entries;
    }
}
