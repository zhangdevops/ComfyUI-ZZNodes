"""ZZNodes 1.0：统一注册各功能模块，前端集中放置于根目录web。"""
from .nodes import shutdown

__version__ = "1.0"
WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

for module in (shutdown,):
    duplicates = NODE_CLASS_MAPPINGS.keys() & module.NODE_CLASS_MAPPINGS.keys()
    if duplicates:
        raise ValueError(f"节点内部名称重复：{duplicates}")
    NODE_CLASS_MAPPINGS.update(module.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(module.NODE_DISPLAY_NAME_MAPPINGS)
