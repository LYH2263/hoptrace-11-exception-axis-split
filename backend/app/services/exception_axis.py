"""异常轴分流领域服务（exception_axis_split）。

落库形态：同一 hops 表以不可变 axis 列区分正式轴 / 异常轴，
每行快照分流时所用的异常集合版本 exc_set_version；
异常 event_code 集合版本化落表 exception_code_versions（仅追加，不改历史）。
分流路径：装载时按当前异常集合分流，单事务提交，提交前做不变量校验，
任一点分流错误即抛 SplitInvariantError，由装载方整趟回滚（失败闭）。
"""
import json
from datetime import datetime, timezone

AXIS_PRIMARY = "primary"
AXIS_EXCEPTION = "exception"


class SplitInvariantError(RuntimeError):
    """双轴不变量被破坏：异常点误入正式轴 / 同一点两轴可见 / 快照缺失。"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 异常集合

def get_exception_set(conn, version: int | None = None) -> dict:
    if version is None:
        row = conn.execute(
            "SELECT * FROM exception_code_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM exception_code_versions WHERE version=?", (version,)
        ).fetchone()
    if not row:
        raise SplitInvariantError("异常 event_code 集合未初始化")
    return {
        "version": row["version"],
        "codes": json.loads(row["codes_json"]),
        "note": row["note"],
        "created_at": row["created_at"],
    }


def get_current_set(conn) -> tuple[set[str], int]:
    snap = get_exception_set(conn)
    return set(snap["codes"]), snap["version"]


def list_versions(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT version, codes_json, note, created_at FROM exception_code_versions ORDER BY version DESC"
    ).fetchall()
    return [
        {
            "version": r["version"],
            "codes": json.loads(r["codes_json"]),
            "note": r["note"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def ensure_baseline_set(conn, codes: list[str]) -> dict:
    """库内无任何版本时写入 v1 基线；已存在则不动历史。"""
    row = conn.execute("SELECT COUNT(*) AS c FROM exception_code_versions").fetchone()
    if row["c"] > 0:
        return get_exception_set(conn)
    return save_new_version(conn, codes, note="基线异常集合 v1")


def _normalize_codes(codes) -> list[str]:
    if not isinstance(codes, list) or not codes:
        raise ValueError("异常 event_code 集合必须是非空数组")
    normalized: list[str] = []
    for code in codes:
        if not isinstance(code, str):
            raise ValueError("event_code 必须是字符串")
        code = code.strip()
        if not code:
            raise ValueError("event_code 不允许空串")
        if code not in normalized:
            normalized.append(code)
    return normalized


def save_new_version(conn, codes: list[str], note: str = "") -> dict:
    """仅追加新版本：绝不回溯改写任何历史 hop 的 axis / 快照版本。"""
    normalized = _normalize_codes(codes)
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM exception_code_versions"
    ).fetchone()
    next_version = row["v"] + 1
    conn.execute(
        "INSERT INTO exception_code_versions(version, codes_json, note, created_at) VALUES (?,?,?,?)",
        (next_version, json.dumps(normalized, ensure_ascii=False), note or "", _now()),
    )
    return get_exception_set(conn, next_version)


# ---------------------------------------------------------------- 装载分流

def classify_axis(conn, event_code: str) -> tuple[str, int]:
    """装载时分流：集合内 event_code 必入异常轴，否则入正式轴。

    返回 (axis, exc_set_version)，随 hop 行固化为快照，之后集合变更不影响该行。
    """
    codes, version = get_current_set(conn)
    axis = AXIS_EXCEPTION if event_code in codes else AXIS_PRIMARY
    return axis, version


def migrate_hops_axis(conn) -> bool:
    """老库一次性迁移：补 axis / 快照列并按当前集合回填（触发器建立之前执行）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hops)").fetchall()}
    migrated = False
    if "axis" not in cols:
        conn.execute(
            "ALTER TABLE hops ADD COLUMN axis TEXT NOT NULL DEFAULT 'primary'"
        )
        migrated = True
    if "exc_set_version" not in cols:
        conn.execute("ALTER TABLE hops ADD COLUMN exc_set_version INTEGER")
        migrated = True
    null_rows = conn.execute(
        "SELECT COUNT(*) AS c FROM hops WHERE exc_set_version IS NULL"
    ).fetchone()["c"]
    if null_rows:
        codes, version = get_current_set(conn)
        if codes:
            placeholders = ",".join("?" * len(codes))
            conn.execute(
                f"""
                UPDATE hops
                SET axis = CASE WHEN event_code IN ({placeholders})
                                THEN 'exception' ELSE 'primary' END,
                    exc_set_version = ?
                WHERE exc_set_version IS NULL
                """,
                (*codes, version),
            )
        else:
            conn.execute(
                "UPDATE hops SET axis='primary', exc_set_version=? WHERE exc_set_version IS NULL",
                (version,),
            )
        migrated = True
    return migrated


def assert_invariants(conn) -> None:
    """提交前失败闭校验，任何一条不满足都抛错，调用方必须整趟回滚。"""
    # 1) 不允许无快照行（分流结论必须落库，不允许隐式轴）
    orphan = conn.execute(
        "SELECT COUNT(*) AS c FROM hops WHERE exc_set_version IS NULL OR axis IS NULL"
    ).fetchone()["c"]
    if orphan:
        raise SplitInvariantError(f"存在 {orphan} 个缺少轴快照的 hop，分流未落库")

    # 2) 每行的轴必须与其快照版本对应的异常集合一致：
    #    异常码不得留在正式轴，正式码不得被放进异常轴（按行快照版本判定，不回溯）
    versions = [
        r["version"]
        for r in conn.execute(
            "SELECT DISTINCT exc_set_version AS version FROM hops"
        ).fetchall()
    ]
    for version in versions:
        snap = get_exception_set(conn, version)
        codes = snap["codes"]
        placeholders = ",".join("?" * len(codes)) if codes else ""
        if codes:
            bad_primary = conn.execute(
                f"""
                SELECT COUNT(*) AS c FROM hops
                WHERE exc_set_version=? AND axis='primary' AND event_code IN ({placeholders})
                """,
                (version, *codes),
            ).fetchone()["c"]
            if bad_primary:
                raise SplitInvariantError(
                    f"异常码 {bad_primary} 个点误入正式轴（集合版本 v{version}）"
                )
            bad_exception = conn.execute(
                f"""
                SELECT COUNT(*) AS c FROM hops
                WHERE exc_set_version=? AND axis='exception'
                  AND event_code NOT IN ({placeholders})
                """,
                (version, *codes),
            ).fetchone()["c"]
            if bad_exception:
                raise SplitInvariantError(
                    f"正式码 {bad_exception} 个点误入异常轴（集合版本 v{version}）"
                )
        else:
            bad_exception = conn.execute(
                "SELECT COUNT(*) AS c FROM hops WHERE exc_set_version=? AND axis='exception'",
                (version,),
            ).fetchone()["c"]
            if bad_exception:
                raise SplitInvariantError("空异常集合下不允许存在异常轴点")

    # 3) 同一个自然点不允许在两条轴上同时可见（UNIQUE 约束之外的显式防线）
    dual = conn.execute(
        """
        SELECT COUNT(*) AS c FROM (
          SELECT waybill_id, event_ts, location_code, event_code
          FROM hops
          GROUP BY waybill_id, event_ts, location_code, event_code
          HAVING COUNT(DISTINCT axis) > 1
        )
        """
    ).fetchone()["c"]
    if dual:
        raise SplitInvariantError(f"存在 {dual} 个点同时出现在正式轴与异常轴")


# ---------------------------------------------------------------- 投影与证明

def _primary_last_hop(conn, waybill_id: str):
    return conn.execute(
        """
        SELECT id, event_code, event_ts
        FROM hops
        WHERE waybill_id=? AND axis='primary' AND retracted=0
        ORDER BY event_ts DESC, id DESC
        LIMIT 1
        """,
        (waybill_id,),
    ).fetchone()


def refresh_waybill_projection(conn, waybill_id: str, exc_version: int | None = None) -> dict:
    """对外投影只由正式有效轴末 hop 派生；异常轴点永不参与。

    同时物化一条分轴证明快照（运单、正式末 hop、异常点数、投影值、集合版本）。
    """
    from app.seed import map_status  # 延迟导入避免循环依赖

    if exc_version is None:
        _, exc_version = get_current_set(conn)
    last = _primary_last_hop(conn, waybill_id)
    wb = conn.execute(
        "SELECT status_proj FROM waybills WHERE id=?", (waybill_id,)
    ).fetchone()
    if last:
        proj = map_status(last["event_code"])
        conn.execute(
            "UPDATE waybills SET status_proj=? WHERE id=?", (proj, waybill_id)
        )
        conn.execute(
            """
            INSERT INTO status_proofs(waybill_id, last_hop_id, mapped_code, proj_value, created_at)
            VALUES (?,?,?,?,?)
            """,
            (waybill_id, last["id"], last["event_code"], proj, _now()),
        )
    else:
        # 正式轴无有效点：保留既有投影，绝不允许异常点改正式投影
        proj = wb["status_proj"] if wb else "未知"

    exc_count = conn.execute(
        "SELECT COUNT(*) AS c FROM hops WHERE waybill_id=? AND axis='exception'",
        (waybill_id,),
    ).fetchone()["c"]
    conn.execute(
        """
        INSERT INTO axis_split_proofs(
            waybill_id, primary_last_hop_id, primary_last_code,
            exception_count, proj_value, exc_set_version, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            waybill_id,
            last["id"] if last else None,
            last["event_code"] if last else None,
            exc_count,
            proj,
            exc_version,
            _now(),
        ),
    )
    return {
        "waybill_id": waybill_id,
        "primary_last_hop_id": last["id"] if last else None,
        "primary_last_code": last["event_code"] if last else None,
        "exception_count": exc_count,
        "proj_value": proj,
        "exc_set_version": exc_version,
    }


def reproject_all(conn) -> None:
    _, version = get_current_set(conn)
    rows = conn.execute("SELECT id FROM waybills ORDER BY id").fetchall()
    for r in rows:
        refresh_waybill_projection(conn, r["id"], version)


def build_live_proof(conn, waybill_id: str) -> dict:
    """只读分轴证明：实时按库内双轴计算，并与最新物化证明互证。"""
    wb = conn.execute("SELECT * FROM waybills WHERE id=?", (waybill_id,)).fetchone()
    if not wb:
        raise KeyError(waybill_id)
    current = get_exception_set(conn)
    last = _primary_last_hop(conn, waybill_id)
    exc_count = conn.execute(
        "SELECT COUNT(*) AS c FROM hops WHERE waybill_id=? AND axis='exception'",
        (waybill_id,),
    ).fetchone()["c"]
    live = {
        "waybill_id": waybill_id,
        "primary_last_hop_id": last["id"] if last else None,
        "primary_last_event_code": last["event_code"] if last else None,
        "exception_count": exc_count,
        "proj_value": wb["status_proj"],
        "exc_set_version": current["version"],
        "exc_codes": current["codes"],
    }
    stored = conn.execute(
        "SELECT * FROM axis_split_proofs WHERE waybill_id=? ORDER BY id DESC LIMIT 1",
        (waybill_id,),
    ).fetchone()
    live["latest_stored_proof"] = dict(stored) if stored else None
    if stored:
        live["consistent"] = (
            stored["primary_last_hop_id"] == live["primary_last_hop_id"]
            and stored["exception_count"] == live["exception_count"]
            and stored["proj_value"] == live["proj_value"]
        )
    else:
        live["consistent"] = False
    return live


def list_exception_hops(conn, waybill_id: str | None = None) -> list[dict]:
    if waybill_id:
        rows = conn.execute(
            """
            SELECT id, waybill_id, event_ts, location_code, event_code, seq,
                   axis, exc_set_version
            FROM hops WHERE waybill_id=? AND axis='exception'
            ORDER BY event_ts ASC, id ASC
            """,
            (waybill_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, waybill_id, event_ts, location_code, event_code, seq,
                   axis, exc_set_version
            FROM hops WHERE axis='exception' ORDER BY event_ts ASC, id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def list_axis_proofs(conn, waybill_id: str | None = None) -> list[dict]:
    if waybill_id:
        rows = conn.execute(
            "SELECT * FROM axis_split_proofs WHERE waybill_id=? ORDER BY id DESC",
            (waybill_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM axis_split_proofs ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]
