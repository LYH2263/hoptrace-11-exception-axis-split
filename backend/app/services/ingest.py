import json
import uuid
from datetime import datetime, timezone

from app.db import FIXTURES_DIR, connect
from app.seed import map_status
from app.services import exception_axis as exc


def run_batch() -> dict:
    """装载分流（失败闭）：

    装载时按当前异常 event_code 集合把每个点分流到正式轴 / 异常轴，
    行内固化 axis 与集合版本快照；全程单事务，提交前跑双轴不变量校验，
    触发器或校验任一失败即整趟回滚——不允许半截迁出、不允许异常点抢正式投影。
    """
    path = FIXTURES_DIR / "ingest_batch.json"
    if not path.exists():
        return {"ok": False, "error": "缺少 ingest_batch.json"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    batch_id = payload.get("batch_id") or f"B-{uuid.uuid4().hex[:8]}"
    cutoff = payload.get("cutoff_ts")
    hops = payload.get("hops", [])

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        codes, exc_version = exc.get_current_set(conn)
        written = 0
        routed_primary = 0
        routed_exception = 0
        touched_waybills: list[str] = []
        for hop in hops:
            wid = hop["waybill_id"]
            exists = conn.execute("SELECT 1 FROM waybills WHERE id=?", (wid,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO waybills(id, title, status_proj, created_at) VALUES (?,?,?,?)",
                    (wid, hop.get("waybill_title") or wid, "未知", _now()),
                )
            axis, snap_version = exc.classify_axis(conn, hop["event_code"])
            conn.execute(
                """
                INSERT OR IGNORE INTO hops(
                    id, waybill_id, event_ts, location_code, event_code, seq,
                    channel, axis, exc_set_version
                )
                VALUES (?,?,?,?,?, ?, 'primary', ?, ?)
                """,
                (
                    hop["id"],
                    wid,
                    hop["event_ts"],
                    hop["location_code"],
                    hop["event_code"],
                    hop.get("seq"),
                    axis,
                    snap_version,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                written += 1
                if axis == exc.AXIS_EXCEPTION:
                    routed_exception += 1
                else:
                    routed_primary += 1
                if wid not in touched_waybills:
                    touched_waybills.append(wid)

        # 提交前失败闭：任何点分错轴 / 缺快照 / 双轴同点 → 抛错整趟回滚
        exc.assert_invariants(conn)

        # 对外投影只由正式有效轴末 hop 派生；异常轴点不参与
        for wid in touched_waybills:
            exc.refresh_waybill_projection(conn, wid, exc_version)

        report = {
            "batch_id": batch_id,
            "cutoff_ts": cutoff,
            "written": written,
            "input": len(hops),
            "routed_primary": routed_primary,
            "routed_exception": routed_exception,
            "exc_set_version": exc_version,
            "exc_codes": sorted(codes),
        }
        conn.execute(
            "INSERT OR REPLACE INTO ingest_batches(id, cutoff_ts, created_at, report_json) VALUES (?,?,?,?)",
            (batch_id, cutoff, _now(), json.dumps(report, ensure_ascii=False)),
        )
        conn.commit()
        return {"ok": True, "report": report}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": f"装载失败闭回滚：{e}"}
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
