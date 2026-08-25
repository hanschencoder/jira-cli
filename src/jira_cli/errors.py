"""错误类型。

设计原则：错误信息要告诉调用者「怎么修」，而不只是「错了」。
AI 读到 stderr 后应能一轮自我纠正，无需再发一次探查请求。
"""

from __future__ import annotations


class JiraCliError(Exception):
    """所有本工具主动抛出的错误的基类。

    hint 承载「怎么修」，会在 message 之后单独成段输出。
    """

    exit_code = 1

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        if self.hint:
            return f"{self.message}\n\n{self.hint}"
        return self.message


class ConfigError(JiraCliError):
    """缺少 url/token，或配置文件损坏。"""

    exit_code = 2


class AuthError(JiraCliError):
    """401/403。"""

    exit_code = 3


class ApiError(JiraCliError):
    """Jira 返回了非 2xx，且不属于上面的特化类型。"""

    exit_code = 4

    def __init__(self, message: str, hint: str = "", status: int = 0) -> None:
        super().__init__(message, hint)
        self.status = status


class ResolveError(JiraCliError):
    """名称解析不到 id（项目/类型/状态/用户/字段）。"""

    exit_code = 5


class TransitionError(JiraCliError):
    """状态流转失败：名称匹配不上，或缺必填字段。"""

    exit_code = 6


def format_api_errors(payload: object) -> str:
    """把 Jira 的错误响应体压成一行行人话。

    Jira 的错误体有两种形态，且经常同时出现：
        {"errorMessages": ["..."], "errors": {"field": "message"}}
    """
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for msg in payload.get("errorMessages") or []:
        parts.append(str(msg))
    errors = payload.get("errors")
    if isinstance(errors, dict):
        for field, msg in errors.items():
            parts.append(f"{field}: {msg}")
    return "\n".join(parts)
