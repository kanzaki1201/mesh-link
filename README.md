# Mesh Link

Mesh Link provides a one-way live mesh link into the Unity Editor.
Nomad Sculpt sends through Nomad App Linking while Unity uses connect mode.
Blender sends through the Mesh Link extension while Unity uses listen mode.
Unity previews meshes with Unity materials that you assign.
Preview objects and meshes are transient; material assignments are saved on the `MeshLinkScene` component.

## Requirements

- Unity 6000.3 or later.
- For Nomad: a Nomad version with App Linking.
- For Blender: Blender 5.0 or later; tested with Blender 5.2.
- The source and Unity run on the same machine or the same local network.
- The configured TCP port must be reachable; Mesh Link defaults to port 48312.

## Install the Unity package

In Unity Package Manager, install the package from this git URL:

```text
https://github.com/kanzaki1201/mesh-link.git?path=unity/com.malloc.mesh-link
```

For a local checkout at Packages/mesh-link, use this entry in the host project's `Packages/manifest.json`:

```json
"com.malloc.mesh-link": "file:mesh-link/unity/com.malloc.mesh-link"
```

Add a `MeshLinkScene` component to an empty GameObject and use its Inspector:

- `Host`: the Nomad device address in connect mode, or the local address Unity binds in listen mode.
- `Port`: the TCP port, matching the source connection settings.
- `Listen`: on for Blender; off for Nomad.
- `Enable Sync` / `Disable Sync`: start or stop the session; connection fields are locked while sync is enabled.

The `Status` field shows connection progress and errors.

<!-- screenshot: MeshLinkScene Inspector connection fields and sync button -->

## Blender

### Install the extension

Zip `blender_manifest.toml` and the Python files inside `blender/mesh_link/` together.
Use Blender's extension installation from disk to select the ZIP, then enable the extension.
For development from a checkout, use the junction instructions under Source development below.

### Tutorial

1. In Unity, set `Host` to 127.0.0.1 for the same machine, or to the Unity computer's local network address for another machine.
   Set `Port`, turn `Listen` on, and press `Enable Sync`.
2. In Blender's 3D Viewport sidebar, open the `Mesh Link` tab.
   Set `Host` to the Unity computer's address and `Port` to the Unity listener port, then press `Connect`.
3. Select the GameObject with `MeshLinkScene` in Unity and press `Accept` in its Inspector when pairing is pending.
   A saved pairing token allows later connections without another approval.
4. Check that visible mesh objects from Blender's current view layer appear in Unity.
5. Edit vertices in Blender and watch the Unity preview update.
6. In Unity, assign a Unity material to each slot in `Synced Object Materials (N)`, where N is the object count.
7. Hide, rename, or delete a synchronized object in Blender and check its visibility, name, or removal in Unity.
8. Press `Disconnect` in Blender to end the link.

The extension sends the base mesh, the active UV layer, and one material slot per Blender slot.
An object with no slots sends one slot named after the object.
Shape keys supply the active key's vertex positions.
Transforms, names, and visibility also travel to Unity.
Armatures, modifier results, normals, and vertex colors do not travel; Unity recalculates normals.
Faces must be triangles or quads; n-gons stop the link.

<!-- screenshot: Blender Mesh Link sidebar beside the Unity mesh preview -->

## Nomad Sculpt

### Setup

Leave Unity's `Listen` off.
Set `Host` to the device IP shown in Nomad and `Port` to the port shown by Nomad.

### Tutorial

1. Enable App Linking in Nomad.
2. Select the GameObject with `MeshLinkScene` in Unity, check `Host` and `Port`, and press `Enable Sync`.
3. Accept the pairing request in Nomad when prompted.
4. Check that the Nomad meshes appear in Unity.
5. Assign a Unity material to each object in `Synced Object Materials (N)`.
6. Sculpt a mesh in Nomad and watch the Unity preview update.
7. Move, hide, or delete an object in Nomad and check the corresponding Unity preview change.
8. Press `Disable Sync` in Unity to stop and clear the preview.

Each Nomad object has one Unity material slot.
Split a Nomad object into separate objects when it needs multiple Unity materials.
Nomad vertex colors and opacity are supported; the assigned Unity shader must use vertex colors to show the paint.

## Materials

The `MeshLinkScene` Inspector shows `Synced Object Materials (N)` with one row per object and slot.
Each row shows the object and slot names, a short mesh ID, and a Unity material field.
Assign, replace, or clear a material in that field.
Objects that share geometry keep independent material assignments.

Assignments are stored on the `MeshLinkScene` component, so they survive a stop, a reconnect, and a Unity restart.
A stored assignment is reapplied when an object with the same mesh ID and slot appears, or, failing that, the same object and slot names.
Assignments record Undo and mark the scene dirty; save the scene to keep them.
`Stored Materials (N)` shows the store size, and `Clear Stored Materials` empties it.
MToon materials can show close-range bloom on synchronized meshes.

<!-- screenshot: Synced Object Materials with separate object and slot assignments -->

## Troubleshooting

- Pairing is pending with no prompt: for Blender, select the owning `MeshLinkScene` GameObject and use `Accept` in Unity's Inspector; for Nomad, approve in Nomad.
- A second client is refused: the Unity listener accepts one client at a time; use `Disconnect` on the first Blender client before connecting another.
- Connection fails: check `Host`, matching `Port` values, and the firewall's TCP access to that port; 127.0.0.1 works only on the same machine.
- Nothing updates: check `Enable Sync` and the session configuration: `live_sync=true`, `sync_objects=true`, and `active_source=client` for Blender or `active_source=nomad` for Nomad.
- A mesh looks flipped: conversion is automatic: Nomad (x, y, z) becomes Unity (x, y, -z); Blender (x, y, z) becomes Unity (x, z, y), with triangle winding reversed by Unity.
- An n-gon error names the object: convert its faces to triangles or quads in Blender, then reconnect.

## Limits

- Unity Editor preview only; one scene owns the global session and one client can use the listener.
- Synchronization is one-way; Unity edits do not return to the source.
- Preview objects and meshes are not saved; stopping sync, losing the connection, closing the scene, reloading scripts, or changing Play Mode clears them.
- No automatic discovery or reconnection.
- No hierarchy, groups, cameras, lights, armatures, or evaluated Blender modifiers.
- No source material assets, textures, or shading transfer; assign materials in Unity.
- Nomad has one material slot per object; Blender vertex colors and n-gons are unsupported.
- Nomad roughness, metalness, masks, density, and base or layer paint channels are ignored.
- Sheared transforms are rejected for the affected object, which keeps its last valid state.
- A Blender object above 2,000,000 vertices stops the link.
- The Blender send queue holds 64 MiB; overflow replaces queued deltas with full updates for affected objects.
- Unity limits JSON payloads to 50 MiB, binary payloads to 512 MiB, and queued frame data to 512 MiB.
- Unsupported delta formats and malformed paint updates are skipped; other malformed supported data can stop the session.
- No file export/import workflow, file watcher, or daemon.

## Source development

Close Blender and link the package folder into your local Blender extension repository.
On Windows, run this from the source repository:

```powershell
$source = (Resolve-Path .\blender\mesh_link).Path
$repository = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.2\extensions\user_default'
New-Item -ItemType Directory -Path $repository -Force | Out-Null
New-Item -ItemType Junction -Path (Join-Path $repository 'mesh_link') -Target $source
```

Use your Blender version's folder and uninstall any existing copy before creating the junction.
Refresh local extensions in Preferences, enable the extension, and restart Blender after source changes.

Run the Python tests from the repository root with Python, NumPy, and pytest installed:

```powershell
python -m pytest blender/tests
```

For Unity tests, include `com.malloc.mesh-link` in the host manifest's `testables` array.
With Unity CLI installed, run this from the repository root for the local checkout layout above:

```powershell
unity test ../.. --mode EditMode --filter Malloc.MeshLink --output mesh-link-test-results.xml
```

## Credits

[Nomad Link](https://github.com/stephomi/nomad-link), by stephomi, is MIT-licensed and defines the App Linking protocol Mesh Link speaks.
The Blender extension is original code and is not derived from the Nomad Link Blender add-on.

## License

MIT; see [LICENSE](LICENSE).
