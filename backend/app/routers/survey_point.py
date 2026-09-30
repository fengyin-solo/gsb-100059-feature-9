"""坐标核验台接口。

在原测绘控制点 CRUD 之上提供：图幅汇总（含空图幅补点入口）、按点号幂等的
登记/修订/核验/签发/离线合并、历史点迁移，以及台账/点位图/核验清单共用的
同一份数据口径。注意路由顺序：静态路径必须排在 /{entry_id} 之前。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BackfillTypePayload,
    BatchPayload,
    EntryPayload,
    OfflineSyncPayload,
    PageResult,
    VerifyPayload,
)
from app.services.survey_point import COORD_SPEC, POINT_TYPES, SurveyPointService

router = APIRouter(prefix="/api/survey_point", tags=["坐标核验台"])

service = SurveyPointService()

LIST_FIELDS = ["点号", "图幅编号", "点类型", "坐标X", "坐标Y", "高程", "精度等级", "观测日期", "责任组", "核验结论"]
STATUSES = ["完好", "损坏", "已恢复", "废弃"]


def _result(ok: bool, message: str, entry: dict[str, Any] | None = None) -> ActionResult:
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/coord-spec")
def coord_spec() -> dict[str, Any]:
    """项目坐标规范：前端取舍、提示与后端落库共用同一份口径。"""
    return {"rounding": "ROUND_HALF_UP", "fields": COORD_SPEC,
            "说明": "坐标X/Y保留3位小数，高程保留4位小数，四舍五入"}


@router.get("/point-types")
def point_types() -> dict[str, Any]:
    """点类型目录：不在目录内的取值视为点类型异常。"""
    return {"items": POINT_TYPES, "默认回填": "图根点"}


@router.get("/sheets")
def list_sheets() -> dict[str, Any]:
    """图幅汇总：含没有任何控制点的空图幅，供补点入口直接使用。"""
    items = service.list_sheets()
    return {"items": items, "total": len(items)}


@router.get("/console")
def console_overview(
    sheet: str | None = Query(default=None, description="按图幅编号过滤"),
) -> dict[str, Any]:
    """核验台总览：统计 + 点位图 + 核验清单共用数据，一次取齐。"""
    points = service.all_entries()
    if sheet:
        points = [point for point in points if str(point.get("图幅编号") or "") == sheet]
    checklist = [
        {
            "id": point["id"],
            "点号": point.get("点号"),
            "图幅编号": point.get("图幅编号"),
            "点类型": point.get("点类型"),
            "坐标X": point.get("坐标X"),
            "坐标Y": point.get("坐标Y"),
            "高程": point.get("高程"),
            "核验结论": point.get("核验结论") or "待核验",
            "结论说明": point.get("结论说明"),
            "坐标缺失": point.get("坐标缺失"),
            "高程缺失": point.get("高程缺失"),
            "点类型异常": point.get("点类型异常"),
            "已签发": bool(point.get("签发坐标")),
            "责任组": point.get("责任组"),
        }
        for point in points
    ]
    return {
        "coordSpec": COORD_SPEC,
        "stats": service.stats(),
        "sheets": service.list_sheets(),
        "mapPoints": points,
        "checklist": checklist,
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按点号检索"),
    status: str | None = Query(default=None, description="完好、损坏、已恢复、废弃"),
    sheet: str | None = Query(default=None, description="按图幅编号过滤"),
    verdict: str | None = Query(default=None, description="待核验、合格、不合格、待复测"),
    anomaly: str | None = Query(default=None, description="坐标缺失 / 点类型异常"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """控制点台账：id 升序稳定分页，翻页期间新增点不会造成重复或漏项。"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码从 1 开始")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, sheet=sheet, verdict=verdict,
        anomaly=anomaly, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/register", response_model=ActionResult)
def register_point(payload: EntryPayload) -> ActionResult:
    """登记控制点（失败重试安全）：点号已存在时幂等返回，绝不重复登记。"""
    entry, missing, notice = service.register_point(payload.values)
    if missing:
        return _result(False, f"缺少必填字段：{'、'.join(missing)}")
    return _result(True, notice or "控制点已登记", entry)


@router.post("/sync-offline", response_model=ActionResult)
def sync_offline(payload: OfflineSyncPayload) -> ActionResult:
    """离线采集合并：按点号幂等，只补空字段，不覆盖已签发坐标，可安全重试。"""
    if not payload.points:
        return _result(False, "没有可合并的离线点")
    summary = service.sync_offline(payload.points)
    return _result(True, summary.pop("说明", "离线采集已合并"), summary)


@router.post("/migrate-legacy", response_model=ActionResult)
def migrate_legacy() -> ActionResult:
    """历史控制点治理：回填缺失点类型、迁移旧责任组；幂等，可重复执行。"""
    summary = service.migrate_legacy()
    return _result(True, summary["说明"], summary)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出测绘控制清单（核验台账全量）。"""
    items = service.all_entries()
    return {"module": "survey_point", "total": len(items), "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条控制点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"控制点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """兼容旧登记入口：内部走幂等登记，重复点号不会产生第二条记录。"""
    entry, missing, notice = service.register_point(payload.values)
    if missing:
        return _result(False, f"缺少必填字段：{'、'.join(missing)}")
    return _result(True, notice or "控制点已登记", entry)


@router.post("/{entry_id}/verify", response_model=ActionResult)
def verify_point(entry_id: int, payload: VerifyPayload) -> ActionResult:
    """下核验结论；结论统一回写，台账/点位图/清单刷新后保持一致。"""
    entry, message = service.verify_point(entry_id, payload.verdict, payload.note)
    return _result(entry is not None, message, entry)


@router.post("/{entry_id}/revise", response_model=ActionResult)
def revise_point(entry_id: int, payload: EntryPayload) -> ActionResult:
    """普通修订；已签发坐标会被拦下，提示改走重新签发。"""
    entry, message = service.revise_point(entry_id, payload.values)
    return _result(entry is not None, message, entry)


@router.post("/{entry_id}/issue", response_model=ActionResult)
def issue_point(entry_id: int, payload: BatchPayload) -> ActionResult:
    """首次签发：合格点锁定坐标快照。"""
    entry, message = service.issue_point(entry_id, payload.batch)
    return _result(entry is not None, message, entry)


@router.post("/{entry_id}/reissue", response_model=ActionResult)
def reissue_point(entry_id: int, payload: BatchPayload) -> ActionResult:
    """重新签发：覆盖历史签发坐标的唯一路径，结论转待复测。"""
    values = dict(payload.values)
    if payload.batch:
        values.setdefault("签发批次", payload.batch)
    entry, message = service.reissue_point(entry_id, values)
    return _result(entry is not None, message, entry)


@router.post("/{entry_id}/backfill-type", response_model=ActionResult)
def backfill_type(entry_id: int, payload: BackfillTypePayload) -> ActionResult:
    """回填点类型（点类型异常处置），可一并迁移责任组。"""
    entry, message = service.backfill_type(entry_id, payload.point_type, payload.group)
    return _result(entry is not None, message, entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条控制点执行登记损坏、安排恢复、标记废弃。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    return _result(entry is not None, message, entry)
