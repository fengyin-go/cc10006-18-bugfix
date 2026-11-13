"""质量监控接口：质量指标分页、检测执行、告警处理与质量回执。

边界约定：
- 分页入口对页码 0/负数、页大小 0/负数一律返回 400，明确拒绝而不是回空页；
- 合法但无记录返回 200 空页并带 empty_code，与「检测未运行」语义分离；
- 告警处理入口与分页入口共用 QualityService 背后的同一份状态中枢，
  告警计数、恢复记录、检测状态跨入口同步，冲突时以恢复记录为准。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, DetectionPayload, EntryPayload, PageResult
from app.services.quality import EMPTY_NO_RECORD, EMPTY_PAGE_OUT_OF_RANGE, QualityService

router = APIRouter(prefix="/api/quality", tags=["质量监控"])

service = QualityService()

LIST_FIELDS = ["指标编号", "监控对象", "指标类型", "阈值范围", "检测周期", "告警级别", "通知渠道", "恢复策略"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]

_EMPTY_MESSAGES = {
    EMPTY_NO_RECORD: "未检测：当前过滤条件下没有质量指标记录，请调整筛选或先登记指标",
    EMPTY_PAGE_OUT_OF_RANGE: "请求页码超出总页数，没有可取数据；这不是健康检测结论",
}


def _page_result(items: list, total: int, page: int, size: int, empty_code: str) -> PageResult:
    return PageResult(
        items=items,
        total=total,
        page=page,
        size=size,
        empty_code=empty_code,
        message=_EMPTY_MESSAGES.get(empty_code, ""),
    )


# 注意：所有静态路径必须声明在 /{entry_id} 动态路径之前，否则会被动态路由吞掉。


@router.get("/monitor-state")
def monitor_state() -> dict:
    """监控页汇总卡片：未检测/健康/失败/中断与未恢复告警计数，全部来自统一状态中枢。"""
    return service.monitor_summary()


@router.get("/targets")
def monitor_targets() -> dict:
    """监控对象候选列表，供对象切换下拉使用。"""
    return {"items": service.monitor_targets()}


@router.get("/alerts", response_model=PageResult[dict])
def list_alerts(
    entry_id: int | None = Query(default=None, description="按质量指标/监控对象过滤"),
    active_only: bool = Query(default=False, description="仅看未恢复告警"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """告警处理入口：与分页入口读取同一份告警清单，支持按对象隔离。"""
    try:
        items, total, empty_code = service.list_alerts(
            entry_id=entry_id, active_only=active_only, page=page, size=size
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _page_result(items, total, page, size, empty_code)


@router.post("/alerts/{alert_id}/recover", response_model=ActionResult)
def recover_alert(alert_id: str, payload: EntryPayload) -> ActionResult:
    """人工恢复单条告警；恢复记录落库后以恢复记录为准回写指标状态。"""
    reason = str(payload.values.get("reason") or "").strip()
    record, message = service.recover_alert(alert_id, reason)
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/recoveries")
def list_recoveries(entry_id: int | None = Query(default=None)) -> dict:
    """恢复记录入口：可按监控对象过滤，保证告警与恢复各自归属对象。"""
    items = service.list_recoveries(entry_id=entry_id)
    return {"total": len(items), "items": items}


@router.get("/receipts/export")
def export_receipts(entry_id: int | None = Query(default=None)) -> dict:
    """导出每轮检测结论的质量回执，复盘空结果与失败/中断结果的区别。"""
    return service.export_receipts(entry_id=entry_id)


@router.get("/export")
def export_entries() -> dict:
    """导出质量监控清单：全量指标及当前检测视图，导出不受分页上限影响。"""
    return service.export_entries()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按指标编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    target: str | None = Query(default=None, description="按监控对象过滤，切换对象时隔离状态"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """质量指标分页：非法页码/页大小明确 400 拒绝；合法空页带语义码，不与检测未运行混淆。"""
    try:
        items, total, empty_code = service.list_entries(
            keyword=keyword, status=status, target=target, page=page, size=size
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _page_result(items, total, page, size, empty_code)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条质量指标，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="质量指标已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条质量指标明细（含检测状态视图）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"质量指标 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条质量指标执行提交校验、启用监控、停用监控；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/detections", response_model=ActionResult)
def run_detection(entry_id: int, payload: DetectionPayload) -> ActionResult:
    """执行一轮质量检测：成功/失败/中断都回写指标状态、告警计数、恢复记录与质量回执。"""
    entry, message, receipt = service.run_detection(entry_id, payload.result, payload.reason)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry, receipt=receipt)


@router.post("/{entry_id}/recover", response_model=ActionResult)
def recover_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按监控对象批量恢复告警；恢复后告警计数、恢复记录、列表状态一起变化。"""
    reason = str(payload.values.get("reason") or "").strip()
    entry, message, _records = service.recover_entry_alerts(entry_id, reason)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
