"""书签模型与校验。

本模块是四层最小闭环（模型与校验 → JSON 文件存储 → 业务服务 → CLI 入口）的最底层，
只依赖 Python 3.12 标准库，既不读写文件也不感知命令行：

- :class:`Bookmark`：书签记录，``id`` 为 ``None`` 表示尚未落库；
- :func:`normalize_url`：URL 规范化，也是“URL 重复”判定的唯一依据；
- :class:`ValidationError`：字段校验失败（用户输入不合法）；
- :class:`DuplicateURLError`：URL 重复，由服务层在比对全量集合时抛出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

__all__ = ["Bookmark", "DuplicateURLError", "ValidationError", "normalize_url"]


class ValidationError(ValueError):
    """书签字段校验失败。"""


class DuplicateURLError(ValidationError):
    """规范化后的 URL 已经存在，拒绝重复添加。"""


def _utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO8601 文本，精确到秒。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_text(value: object, label: str) -> str:
    """要求非空文本，并返回去除首尾空白后的结果。"""
    if not isinstance(value, str):
        raise ValidationError(f"{label}必须是文本。")
    text = value.strip()
    if not text:
        raise ValidationError(f"{label}不能为空。")
    return text


def _normalize_netloc(netloc: str) -> str:
    """主机名大小写不敏感；用户信息与端口按原样保留。"""
    if "@" in netloc:
        userinfo, _, host = netloc.rpartition("@")
        return f"{userinfo}@{host.lower()}"
    return netloc.lower()


def _validate_id(value: object) -> int | None:
    """校验书签 ID：``None``（未落库）或正整数。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("书签 id 必须是正整数，或用 None 表示尚未落库。")
    if value <= 0:
        raise ValidationError("书签 id 必须是正整数，或用 None 表示尚未落库。")
    return value


def normalize_url(url: str) -> str:
    """把 URL 规范化为用于唯一性判定的形式。

    规则：去除首尾空白 → 协议与主机名转小写 → 根路径 ``/`` 归一为空路径；
    路径、查询串、片段的大小写保持原样。规范化结果相等即视为同一 URL。
    """
    text = _require_text(url, "URL")
    if any(character.isspace() for character in text):
        raise ValidationError("URL 不能包含空白字符。")
    try:
        parts = urlsplit(text)
    except ValueError as error:
        raise ValidationError("URL 格式无法解析。") from error
    path = "" if parts.path == "/" else parts.path
    netloc = _normalize_netloc(parts.netloc)
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, parts.fragment))


@dataclass
class Bookmark:
    """单条书签记录。

    字段顺序与数据文件中的记录一致（``id`` / ``title`` / ``url`` / ``created_at``），
    因此既支持 ``Bookmark(**记录字典)`` 与按记录顺序的位置参数，也支持全部关键字构造。
    ``id`` 为 ``None`` 表示尚未落库；``created_at`` 省略时由模型生成当前 UTC 时间。
    ``title`` 与 ``url`` 的空字符串默认值只为保持字段顺序，构造时立即被校验拒绝。
    """

    id: int | None = None
    title: str = ""
    url: str = ""
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.id = _validate_id(self.id)
        self.title = _require_text(self.title, "标题")
        self.url = normalize_url(self.url)
        self.created_at = _require_text(self.created_at, "创建时间")
