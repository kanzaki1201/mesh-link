import bpy
from bpy.props import StringProperty


class MeshLinkChannelNode(bpy.types.ShaderNodeCustomGroup):
    bl_idname = "MeshLinkChannelNode"
    bl_label = "Mesh Link Channel"

    channel: StringProperty(name="Channel")

    def init(self, _context):
        group = bpy.data.node_groups.get(".Mesh Link Channel")
        if group is None:
            group = bpy.data.node_groups.new(".Mesh Link Channel", 'ShaderNodeTree')
        if not any(item.item_type == 'SOCKET' and item.in_out == 'INPUT'
                   and item.name == 'Color' for item in group.interface.items_tree):
            group.interface.new_socket(name="Color", in_out='INPUT',
                                       socket_type='NodeSocketColor')
        self.node_tree = group

    def draw_label(self):
        return f"Mesh Link: {self.channel}" if self.channel else "Mesh Link Channel"

    def draw_buttons(self, _context, layout):
        layout.prop(self, "channel", text="")


class NODE_MT_mesh_link(bpy.types.Menu):
    bl_label = "Mesh Link"

    def draw(self, _context):
        operator = self.layout.operator("node.add_node", text="Mesh Link Channel")
        operator.type = MeshLinkChannelNode.bl_idname
        operator.use_transform = True


def _add_menu(self, context):
    if context.space_data.tree_type == 'ShaderNodeTree':
        self.layout.menu(NODE_MT_mesh_link.__name__)


_menu = None


def register():
    global _menu
    bpy.utils.register_class(MeshLinkChannelNode)
    bpy.utils.register_class(NODE_MT_mesh_link)
    _menu = (bpy.types.NODE_MT_shader_node_add_all
             if hasattr(bpy.types, 'NODE_MT_shader_node_add_all') else bpy.types.NODE_MT_add)
    _menu.append(_add_menu)


def unregister():
    _menu.remove(_add_menu)
    bpy.utils.unregister_class(NODE_MT_mesh_link)
    bpy.utils.unregister_class(MeshLinkChannelNode)
