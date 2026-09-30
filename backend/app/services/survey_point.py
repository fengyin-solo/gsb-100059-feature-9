"""坐标核验台业务规则。

控制点的点号是自然键：登记、重试、离线合并全部按点号幂等，任何路径都不得
重复登记同一个点号。坐标精度以本模块的项目坐标规范（COORD_SPEC）为准，所有
写入口都先按规范四舍五入再落库；已签发坐标保存快照并锁定，普通修订只能走
重新签发。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any

from app.store import store

MODULE = "survey_point"

# ---- 项目坐标规范：X/Y/高程各保留几位小数（取舍一律 ROUND_HALF_UP，即四舍五入）----
COORD_SPEC = {"X": 3, "Y": 3, "高程": 4}
COORD_FIELDS = ("坐标X", "坐标Y", "高程")

# 点类型目录：不在目录内的取值属于点类型异常，须回填后才能签发
POINT_TYPES = ["控制点", "图根点", "水准点", "GPS点", "三角点"]
# 历史控制点缺点类型时的默认回填值
LEGACY_POINT_TYPE = "图根点"
# 历史责任组迁移目标
CURRENT_RESPONSIBLE_GROUP = "测绘控制组"
LEGACY_RESPONSIBLE_GROUPS = {"历史测绘班", "老测绘队", "外业班", None, ""}

VERDICTS = ["待核验", "合格", "不合格", "待复测"]
FINAL_VERDICTS = {"合格", "不合格", "待复测"}
STATUS_ORDER = ["完好", "损坏", "已恢复", "废弃"]
ACTION_RULES = {"登记损坏": "损坏", "安排恢复": "已恢复", "标记废弃": "废弃"}

REQUIRED_REGISTER_FIELDS = ["点号", "图幅编号"]


def _round_coord(value: Any, digits: int) -> float | None:
    """按项目坐标规范取舍；无法解析的数值按缺失处理，不写脏数据。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    quant = Decimal(1).scaleb(-digits)
    return float(dec.quantize(quant, rounding=ROUND_HALF_UP))


def _parse_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _is_number(value: Any) -> bool:
    return _parse_float(value) is not None


def _apply_coords(entry: dict[str, Any], values: dict[str, Any]) -> set[str]:
    """按规范写回 X/Y/高程，返回本次被规范化的字段名集合。"""
    normalized: set[str] = set()
    for key, spec_key in zip(COORD_FIELDS, ("X", "Y", "高程")):
        if key in values:
            rounded = _round_coord(values[key], COORD_SPEC[spec_key])
            entry[key] = rounded
            normalized.add(key)
    return normalized


def _coord_missing(entry: dict[str, Any]) -> bool:
    """平面坐标（X 或 Y）缺失即判定坐标缺失；高程单列但不在此列。"""
    return not _is_number(entry.get("坐标X")) or not _is_number(entry.get("坐标Y"))


def _type_abnormal(entry: dict[str, Any]) -> bool:
    return str(entry.get("点类型") or "").strip() not in POINT_TYPES


def _derive_flags(entry: dict[str, Any]) -> None:
    verdict = str(entry.get("核验结论") or "待核验")
    entry["pending"] = verdict != "合格"
    entry["abnormal"] = _coord_missing(entry) or _type_abnormal(entry) or verdict == "不合格"


def _point_view(entry: dict[str, Any]) -> dict[str, Any]:
    """台账、点位图、核验清单共用的同一份口径：派生异常标记后原样返回。"""
    _derive_flags(entry)
    view = dict(entry)
    view["坐标缺失"] = _coord_missing(entry)
    view["高程缺失"] = not _is_number(entry.get("高程"))
    view["点类型异常"] = _type_abnormal(entry)
    return view


class SurveyPointService:
    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        sheet: str | None = None,
        verdict: str | None = None,
        anomaly: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("点号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if sheet:
            rows = [row for row in rows if str(row.get("图幅编号") or "") == sheet]
        if verdict:
            rows = [row for row in rows if str(row.get("核验结论") or "待核验") == verdict]
        if anomaly == "坐标缺失":
            rows = [row for row in rows if _coord_missing(row)]
        elif anomaly == "点类型异常":
            rows = [row for row in rows if _type_abnormal(row)]
        # 稳定分页：固定按 id 升序。翻页期间即便有新点并入，也只在末页追加，
        # 已翻过的页不会错位，杜绝重复或漏项。
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return [_point_view(row) for row in rows[start:start + size]], total

    def all_entries(self) -> list[dict[str, Any]]:
        return [_point_view(row) for row in sorted(store.rows(MODULE), key=lambda r: int(r.get("id", 0)))]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return _point_view(row) if row is not None else None

    def _find_by_code(self, code: str) -> dict[str, Any] | None:
        code = code.strip()
        for row in store.rows(MODULE):
            if str(row.get("点号") or "").strip() == code:
                return row
        return None

    def list_sheets(self) -> list[dict[str, Any]]:
        """图幅汇总：合并填图单元里登记过的图幅，空图幅也要出现在核验台里。"""
        sheets: dict[str, dict[str, Any]] = {}
        for row in store.rows(MODULE):
            code = str(row.get("图幅编号") or "").strip()
            if not code:
                continue
            bucket = sheets.setdefault(code, {"图幅编号": code, "图幅名称": "", "控制点数": 0,
                                              "待核验": 0, "异常点": 0})
            bucket["控制点数"] += 1
            if str(row.get("核验结论") or "待核验") != "合格":
                bucket["待核验"] += 1
            view = _point_view(row)
            if view["abnormal"]:
                bucket["异常点"] += 1
        for row in store.rows("mapping"):
            code = str(row.get("图幅编号") or "").strip()
            if not code:
                continue
            bucket = sheets.setdefault(code, {"图幅编号": code, "图幅名称": "", "控制点数": 0,
                                              "待核验": 0, "异常点": 0})
            bucket["图幅名称"] = str(row.get("图幅名称") or bucket["图幅名称"])
        return sorted(sheets.values(), key=lambda item: item["图幅编号"])

    def stats(self) -> dict[str, int]:
        rows = store.rows(MODULE)
        views = [_point_view(row) for row in rows]
        return {
            "控制点总数": len(views),
            "待核验": sum(1 for v in views if str(v.get("核验结论") or "待核验") in {"待核验", "待复测"}),
            "合格": sum(1 for v in views if v.get("核验结论") == "合格"),
            "坐标缺失": sum(1 for v in views if v["坐标缺失"]),
            "点类型异常": sum(1 for v in views if v["点类型异常"]),
            "已签发": sum(1 for v in views if v.get("签发坐标")),
        }

    # ---------- 写入（全部按点号幂等）----------
    def register_point(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        """登记控制点。点号已存在时按幂等处理返回原记录，绝不重复登记同一点号。"""
        missing = [field for field in REQUIRED_REGISTER_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        code = str(values["点号"]).strip()
        existing = self._find_by_code(code)
        if existing is not None:
            return _point_view(existing), [], "点号已存在，按幂等返回既有控制点，未重复登记"
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "点号": code,
            "图幅编号": str(values["图幅编号"]).strip(),
            "点类型": str(values.get("点类型") or "").strip() or None,
            "坐标X": None,
            "坐标Y": None,
            "高程": None,
            "精度等级": str(values.get("精度等级") or "").strip() or None,
            "观测日期": str(values.get("观测日期") or "").strip() or None,
            "责任组": str(values.get("责任组") or CURRENT_RESPONSIBLE_GROUP).strip(),
            "核验结论": "待核验",
            "结论说明": None,
            "签发坐标": None,
            "status": "完好",
        }
        _apply_coords(entry, values)
        _derive_flags(entry)
        rows.append(entry)
        return _point_view(entry), [], "控制点已登记，等待坐标核验"

    def verify_point(
        self, entry_id: int, verdict: str, note: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """下核验结论：合格 / 不合格 / 待复测。结论统一回写到控制点主记录。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if verdict not in FINAL_VERDICTS:
            return None, f"核验结论「{verdict}」不被支持，可选：{'、'.join(sorted(FINAL_VERDICTS))}"
        if verdict == "合格" and _coord_missing(entry):
            return None, "坐标缺失的控制点不能判合格，请先补测平面坐标"
        if verdict == "合格" and _type_abnormal(entry):
            return None, "点类型异常的控制点不能判合格，请先在目录内回填点类型"
        entry["核验结论"] = verdict
        entry["结论说明"] = (note or "").strip() or entry.get("结论说明")
        _derive_flags(entry)
        return _point_view(entry), f"核验结论已更新为「{verdict}」，台账、点位图与核验清单已同步"

    def revise_point(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """普通修订：未签发的点可改坐标；已签发点只接受非坐标字段，坐标必须走重新签发。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if entry.get("签发坐标"):
            coord_keys = [key for key in COORD_FIELDS if key in values and str(values[key] or "").strip() != ""]
            if coord_keys:
                snap = entry["签发坐标"]
                return None, (
                    f"点号 {entry.get('点号')} 的坐标已于历史批次签发并锁定，"
                    f"普通修订不得覆盖（签发快照 X={snap.get('X')}、Y={snap.get('Y')}、"
                    f"高程={snap.get('高程')}）；如需变更请走「重新签发」"
                )
        _apply_coords(entry, values)
        for key in ("点类型", "精度等级", "观测日期", "责任组", "图幅编号"):
            if key in values and str(values[key] or "").strip() != "":
                entry[key] = str(values[key]).strip()
        # 坐标被普通修订改动后，结论回退待核验，避免旧结论盖在新数据上
        if any(key in values for key in COORD_FIELDS) and not entry.get("签发坐标"):
            entry["核验结论"] = "待核验"
        _derive_flags(entry)
        return _point_view(entry), "修订已按项目坐标规范取舍后保存"

    def reissue_point(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """重新签发：覆盖已签发坐标的唯一路径，留新快照，结论强制转待复测复核。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        merged = {"坐标X": entry.get("坐标X"), "坐标Y": entry.get("坐标Y"), "高程": entry.get("高程")}
        merged.update({k: v for k, v in values.items() if str(v or "").strip() != ""})
        if _coord_missing(merged):
            return None, "重新签发要求 X/Y 坐标齐全，缺项请先补测"
        _apply_coords(entry, merged)
        entry["签发坐标"] = {
            "X": entry["坐标X"],
            "Y": entry["坐标Y"],
            "高程": entry["高程"],
            "签发批次": str(values.get("签发批次") or "").strip() or None,
        }
        entry["核验结论"] = "待复测"
        entry["结论说明"] = "坐标已重新签发，需复测复核后再判合格"
        _derive_flags(entry)
        return _point_view(entry), "坐标已重新签发并生成新快照，结论转待复测"

    def issue_point(self, entry_id: int, batch: str | None) -> tuple[dict[str, Any] | None, str]:
        """首次签发：核验合格的点锁定坐标，之后普通修订无法覆盖。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if entry.get("签发坐标"):
            return None, "该点坐标已签发；如需变更只能走「重新签发」"
        if _coord_missing(entry):
            return None, "坐标缺失的控制点不能签发"
        if _type_abnormal(entry):
            return None, "点类型异常的控制点不能签发，请先回填点类型"
        if str(entry.get("核验结论") or "") != "合格":
            return None, "仅核验结论为「合格」的控制点可以签发"
        entry["签发坐标"] = {
            "X": entry["坐标X"],
            "Y": entry["坐标Y"],
            "高程": entry["高程"],
            "签发批次": (batch or "").strip() or None,
        }
        _derive_flags(entry)
        return _point_view(entry), "坐标已签发并锁定，普通修订不再覆盖"

    def backfill_type(
        self, entry_id: int, point_type: str, group: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """回填点类型（异常点处置入口），可同时迁移责任组。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"控制点 {entry_id} 不存在或已归档"
        if point_type not in POINT_TYPES:
            return None, f"点类型「{point_type}」不在目录内，可选：{'、'.join(POINT_TYPES)}"
        entry["点类型"] = point_type
        if group and group.strip():
            entry["责任组"] = group.strip()
        _derive_flags(entry)
        return _point_view(entry), f"点类型已回填为「{point_type}」，可继续核验"

    def sync_offline(self, points: list[dict[str, Any]]) -> dict[str, Any]:
        """离线采集合并：按点号幂等 upsert。

        - 新点：登记并按规范取舍坐标；
        - 已有点：只补空字段（含点类型、责任组、高程等），非空字段不覆盖；
        - 已签发坐标：任何离线数据都不得改动；
        - 同一批次重复提交结果一致，可安全重试。
        """
        results: list[dict[str, Any]] = []
        merged = skipped = created = 0
        for item in points:
            code = str(item.get("点号") or "").strip()
            if not code:
                results.append({"点号": code, "结果": "跳过", "说明": "缺少点号，无法幂等合并"})
                skipped += 1
                continue
            rows = store.rows(MODULE)
            entry = next((row for row in rows if str(row.get("点号") or "").strip() == code), None)
            if entry is None:
                new_entry: dict[str, Any] = {
                    "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                    "点号": code,
                    "图幅编号": str(item.get("图幅编号") or "").strip() or None,
                    "点类型": str(item.get("点类型") or "").strip() or None,
                    "坐标X": None,
                    "坐标Y": None,
                    "高程": None,
                    "精度等级": str(item.get("精度等级") or "").strip() or None,
                    "观测日期": str(item.get("观测日期") or "").strip() or None,
                    "责任组": str(item.get("责任组") or CURRENT_RESPONSIBLE_GROUP).strip(),
                    "核验结论": "待核验",
                    "结论说明": "离线采集合并，待核验",
                    "签发坐标": None,
                    "status": "完好",
                }
                _apply_coords(new_entry, item)
                _derive_flags(new_entry)
                rows.append(new_entry)
                created += 1
                results.append({"点号": code, "结果": "新增", "id": new_entry["id"],
                                "说明": "离线点已按点号登记"})
                continue
            notes: list[str] = []
            locked = bool(entry.get("签发坐标"))
            if locked:
                notes.append("坐标已签发锁定，离线坐标未覆盖")
            else:
                spec_by_field = {"坐标X": COORD_SPEC["X"], "坐标Y": COORD_SPEC["Y"], "高程": COORD_SPEC["高程"]}
                for key in COORD_FIELDS:
                    if not _is_number(entry.get(key)) and str(item.get(key) or "").strip() != "":
                        entry[key] = _round_coord(item[key], spec_by_field[key])
                        notes.append(f"{key}已补齐")
            for key in ("点类型", "图幅编号", "精度等级", "观测日期", "责任组"):
                if not str(entry.get(key) or "").strip() and str(item.get(key) or "").strip():
                    entry[key] = str(item[key]).strip()
                    notes.append(f"{key}已补齐")
            if not locked and entry.get("核验结论") == "合格" and (_coord_missing(entry) or _type_abnormal(entry)):
                entry["核验结论"] = "待核验"
            _derive_flags(entry)
            merged += 1
            results.append({"点号": code, "结果": "合并", "id": entry["id"],
                            "说明": "；".join(notes) or "已有同点号记录，空字段无需补齐，未重复登记"})
        return {"新增": created, "合并": merged, "跳过": skipped, "明细": results}

    def migrate_legacy(self, cutoff: str = "2020-01-01") -> dict[str, Any]:
        """历史控制点治理（幂等，可反复执行）。

        治理范围仅限历史点：责任组属于已撤销旧班组，或观测日期早于 cutoff。
        - 缺点类型（字段缺失或为空）：回填为图根点；点类型取值不在目录属于
          「点类型异常」，留给核验台人工处置，不在本迁移里静默改写；
        - 旧班组责任组：迁移到测绘控制组。
        新建的缺点类型点不在治理范围内，仍走异常处置路径。
        """
        backfilled: list[str] = []
        migrated: list[str] = []
        for entry in store.rows(MODULE):
            code = str(entry.get("点号"))
            group = str(entry.get("责任组") or "").strip()
            observed = str(entry.get("观测日期") or "").strip()
            is_legacy = group in LEGACY_RESPONSIBLE_GROUPS or (bool(observed) and observed < cutoff)
            if not is_legacy:
                continue
            point_type = str(entry.get("点类型") or "").strip()
            if not point_type:
                entry["点类型"] = LEGACY_POINT_TYPE
                backfilled.append(code)
                entry["迁移备注"] = f"历史数据治理：缺点类型，回填为{LEGACY_POINT_TYPE}"
            if group in LEGACY_RESPONSIBLE_GROUPS:
                entry["责任组"] = CURRENT_RESPONSIBLE_GROUP
                migrated.append(code)
                prior = f"原责任组{group}" if group else "原责任组缺失"
                entry["迁移备注"] = (entry.get("迁移备注", "") + f"；{prior}，迁入{CURRENT_RESPONSIBLE_GROUP}").strip("；")
            # 历史数据缺图幅编号时挂到首张图幅，避免落到图幅之外无人核验
            if not str(entry.get("图幅编号") or "").strip():
                sheets = [r.get("图幅编号") for r in store.rows("mapping") if r.get("图幅编号")]
                if sheets:
                    entry["图幅编号"] = sheets[0]
            entry.setdefault("核验结论", "待核验")
            entry.setdefault("签发坐标", None)
            _derive_flags(entry)
        return {"回填点类型": backfilled, "迁移责任组": migrated,
                "说明": f"回填 {len(backfilled)} 点，迁移责任组 {len(migrated)} 点"}

    # ---------- 兼容旧版点位状态动作 ----------
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
        _derive_flags(entry)
        return _point_view(entry), f"控制点已{action}"
