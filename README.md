# Mesh Link

Mesh Link shows your [Blender](https://www.blender.org/) or [Nomad Sculpt](https://nomadsculpt.com/) meshes live in the Unity Editor.
You edit in Blender or Nomad.
Unity updates the preview while you work, with your own Unity materials and shaders.

This is an unofficial project.
It is not affiliated with or endorsed by Blender, Nomad Sculpt or their developer.

<!-- screenshot: Blender and Unity side by side with a live preview -->

## Features

- **Live mesh preview.** Vertex edits, sculpt strokes, transforms, names, and visibility reach Unity as you work.
- **Blender and Nomad Sculpt.** Blender connects through the Mesh Link extension. Nomad connects through App Linking.
- **Your Unity materials.** Assign any Unity material to each material slot. lilToon, MK Toon, MToon, URP Lit, and other shaders work.
- **Saved assignments.** A Material Map asset keeps your material choices across sessions, Unity restarts, and scenes.
- **Texture preview from Blender.** One button bakes your painted maps and shows them on the Unity materials. Layered painting add-ons such as Paint System and Ucupaint work.
- **Vertex paint from Nomad.** Nomad vertex colors and opacity reach Unity.
- **Non-destructive.** The preview is temporary. Mesh Link never changes your material assets.

## Requirements

- Unity 6000.3 or later.
- Blender 5.0 or later (tested with Blender 5.2), or a Nomad Sculpt version with App Linking.
- The source and Unity run on the same machine or on the same local network.
- The TCP port must be open between them. The default port is 48312.

## Installation

### Unity package

In Unity, open **Window > Package Manager**, select **+ > Install package from git URL**, and enter:

```text
https://github.com/kanzaki1201/mesh-link.git?path=unity/com.malloc.mesh-link
```

### Blender extension

1. Zip the contents of `blender/mesh_link/` (the `blender_manifest.toml` file and the Python files).
2. In Blender, open **Edit > Preferences > Get Extensions**.
3. Select **Install from Disk** and choose the ZIP file.
4. Enable **Mesh Link**.

## Quick start: Blender to Unity

1. In Unity, create an empty GameObject and add the **MeshLinkScene** component.
2. In its Inspector, turn **Listen** on.
3. Set **Host** to `127.0.0.1` when Blender runs on the same machine. Otherwise, set it to the local network address of the Unity machine.
4. Press **Enable Sync**.
5. In Blender, open the 3D Viewport sidebar (**N**) and select the **Mesh Link** tab.
6. Set **Host** and **Port** to the Unity values and press **Connect**.
7. The first time, Unity shows **Accept** in the MeshLinkScene Inspector. Press it. Later connections skip this step.
8. Your visible mesh objects appear in Unity. Edit them in Blender and watch Unity update.
9. Assign Unity materials in the Inspector. See [Materials](#materials).
10. Press **Disconnect** in Blender when you are done.

<!-- screenshot: Blender Mesh Link sidebar beside the Unity MeshLinkScene Inspector -->

Blender sends the base mesh of every visible mesh object in the current view layer.
Modifiers are not applied.
Shape keys send the positions of the active key.
Faces must be triangles or quads.

## Quick start: Nomad Sculpt to Unity

1. In Nomad, enable App Linking and note the device address and port.
2. In Unity, create an empty GameObject and add the **MeshLinkScene** component.
3. Leave **Listen** off.
4. Set **Host** and **Port** to the values from Nomad.
5. Press **Enable Sync**.
6. Accept the pairing request in Nomad.
7. Your Nomad meshes appear in Unity. Sculpt and watch Unity update.
8. Press **Disable Sync** in Unity to stop.

Each Nomad object has one material slot.
To use several Unity materials on one Nomad mesh, split it into separate objects.
To show Nomad vertex paint, use a Unity shader that reads vertex colors.

## Materials

The MeshLinkScene Inspector lists **Synced Object Materials**.
Each row is one object and one material slot.
Drop a Unity material into a row to assign it.

<!-- screenshot: Synced Object Materials rows with assigned materials -->

To keep your assignments, give the scene a **Material Map** asset:

1. In the MeshLinkScene Inspector, press **Create Material Map**, or select an existing map in the **Material Map** field.
2. Assign materials as usual.

The map saves each assignment.
When an object appears again, Mesh Link applies its saved material.
Several scenes can share one map.
**Clear Stored Materials** empties the map for every scene that uses it.
Without a map, assignments last only until sync stops.

## Texture preview (Blender)

Texture preview shows your painted maps from Blender on the Unity materials.
It works with plain image textures, baked maps, and layered painting add-ons such as Paint System and Ucupaint.

### How to use it

1. Connect Blender to Unity as in the quick start.
2. In Unity, assign materials to the slots.
3. Paint in Blender.
4. In the Mesh Link sidebar, press **Bake & Send Textures**.
5. Unity shows the baked maps on the preview.

Select **Auto Bake** under the button to bake and send changes without the button.
When you stop painting for two seconds, Mesh Link bakes again only the channels whose sources changed.
A brush stroke or a viewport drag in progress delays the bake.
Auto Bake works in Texture Paint mode and adds no undo steps.
The checkbox sends nothing when you turn it on.
Press **Bake & Send Textures** once to send a full set, then paint.
If an auto bake fails, Mesh Link turns **Auto Bake** off and shows the error in the sidebar.

Mesh Link bakes each material slot of each visible object with Cycles, on the active UV map.
Blender waits until the bake ends.
Set the bake size in the extension preferences with **Texture Size** (512 to 4096, default 1024).
Hidden objects and objects that do not render are skipped.

### What is sent

Mesh Link reads the inputs of the first Principled BSDF in the material.
The Principled BSDF does not need to feed the Material Output.
A channel preview of a layer add-on, such as the Paint System preview, therefore keeps the channels.
A material that has a **Mesh Link Output** node sends only what that node receives.

| Blender input | Channel | Unity property by default |
|---|---|---|
| Base Color | `color` | The shader's main texture, else `_MainTex`, else `_BaseMap` |
| Emission Color | `emissive` | `_EmissionMap` |
| Normal | `normal` | `_BumpMap`, else `_NormalMap` |
| Metallic | `metalness` | `_MetallicMap`, else `_MetallicGlossMap` |
| Roughness | `roughness` | `_RoughnessMap`, else `_SmoothnessTex` with invert |

An input that has no link sends nothing.
Emission strength is not sent.
In Unity, each channel appears under its material row.
Select a shader property from the channel's drop-down list to show the map.
The list needs a Material Map.
Select `(unbound)` to turn the channel off for that material, and select a property again to turn it back on.
Only linear channel rows (`normal`, `metalness`, `roughness`, and `x_*`) show the **Invert** toggle.
The toggle needs a Material Map.
Invert reverses the map's RGB values and keeps its alpha value.
For `roughness`, invert is on by default when the shader has `_SmoothnessTex` and no `_RoughnessMap`, as with lilToon.

### Extra maps, such as toon masks

Toon shaders use extra maps, for example shadow or rim masks.
Send them with a **Mesh Link Output** node:

1. In the Shader Editor, add **Shift+A > Mesh Link > Mesh Link Output**.
2. Open the sidebar (**N**), select the **Node** tab, and find the node's input list.
3. Press **+** beside the list. Double-click the new input and type `Shadow Mask`.
4. Connect your mask to that input.
5. Press **Bake & Send Textures**.

The input `Shadow Mask` sends `x_shadow_mask`.
Mesh Link trims and lowercases the input name, and replaces spaces and hyphens with `_`.
After conversion, the name can contain only `a-z`, `0-9`, and `_`.
An empty name, or two names that give the same key, stops the bake with an error.
Rename and reorder custom inputs in the same list.
Press **-** to remove the selected custom input.

The node also has the five standard inputs, `Color`, `Emission`, `Normal`, `Metallic`, and `Roughness`.
They send the channels of the table above.
Do not rename them or remove them, or the bake stops with an error.
If a material has a Mesh Link Output node, Mesh Link ignores its Principled BSDF.
Link all the standard channels you want to send to the node.
A material with more than one node uses the first one.

Outputs of a layer add-on that do not feed the Principled BSDF, such as the Paint System `Occlusion` output, are not sent by default.
Add a custom input to the Mesh Link Output node and connect the output to it.

### Shader settings

Mesh Link cannot turn on shader features.
Turn on the feature on the material before you preview a map for it.

| Shader | Base color | Normal | Emission |
|---|---|---|---|
| lilToon | Always shown | Turn on **Normal Map** (`_UseBumpMap`) | Turn on **Emission** (`_UseEmission`) |
| MK Toon | Assign any placeholder albedo map | Assign any placeholder normal map | Assign any placeholder emission map |
| URP Lit | Always shown | Assign any placeholder **Normal Map** | Turn on **Emission** and set its color to white |

MK Toon and URP Lit enable a map only when their Inspector receives a texture, so a placeholder texture is necessary.
The URP Lit emission color multiplies the map, and its default is black.
URP Lit packs smoothness into the alpha of `_MetallicGlossMap`.
Mesh Link does not fill that alpha.

Preview textures are temporary.
Mesh Link removes them when sync stops.
Your material assets do not change.

## Troubleshooting

- **Pairing waits and nothing happens.** For Blender, select the MeshLinkScene GameObject in Unity and press **Accept**. For Nomad, accept the request in Nomad.
- **The connection fails.** Check **Host** and **Port** on both sides. Check that the firewall allows TCP on that port. The address `127.0.0.1` works only on the same machine.
- **A second Blender instance cannot connect.** Unity accepts one Blender client at a time. Disconnect the first one.
- **Blender reports an n-gon.** Convert the faces of that object to triangles or quads, then connect again.
- **Bake & Send Textures is grayed out.** Connect to Unity first.
- **Auto Bake turned itself off.** An auto bake failed. Read the error in the Mesh Link sidebar, fix it, and select **Auto Bake** again.
- **A texture does not show.** Turn on the matching feature on the material. See [Shader settings](#shader-settings). For an `x_` channel, select a property in the drop-down list.
- **MToon looks too bright up close.** This is a known issue with MToon on preview meshes.

## Limits

- Mesh Link works in the Unity Editor only, not in a built player.
- Changes go one way, from the source to Unity.
- The preview is not saved. Stopping sync, a lost connection, closing the scene, reloading scripts, or entering Play Mode clears it.
- Mesh Link does not find sources automatically and does not reconnect by itself.
- Mesh Link does not send hierarchy, cameras, lights, armatures, or modifier results.
- Mesh Link does not send material values such as colors and numbers; only linked inputs bake.
- Nomad textures are not sent. Nomad roughness, metalness, and paint layers are ignored.
- Blender vertex colors are not sent.
- A Blender object with more than 2,000,000 vertices stops the link.
- An object with a sheared transform keeps its last valid state.

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

All projects and the Blender junction then use the same checkout.

### Tests

Python tests need Python, NumPy, and pytest.
Run them from the repository root:

```powershell
python -m pytest blender/tests
```

The Blender bake check runs in Blender:

```powershell
blender --background --factory-startup --python blender/tests/blender_bake_smoke.py
```

For Unity tests, add `com.malloc.mesh-link` to the `testables` array of the host manifest.
With the Unity CLI, run this with the path of that Unity project:

```powershell
unity test C:/path/to/UnityProject --mode EditMode --filter Malloc.MeshLink --output mesh-link-test-results.xml
```

## Credits
[Nomad Link](https://github.com/stephomi/nomad-link), by stephomi

Support Nomad Sculpt!: https://nomadsculpt.com/


## License

- Unity package (`unity/`): MIT. See [unity/com.malloc.mesh-link/LICENSE](unity/com.malloc.mesh-link/LICENSE).
- Blender extension (`blender/`): GPL-3.0-or-later. See [blender/mesh_link/LICENSE](blender/mesh_link/LICENSE).
