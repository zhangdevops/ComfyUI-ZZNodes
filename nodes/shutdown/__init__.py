"""自动关机功能模块：导出节点注册信息。"""
from .node import QueueShutdown

NODE_CLASS_MAPPINGS = {"QueueShutdown": QueueShutdown}
NODE_DISPLAY_NAME_MAPPINGS = {"QueueShutdown": "自动关机控制"}
