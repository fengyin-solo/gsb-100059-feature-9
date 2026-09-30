"""测绘控制 · 坐标核验台业务规则。

设计口径：
- 控制点以「图幅编号 + 点号」为业务唯一键，登记/离线合并全部按该键幂等；
- 核验结论（通过/待补坐标/待校正点类型/超时待重试）落库到控制点记录本身，
  控制点台账、点位图、高程坐标核验清单从同一份记录派生，刷新后不会对不上；
- X/Y/高程的取舍精度以项目坐标规范为准（ROUND_HALF_UP），不由前端传参决定；
- 已签发坐标拒绝普通修订，只能走「重新签发」；
- 旧控制点缺点类型时由启动迁移兜底并迁移责任组，再由人工回填点类型。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.store import store

MODULE = "survey_point"
MAPPING_MODULE = "mapping"

# ---- 项目坐标规范（真实项目应来自规范配置；X/Y 取舍精度只以此处为准）----
COORD_SPEC: dict[str, Any] = {
    "名称": "项目坐标规范（2000国家大地坐标系）",
    "坐标X小数位": 3,
    "坐标Y小数位": 3,
    "高程小数位": 3,
    "允许点类型": ["三角点", "GPS控制点", "导线点", "水准点", "图根点", "天文点"],
    "默认责任组": "控制测量一组",
}

REQUIRED_FIELDS = ["点号", "图幅编号"]
STATUS_ORDER = ["完好", "损坏", "已恢复", "废弃"]
ACTION_RULES = {"登记损坏": "损坏", "安排恢复": "已恢复", "标记废弃": "废弃"}
NEGATIVE_ACTIONS: list[str] = []

CONCLUSION_PASS = "通过"
CONCLUSION_MISSING = "待补坐标"
CONCLUSION_BAD_TYPE = "待校正点类型"
CONCLUSION_TIMEOUT = "超时待重试"
CONCLUSION_PENDING = "待核验"
ABNORMAL_CONCLUSIONS = {CONCLUSION_MISSING, CONCLUSION_BAD_TYPE, CONCLUSION_TIMEOUT}

# 每种结论对应到前端的处置路径
DISPOSITION: dict[str, str] = {
    CONCLUSION_PASS: "坐标可用；如需变更请走坐标修订，已签发点须重新签发",
    CONCLUSION_PENDING: "发起在线核验，核验通过后进入控制点台账",
    CONCLUSION_MISSING: "补录缺失的 X/Y 坐标，系统按项目规范取舍精度后重新核验",
    CONCLUSION_BAD_TYPE: "把点类型校正为规范允许类型后重新核验",
    CONCLUSION_TIMEOUT: "核验服务超时，坐标未被改动；请直接重新核验，不要重新登记点号",
}


def _round_half_up(value: float, ndigits: int) -> float:
    """按项目规范做四舍五入（避免银行家舍入与前端各自取舍）。"""
    quantum = Decimal(1).scaleb(-ndigits)
    rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return float(rounded)


def _to_float(value: Any) -> float | None:
    """把历史脏数据（空串、示例文案等）统一识别为坐标缺失，而不是抛异常。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number:  # NaN
        return None
    return number


class SurveyPointService:
    def __init__(self) -> None:
        self._migrate_legacy(store.rows(MODULE))

    # ------------------------------------------------------------------
    # 启动迁移：旧控制点缺了点类型的，先挂待回填标记并迁移责任组
    # ------------------------------------------------------------------
    def _migrate_legacy(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            row.setdefault("图幅编号", "")
            row.setdefault("责任组", COORD_SPEC["默认责任组"])
            row.setdefault("来源", "台账")
            row.setdefault("已签发", False)
            row.setdefault("签发批次", None)
            row.setdefault("签发日期", None)
            row.setdefault("修订记录", [])
            row.setdefault("待回填点类型", False)
            row["坐标X"] = _to_float(row.get("坐标X"))
            row["坐标Y"] = _to_float(row.get("坐标Y"))
            row["高程"] = _to_float(row.get("高程"))
            point_type = str(row.get("点类型") or "").strip()
            if not point_type:
                row["点类型"] = ""
                row["待回填点类型"] = True
                row["责任组"] = COORD_SPEC["默认责任组"]
            conclusion, issues = self._evaluate(row, simulate_timeout=False)
            row.setdefault("核验时间", None)
            row["核验结论"] = conclusion
            row["核验问题"] = issues
            row["abnormal"] = conclusion in ABNORMAL_CONCLUSIONS or row["待回填点类型"]
            row["pending"] = conclusion != CONCLUSION_PASS

    # ------------------------------------------------------------------
    # 核验规则：坐标缺失、点类型异常、服务超时分别给不同结论
    # ------------------------------------------------------------------
    def _evaluate(
        self, row: dict[str, Any], *, simulate_timeout: bool
    ) -> tuple[str, list[dict[str, str]]]:
        issues: list[dict[str, str]] = []
        if row.get("坐标X") is None:
            issues.append({"类别": "坐标缺失", "说明": "缺少坐标X，需外业补测后补录"})
        if row.get("坐标Y") is None:
            issues.append({"类别": "坐标缺失", "说明": "缺少坐标Y，需外业补测后补录"})
        point_type = str(row.get("点类型") or "").strip()
        if point_type not in COORD_SPEC["允许点类型"]:
            issues.append({
                "类别": "点类型异常",
                "说明": f"点类型「{point_type or '空'}」不在规范允许范围内，需校正或回填",
            })
        if row.get("高程") is None:
            issues.append({"类别": "高程缺失", "说明": "缺少高程，补录后进入高程坐标核验清单"})

        if simulate_timeout:
            return CONCLUSION_TIMEOUT, [
                {"类别": "服务超时", "说明": "核验服务未在时限内响应，坐标未改动，可直接重新核验"}
            ]
        if any(item["类别"] == "坐标缺失" for item in issues):
            return CONCLUSION_MISSING, issues
        if any(item["类别"] == "点类型异常" for item in issues):
            return CONCLUSION_BAD_TYPE, issues
        return CONCLUSION_PASS, issues

    def _apply_conclusion(self, row: dict[str, Any], *, simulate_timeout: bool) -> None:
        conclusion, issues = self._evaluate(row, simulate_timeout=simulate_timeout)
        row["核验结论"] = conclusion
        row["核验问题"] = issues
        row["核验时间"] = date.today().isoformat()
        row["abnormal"] = conclusion in ABNORMAL_CONCLUSIONS or bool(row.get("待回填点类型"))
        row["pending"] = conclusion != CONCLUSION_PASS

    def _normalize_coordinates(self, row: dict[str, Any], values: dict[str, Any]) -> None:
        """按项目坐标规范取舍 X/Y/高程精度后写回。"""
        if "坐标X" in values:
            x_value = _to_float(values.get("坐标X"))
            row["坐标X"] = (
                _round_half_up(x_value, COORD_SPEC["坐标X小数位"]) if x_value is not None else None
            )
        if "坐标Y" in values:
            y_value = _to_float(values.get("坐标Y"))
            row["坐标Y"] = (
                _round_half_up(y_value, COORD_SPEC["坐标Y小数位"]) if y_value is not None else None
            )
        if "高程" in values:
            h_value = _to_float(values.get("高程"))
            row["高程"] = (
                _round_half_up(h_value, COORD_SPEC["高程小数位"]) if h_value is not None else None
            )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def _filtered_rows(
        self, *, sheet: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if sheet:
            rows = [row for row in rows if str(row.get("图幅编号", "")) == sheet]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("点号", ""))]
        return sorted(rows, key=lambda row: int(row["id"]))

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered_rows(keyword=keyword)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def _find_by_business_key(self, sheet: str, point_no: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("图幅编号", "")) == sheet and str(row.get("点号", "")) == point_no:
                return row
        return None

    # ------------------------------------------------------------------
    # 登记：按「图幅+点号」幂等，失败/超时重试不会重复登记同一笔
    # ------------------------------------------------------------------
    def register_point(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str], bool]:
        point_no = str(values.get("点号") or "").strip()
        sheet_no = str(values.get("图幅编号") or "").strip()
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, False

        existing = self._find_by_business_key(sheet_no, point_no)
        if existing is not None:
            # 幂等命中：直接回既有记录，绝不新建第二笔
            return existing, [], True

        rows = store.rows(MODULE)
        point_type = str(values.get("点类型") or "").strip()
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": STATUS_ORDER[0],
            "点号": point_no,
            "图幅编号": sheet_no,
            "点类型": point_type,
            "坐标X": None,
            "坐标Y": None,
            "高程": None,
            "精度等级": str(values.get("精度等级") or "").strip() or "图根级",
            "观测日期": str(values.get("观测日期") or "").strip()
            or date.today().isoformat(),
            "责任组": str(values.get("责任组") or "").strip() or COORD_SPEC["默认责任组"],
            "来源": str(values.get("来源") or "在线补测"),
            "已签发": False,
            "签发批次": None,
            "签发日期": None,
            "修订记录": [],
            "待回填点类型": point_type == "",
        }
        self._normalize_coordinates(entry, values)
        conclusion, issues = self._evaluate(entry, simulate_timeout=False)
        entry["核验结论"] = conclusion
        entry["核验问题"] = issues
        entry["核验时间"] = None
        entry["abnormal"] = conclusion in ABNORMAL_CONCLUSIONS or entry["待回填点类型"]
        entry["pending"] = conclusion != CONCLUSION_PASS
        rows.append(entry)
        return entry, [], False

    # 兼容旧的登记接口
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing, _ = self.register_point(values)
        return entry, missing

    # ------------------------------------------------------------------
    # 核验 / 分类处置
    # ------------------------------------------------------------------
    def verify_point(
        self, entry_id: int, *, simulate_timeout: bool = False
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        self._apply_conclusion(entry, simulate_timeout=simulate_timeout)
        if simulate_timeout:
            return entry, "核验服务超时，坐标与登记均未改动，请直接重新核验"
        if entry["核验结论"] == CONCLUSION_PASS:
            return entry, "核验通过，结论已同步到台账、点位图与核验清单"
        return entry, f"核验结论：{entry['核验结论']}，请按处置路径处理"

    def supplement_coordinates(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """坐标缺失处置路径：补录 X/Y（高程可选），按规范取舍后自动重新核验。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        missing = [
            axis for axis in ("坐标X", "坐标Y")
            if _to_float(values.get(axis)) is None and entry.get(axis) is None
        ]
        if missing:
            return None, f"仍缺少 {'、'.join(missing)}，补齐后才能完成坐标补录"
        self._normalize_coordinates(entry, values)
        entry["修订记录"].append({
            "日期": date.today().isoformat(),
            "类型": "补录坐标",
            "说明": "外业补测后补录，按项目坐标规范取舍精度",
        })
        self._apply_conclusion(entry, simulate_timeout=False)
        return entry, "坐标已补录并按规范取舍，系统已自动重新核验"

    def fix_point_type(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """点类型异常处置路径：校正为规范允许类型后重新核验。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        new_type = str(values.get("点类型") or "").strip()
        if new_type not in COORD_SPEC["允许点类型"]:
            return None, "点类型必须取自项目坐标规范允许列表"
        entry["点类型"] = new_type
        if values.get("责任组"):
            entry["责任组"] = str(values["责任组"]).strip()
        self._apply_conclusion(entry, simulate_timeout=False)
        return entry, "点类型已校正，系统已自动重新核验"

    def backfill_legacy(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """旧控制点回填点类型，并把责任组迁移到本次指定（缺省落到默认组）。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        new_type = str(values.get("点类型") or "").strip()
        if new_type not in COORD_SPEC["允许点类型"]:
            return None, "回填点类型必须取自项目坐标规范允许列表"
        entry["点类型"] = new_type
        entry["责任组"] = str(values.get("责任组") or "").strip() or COORD_SPEC["默认责任组"]
        entry["待回填点类型"] = False
        self._apply_conclusion(entry, simulate_timeout=False)
        return entry, f"旧控制点已回填点类型，责任组迁移到「{entry['责任组']}」"

    # ------------------------------------------------------------------
    # 修订 / 签发：历史已签发坐标不得被普通修订覆盖
    # ------------------------------------------------------------------
    def revise_coordinates(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if _to_float(values.get("坐标X")) is None or _to_float(values.get("坐标Y")) is None:
            return None, "坐标修订必须同时提供坐标X、坐标Y"
        if entry.get("已签发"):
            return (
                None,
                f"点号 {entry['点号']} 的坐标已于 {entry.get('签发日期')} 随批次 "
                f"{entry.get('签发批次')} 签发，普通修订不得覆盖，请走「重新签发」",
            )
        before = {"坐标X": entry.get("坐标X"), "坐标Y": entry.get("坐标Y"), "高程": entry.get("高程")}
        self._normalize_coordinates(entry, values)
        entry["修订记录"].append({
            "日期": date.today().isoformat(),
            "类型": "普通修订",
            "修订前": before,
            "说明": str(values.get("remark") or "坐标普通修订，按项目坐标规范取舍"),
        })
        self._apply_conclusion(entry, simulate_timeout=False)
        return entry, "坐标已按项目坐标规范修订并重新核验"

    def reissue_coordinates(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if _to_float(values.get("坐标X")) is None or _to_float(values.get("坐标Y")) is None:
            return None, "重新签发必须同时提供坐标X、坐标Y"
        before = {"坐标X": entry.get("坐标X"), "坐标Y": entry.get("坐标Y")}
        batch = str(values.get("签发批次") or "").strip() or self._next_batch()
        self._normalize_coordinates(entry, values)
        entry["已签发"] = True
        entry["签发批次"] = batch
        entry["签发日期"] = date.today().isoformat()
        entry["修订记录"].append({
            "日期": entry["签发日期"],
            "类型": "重新签发",
            "签发批次": batch,
            "修订前": before,
            "说明": str(values.get("remark") or "已签发坐标变更，按重新签发留痕"),
        })
        self._apply_conclusion(entry, simulate_timeout=False)
        return entry, f"坐标已按重新签发流程更新到批次 {batch}，历史签发已留痕"

    def _next_batch(self) -> str:
        today = date.today().strftime("%Y%m%d")
        serial = sum(
            1
            for row in store.rows(MODULE)
            for record in row.get("修订记录", [])
            if record.get("类型") == "重新签发"
        ) + 1
        return f"PC-{today}-{serial:02d}"

    # ------------------------------------------------------------------
    # 离线采集合并：按点号幂等，已签发坐标受保护
    # ------------------------------------------------------------------
    def offline_merge(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        summary = {"received": len(items), "inserted": 0, "updated": 0, "unchanged": 0,
                   "skipped_protected": 0, "details": []}
        merge_fields = ["点类型", "精度等级", "观测日期", "责任组", "高程", "坐标X", "坐标Y"]
        for raw in items:
            values = raw if isinstance(raw, dict) else {}
            point_no = str(values.get("点号") or "").strip()
            sheet_no = str(values.get("图幅编号") or "").strip()
            if not point_no or not sheet_no:
                summary["details"].append({"点号": point_no or "（空）", "结果": "忽略：缺图幅编号或点号"})
                continue

            entry = self._find_by_business_key(sheet_no, point_no)
            if entry is None:
                values = dict(values)
                values["来源"] = "离线采集"
                new_entry, missing, _ = self.register_point(values)
                if new_entry is None:
                    summary["details"].append({"点号": point_no, "结果": f"忽略：缺少 {'、'.join(missing)}"})
                    continue
                summary["inserted"] += 1
                summary["details"].append({"点号": point_no, "图幅编号": sheet_no, "结果": "新增入库"})
                continue

            changed: list[str] = []
            protected: list[str] = []
            for field in merge_fields:
                if field not in values:
                    continue
                if field in ("坐标X", "坐标Y") and entry.get("已签发"):
                    if _to_float(values.get(field)) is not None:
                        protected.append(field)
                    continue
                if field in ("坐标X", "坐标Y", "高程"):
                    new_value = _to_float(values.get(field))
                    if new_value is None:
                        continue
                    digits = COORD_SPEC[{"坐标X": "坐标X小数位", "坐标Y": "坐标Y小数位", "高程": "高程小数位"}[field]]
                    new_value = _round_half_up(new_value, digits)
                    if entry.get(field) != new_value:
                        entry[field] = new_value
                        changed.append(field)
                else:
                    text = str(values.get(field) or "").strip()
                    if text and entry.get(field) != text:
                        entry[field] = text
                        changed.append(field)

            entry["来源"] = "离线采集+台账" if entry.get("来源") == "台账" else entry.get("来源", "离线采集")
            self._apply_conclusion(entry, simulate_timeout=False)
            if protected:
                summary["skipped_protected"] += 1
                result = f"已存在：{'、'.join(changed) or '无字段'}更新；已签发的 {'、'.join(protected)} 受保护未覆盖"
            elif changed:
                summary["updated"] += 1
                result = f"已存在：幂等合并 {('、'.join(changed))}"
            else:
                summary["unchanged"] += 1
                result = "已存在：数据一致，未重复登记"
            summary["details"].append({"点号": point_no, "图幅编号": sheet_no, "结果": result})
        return summary

    # ------------------------------------------------------------------
    # 核验台：台账 / 点位图 / 高程坐标核验清单 同源于同一份记录
    # ------------------------------------------------------------------
    def board(
        self,
        *,
        sheet: str | None = None,
        keyword: str | None = None,
        cursor: int = 0,
        size: int = 10,
    ) -> dict[str, Any]:
        filtered = self._filtered_rows(sheet=sheet or None, keyword=keyword or None)
        page_items = [row for row in filtered if int(row["id"]) > cursor][:size]
        next_cursor = int(page_items[-1]["id"]) if page_items else cursor

        stats = {
            "total": len(filtered),
            "待核验": sum(1 for row in filtered if row.get("核验结论") == CONCLUSION_PENDING),
            "待补坐标": sum(1 for row in filtered if row.get("核验结论") == CONCLUSION_MISSING),
            "待校正点类型": sum(1 for row in filtered if row.get("核验结论") == CONCLUSION_BAD_TYPE),
            "超时待重试": sum(1 for row in filtered if row.get("核验结论") == CONCLUSION_TIMEOUT),
            "通过": sum(1 for row in filtered if row.get("核验结论") == CONCLUSION_PASS),
            "已签发": sum(1 for row in filtered if row.get("已签发")),
            "待回填点类型": sum(1 for row in filtered if row.get("待回填点类型")),
        }

        return {
            "spec": COORD_SPEC,
            "sheets": self.list_sheets(),
            "stats": stats,
            "empty_sheet": bool(sheet) and not keyword and len(filtered) == 0,
            "ledger": {
                "items": [self._with_disposition(row) for row in page_items],
                "total": len(filtered),
                "cursor": cursor,
                "next_cursor": next_cursor,
                "has_more": next_cursor < max((int(row["id"]) for row in filtered), default=0),
            },
            "projections": self._projections(filtered),
            "verification": [self._verification_row(row) for row in filtered],
        }

    def list_sheets(self) -> list[dict[str, str]]:
        sheets: dict[str, str] = {}
        for row in store.rows(MAPPING_MODULE):
            number = str(row.get("图幅编号") or "").strip()
            if number:
                sheets.setdefault(number, str(row.get("图幅名称") or "").strip())
        for row in store.rows(MODULE):
            number = str(row.get("图幅编号") or "").strip()
            if number:
                sheets.setdefault(number, "")
        return [{"图幅编号": number, "图幅名称": name} for number, name in sorted(sheets.items())]

    def _with_disposition(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["处置建议"] = DISPOSITION.get(row.get("核验结论", CONCLUSION_PENDING), "")
        return result

    def _verification_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "点号": row.get("点号"),
            "图幅编号": row.get("图幅编号"),
            "点类型": row.get("点类型") or "（待回填）",
            "坐标X": row.get("坐标X"),
            "坐标Y": row.get("坐标Y"),
            "高程": row.get("高程"),
            "精度等级": row.get("精度等级"),
            "核验结论": row.get("核验结论", CONCLUSION_PENDING),
            "核验问题": row.get("核验问题", []),
            "核验时间": row.get("核验时间"),
            "已签发": bool(row.get("已签发")),
            "待回填点类型": bool(row.get("待回填点类型")),
            "处置建议": DISPOSITION.get(row.get("核验结论", CONCLUSION_PENDING), ""),
        }

    def _projections(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        located = [
            row for row in rows
            if row.get("坐标X") is not None and row.get("坐标Y") is not None
        ]
        missing = [
            {"id": row["id"], "点号": row.get("点号"), "原因": "缺坐标，未上图"}
            for row in rows
            if row.get("坐标X") is None or row.get("坐标Y") is None
        ]
        if not located:
            return {"points": [], "missing": missing}
        xs = [float(row["坐标X"]) for row in located]
        ys = [float(row["坐标Y"]) for row in located]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        def _scale(value: float, low: float, high: float) -> float:
            if high == low:
                return 50.0
            # 预留 8% 边距，避免点压在图框上
            return 8.0 + (value - low) / (high - low) * 84.0

        points = [
            {
                "id": row["id"],
                "点号": row.get("点号"),
                "点类型": row.get("点类型"),
                "x": round(_scale(float(row["坐标X"]), min_x, max_x), 2),
                "y": round(_scale(float(row["坐标Y"]), min_y, max_y), 2),
                "核验结论": row.get("核验结论", CONCLUSION_PENDING),
                "已签发": bool(row.get("已签发")),
            }
            for row in located
        ]
        return {"points": points, "missing": missing}

    # ------------------------------------------------------------------
    # 旧的状态流转动作保持可用
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于测绘控制可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        return entry, f"控制点已{action}"
