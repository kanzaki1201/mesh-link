import bpy

from . import handlers


class MESHLINK_OT_connect(bpy.types.Operator):
    bl_idname = "mesh_link.connect"
    bl_label = "Connect"

    def execute(self, context):
        handlers.connect(context)
        return {'FINISHED'}


class MESHLINK_OT_disconnect(bpy.types.Operator):
    bl_idname = "mesh_link.disconnect"
    bl_label = "Disconnect"

    def execute(self, context):
        handlers.disconnect()
        return {'FINISHED'}


class MESHLINK_OT_bake_textures(bpy.types.Operator):
    bl_idname = "mesh_link.bake_textures"
    bl_label = "Bake & Send Textures"

    @classmethod
    def poll(cls, _context):
        return handlers.can_bake()

    def execute(self, context):
        try:
            handlers.bake_and_send(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class MESHLINK_PT_panel(bpy.types.Panel):
    bl_label = "Mesh Link"
    bl_idname = "MESHLINK_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Mesh Link"

    def draw(self, context):
        layout = self.layout
        fields = layout.column()
        fields.enabled = not handlers.running()
        fields.prop(context.scene, "mesh_link_host")
        fields.prop(context.scene, "mesh_link_port")
        action = "disconnect" if handlers.running() else "connect"
        layout.operator("mesh_link." + action)
        layout.operator("mesh_link.bake_textures")
        layout.label(text=handlers.status())


CLASSES = (MESHLINK_OT_connect, MESHLINK_OT_disconnect,
           MESHLINK_OT_bake_textures, MESHLINK_PT_panel)
