"""AISL URL adapter for community-base's repository-ordered curriculum tree."""


def get_site_curriculum_tree(course, modules=None):
    """Return shared curriculum projections with stable site URL paths cached.

    Community-base owns tree parsing, sibling order, and projection. AISL owns
    the public course URL prefix; cache those projection paths on the ORM
    objects so templates and reader controls can keep calling
    ``get_absolute_url()`` without querying each ancestor separately.
    """
    from community_base.curriculum.services import get_curriculum_tree

    tree = get_curriculum_tree(course, modules=modules)

    def annotate(module_projection):
        module = module_projection.module
        module._curriculum_path = f'/courses/{course.slug}/{module_projection.path}'
        for item in module_projection.items:
            if item.kind == 'module':
                annotate(item)
            else:
                item.unit._curriculum_path = f'/courses/{course.slug}/{item.path}'

    for root in tree:
        annotate(root)
    return tree


def get_module_ancestors(module):
    """Return the physical parent modules in root-to-parent order."""
    ancestors = []
    parent = module.parent if module.parent_id else None
    while parent is not None:
        ancestors.append(parent)
        parent = parent.parent if parent.parent_id else None
    ancestors.reverse()
    return ancestors
