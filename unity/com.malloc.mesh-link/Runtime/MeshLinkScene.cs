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

    [DisallowMultipleComponent]
    public sealed class MeshLinkScene : MonoBehaviour
    {
        [SerializeField] private string host = "127.0.0.1";
        [SerializeField] private int port = 48312;
        [SerializeField] private bool listen;

        [SerializeField] private List<MaterialStoreEntry> materialStore = new List<MaterialStoreEntry>();

        internal List<MaterialStoreEntry> MaterialStore => materialStore;

        public string Host => host;
        public int Port => port;
        public bool Listen => listen;
    }
}
