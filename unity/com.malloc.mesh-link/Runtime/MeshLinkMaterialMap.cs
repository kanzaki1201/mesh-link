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

    [Serializable]
    internal sealed class TextureBindingEntry
    {
        public Material material;
        public string channel;
        public string property;
    }

    public sealed class MeshLinkMaterialMap : ScriptableObject
    {
        [SerializeField] private List<MaterialStoreEntry> entries = new List<MaterialStoreEntry>();
        [SerializeField] private List<TextureBindingEntry> textureBindings = new List<TextureBindingEntry>();

        internal List<MaterialStoreEntry> Entries => entries;
        internal List<TextureBindingEntry> TextureBindings => textureBindings;
    }
}
