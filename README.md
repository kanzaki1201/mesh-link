# Mesh Link

See your [Blender](https://www.blender.org/) or [Nomad Sculpt](https://nomadsculpt.com/) meshes live in the Unity Editor, with your own Unity materials and shaders.

This is an unofficial project.
It is not affiliated with or endorsed by Blender, Nomad Sculpt or their developer.

<!-- screenshot: Blender and Unity side by side with a live preview -->

## Features

- **Live preview.** Edits, sculpt strokes, transforms, and visibility show in Unity as you work.
- **Your materials.** Put any Unity material on each slot: lilToon, MK Toon, MToon, URP Lit, and others.
- **Painted textures from Blender.** Your painted maps show on the Unity materials. **Auto Bake** sends them after each paint pause. Paint System and Ucupaint work.
- **Vertex paint from Nomad.** Nomad vertex colors show in Unity.
- **Safe.** The preview is temporary. Your material assets never change.

## Requirements

- Unity 6000.3 or later.
- Blender 5.0 or later, or Nomad Sculpt with App Linking.
- Both apps on the same machine or the same local network, with TCP port 48312 open.

## Install

**Unity:** open **Window > Package Manager**, select **+ > Install package from git URL**, and enter:

```text
https://github.com/kanzaki1201/mesh-link.git?path=unity/com.malloc.mesh-link
```

**Blender:**

1. Download `mesh_link-<version>.zip` from the [latest release](https://github.com/kanzaki1201/mesh-link/releases/latest).
2. In Blender, open **Edit > Preferences > Get Extensions > Install from Disk** and select the ZIP file.
3. Enable **Mesh Link**.

To update, install the newer ZIP file the same way.

## Start with Blender

### 1. Connect

1. In Unity, create an empty GameObject and add the **MeshLinkScene** component.
2. In its Inspector, turn **Listen** on and press **Enable Sync**.
3. In Blender, press **N** in the 3D Viewport and open the **Mesh Link** tab.
4. Press **Connect**.
5. The first time only, press **Accept** in the Unity Inspector.

Your visible meshes now show in Unity.

For two machines, set **Host** in Unity and Blender to the address of the Unity machine.

<!-- screenshot: Blender Mesh Link sidebar beside the Unity MeshLinkScene Inspector -->

### 2. Add materials

1. In the MeshLinkScene Inspector, press **Create Material Map**.
2. Drop a Unity material on each row of **Synced Object Materials**.

The Material Map remembers your choices for the next session.
Several scenes can share one map.

<!-- screenshot: Synced Object Materials rows with assigned materials -->

### 3. Show painted textures

1. In Blender, press **Bake & Send Textures** once.
2. In Unity, each map shows under its material row, on a matching shader property. Change the property in the drop-down list if needed.
3. Select **Auto Bake** in Blender and paint.

Two seconds after you stop painting, Auto Bake sends the maps that changed.
It works in Texture Paint mode and does not add undo steps.

Some shaders need a setting before a map shows. See [Shader setup](#shader-setup).

## Start with Nomad Sculpt

1. In Nomad, enable App Linking and note the address and port.
2. In Unity, add the **MeshLinkScene** component to an empty GameObject.
3. Leave **Listen** off, set **Host** and **Port** to the Nomad values, and press **Enable Sync**.
4. Accept the request in Nomad.

Add materials as in [Add materials](#2-add-materials).
Each Nomad object has one material slot.
To show vertex paint, use a Unity shader that reads vertex colors.

## Textures in detail

### Which maps are sent

Mesh Link reads what is linked into the Principled BSDF of each material:

| Blender input | Unity property by default |
|---|---|
| Base Color | The main texture of the shader (`_MainTex` or `_BaseMap`) |
| Emission Color | `_EmissionMap` |
| Normal | `_BumpMap` or `_NormalMap` |
| Metallic | `_MetallicMap` or `_MetallicGlossMap` |
| Roughness | `_RoughnessMap`, or `_SmoothnessTex` inverted |

- An input with no link sends nothing. Plain values, such as a color swatch, are not sent.
- A Paint System channel preview does not stop the maps.
- To change the property, use the drop-down list under the material row. Select `(unbound)` to hide a map.
- **Invert** flips a gray map, for example roughness into smoothness.
- Hidden objects and objects that do not render are skipped.
- Set the bake size in the extension preferences with **Texture Size** (default 1024).

### Extra maps, such as toon masks

Use a **Mesh Link Output** node:

1. In the Shader Editor, add **Shift+A > Mesh Link > Mesh Link Output**.
2. Press **N**, open the **Node** tab, and press **+** beside the input list.
3. Double-click the new input and name it, for example `Shadow Mask`.
4. Connect your mask to it.

The node also has the standard inputs `Color`, `Emission`, `Normal`, `Metallic`, and `Roughness`.
When a material has this node, Mesh Link reads only the node, so connect every map that you want to send.
Paint System outputs that do not go into the Principled BSDF, such as `Occlusion`, need this node.

Input names can use letters, digits, spaces, and hyphens.
In Unity, `Shadow Mask` shows as `x_shadow_mask`.

### Shader setup

Mesh Link cannot turn on shader features.
Turn on the feature before you send a map for it:

| Shader | Normal | Emission |
|---|---|---|
| lilToon | Turn on **Normal Map** | Turn on **Emission** |
| MK Toon | Assign any placeholder normal map | Assign any placeholder emission map |
| URP Lit | Assign any placeholder **Normal Map** | Turn on **Emission** and set its color to white |

MK Toon also needs a placeholder albedo map.
URP Lit smoothness is not filled.

## Troubleshooting

- **Nothing happens after Connect.** Press **Accept** on the MeshLinkScene in Unity. For Nomad, accept in Nomad.
- **The connection fails.** Check **Host** and **Port** on both sides, and the firewall. `127.0.0.1` works only on one machine.
- **Bake & Send Textures is grayed out.** Connect to Unity first.
- **Auto Bake turned itself off.** Read the error in the Mesh Link tab, fix it, and turn Auto Bake on again.
- **A texture does not show.** Check [Shader setup](#shader-setup), and select a property in the channel's drop-down list.
- **Blender reports an n-gon.** Make the faces triangles or quads, then connect again.
- **A second Blender cannot connect.** Unity accepts one Blender at a time.
- **MToon looks too bright up close.** This is a known issue.

## Limits

- Unity Editor only, one way from the source to Unity.
- The preview clears when sync stops, the connection drops, scripts reload, or Play Mode starts.
- No modifiers, armatures, hierarchy, cameras, or lights. Shape keys send the active key.
- Blender vertex colors and Nomad textures are not sent.
- Objects above 2,000,000 vertices stop the link.

## Development

### Blender extension from source

Link the extension folder into the Blender extension repository.
On Windows, close Blender and run this from the repository root:

```powershell
$source = (Resolve-Path .\blender\mesh_link).Path
$repository = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.2\extensions\user_default'
New-Item -ItemType Directory -Path $repository -Force | Out-Null
New-Item -ItemType Junction -Path (Join-Path $repository 'mesh_link') -Target $source
```

Use the folder of your Blender version.
Remove any installed copy of the extension first.
Then refresh local extensions in Preferences and enable Mesh Link.
Restart Blender after source changes.

### Unity package from source

Clone the repository to a folder outside your Unity projects.
Add an entry with the absolute path to `Packages/manifest.json` of each Unity project that uses it:

```json
"com.malloc.mesh-link": "file:C:/path/to/mesh-link/unity/com.malloc.mesh-link"
```

### Tests

```powershell
python -m pytest blender/tests
blender --background --factory-startup --python blender/tests/blender_bake_smoke.py
unity test C:/path/to/UnityProject --mode EditMode --filter Malloc.MeshLink --output mesh-link-test-results.xml
```

Python tests need NumPy and pytest.
For Unity tests, add `com.malloc.mesh-link` to the `testables` array of the host manifest.

## Credits
[Nomad Link](https://github.com/stephomi/nomad-link), by stephomi

Support Nomad Sculpt!: https://nomadsculpt.com/


## License

- Unity package (`unity/`): MIT. See [unity/com.malloc.mesh-link/LICENSE](unity/com.malloc.mesh-link/LICENSE).
- Blender extension (`blender/`): GPL-3.0-or-later. See [blender/mesh_link/LICENSE](blender/mesh_link/LICENSE).
