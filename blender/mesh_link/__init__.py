CLASSES = ()


def register():
    import bpy
    from . import channel_node, handlers, panel

    class MeshLinkToken(bpy.types.PropertyGroup):
        token: bpy.props.StringProperty(options={'HIDDEN'})

    class MeshLinkPreferences(bpy.types.AddonPreferences):
        bl_idname = __package__
        pair_tokens: bpy.props.CollectionProperty(type=MeshLinkToken)
        texture_size: bpy.props.EnumProperty(
            name="Texture Size", items=[(value, value, "")
                                        for value in ("512", "1024", "2048", "4096")],
            default="1024")

        def draw(self, _context):
            self.layout.prop(self, "texture_size")

    global CLASSES
    CLASSES = (MeshLinkToken, MeshLinkPreferences, *panel.CLASSES)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    channel_node.register()
    bpy.types.Scene.mesh_link_host = bpy.props.StringProperty(
        name="Host", default="127.0.0.1")
    bpy.types.Scene.mesh_link_port = bpy.props.IntProperty(
        name="Port", default=48312, min=1, max=65535)
    handlers.register()


def unregister():
    import bpy
    from . import channel_node, handlers

    handlers.unregister()
    channel_node.unregister()
    del bpy.types.Scene.mesh_link_port
    del bpy.types.Scene.mesh_link_host
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
