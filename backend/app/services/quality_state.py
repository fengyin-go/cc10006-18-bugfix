"""质量检测状态中枢：告警、恢复记录与质量回执的唯一数据源。

分页入口（quality 路由）与告警处理入口（quality/alerts、quality/recoveries）
都从这一份状态读取，保证两边看到的告警计数、恢复记录、检测结论完全一致，
避免空结果被当成健康结论、告警和恢复各记各的。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# 检测状态：未运行（从未发起过检测）、检测中、健康、失败、中断
STATE_NOT_RUN = "未检测"
STATE_RUNNING = "检测中"
STATE_HEALTHY = "健康"
STATE_FAILED = "失败"
STATE_INTERRUPTED = "中断"

# 检测结论：成功、失败、中断（中断必须带原因）
RESULT_SUCCESS = "成功"
RESULT_FAILED = "失败"
RESULT_INTERRUPTED = "中断"
DETECTION_RESULTS = (RESULT_SUCCESS, RESULT_FAILED, RESULT_INTERRUPTED)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class QualityStateRegistry:
    """进程内统一状态仓库；真实项目中可替换成数据库事务实现。"""

    def __init__(self) -> None:
        # entry_id -> 检测状态
        self._detect_state: dict[int, str] = {}
        # entry_id -> 最近一次失败/中断原因，成功或恢复后清空
        self._failure_reason: dict[int, str] = {}
        # entry_id -> 进行轮次（检测中标记）
        self._running_round: dict[int, int] = {}
        # entry_id -> 最新一轮检测回执编号
        self._latest_receipt_id: dict[int, str] = {}
        self._alerts: list[dict[str, Any]] = []
        self._recoveries: list[dict[str, Any]] = []
        self._receipts: list[dict[str, Any]] = []
        self._round_seq = 0
        self._alert_seq = 0
        self._recovery_seq = 0

    # ---------- 检测回执 ----------
    def open_round(self, entry_id: int) -> dict[str, Any]:
        """发起一轮检测：先落一条「检测中」回执，中断/超时也有据可查。"""
        self._round_seq += 1
        round_no = self._round_seq
        receipt = {
            "receipt_id": f"QR-{datetime.now():%Y%m%d}-{round_no:04d}",
            "entry_id": entry_id,
            "round": round_no,
            "started_at": _now(),
            "finished_at": None,
            "result": STATE_RUNNING,
            "reason": "",
            "alert_count": 0,
            "recovered_count": 0,
        }
        self._receipts.append(receipt)
        self._detect_state[entry_id] = STATE_RUNNING
        self._running_round[entry_id] = round_no
        return receipt

    def close_round(
        self,
        entry_id: int,
        round_no: int,
        result: str,
        reason: str,
        monitor_target: str,
        alert_level: str = "",
        notify_channel: str = "",
        recovery_policy: str = "",
    ) -> dict[str, Any]:
        """落定一轮检测结论，并同步回写告警与状态。"""
        receipt = next(
            (item for item in self._receipts if item["entry_id"] == entry_id and item["round"] == round_no),
            None,
        )
        reason = (reason or "").strip()
        if result == RESULT_INTERRUPTED and not reason:
            reason = "检测执行被中断，未产出有效结论"
        if result == RESULT_FAILED and not reason:
            reason = "检测未通过，指标值超出阈值范围"

        created_alerts: list[dict[str, Any]] = []
        recovered_alerts: list[dict[str, Any]] = []

        if result == RESULT_SUCCESS:
            # 重试成功：自动恢复该对象此前所有未恢复告警，恢复记录随成功结论一起落库
            recovered_alerts = self._auto_recover(
                entry_id,
                monitor_target,
                reason or "重试检测通过，历史告警自动恢复",
            )
            self._failure_reason.pop(entry_id, None)
            self._detect_state[entry_id] = STATE_HEALTHY
        else:
            # 失败 / 中断都要说明原因，并回写质量指标状态与告警
            self._failure_reason[entry_id] = reason
            self._detect_state[entry_id] = (
                STATE_INTERRUPTED if result == RESULT_INTERRUPTED else STATE_FAILED
            )
            self._alert_seq += 1
            alert = {
                "alert_id": f"QA-{self._alert_seq:04d}",
                "entry_id": entry_id,
                "监控对象": monitor_target,
                "告警级别": alert_level or "未分级",
                "告警原因": reason,
                "检测轮次": round_no,
                "状态": "未恢复",
                "通知渠道": notify_channel,
                "恢复策略": recovery_policy,
                "产生时间": _now(),
                "恢复时间": None,
            }
            self._alerts.append(alert)
            created_alerts.append(alert)

        self._running_round.pop(entry_id, None)
        if receipt is not None:
            receipt["finished_at"] = _now()
            receipt["result"] = result
            receipt["reason"] = reason
            receipt["alert_count"] = len(created_alerts)
            receipt["recovered_count"] = len(recovered_alerts)
            self._latest_receipt_id[entry_id] = receipt["receipt_id"]
        return receipt if receipt is not None else {}

    def _auto_recover(self, entry_id: int, monitor_target: str, reason: str) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        for alert in self._alerts:
            if alert["entry_id"] == entry_id and alert["状态"] == "未恢复":
                alert["状态"] = "已恢复"
                alert["恢复时间"] = _now()
                record = self._write_recovery(alert, reason, source="检测重试自动恢复")
                recovered.append(record)
        return recovered

    def _write_recovery(self, alert: dict[str, Any], reason: str, *, source: str) -> dict[str, Any]:
        self._recovery_seq += 1
        record = {
            "recovery_id": f"QRC-{self._recovery_seq:04d}",
            "alert_id": alert["alert_id"],
            "entry_id": alert["entry_id"],
            "监控对象": alert["监控对象"],
            "恢复原因": reason,
            "恢复来源": source,
            "恢复时间": _now(),
        }
        self._recoveries.append(record)
        return record

    # ---------- 告警处理入口 ----------
    def recover_alert(self, alert_id: str, reason: str) -> tuple[dict[str, Any] | None, str]:
        """人工恢复单条告警；恢复记录以本入口为准（冲突时恢复记录优先）。"""
        alert = next((item for item in self._alerts if item["alert_id"] == alert_id), None)
        if alert is None:
            return None, f"告警 {alert_id} 不存在或已归档"
        if alert["状态"] == "已恢复":
            return None, f"告警 {alert_id} 已恢复，请勿重复操作"
        alert["状态"] = "已恢复"
        alert["恢复时间"] = _now()
        record = self._write_recovery(alert, reason.strip() or "人工确认恢复", source="人工恢复")
        # 恢复记录优先：该对象没有其他未恢复告警时，指标状态回到健康
        if not self.active_alert_count(alert["entry_id"]):
            self._detect_state[alert["entry_id"]] = STATE_HEALTHY
            self._failure_reason.pop(alert["entry_id"], None)
        return record, "告警已恢复，恢复记录已同步"

    def recover_entry_alerts(self, entry_id: int, reason: str) -> list[dict[str, Any]]:
        """按监控对象批量恢复，保证对象切换后告警各自归属、恢复也各自归属。"""
        records: list[dict[str, Any]] = []
        for alert in self._alerts:
            if alert["entry_id"] == entry_id and alert["状态"] == "未恢复":
                alert["状态"] = "已恢复"
                alert["恢复时间"] = _now()
                records.append(
                    self._write_recovery(alert, reason.strip() or "按对象批量恢复", source="按对象恢复")
                )
        if records and not self.active_alert_count(entry_id):
            self._detect_state[entry_id] = STATE_HEALTHY
            self._failure_reason.pop(entry_id, None)
        return records

    # ---------- 统一读取入口（分页与告警处理共用同一份状态） ----------
    def detect_state(self, entry_id: int) -> str:
        return self._detect_state.get(entry_id, STATE_NOT_RUN)

    def failure_reason(self, entry_id: int) -> str:
        return self._failure_reason.get(entry_id, "")

    def active_alert_count(self, entry_id: int) -> int:
        return sum(
            1
            for alert in self._alerts
            if alert["entry_id"] == entry_id and alert["状态"] == "未恢复"
        )

    def refresh_entry_state(self, entry_id: int) -> dict[str, Any]:
        """以恢复记录为准重算单条指标的检测视图字段。"""
        active = self.active_alert_count(entry_id)
        recorded = self._detect_state.get(entry_id, STATE_NOT_RUN)
        if active > 0:
            # 存在未恢复告警：展示最近一次失败/中断原因
            if recorded == STATE_RUNNING or recorded == STATE_INTERRUPTED:
                state = STATE_INTERRUPTED if recorded == STATE_INTERRUPTED else STATE_FAILED
            else:
                state = STATE_FAILED
        elif recorded == STATE_RUNNING:
            state = STATE_RUNNING
        elif recorded == STATE_NOT_RUN:
            # 从未发起过检测：必须保持未检测，绝不能因为没有告警就显示健康
            state = STATE_NOT_RUN
        else:
            # 有过检测且无未恢复告警（成功或已被恢复记录覆盖）：健康
            state = STATE_HEALTHY
        self._detect_state[entry_id] = state
        if state in (STATE_HEALTHY, STATE_NOT_RUN) and active == 0:
            self._failure_reason.pop(entry_id, None)
        return {
            "检测状态": state,
            "失败原因": self._failure_reason.get(entry_id, ""),
            "告警计数": active,
            "最近回执": self._latest_receipt_id.get(entry_id, ""),
        }

    def alerts(self, *, entry_id: int | None = None, active_only: bool = False) -> list[dict[str, Any]]:
        rows = self._alerts
        if entry_id is not None:
            rows = [row for row in rows if row["entry_id"] == entry_id]
        if active_only:
            rows = [row for row in rows if row["状态"] == "未恢复"]
        return [dict(row) for row in rows]

    def recoveries(self, *, entry_id: int | None = None) -> list[dict[str, Any]]:
        rows = self._recoveries
        if entry_id is not None:
            rows = [row for row in rows if row["entry_id"] == entry_id]
        return [dict(row) for row in rows]

    def receipts(self, *, entry_id: int | None = None) -> list[dict[str, Any]]:
        rows = self._receipts
        if entry_id is not None:
            rows = [row for row in rows if row["entry_id"] == entry_id]
        return [dict(row) for row in rows]

    def latest_receipt(self, entry_id: int) -> dict[str, Any] | None:
        receipt_id = self._latest_receipt_id.get(entry_id)
        if not receipt_id:
            return None
        return next((dict(item) for item in self._receipts if item["receipt_id"] == receipt_id), None)

    def monitor_summary(self, entry_ids: list[int]) -> dict[str, int]:
        """监控页卡片：所有数字都从同一份状态实时汇总。"""
        summary = {
            "指标总数": len(entry_ids),
            "未检测": 0,
            "健康": 0,
            "失败": 0,
            "中断": 0,
            "检测中": 0,
            "未恢复告警": 0,
        }
        for entry_id in entry_ids:
            view = self.refresh_entry_state(entry_id)
            state = view["检测状态"]
            if state in summary:
                summary[state] += 1
            summary["未恢复告警"] += view["告警计数"]
        return summary


# 模块级单例：路由层所有入口共享，跨入口同步告警清单
quality_state = QualityStateRegistry()
