"""分页参数统一校验。

所有列表入口（质量指标分页、告警清单等）都走这一份边界规则：
页码和页大小必须是不小于 1 的正整数，页大小另有上限。
非法值直接抛 ValueError，由路由层转成 400，绝不允许静默回退成空页。
"""
from __future__ import annotations

from typing import Any, Sequence

MAX_PAGE_SIZE = 200


def validate_pagination(page: Any, size: Any) -> None:
    """校验页码与页大小；bool 是 int 的子类，这里也一并拒绝。"""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(f"页码必须是不小于 1 的正整数，收到的页码为：{page!r}")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValueError(f"每页条数必须是不小于 1 的正整数，收到的页大小为：{size!r}")
    if size > MAX_PAGE_SIZE:
        raise ValueError(f"每页最多 {MAX_PAGE_SIZE} 条，请缩小分页范围")


def paginate(rows: Sequence[dict[str, Any]], page: int, size: int) -> tuple[list[dict[str, Any]], int]:
    """按已校验的分页参数切片；调用方需先通过 validate_pagination。"""
    validate_pagination(page, size)
    total = len(rows)
    start = (page - 1) * size
    return list(rows[start:start + size]), total
