# Mesh Link

## Unity package

Requires Unity 6000.3 or later.
For the local checkout, use this entry in the host project's `Packages/manifest.json`:

```json
"com.malloc.mesh-link": "file:unity-link-blender/unity/com.malloc.mesh-link"
```

Or install through Unity Package Manager with this Git URL:

```text
https://github.com/kanzaki1201/mesh-link.git?path=unity/com.malloc.mesh-link
```

Add `MeshLinkScene` to an empty GameObject.
For Nomad, leave **Listen** off, set Host and Port to the Nomad device, and enable sync.
For Blender, turn **Listen** on and enable sync.

## Blender extension

Zip `blender_manifest.toml` and the Python files inside `blender/mesh_link/` together.
In Blender 4.2+, open Preferences → Get Extensions → Install from Disk and select the ZIP.

### Source development

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

## Usage

1. Open the 3D Viewport sidebar → Mesh Link and set Host and Port to the Unity listener.
2. Select Connect and accept the pairing in Unity when prompted.
3. Edit visible base meshes; select Disconnect to stop.
