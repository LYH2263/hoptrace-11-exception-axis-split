"""exception_axis_split 端到端测试：双轴落库、失败闭、投影、证明、集合版本化。"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    """每个用例独立 SQLite 库 + 独立夹具目录。"""
    data_dir = tmp_path / "data"
    fx_dir = tmp_path / "fixtures"
    data_dir.mkdir()
    fx_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FIXTURES_DIR", str(fx_dir))

    # 让 app.db 在新环境变量下重新解析路径
    import app.db as db_mod
    importlib.reload(db_mod)
    import app.seed as seed_mod
    importlib.reload(seed_mod)
    import app.services.exception_axis as exc_mod
    importlib.reload(exc_mod)
    import app.services.timeline as tl_mod
    importlib.reload(tl_mod)
    import app.services.ingest as ingest_mod
    importlib.reload(ingest_mod)
    import app.modules.exception_axis_split as mod
    importlib.reload(mod)

    seed_mod.BASELINE_EXCEPTION_CODES = ["EXCEPTION", "DAMAGED", "LOST"]
    yield {
        "db": db_mod,
        "seed": seed_mod,
        "exc": exc_mod,
        "timeline": tl_mod,
        "ingest": ingest_mod,
        "mod": mod,
        "fx_dir": fx_dir,
    }


SEED = {
    "waybills": [
        {"id": "WB-1", "title": "样件1", "status_proj": "未知",
         "created_at": "2026-11-17T08:00:00Z"},
        {"id": "WB-2", "title": "样件2", "status_proj": "未知",
         "created_at": "2026-11-17T08:00:00Z"},
    ],
    "hops": [
        {"id": "H1", "waybill_id": "WB-1", "event_ts": "2026-11-17T08:10:00Z",
         "location_code": "A", "event_code": "PICKED", "seq": 1},
        {"id": "H2", "waybill_id": "WB-1", "event_ts": "2026-11-17T09:00:00Z",
         "location_code": "B", "event_code": "IN_TRANSIT", "seq": 2},
        # 异常点，时间戳最晚：旧逻辑会抢末位
        {"id": "H3", "waybill_id": "WB-1", "event_ts": "2026-11-17T10:00:00Z",
         "location_code": "C", "event_code": "DAMAGED", "seq": 3},
        {"id": "H4", "waybill_id": "WB-2", "event_ts": "2026-11-17T08:20:00Z",
         "location_code": "A", "event_code": "PICKED", "seq": 1},
    ],
}


def _write_fixture(ctx, name, payload):
    (ctx["fx_dir"] / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _init(ctx):
    ctx["seed"].init_db()


# ---------------------------------------------------------------- 1. 落库分轴

def test_seed_splits_axes_and_persists_set(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        # 异常集合落库且版本化
        snap = app_ctx["exc"].get_exception_set(conn)
        assert snap["version"] == 1
        assert set(snap["codes"]) == {"EXCEPTION", "DAMAGED", "LOST"}

        # 点必须按集合落双轴，且带不可变快照版本
        h3 = conn.execute("SELECT * FROM hops WHERE id='H3'").fetchone()
        assert h3["axis"] == "exception"
        assert h3["exc_set_version"] == 1
        primary = app_ctx["timeline"].list_primary(conn, "WB-1")
        assert [h["id"] for h in primary] == ["H1", "H2"]
        exceptions = app_ctx["exc"].list_exception_hops(conn, "WB-1")
        assert [h["id"] for h in exceptions] == ["H3"]
        assert all(h["axis"] == "exception" for h in exceptions)
    finally:
        conn.close()


def test_projection_only_from_primary_last_hop(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        # 正式末 hop 是 H2(IN_TRANSIT)，最晚的 DAMAGED 不得抢末位
        wb = conn.execute("SELECT * FROM waybills WHERE id='WB-1'").fetchone()
        assert wb["status_proj"] == "运输中"
        proof = app_ctx["exc"].build_live_proof(conn, "WB-1")
        assert proof["primary_last_hop_id"] == "H2"
        assert proof["primary_last_event_code"] == "IN_TRANSIT"
        assert proof["exception_count"] == 1
        assert proof["proj_value"] == "运输中"
        assert proof["exc_set_version"] == 1
        assert proof["consistent"] is True
    finally:
        conn.close()


# ---------------------------------------------------------------- 2. 失败闭

def test_ingest_routes_fail_closed_and_reports(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    _write_fixture(app_ctx, "ingest_batch.json", {
        "batch_id": "B-T1",
        "cutoff_ts": "2026-11-17T12:00:00Z",
        "hops": [
            {"id": "H5", "waybill_id": "WB-2", "event_ts": "2026-11-17T09:00:00Z",
             "location_code": "B", "event_code": "OUT_FOR_DELIVERY", "seq": 2},
            {"id": "H6", "waybill_id": "WB-2", "event_ts": "2026-11-17T11:00:00Z",
             "location_code": "C", "event_code": "EXCEPTION", "seq": 3},
        ],
    })
    result = app_ctx["ingest"].run_batch()
    assert result["ok"] is True
    rep = result["report"]
    assert rep["routed_primary"] == 1
    assert rep["routed_exception"] == 1
    assert rep["exc_set_version"] == 1

    conn = app_ctx["db"].connect()
    try:
        # 异常点独立可查、不进正式轴
        assert conn.execute(
            "SELECT axis FROM hops WHERE id='H6'").fetchone()["axis"] == "exception"
        primary = app_ctx["timeline"].list_primary(conn, "WB-2")
        assert [h["id"] for h in primary] == ["H4", "H5"]
        # 投影取正式末 hop H5，异常 H6 虽时间戳更晚也不改投影
        wb = conn.execute("SELECT * FROM waybills WHERE id='WB-2'").fetchone()
        assert wb["status_proj"] == "派送中"
        proof = app_ctx["exc"].build_live_proof(conn, "WB-2")
        assert proof["primary_last_hop_id"] == "H5"
        assert proof["exception_count"] == 1
    finally:
        conn.close()


def test_rollback_when_exception_point_misrouted_to_primary(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        # 库层：触发器直接拒绝异常码进入正式轴
        with pytest.raises(Exception) as ei:
            conn.execute(
                """INSERT INTO hops(id, waybill_id, event_ts, location_code, event_code,
                                    seq, channel, axis, exc_set_version)
                   VALUES ('HX','WB-1','2026-11-17T10:30:00Z','X','DAMAGED',
                           9,'primary','primary',1)"""
            )
        assert "失败闭" in str(ei.value)
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) AS c FROM hops WHERE id='HX'").fetchone()["c"] == 0

        # 服务层：模拟触发器失效时坏数据混入，assert_invariants 必须抓到，
        # 且调用方可以整趟回滚（SQLite 的 DDL 也在事务内，回滚恢复触发器）
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TRIGGER trg_hops_no_exception_on_primary")
        conn.execute(
            """INSERT INTO hops(id, waybill_id, event_ts, location_code, event_code,
                                seq, channel, axis, exc_set_version)
               VALUES ('HY','WB-1','2026-11-17T10:31:00Z','X','DAMAGED',
                       9,'primary','primary',1)"""
        )
        with pytest.raises(app_ctx["exc"].SplitInvariantError):
            app_ctx["exc"].assert_invariants(conn)
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) AS c FROM hops WHERE id='HY'").fetchone()["c"] == 0
        # 回滚后触发器仍在，坏插入依旧被拒
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO hops(id, waybill_id, event_ts, location_code, event_code,
                                    seq, channel, axis, exc_set_version)
                   VALUES ('HZ','WB-1','2026-11-17T10:32:00Z','X','DAMAGED',
                           9,'primary','primary',1)"""
            )
        conn.rollback()
    finally:
        conn.close()


def test_dual_visibility_blocked(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        # H3 已占自然键 (WB-1,10:00,C,DAMAGED) 在异常轴；
        # 试图把同一自然点再挂到正式轴 → 库层（UNIQUE + 双轴触发器）必须拒绝
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO hops(id, waybill_id, event_ts, location_code, event_code,
                                    seq, channel, axis, exc_set_version)
                   VALUES ('HDUP','WB-1','2026-11-17T10:00:00Z','C','DAMAGED',
                           5,'primary','primary',1)"""
            )
        conn.rollback()
        rows = conn.execute(
            """SELECT axis, COUNT(*) AS c FROM hops
               WHERE waybill_id='WB-1' AND event_ts='2026-11-17T10:00:00Z'
                 AND location_code='C' AND event_code='DAMAGED'
               GROUP BY axis"""
        ).fetchall()
        # 同一点只可能在一条轴上可见，且就是异常轴
        assert [(r["axis"], r["c"]) for r in rows] == [("exception", 1)]

        # 不可变触发器：禁止把已在异常轴的点改挂正式轴
        with pytest.raises(Exception) as ei:
            conn.execute("UPDATE hops SET axis='primary' WHERE id='H3'")
        assert "不可变" in str(ei.value)
        conn.rollback()
    finally:
        conn.close()


def test_axis_immutable_no_historical_rewrite(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        # 改异常集合 → 只追加 v2，历史 H3 仍按 v1 快照留在异常轴
        snap2 = app_ctx["exc"].save_new_version(conn, ["EXCEPTION"], note="移除 DAMAGED")
        conn.commit()
        assert snap2["version"] == 2
        h3 = conn.execute("SELECT * FROM hops WHERE id='H3'").fetchone()
        assert h3["axis"] == "exception"
        assert h3["exc_set_version"] == 1

        # 任何 UPDATE 改 axis / 快照都被不可变触发器拒绝
        with pytest.raises(Exception) as ei:
            conn.execute("UPDATE hops SET axis='primary' WHERE id='H3'")
        assert "不可变" in str(ei.value)
        conn.rollback()

        # 新集合只影响此后装载：DAMAGED 现已不在集合 → 新点进正式轴
        axis, ver = app_ctx["exc"].classify_axis(conn, "DAMAGED")
        assert axis == "primary" and ver == 2
        axis2, _ = app_ctx["exc"].classify_axis(conn, "EXCEPTION")
        assert axis2 == "exception"

        # 历史可审计：两个版本都在
        versions = app_ctx["exc"].list_versions(conn)
        assert [v["version"] for v in versions] == [2, 1]
    finally:
        conn.close()


# ---------------------------------------------------------------- 3. 证明接口

def test_axis_proof_records_all_required_fields(app_ctx):
    _write_fixture(app_ctx, "seed_hops.json", SEED)
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        rows = app_ctx["exc"].list_axis_proofs(conn, "WB-1")
        assert rows, "物化分轴证明应在种子初始化时写入"
        p = rows[0]
        assert p["waybill_id"] == "WB-1"
        assert p["primary_last_hop_id"] == "H2"
        assert p["primary_last_code"] == "IN_TRANSIT"
        assert p["exception_count"] == 1
        assert p["proj_value"] == "运输中"
        assert p["exc_set_version"] == 1
    finally:
        conn.close()


def test_empty_primary_axis_keeps_projection(app_ctx):
    # 运单只有异常点：正式轴为空，投影不得被异常点改写
    _write_fixture(app_ctx, "seed_hops.json", {
        "waybills": [
            {"id": "WB-9", "title": "全异常单", "status_proj": "未知",
             "created_at": "2026-11-17T08:00:00Z"}
        ],
        "hops": [
            {"id": "E1", "waybill_id": "WB-9", "event_ts": "2026-11-17T09:00:00Z",
             "location_code": "X", "event_code": "LOST", "seq": 1}
        ],
    })
    _init(app_ctx)
    conn = app_ctx["db"].connect()
    try:
        wb = conn.execute("SELECT * FROM waybills WHERE id='WB-9'").fetchone()
        assert wb["status_proj"] == "未知"
        proof = app_ctx["exc"].build_live_proof(conn, "WB-9")
        assert proof["primary_last_hop_id"] is None
        assert proof["exception_count"] == 1
        assert app_ctx["timeline"].list_primary(conn, "WB-9") == []
        assert len(app_ctx["exc"].list_exception_hops(conn, "WB-9")) == 1
    finally:
        conn.close()
