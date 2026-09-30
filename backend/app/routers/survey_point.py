"""测绘控制 · 坐标核验台接口。

核验结论在后端落库后，由 /board 一次性派生控制点台账、点位图投影与
高程坐标核验清单，三处共用同一份数据，刷新后不会对不上。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.survey_point import (
    COORD_SPEC,
    SurveyPointService,
)

router = APIRouter(prefix="/api/survey_point", tags=["测绘控制"])

service = SurveyPointService()

LIST_FIELDS = ["点号", "点类型", "坐标X", "坐标Y", "高程", "精度等级", "观测日期", "点位状态"]
STATUSES = ["完好", "损坏", "已恢复", "废弃"]


class VerifyPayload(BaseModel):
    simulate_timeout: bool = False


class OfflinePayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/spec")
def get_coord_spec() -> dict[str, Any]:
    """项目坐标规范：X/Y/高程取舍精度与允许点类型以此为唯一口径。"""
    return COORD_SPEC


@router.get("/sheets")
def list_sheets() -> dict[str, Any]:
    """图幅清单（来自填图台账与控制点台账）。"""
    return {"items": service.list_sheets()}


@router.get("/board")
def get_board(
    sheet: str | None = Query(default=None, description="按图幅编号筛选"),
    keyword: str | None = Query(default=None, description="按点号检索"),
    cursor: int = Query(default=0, description="游标：上次列表末尾控制点 id，翻页防重防漏"),
    size: int = Query(default=10, le=200, description="每页条数"),
) -> dict[str, Any]:
    """核验台数据：台账、点位图投影、高程坐标核验清单同源于此。"""
    return service.board(sheet=sheet, keyword=keyword, cursor=cursor, size=size)


@router.post("/offline-merge")
def offline_merge(payload: OfflinePayload) -> dict[str, Any]:
    """离线采集批量合并：按「图幅+点号」幂等，已签发坐标不被覆盖。"""
    if not payload.items:
        raise HTTPException(status_code=400, detail="离线采集包为空，没有可合并的点")
    return service.offline_merge(payload.items)


@router.post("/register", response_model=ActionResult)
def register_point(payload: EntryPayload) -> ActionResult:
    """空图幅补点入口：按图幅+点号幂等登记，重复提交/超时重试不会产生第二笔。"""
    entry, missing, duplicated = service.register_point(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if duplicated:
        return ActionResult(ok=True, message=f"点号 {entry['点号']} 已登记，已幂等返回既有记录，未重复登记", entry=entry)
    return ActionResult(ok=True, message="控制点已登记，请发起在线核验", entry=entry)


@router.post("/{entry_id}/verify", response_model=ActionResult)
def verify_point(entry_id: int, payload: VerifyPayload) -> ActionResult:
    """发起在线核验；simulate_timeout=true 时模拟核验服务超时（不会改动任何数据）。"""
    entry, message = service.verify_point(entry_id, simulate_timeout=payload.simulate_timeout)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/supplement", response_model=ActionResult)
def supplement_coordinates(entry_id: int, payload: EntryPayload) -> ActionResult:
    """坐标缺失处置路径：补录坐标，按项目规范取舍精度并自动重新核验。"""
    entry, message = service.supplement_coordinates(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/fix-type", response_model=ActionResult)
def fix_point_type(entry_id: int, payload: EntryPayload) -> ActionResult:
    """点类型异常处置路径：校正点类型为规范允许类型并重新核验。"""
    entry, message = service.fix_point_type(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/backfill", response_model=ActionResult)
def backfill_legacy(entry_id: int, payload: EntryPayload) -> ActionResult:
    """旧控制点回填点类型，并迁移责任组。"""
    entry, message = service.backfill_legacy(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/revise", response_model=ActionResult)
def revise_coordinates(entry_id: int, payload: EntryPayload) -> ActionResult:
    """普通坐标修订；历史已签发坐标会被拒绝，避免覆盖签发成果。"""
    entry, message = service.revise_coordinates(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/reissue", response_model=ActionResult)
def reissue_coordinates(entry_id: int, payload: EntryPayload) -> ActionResult:
    """已签发坐标的变更通道：重新签发并留痕。"""
    entry, message = service.reissue_coordinates(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# ---- 以下为兼容原有列表/动作/导出的接口 ----
@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按点号检索"),
    status: str | None = Query(default=None, description="完好、损坏、已恢复、废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按点号与状态过滤测绘控制列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出测绘控制清单：返回全量台账（含核验结论）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "survey_point", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条控制点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"控制点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条控制点，缺字段时说明原因而不是静默丢弃（幂等）。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="控制点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条控制点执行登记损坏、安排恢复、标记废弃；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
