import bpy

from . import handlers


class UNITYLINK_OT_connect(bpy.types.Operator):
    bl_idname = "unity_link.connect"
    bl_label = "Connect"

    def execute(self, context):
        handlers.connect(context)
        return {'FINISHED'}


class UNITYLINK_OT_disconnect(bpy.types.Operator):
    bl_idname = "unity_link.disconnect"
    bl_label = "Disconnect"

    def execute(self, context):
        handlers.disconnect()
        return {'FINISHED'}


class UNITYLINK_PT_panel(bpy.types.Panel):
    bl_label = "Unity Link"
    bl_idname = "UNITYLINK_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Unity Link"

    def draw(self, context):
        layout = self.layout
        fields = layout.column()
        fields.enabled = not handlers.running()
        fields.prop(context.scene, "unity_link_host")
        fields.prop(context.scene, "unity_link_port")
        action = "disconnect" if handlers.running() else "connect"
        layout.operator("unity_link." + action)
        layout.label(text=handlers.status())


CLASSES = (UNITYLINK_OT_connect, UNITYLINK_OT_disconnect, UNITYLINK_PT_panel)
