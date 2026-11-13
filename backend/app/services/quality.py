"""质量监控业务规则：分页边界、检测状态流转、告警与恢复回写都收在这里。

设计约定：
- 分页非法值（页码/页大小小于 1、页大小超上限）直接抛 ValueError，
  由路由层转 400，绝不静默返回空页。
- 合法但无数据与「检测未运行」是两种语义：前者用 empty_code=NO_RECORD 表达，
  后者通过每条指标上的「检测状态=未检测」表达，监控页不得混成同一种空态。
- 检测失败、中断、重试的结论统一经 QualityStateRegistry 回写，
  质量指标状态、告警计数、恢复记录、质量回执在同一处变更。
"""
from __future__ import annotations

from typing import Any

from app.pagination import paginate, validate_pagination
from app.services.quality_state import (
    DETECTION_RESULTS,
    RESULT_FAILED,
    RESULT_INTERRUPTED,
    STATE_NOT_RUN,
    quality_state,
)
from app.store import store

MODULE = "quality"
REQUIRED_FIELDS = ["指标编号", "监控对象", "指标类型"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用监控": "已启用", "停用监控": "已停用"}
NEGATIVE_ACTIONS = ["停用监控"]

# 空页语义标记：前端据此区分「无记录」与「检测未运行」
EMPTY_NO_RECORD = "NO_RECORD"
EMPTY_PAGE_OUT_OF_RANGE = "PAGE_OUT_OF_RANGE"

# 新建指标时补的检测视图默认值
_DETECT_VIEW_DEFAULTS = {
    "检测状态": STATE_NOT_RUN,
    "失败原因": "",
    "告警计数": 0,
    "最近回执": "",
}


class QualityService:
    # ---------- 列表与分页 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        target: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, str]:
        """返回（当前页数据, 总数, 空页语义码）；非法分页参数抛 ValueError。"""
        validate_pagination(page, size)
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("指标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if target:
            rows = [row for row in rows if target in str(row.get("监控对象", ""))]
        items, total = paginate(rows, page, size)
        items = [self._with_detect_view(dict(row)) for row in items]
        if total == 0:
            empty_code = EMPTY_NO_RECORD
        elif not items:
            # 总数非零但当前页切不到数据：页码超出范围，而不是「没有数据」
            empty_code = EMPTY_PAGE_OUT_OF_RANGE
        else:
            empty_code = ""
        return items, total, empty_code

    def monitor_targets(self) -> list[str]:
        """监控对象下拉项；切换对象时前端用它做对象级隔离。"""
        targets = {str(row.get("监控对象", "")).strip() for row in store.rows(MODULE)}
        return sorted(target for target in targets if target)

    def export_entries(self) -> dict[str, Any]:
        """导出质量监控清单全量数据；导出不走分页，因此不受页大小上限约束。"""
        items = [self._with_detect_view(dict(row)) for row in store.rows(MODULE)]
        return {"module": "quality", "total": len(items), "items": items}

    def _with_detect_view(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把统一状态中枢的检测视图合并到指标行上，两个入口读的是同一份状态。"""
        entry_id = int(entry.get("id", 0))
        view = quality_state.refresh_entry_state(entry_id)
        entry.update(view)
        return entry

    # ---------- 单条与登记 ----------
    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_detect_view(dict(entry))

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry.update(_DETECT_VIEW_DEFAULTS)
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"质量指标 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于质量监控可执行范围"
        target_state = ACTION_RULES[action]
        if target_state not in STATUS_ORDER:
            return None, f"目标状态「{target_state}」不在允许的状态序列里"
        entry["status"] = target_state
        entry["pending"] = target_state != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._with_detect_view(dict(entry)), f"质量指标已{action}"

    # ---------- 检测执行：失败 / 中断 / 重试都要落原因 ----------
    def run_detection(
        self,
        entry_id: int,
        result: str,
        reason: str,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """执行一轮质量检测并回写全部关联状态。

        返回（更新后的指标, 提示信息, 质量回执）；入参非法或指标不存在时指标为 None。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"质量指标 {entry_id} 不存在或已归档", None
        normalized = str(result or "").strip()
        if normalized not in DETECTION_RESULTS:
            return (
                None,
                f"检测结论「{result}」不支持，仅支持：{'、'.join(DETECTION_RESULTS)}",
                None,
            )
        reason = str(reason or "").strip()
        if normalized in (RESULT_FAILED, RESULT_INTERRUPTED) and not reason:
            return None, "检测失败或中断时必须填写原因，便于告警与复盘定位", None

        receipt_open = quality_state.open_round(entry_id)
        receipt = quality_state.close_round(
            entry_id,
            receipt_open["round"],
            normalized,
            reason,
            monitor_target=str(entry.get("监控对象", "")),
            alert_level=str(entry.get("告警级别", "")),
            notify_channel=str(entry.get("通知渠道", "")),
            recovery_policy=str(entry.get("恢复策略", "")),
        )
        view = quality_state.refresh_entry_state(entry_id)
        # 异常处理结果回写质量指标状态
        entry["abnormal"] = view["告警计数"] > 0 or view["检测状态"] in (
            "失败",
            "中断",
        )
        entry["pending"] = entry["abnormal"] or entry.get("status") != STATUS_ORDER[-1]
        updated = self._with_detect_view(dict(entry))
        message = self._detection_message(normalized, view, receipt)
        return updated, message, receipt

    @staticmethod
    def _detection_message(normalized: str, view: dict[str, Any], receipt: dict[str, Any]) -> str:
        if normalized == "成功":
            recovered = receipt.get("recovered_count", 0)
            tail = f"，并自动恢复 {recovered} 条历史告警" if recovered else "，无未恢复告警"
            return f"第 {receipt.get('round')} 轮检测通过{tail}（回执 {receipt.get('receipt_id')}）"
        if normalized == RESULT_INTERRUPTED:
            return (
                f"第 {receipt.get('round')} 轮检测中断：{view['失败原因']}"
                f"（回执 {receipt.get('receipt_id')}）"
            )
        return (
            f"第 {receipt.get('round')} 轮检测失败：{view['失败原因']}"
            f"，当前未恢复告警 {view['告警计数']} 条（回执 {receipt.get('receipt_id')}）"
        )

    # ---------- 告警处理与恢复（与分页入口共享 quality_state） ----------
    def list_alerts(
        self,
        *,
        entry_id: int | None = None,
        active_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, str]:
        validate_pagination(page, size)
        rows = quality_state.alerts(entry_id=entry_id, active_only=active_only)
        items, total = paginate(rows, page, size)
        empty_code = EMPTY_NO_RECORD if total == 0 else ("" if items else EMPTY_PAGE_OUT_OF_RANGE)
        return items, total, empty_code

    def list_recoveries(self, *, entry_id: int | None = None) -> list[dict[str, Any]]:
        return quality_state.recoveries(entry_id=entry_id)

    def recover_alert(self, alert_id: str, reason: str) -> tuple[dict[str, Any] | None, str]:
        return quality_state.recover_alert(alert_id, reason)

    def recover_entry_alerts(self, entry_id: int, reason: str) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
        if store.find(MODULE, entry_id) is None:
            return None, f"质量指标 {entry_id} 不存在或已归档", []
        records = quality_state.recover_entry_alerts(entry_id, reason)
        if not records:
            return None, f"质量指标 {entry_id} 当前没有未恢复告警", []
        entry = store.find(MODULE, entry_id)
        view = quality_state.refresh_entry_state(entry_id)
        entry["abnormal"] = view["告警计数"] > 0 or view["检测状态"] in ("失败", "中断")
        return self._with_detect_view(dict(entry)), f"已恢复 {len(records)} 条告警，恢复记录已同步", records

    # ---------- 监控汇总与质量回执 ----------
    def monitor_summary(self) -> dict[str, Any]:
        entry_ids = [int(row.get("id", 0)) for row in store.rows(MODULE)]
        return quality_state.monitor_summary(entry_ids)

    def export_receipts(self, *, entry_id: int | None = None) -> dict[str, Any]:
        """导出每轮检测的质量回执，便于复盘空结果与失败结果的区别。"""
        if entry_id is not None and store.find(MODULE, entry_id) is None:
            return {
                "module": "quality",
                "export": "detection-receipts",
                "entry_id": entry_id,
                "total": 0,
                "empty_code": EMPTY_NO_RECORD,
                "message": f"质量指标 {entry_id} 不存在或已归档",
                "items": [],
            }
        receipts = quality_state.receipts(entry_id=entry_id)
        return {
            "module": "quality",
            "export": "detection-receipts",
            "entry_id": entry_id,
            "total": len(receipts),
            "empty_code": EMPTY_NO_RECORD if not receipts else "",
            "message": "暂无检测回执：该对象尚未运行检测" if not receipts else "",
            "items": receipts,
        }
