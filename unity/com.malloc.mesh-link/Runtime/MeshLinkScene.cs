using UnityEngine;

namespace Malloc.MeshLink
{
    [DisallowMultipleComponent]
    public sealed class MeshLinkScene : MonoBehaviour
    {
        [SerializeField] private string host = "127.0.0.1";
        [SerializeField] private int port = 48312;
        [SerializeField] private bool listen;

        public string Host => host;
        public int Port => port;
        public bool Listen => listen;
    }
}
