"""装载与发布过程中的异常类型。"""

from __future__ import annotations


class CareOrgError(Exception):
    """带稳定错误码的基类异常。"""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "context": self.context}


class SourceError(CareOrgError):
    """来源资料不可读、指纹不符或引用不可解析。"""


class AllocationError(CareOrgError):
    """份额或金额分配无法保持可加性。"""


class PublishError(CareOrgError):
    """口径版本无法发布，例如使用了已撤回的组织资料。"""
