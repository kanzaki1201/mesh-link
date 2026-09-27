using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace Malloc.MeshLink
{
    internal sealed class MeshLinkTextures
    {
        private static readonly Regex ExtraChannel = new Regex("^x_[a-z0-9_]+$");
        private readonly Dictionary<string, byte[]> blobs =
            new Dictionary<string, byte[]>(StringComparer.Ordinal);
        private readonly Dictionary<string, Texture2D> decoded =
            new Dictionary<string, Texture2D>(StringComparer.Ordinal);
        private readonly Dictionary<string, Dictionary<int, Dictionary<string, ChannelState>>> slots =
            new Dictionary<string, Dictionary<int, Dictionary<string, ChannelState>>>(StringComparer.Ordinal);
        private readonly HashSet<string> referenced = new HashSet<string>(StringComparer.Ordinal);
        private readonly Action<string> requestTexture;
        private readonly Action<string> setStatus;
        private readonly Action notifyInspector;

        internal MeshLinkTextures(Action<string> requestTexture, Action<string> setStatus,
            Action notifyInspector)
        {
            this.requestTexture = requestTexture;
            this.setStatus = setStatus;
            this.notifyInspector = notifyInspector;
        }

        internal int BlobCount => blobs.Count;
        internal int DecodedCount => decoded.Count;

        internal string[] GetChannels(string meshId, int slot)
        {
            return slots.TryGetValue(meshId, out var objectSlots) &&
                objectSlots.TryGetValue(slot, out var channels)
                ? channels.Keys.OrderBy(channel => channel, StringComparer.Ordinal).ToArray()
                : Array.Empty<string>();
        }

        internal void ApplyMaterial(string json, Func<string, Renderer> findRenderer,
            MeshLinkMaterialMap map)
        {
            try
            {
                var fields = ReadFields(json);
                if (!fields.TryGetValue("mesh_id", out var meshJson)) return;
                var meshId = JsonUtility.FromJson<StringValue>("{\"value\":" + meshJson + "}").value;
                var renderer = findRenderer(meshId);
                if (renderer == null)
                {
                    setStatus("Connected. Skipped material: unknown mesh_id.");
                    return;
                }

                var slot = 0;
                if (fields.TryGetValue("slot_index", out var slotJson) &&
                    !int.TryParse(slotJson, out slot) ||
                    slot < 0 || slot >= renderer.sharedMaterials.Length)
                {
                    setStatus("Connected. Skipped material: slot_index is out of range.");
                    return;
                }

                if (!fields.TryGetValue("material", out var materialJson) ||
                    !ReadFields(materialJson).TryGetValue("textures", out var texturesJson)) return;
                var updates = ReadFields(texturesJson);
                var textureIds = updates.Where(update => IsSupportedChannel(update.Key))
                    .ToDictionary(update => update.Key,
                        update => ReadTextureId(update.Value), StringComparer.Ordinal);
                var channels = GetOrCreateChannels(meshId, slot);
                foreach (var update in textureIds)
                {
                    ApplyChannel(channels, update.Key, update.Value);
                }

                Reapply(meshId, renderer, map);
                TrimCache();
                notifyInspector();
            }
            catch (Exception exception) when (exception is FormatException ||
                exception is ArgumentException)
            {
                setStatus("Connected. Skipped material: " + exception.Message);
            }
        }

        private static string ReadTextureId(string json)
        {
            if (json == "null") return null;
            var fields = ReadFields(json);
            if (!fields.TryGetValue("texture_id", out var idJson) || idJson == "null") return null;
            if (idJson.Length < 2 || idJson[0] != '"' || idJson[idJson.Length - 1] != '"')
                throw new FormatException("Invalid texture_id.");
            return JsonUtility.FromJson<TextureReference>(json).texture_id;
        }

        private void ApplyChannel(Dictionary<string, ChannelState> channels,
            string channel, string textureId)
        {
            if (!channels.TryGetValue(channel, out var state))
            {
                state = new ChannelState();
                channels.Add(channel, state);
            }
            if (string.IsNullOrEmpty(textureId))
            {
                state.Id = null;
                state.PendingId = null;
            }
            else if (blobs.ContainsKey(textureId))
            {
                state.PendingId = null;
                if (Decode(textureId, IsLinear(channel)) != null)
                {
                    state.Id = textureId;
                }
            }
            else
            {
                state.PendingId = textureId;
                requestTexture(textureId);
            }
            if (!string.IsNullOrEmpty(textureId)) referenced.Add(textureId);
        }

        internal void ApplyBlob(string json, byte[] binary,
            Func<string, Renderer> findRenderer, MeshLinkMaterialMap map)
        {
            var header = JsonUtility.FromJson<TextureMessage>(json);
            if (header == null || string.IsNullOrEmpty(header.texture_id))
            {
                setStatus("Connected. Skipped texture: missing texture_id.");
                return;
            }

            if (blobs.ContainsKey(header.texture_id)) return;
            using (var sha = SHA256.Create())
            {
                var hash = BitConverter.ToString(sha.ComputeHash(binary))
                    .Replace("-", string.Empty).ToLowerInvariant();
                if (header.texture_id != hash)
                {
                    setStatus("Connected. Skipped texture: texture_id does not match the PNG.");
                    return;
                }
            }

            blobs.Add(header.texture_id, binary);
            foreach (var objectSlots in slots)
            {
                foreach (var slot in objectSlots.Value.ToArray())
                {
                    var changed = false;
                    foreach (var channel in slot.Value)
                    {
                        if (channel.Value.PendingId != header.texture_id) continue;
                        if (Decode(header.texture_id, IsLinear(channel.Key)) == null)
                        {
                            channel.Value.PendingId = null;
                            continue;
                        }
                        channel.Value.Id = header.texture_id;
                        channel.Value.PendingId = null;
                        changed = true;
                    }
                    if (changed)
                        Reapply(objectSlots.Key, findRenderer(objectSlots.Key), map);
                }
            }
        }

        internal void Reapply(string meshId, Renderer renderer, MeshLinkMaterialMap map)
        {
            if (renderer == null || !slots.TryGetValue(meshId, out var objectSlots)) return;
            var materials = renderer.sharedMaterials;
            foreach (var slot in objectSlots.Keys.Where(slot => slot >= materials.Length).ToArray())
                objectSlots.Remove(slot);
            foreach (var slot in objectSlots)
            {
                if (slot.Key >= materials.Length) continue;
                var block = new MaterialPropertyBlock();
                foreach (var channel in slot.Value)
                {
                    var state = channel.Value;
                    var binding = ResolveProperty(materials[slot.Key], channel.Key, map);
                    if (state.Id == null || binding.Property == null) continue;
                    var texture = Decode(state.Id, IsLinear(channel.Key), binding.Invert);
                    if (texture == null) continue;
                    block.SetTexture(binding.Property, texture);
                }
                // Unity ignores a block that matches the current one by value, even under another property name.
                renderer.SetPropertyBlock(null, slot.Key);
                renderer.SetPropertyBlock(block, slot.Key);
            }
        }

        internal void ReapplyAll(Func<string, Renderer> findRenderer, MeshLinkMaterialMap map)
        {
            foreach (var meshId in slots.Keys.ToArray())
                Reapply(meshId, findRenderer(meshId), map);
        }

        internal (string Property, bool Invert) ResolveProperty(Material material, string channel,
            MeshLinkMaterialMap map)
        {
            if (material == null || material.shader == null) return (null, false);
            var binding = map?.TextureBindings.Find(item =>
                item.material == material && item.channel == channel);
            if (binding != null)
                return (HasTextureProperty(material.shader, binding.property) ? binding.property : null,
                    binding.invert && IsLinear(channel));
            if (channel == "color")
                return (ResolveColorProperty(material.shader), false);
            if (channel == "emissive") return (FirstTextureProperty(material.shader, "_EmissionMap"), false);
            if (channel == "normal") return (FirstTextureProperty(material.shader, "_BumpMap", "_NormalMap"), false);
            if (channel == "metalness")
                return (FirstTextureProperty(material.shader, "_MetallicMap", "_MetallicGlossMap"), false);
            if (channel == "roughness")
            {
                var property = FirstTextureProperty(material.shader, "_RoughnessMap");
                if (property != null) return (property, false);
                property = FirstTextureProperty(material.shader, "_SmoothnessTex");
                return (property, property != null);
            }
            return (null, false);
        }

        private static string ResolveColorProperty(Shader shader)
        {
            for (var index = 0; index < shader.GetPropertyCount(); index++)
                if (shader.GetPropertyType(index) == ShaderPropertyType.Texture &&
                    (shader.GetPropertyFlags(index) & ShaderPropertyFlags.MainTexture) != 0)
                    return shader.GetPropertyName(index);
            return FirstTextureProperty(shader, "_MainTex", "_BaseMap");
        }

        internal bool SetBinding(string meshId, int slot, string channel, string property, bool invert,
            bool updateProperty, Func<string, Renderer> findRenderer, MeshLinkMaterialMap map)
        {
            if (map == null) return false;
            var material = BindableMaterial(findRenderer(meshId), meshId, slot, channel,
                property, updateProperty);
            if (material == null) return false;
            var binding = map.TextureBindings.Find(item =>
                item.material == material && item.channel == channel);
            if (!updateProperty && binding == null)
                property = ResolveProperty(material, channel, map).Property;
            Undo.RecordObject(map, "Bind Mesh Link texture");
            if (binding == null)
                map.TextureBindings.Add(new TextureBindingEntry
                {
                    material = material, channel = channel, property = property, invert = invert
                });
            else
            {
                if (updateProperty) binding.property = property;
                binding.invert = invert;
            }
            EditorUtility.SetDirty(map);
            if (EditorUtility.IsPersistent(map)) AssetDatabase.SaveAssetIfDirty(map);
            foreach (var objectSlots in slots)
            {
                var target = findRenderer(objectSlots.Key);
                if (target != null && target.sharedMaterials.Any(item => item == material))
                    Reapply(objectSlots.Key, target, map);
            }
            notifyInspector();
            return true;
        }

        private Material BindableMaterial(Renderer renderer, string meshId, int slot,
            string channel, string property, bool updateProperty)
        {
            if (renderer == null || slot < 0 || slot >= renderer.sharedMaterials.Length)
                return null;
            var material = renderer.sharedMaterials[slot];
            return material != null && GetChannels(meshId, slot).Contains(channel) &&
                (!updateProperty || string.IsNullOrEmpty(property) ||
                HasTextureProperty(material.shader, property)) ? material : null;
        }

        internal static string[] GetTextureProperties(Material material)
        {
            if (material == null || material.shader == null) return Array.Empty<string>();
            var shader = material.shader;
            return Enumerable.Range(0, shader.GetPropertyCount())
                .Where(index => shader.GetPropertyType(index) == ShaderPropertyType.Texture)
                .Select(shader.GetPropertyName).ToArray();
        }

        internal void RemoveObject(string meshId)
        {
            slots.Remove(meshId);
            TrimCache();
        }

        internal void Clear()
        {
            foreach (var texture in decoded.Values)
                if (texture != null) UnityEngine.Object.DestroyImmediate(texture);
            decoded.Clear();
            blobs.Clear();
            slots.Clear();
            referenced.Clear();
        }

        private static bool IsSupportedChannel(string channel)
        {
            return channel == "color" || channel == "emissive" ||
                channel == "normal" || channel == "metalness" ||
                channel == "roughness" || ExtraChannel.IsMatch(channel);
        }

        internal static bool IsLinear(string channel)
        {
            return channel != "color" && channel != "emissive";
        }

        private static string FirstTextureProperty(Shader shader, params string[] names)
        {
            return names.FirstOrDefault(name => HasTextureProperty(shader, name));
        }

        private static bool HasTextureProperty(Shader shader, string name)
        {
            if (shader == null || string.IsNullOrEmpty(name)) return false;
            var index = shader.FindPropertyIndex(name);
            return index >= 0 && shader.GetPropertyType(index) == ShaderPropertyType.Texture;
        }

        private Texture2D Decode(string id, bool linear, bool invert = false)
        {
            var key = id + (linear ? ":linear" : ":srgb") + (invert ? ":invert" : ":direct");
            if (decoded.TryGetValue(key, out var texture)) return texture;
            if (!blobs.TryGetValue(id, out var bytes)) return null;
            texture = new Texture2D(2, 2, TextureFormat.RGBA32, true, linear)
            {
                hideFlags = HideFlags.DontSave,
                wrapMode = TextureWrapMode.Repeat,
                filterMode = FilterMode.Bilinear
            };
            var loaded = false;
            try { loaded = ImageConversion.LoadImage(texture, bytes, false); }
            catch (Exception)
            {
            }
            if (!loaded)
            {
                UnityEngine.Object.DestroyImmediate(texture);
                setStatus("Connected. Skipped texture channel: PNG decode failed.");
                return null;
            }
            if (invert)
            {
                var pixels = texture.GetPixels32();
                for (var index = 0; index < pixels.Length; index++)
                {
                    pixels[index].r = (byte)(255 - pixels[index].r);
                    pixels[index].g = (byte)(255 - pixels[index].g);
                    pixels[index].b = (byte)(255 - pixels[index].b);
                }
                texture.SetPixels32(pixels);
                texture.Apply(true, true);
            }
            decoded.Add(key, texture);
            return texture;
        }

        private Dictionary<string, ChannelState> GetOrCreateChannels(string meshId, int slot)
        {
            if (!slots.TryGetValue(meshId, out var objectSlots))
            {
                objectSlots = new Dictionary<int, Dictionary<string, ChannelState>>();
                slots.Add(meshId, objectSlots);
            }
            if (!objectSlots.TryGetValue(slot, out var channels))
            {
                channels = new Dictionary<string, ChannelState>(StringComparer.Ordinal);
                objectSlots.Add(slot, channels);
            }
            return channels;
        }

        private void TrimCache()
        {
            var active = new HashSet<string>(StringComparer.Ordinal);
            foreach (var objectSlots in slots.Values)
                foreach (var channels in objectSlots.Values)
                    foreach (var state in channels.Values)
                    {
                        if (state.Id != null) active.Add(state.Id);
                        if (state.PendingId != null) active.Add(state.PendingId);
                    }
            foreach (var id in blobs.Keys.Where(id => referenced.Contains(id) &&
                !active.Contains(id)).ToArray())
            {
                blobs.Remove(id);
                referenced.Remove(id);
                foreach (var key in new[] { id + ":linear:direct", id + ":srgb:direct",
                    id + ":linear:invert", id + ":srgb:invert" })
                {
                    if (!decoded.TryGetValue(key, out var texture)) continue;
                    UnityEngine.Object.DestroyImmediate(texture);
                    decoded.Remove(key);
                }
            }
            referenced.RemoveWhere(id => !active.Contains(id) && !blobs.ContainsKey(id));
        }

        private static Dictionary<string, string> ReadFields(string json)
        {
            var fields = new Dictionary<string, string>(StringComparer.Ordinal);
            var position = 0;
            SkipWhitespace(json, ref position);
            if (position >= json.Length || json[position++] != '{')
                throw new FormatException("Expected a JSON object.");
            while (true)
            {
                SkipWhitespace(json, ref position);
                if (position < json.Length && json[position] == '}') return fields;
                var key = ReadString(json, ref position);
                SkipWhitespace(json, ref position);
                if (position >= json.Length || json[position++] != ':')
                    throw new FormatException("Expected a JSON field value.");
                SkipWhitespace(json, ref position);
                var start = position;
                SkipValue(json, ref position);
                fields[key] = json.Substring(start, position - start).Trim();
                SkipWhitespace(json, ref position);
                if (position >= json.Length) throw new FormatException("Incomplete JSON object.");
                if (json[position++] == '}') return fields;
                if (json[position - 1] != ',') throw new FormatException("Expected a JSON field.");
            }
        }

        private static string ReadString(string json, ref int position)
        {
            if (position >= json.Length || json[position++] != '"')
                throw new FormatException("Expected a JSON field name.");
            var start = position;
            while (position < json.Length)
            {
                if (json[position] == '\\')
                {
                    position += 2;
                    continue;
                }
                if (json[position++] == '"')
                    return json.Substring(start, position - start - 1);
            }
            throw new FormatException("Incomplete JSON string.");
        }

        private static void SkipValue(string json, ref int position)
        {
            if (position >= json.Length) throw new FormatException("Missing JSON value.");
            if (json[position] == '"')
            {
                ReadString(json, ref position);
                return;
            }
            if (json[position] == '{' || json[position] == '[')
            {
                var closing = json[position++] == '{' ? '}' : ']';
                while (position < json.Length && json[position] != closing)
                {
                    if (json[position] == '"') ReadString(json, ref position);
                    else if (json[position] == '{' || json[position] == '[')
                        SkipValue(json, ref position);
                    else position++;
                }
                if (position >= json.Length) throw new FormatException("Incomplete JSON value.");
                position++;
                return;
            }
            while (position < json.Length && json[position] != ',' && json[position] != '}')
                position++;
        }

        private static void SkipWhitespace(string json, ref int position)
        {
            while (position < json.Length && char.IsWhiteSpace(json[position])) position++;
        }

        private sealed class ChannelState
        {
            internal string Id;
            internal string PendingId;
        }

        [Serializable]
        private sealed class StringValue
        {
            public string value;
        }

        [Serializable]
        private sealed class TextureReference
        {
            public string texture_id;
        }

        [Serializable]
        private sealed class TextureMessage
        {
            public string texture_id;
        }
    }
}
