# Unity Link

Zip `blender_manifest.toml` and the Python files inside `unity_link/` together.
In Blender 4.2+, open Preferences → Get Extensions → Install from Disk and select the ZIP.
Unity needs Nomad Unity Bridge with `Listen` on.

1. Open the 3D Viewport sidebar → Unity Link and set Host and Port.
2. Select Connect and accept the pairing in Unity when prompted.
3. Edit visible base meshes; select Disconnect to stop.

## Source development

For live source development, close Blender and link the `unity_link` package folder into your local Blender extension repository.
On Windows, run this from the source repository:

```powershell
$source = (Resolve-Path .\unity_link).Path
$repository = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.2\extensions\user_default'
New-Item -ItemType Directory -Path $repository -Force | Out-Null
New-Item -ItemType Junction -Path (Join-Path $repository 'unity_link') -Target $source
```

Use your Blender version's folder and uninstall any existing copy before creating the junction.
Refresh local extensions in Preferences, enable the extension, and restart Blender after source changes.
