"""乡镇服务机构改组后的维度装载、事实关联、口径发布与多视角查询。

本包只使用 Python 标准库。来源资料均为合成数据。
"""

from .errors import AllocationError, CareOrgError, PublishError, SourceError
from .warehouse import Warehouse

__all__ = [
    "AllocationError",
    "CareOrgError",
    "PublishError",
    "SourceError",
    "Warehouse",
]

__version__ = "1.0.0"
