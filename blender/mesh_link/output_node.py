import bpy

from .bake import FIXED_INPUTS


def _shared(group, node):
    # ID user counts drift for Python custom group nodes, so count the nodes themselves.
    for owner in bpy.data.user_map(subset={group}).get(group, ()):
        tree = owner if isinstance(owner, bpy.types.NodeTree) else getattr(owner, 'node_tree', None)
        if tree is not None and any(
                other.bl_idname == node.bl_idname and other.node_tree == group
                and other.as_pointer() != node.as_pointer() for other in tree.nodes):
            return True
    return False


class MeshLinkOutputNode(bpy.types.ShaderNodeCustomGroup):
    bl_idname = "MeshLinkOutputNode"
    bl_label = "Mesh Link Output"

    def init(self, _context):
        group = bpy.data.node_groups.new(".Mesh Link Output", 'ShaderNodeTree')
        for name, *_ in FIXED_INPUTS:
            group.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketColor')
        self.node_tree = group

    def copy(self, node):
        self.node_tree = node.node_tree.copy()

    def free(self):
        group = self.node_tree
        if group is not None and not _shared(group, self):
            bpy.data.node_groups.remove(group)

    def draw_buttons_ext(self, _context, layout):
        layout.template_node_tree_interface(self.node_tree.interface)


class NODE_MT_mesh_link(bpy.types.Menu):
    bl_label = "Mesh Link"

    def draw(self, _context):
        operator = self.layout.operator("node.add_node", text="Mesh Link Output")
        operator.type = MeshLinkOutputNode.bl_idname
        operator.use_transform = True


def _add_menu(self, context):
    if context.space_data.tree_type == 'ShaderNodeTree':
        self.layout.menu(NODE_MT_mesh_link.__name__)


_menu = None


def register():
    global _menu
    bpy.utils.register_class(MeshLinkOutputNode)
    bpy.utils.register_class(NODE_MT_mesh_link)
    _menu = (bpy.types.NODE_MT_shader_node_add_all
             if hasattr(bpy.types, 'NODE_MT_shader_node_add_all') else bpy.types.NODE_MT_add)
    _menu.append(_add_menu)


def unregister():
    _menu.remove(_add_menu)
    bpy.utils.unregister_class(NODE_MT_mesh_link)
    bpy.utils.unregister_class(MeshLinkOutputNode)
