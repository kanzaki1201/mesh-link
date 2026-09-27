using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace Malloc.MeshLink
{
    internal sealed class MeshLinkListener
    {
        private const int ProtocolVersion = 1;

        private static readonly UTF8Encoding StrictUtf8 =
            new UTF8Encoding(false, true);

        private static readonly string[] Capabilities =
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
        };

        private readonly MeshLinkSession session;
        private readonly string host;
        private readonly int port;
        private readonly ConcurrentQueue<ListenerItem> incoming =
            new ConcurrentQueue<ListenerItem>();
        private readonly Queue<byte[]> outgoing = new Queue<byte[]>();
        private readonly object outgoingLock = new object();

        private TcpListener tcpListener;
        private volatile TcpClient client;
        private Thread worker;
        private long queuedBytes;
        private volatile bool stopRequested;
        private volatile bool closeClientAfterWrites;
        private volatile bool stopAfterWrites;
        private string failureMessage = string.Empty;
        private bool paired;
        private string clientName = "Client";
        private SessionConfigDto configuration;

        internal MeshLinkListener(
            MeshLinkSession session,
            string host,
            int port)
        {
            this.session = session;
            this.host = host;
            this.port = port;
        }

        internal bool PairingPending { get; private set; }

        internal static string PairTokenKey(string targetHost, int targetPort)
        {
            return $"Malloc.MeshLink.PairToken.{targetHost}:{targetPort}";
        }

        internal void Start()
        {
            tcpListener = new TcpListener(ResolveAddress(host), port);
            tcpListener.Start();
            worker = new Thread(WorkerRun)
            {
                IsBackground = true,
                Name = "Mesh Link Listener"
            };
            worker.Start();
            session.SetListenerStatus($"Listening on {host}:{port}");
        }

        internal void Pump()
        {
            while (incoming.TryDequeue(out var item))
            {
                if (item.Kind == ListenerItemKind.Frame)
                {
                    Interlocked.Add(ref queuedBytes, -item.Frame.Size);
                    ProcessFrame(item.Frame);
                    continue;
                }

                if (item.Kind == ListenerItemKind.ClientClosed)
                {
                    ResetClientState();
                    session.SetListenerStatus($"Listening on {host}:{port}");
                    continue;
                }

                if (item.Kind == ListenerItemKind.ClientAccepted)
                {
                    ResetClientState();
                    continue;
                }

                session.StopFromListener(item.Error);
                return;
            }
        }

        internal void AcceptPairing()
        {
            if (!PairingPending || client == null)
            {
                return;
            }

            var token = Guid.NewGuid().ToString("N");
            EditorPrefs.SetString(PairTokenKey(host, port), token);
            CompletePairing(token);
        }

        internal void RejectPairing()
        {
            if (!PairingPending || client == null)
            {
                return;
            }

            PairingPending = false;
            SendError("Pairing rejected.", string.Empty);
            closeClientAfterWrites = true;
            session.SetListenerStatus($"Listening on {host}:{port}");
        }

        internal void SendError(string message, string requestId)
        {
            Send(new ErrorDto
            {
                type = "error",
                message = message,
                request_id = requestId
            });
        }

        internal void RequestTexture(string textureId)
        {
            Send(new RequestTextureDto
            {
                type = "request_texture",
                texture_id = textureId
            });
        }

        internal void Fail(string message, string requestId)
        {
            failureMessage = string.IsNullOrWhiteSpace(message)
                ? "The Mesh Link protocol failed."
                : message;
            SendError(failureMessage, requestId);
            stopAfterWrites = true;
        }

        internal void Stop()
        {
            stopRequested = true;
            tcpListener?.Stop();
            CloseClient();
            if (worker != null && worker != Thread.CurrentThread)
            {
                worker.Join();
            }

            worker = null;
            tcpListener = null;
            ClearQueues();
            ResetClientState();
        }

        private void ProcessFrame(MeshLinkSession.Frame frame)
        {
            try
            {
                var json = StrictUtf8.GetString(frame.Json);
                var header = JsonUtility.FromJson<TypeDto>(json);
                if (header == null || string.IsNullOrEmpty(header.type))
                {
                    throw new InvalidDataException("The frame has no message type.");
                }

                if (IsControlFrame(header.type) && frame.Binary.Length != 0)
                {
                    throw new InvalidDataException(
                        "The message has an unexpected binary payload.");
                }

                DispatchFrame(json, frame, header.type);
            }
            catch (Exception exception)
            {
                Fail(exception.Message, string.Empty);
            }
        }

        private void DispatchFrame(
            string json,
            MeshLinkSession.Frame frame,
            string type)
        {
            switch (type)
            {
                case "ping":
                    Send(new TypeDto { type = "pong" });
                    return;
                case "pong":
                    return;
                case "error":
                    HandleRemoteError(json);
                    return;
            }

            if (!paired)
            {
                if (type == "hello")
                {
                    HandleHello(json);
                }

                return;
            }

            if (type == "set_session_config")
            {
                HandleSetSessionConfig(json);
            }
            else if (type == "claim_sync")
            {
                HandleClaimSync(json);
            }
            else if (IsSceneFrame(type))
            {
                session.ProcessListenerFrame(frame);
            }
        }

        private void HandleHello(string json)
        {
            var hello = JsonUtility.FromJson<ClientHelloDto>(json);
            if (hello == null || hello.protocol != ProtocolVersion)
            {
                throw new InvalidDataException(
                    "The client uses an unsupported protocol.");
            }

            clientName = string.IsNullOrWhiteSpace(hello.client_name)
                ? "Client"
                : hello.client_name;
            var stored = EditorPrefs.GetString(
                PairTokenKey(host, port), string.Empty);
            if (!string.IsNullOrEmpty(stored) &&
                string.Equals(stored, hello.pair_token, StringComparison.Ordinal))
            {
                CompletePairing(null);
                return;
            }

            PairingPending = true;
            Send(new TypeDto { type = "pairing_pending" });
            session.SetListenerStatus("Pairing: accept?");
        }

        private void CompletePairing(string token)
        {
            PairingPending = false;
            paired = true;
            SendHello(token);
            configuration = SessionConfigDto.Initial();
            ApplyConfiguration();
            Send(configuration);
        }

        private void SendHello(string token)
        {
            if (token == null)
            {
                Send(new HelloDto
                {
                    type = "hello",
                    protocol = ProtocolVersion,
                    client_name = "Unity",
                    capabilities = Capabilities
                });
                return;
            }

            Send(new ApprovedHelloDto
            {
                type = "hello",
                protocol = ProtocolVersion,
                client_name = "Unity",
                capabilities = Capabilities,
                pair_token = token
            });
        }

        private void HandleSetSessionConfig(string json)
        {
            var update = JsonUtility.FromJson<SetSessionConfigDto>(json);
            if (update != null &&
                update.base_revision == configuration.revision)
            {
                configuration = SessionConfigDto.FromUpdate(
                    configuration.revision + 1, update);
                ApplyConfiguration();
            }

            Send(configuration);
        }

        private void HandleClaimSync(string json)
        {
            var claim = JsonUtility.FromJson<ClaimSyncDto>(json);
            if (claim != null && claim.source == "client")
            {
                Send(configuration);
            }
        }

        private void ApplyConfiguration()
        {
            session.SetListenerConfiguration(
                clientName,
                configuration.live_sync,
                configuration.sync_objects,
                configuration.sync_materials,
                configuration.active_source);
        }

        private void HandleRemoteError(string json)
        {
            var remoteError = JsonUtility.FromJson<ErrorDto>(json);
            var message = remoteError == null ||
                string.IsNullOrWhiteSpace(remoteError.message)
                ? "The client reported an error."
                : remoteError.message;
            session.StopFromListener(message);
        }

        private void Send(object dto)
        {
            if (stopRequested)
            {
                return;
            }

            var frame = MeshLinkSession.EncodeFrame(
                JsonUtility.ToJson(dto), Array.Empty<byte>());
            lock (outgoingLock)
            {
                outgoing.Enqueue(frame);
            }
        }

        private void WorkerRun()
        {
            try
            {
                while (!stopRequested)
                {
                    AcceptPendingClients();
                    ServiceClient();
                }
            }
            catch (Exception exception)
            {
                if (!stopRequested)
                {
                    var message = string.IsNullOrEmpty(failureMessage)
                        ? exception.Message
                        : failureMessage;
                    incoming.Enqueue(ListenerItem.Stopped(message));
                }
            }
            finally
            {
                CloseClient();
                tcpListener?.Stop();
            }
        }

        private void AcceptPendingClients()
        {
            while (tcpListener.Pending())
            {
                var candidate = tcpListener.AcceptTcpClient();
                candidate.NoDelay = true;
                if (client != null)
                {
                    RejectSecondClient(candidate);
                    continue;
                }

                client = candidate;
                incoming.Enqueue(ListenerItem.ClientAccepted());
            }
        }

        private void ServiceClient()
        {
            var activeClient = client;
            if (activeClient == null)
            {
                Thread.Sleep(10);
                return;
            }

            var stream = activeClient.GetStream();
            DrainOutgoing(stream);
            if (CloseAfterWrites())
            {
                return;
            }

            if (!activeClient.Client.Poll(50000, SelectMode.SelectRead))
            {
                return;
            }

            if (activeClient.Client.Available == 0)
            {
                throw new IOException("The client closed the connection.");
            }

            EnqueueFrame(MeshLinkSession.ReadFrame(stream));
        }

        private bool CloseAfterWrites()
        {
            if (!OutgoingIsEmpty())
            {
                return false;
            }

            if (stopAfterWrites)
            {
                incoming.Enqueue(ListenerItem.Stopped(failureMessage));
                stopRequested = true;
                return true;
            }

            if (!closeClientAfterWrites)
            {
                return false;
            }

            closeClientAfterWrites = false;
            CloseClient();
            incoming.Enqueue(ListenerItem.ClientClosed());
            return true;
        }

        private void RejectSecondClient(TcpClient candidate)
        {
            using (candidate)
            {
                var frame = MeshLinkSession.EncodeFrame(
                    "{\"type\":\"error\",\"message\":\"A client is already connected.\"}",
                    Array.Empty<byte>());
                try
                {
                    using (var stream = candidate.GetStream())
                    {
                        stream.Write(frame, 0, frame.Length);
                    }
                }
                catch (IOException)
                {
                }
                catch (SocketException)
                {
                }
            }
        }

        private void DrainOutgoing(NetworkStream stream)
        {
            while (TryDequeueOutgoing(out var frame))
            {
                stream.Write(frame, 0, frame.Length);
            }
        }

        private bool TryDequeueOutgoing(out byte[] frame)
        {
            lock (outgoingLock)
            {
                if (outgoing.Count == 0)
                {
                    frame = null;
                    return false;
                }

                frame = outgoing.Dequeue();
                return true;
            }
        }

        private bool OutgoingIsEmpty()
        {
            lock (outgoingLock)
            {
                return outgoing.Count == 0;
            }
        }

        private void EnqueueFrame(MeshLinkSession.Frame frame)
        {
            var total = Interlocked.Add(ref queuedBytes, frame.Size);
            if (total > MeshLinkSession.QueueLimit)
            {
                Interlocked.Add(ref queuedBytes, -frame.Size);
                throw new InvalidDataException(
                    "The Mesh Link frame queue is full.");
            }

            incoming.Enqueue(ListenerItem.FromFrame(frame));
        }

        private void CloseClient()
        {
            var activeClient = client;
            client = null;
            try
            {
                activeClient?.Close();
            }
            catch (Exception)
            {
            }
        }

        private void ClearQueues()
        {
            while (incoming.TryDequeue(out _))
            {
            }

            Interlocked.Exchange(ref queuedBytes, 0);
            lock (outgoingLock)
            {
                outgoing.Clear();
            }
        }

        private void ResetClientState()
        {
            PairingPending = false;
            paired = false;
            clientName = "Client";
            configuration = null;
            closeClientAfterWrites = false;
        }

        private static IPAddress ResolveAddress(string value)
        {
            if (IPAddress.TryParse(value, out var address))
            {
                return address;
            }

            var addresses = Dns.GetHostAddresses(value);
            var ipv4 = addresses.FirstOrDefault(
                item => item.AddressFamily == AddressFamily.InterNetwork);
            return ipv4 ?? addresses.FirstOrDefault() ??
                throw new SocketException((int)SocketError.HostNotFound);
        }

        private static bool IsControlFrame(string type)
        {
            return type == "hello" || type == "ping" || type == "pong" ||
                type == "error" || type == "set_session_config" ||
                type == "claim_sync";
        }

        private static bool IsSceneFrame(string type)
        {
            return type == "mesh_full" || type == "mesh_delta" ||
                type == "mesh_attributes" || type == "mesh_instance" ||
                type == "object_state" || type == "object_delete" ||
                type == "material" || type == "texture";
        }

        [Serializable]
        private sealed class RequestTextureDto : TypeDto
        {
            public string texture_id;
        }

        private sealed class ListenerItem
        {
            private ListenerItem(
                ListenerItemKind kind,
                MeshLinkSession.Frame frame,
                string error)
            {
                Kind = kind;
                Frame = frame;
                Error = error;
            }

            internal ListenerItemKind Kind { get; }
            internal MeshLinkSession.Frame Frame { get; }
            internal string Error { get; }

            internal static ListenerItem ClientAccepted()
            {
                return new ListenerItem(
                    ListenerItemKind.ClientAccepted, null, string.Empty);
            }

            internal static ListenerItem ClientClosed()
            {
                return new ListenerItem(
                    ListenerItemKind.ClientClosed, null, string.Empty);
            }

            internal static ListenerItem FromFrame(MeshLinkSession.Frame frame)
            {
                return new ListenerItem(
                    ListenerItemKind.Frame, frame, string.Empty);
            }

            internal static ListenerItem Stopped(string error)
            {
                return new ListenerItem(
                    ListenerItemKind.Stopped, null,
                    string.IsNullOrEmpty(error)
                        ? "The listener stopped."
                        : error);
            }
        }

        private enum ListenerItemKind
        {
            ClientAccepted,
            ClientClosed,
            Frame,
            Stopped
        }

        [Serializable]
        private class TypeDto
        {
            public string type;
        }

        [Serializable]
        private sealed class ClientHelloDto : TypeDto
        {
            public int protocol;
            public string pair_token;
            public string client_name;
        }

        [Serializable]
        private class HelloDto : TypeDto
        {
            public int protocol;
            public string client_name;
            public string[] capabilities;
        }

        [Serializable]
        private sealed class ApprovedHelloDto : HelloDto
        {
            public string pair_token;
        }

        [Serializable]
        private sealed class ErrorDto : TypeDto
        {
            public string message;
            public string request_id;
        }

        [Serializable]
        private sealed class ClaimSyncDto : TypeDto
        {
            public string source;
        }

        [Serializable]
        private sealed class SetSessionConfigDto : TypeDto
        {
            public int base_revision;
            public bool live_sync;
            public bool sync_view;
            public bool sync_objects;
            public bool sync_materials;
            public bool sync_lights;
            public bool sync_cameras;
            public bool sync_shading;
            public bool sync_postprocess;
        }

        [Serializable]
        private sealed class SessionConfigDto : TypeDto
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

            internal static SessionConfigDto Initial()
            {
                return new SessionConfigDto
                {
                    type = "session_config",
                    revision = 1,
                    live_sync = true,
                    sync_mode = "client",
                    active_source = "client",
                    sync_objects = true
                };
            }

            internal static SessionConfigDto FromUpdate(
                int revision,
                SetSessionConfigDto update)
            {
                return new SessionConfigDto
                {
                    type = "session_config",
                    revision = revision,
                    live_sync = update.live_sync,
                    sync_mode = "client",
                    active_source = "client",
                    sync_view = update.sync_view,
                    sync_objects = update.sync_objects,
                    sync_materials = update.sync_materials,
                    sync_lights = update.sync_lights,
                    sync_cameras = update.sync_cameras,
                    sync_shading = update.sync_shading,
                    sync_postprocess = update.sync_postprocess
                };
            }
        }
    }
}
