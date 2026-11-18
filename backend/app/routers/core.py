from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect
from app.services import exception_axis as exc
from app.services import ingest as ingest_svc
from app.services import timeline as timeline_svc

router = APIRouter(prefix="/api")


@router.get("/overview")
def overview():
    conn = connect()
    try:
        return {
            "waybills": conn.execute("SELECT COUNT(*) AS c FROM waybills").fetchone()["c"],
            "hops_primary": conn.execute(
                "SELECT COUNT(*) AS c FROM hops WHERE axis='primary' AND retracted=0"
            ).fetchone()["c"],
            "hops_exception": conn.execute(
                "SELECT COUNT(*) AS c FROM hops WHERE axis='exception'"
            ).fetchone()["c"],
            "hops_sidecar": conn.execute(
                "SELECT COUNT(*) AS c FROM hops WHERE channel='sidecar'"
            ).fetchone()["c"],
            "quarantine": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
            "exception_set": exc.get_exception_set(conn),
        }
    finally:
        conn.close()


@router.get("/waybills")
def list_waybills():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, title, status_proj, created_at FROM waybills ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/waybills/{waybill_id}/timeline")
def waybill_timeline(waybill_id: str):
    conn = connect()
    try:
        wb = conn.execute("SELECT * FROM waybills WHERE id=?", (waybill_id,)).fetchone()
        if not wb:
            raise HTTPException(404, "运单不存在")
        primary_hops = timeline_svc.list_primary(conn, waybill_id)
        exception_hops = timeline_svc.list_exception(conn, waybill_id)
        proof = conn.execute(
            "SELECT * FROM status_proofs WHERE waybill_id=? ORDER BY id DESC LIMIT 1",
            (waybill_id,),
        ).fetchone()
        split_proof = conn.execute(
            "SELECT * FROM axis_split_proofs WHERE waybill_id=? ORDER BY id DESC LIMIT 1",
            (waybill_id,),
        ).fetchone()
        return {
            "waybill": dict(wb),
            "hops": primary_hops,
            "exception_hops": exception_hops,
            "proof": dict(proof) if proof else None,
            "axis_split_proof": dict(split_proof) if split_proof else None,
            "exception_set": exc.get_exception_set(conn),
        }
    finally:
        conn.close()


@router.get("/quarantine")
def list_quarantine():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, waybill_id, reason, member_json, created_at FROM quarantine ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/sidecar")
def list_sidecar():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, waybill_id, event_ts, location_code, event_code, channel FROM hops WHERE channel='sidecar' ORDER BY event_ts"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/ingest/run")
def run_ingest():
    return ingest_svc.run_batch()


@router.get("/status-proofs")
def status_proofs(waybill_id: str | None = None):
    conn = connect()
    try:
        if waybill_id:
            rows = conn.execute(
                "SELECT * FROM status_proofs WHERE waybill_id=? ORDER BY id DESC",
                (waybill_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM status_proofs ORDER BY id DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ------------------------------------------------------- 异常集合配置（落库）

class ExceptionSetIn(BaseModel):
    codes: list[str] = Field(..., description="异常 event_code 集合，非空；保存即生成新版本")
    note: str = ""


@router.get("/exception-set")
def get_exception_set():
    """当前异常集合 + 全部历史版本（仅追加，可审计）。"""
    conn = connect()
    try:
        return {
            "current": exc.get_exception_set(conn),
            "versions": exc.list_versions(conn),
        }
    finally:
        conn.close()


@router.put("/exception-set")
def put_exception_set(payload: ExceptionSetIn):
    """保存异常集合新版本。

    硬约束：只追加新版本，绝不回溯改写任何历史 hop 的 axis / 快照；
    历史点按其行内快照版本解释，新集合只影响此后装载的点。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snap = exc.save_new_version(conn, payload.codes, note=payload.note)
        conn.commit()
        return {"ok": True, "current": snap}
    except ValueError as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"异常集合保存失败：{e}")
    finally:
        conn.close()


# ------------------------------------------------------- 异常轴独立查询

@router.get("/exception-hops")
def exception_hops(waybill_id: str | None = None):
    conn = connect()
    try:
        return exc.list_exception_hops(conn, waybill_id)
    finally:
        conn.close()


# ------------------------------------------------------- 分轴证明（只读）

@router.get("/axis-proofs")
def axis_proofs(waybill_id: str | None = None):
    """物化分轴证明列表：运单、正式末 hop、异常点数、投影值、集合版本。"""
    conn = connect()
    try:
        return exc.list_axis_proofs(conn, waybill_id)
    finally:
        conn.close()


@router.get("/waybills/{waybill_id}/axis-proof")
def waybill_axis_proof(waybill_id: str):
    """只读分轴证明：实时双轴计算并与最新物化证明互证。"""
    conn = connect()
    try:
        try:
            return exc.build_live_proof(conn, waybill_id)
        except KeyError:
            raise HTTPException(404, "运单不存在")
    finally:
        conn.close()
