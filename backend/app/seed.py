import json

from app.db import FIXTURES_DIR, connect
from app.services import exception_axis as exc


SCHEMA = """
CREATE TABLE IF NOT EXISTS waybills (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status_proj TEXT NOT NULL DEFAULT '未知',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hops (
  id TEXT PRIMARY KEY,
  waybill_id TEXT NOT NULL REFERENCES waybills(id),
  event_ts TEXT NOT NULL,
  location_code TEXT NOT NULL,
  event_code TEXT NOT NULL,
  seq INTEGER,
  channel TEXT NOT NULL DEFAULT 'primary',
  retracted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(waybill_id, event_ts, location_code, event_code)
);

CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waybill_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  member_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_batches (
  id TEXT PRIMARY KEY,
  cutoff_ts TEXT,
  created_at TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS status_proofs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waybill_id TEXT NOT NULL,
  last_hop_id TEXT,
  mapped_code TEXT,
  proj_value TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 异常 event_code 集合：仅追加的版本化配置，改集合只增新版本，永不改写历史
CREATE TABLE IF NOT EXISTS exception_code_versions (
  version INTEGER PRIMARY KEY,
  codes_json TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

-- 分轴证明（运单、正式末 hop、异常点数、投影值、异常集合版本）
CREATE TABLE IF NOT EXISTS axis_split_proofs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waybill_id TEXT NOT NULL,
  primary_last_hop_id TEXT,
  primary_last_code TEXT,
  exception_count INTEGER NOT NULL DEFAULT 0,
  proj_value TEXT NOT NULL,
  exc_set_version INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
"""

# 库内强制的双轴不变量触发器：axis 不可变、快照不可变、异常点禁止进正式轴、
# 正式点禁止挂异常轴、自然点禁止两轴同时可见。
TRIGGERS = """
DROP TRIGGER IF EXISTS trg_hops_axis_immutable;
CREATE TRIGGER trg_hops_axis_immutable
BEFORE UPDATE OF axis, exc_set_version ON hops
WHEN OLD.axis IS NOT NULL AND (
  NEW.axis IS NOT OLD.axis OR
  COALESCE(NEW.exc_set_version, -1) IS NOT COALESCE(OLD.exc_set_version, -1)
)
BEGIN
  SELECT RAISE(ABORT, 'hop 的 axis / 异常集合快照不可变，禁止回溯改轴');
END;

DROP TRIGGER IF EXISTS trg_hops_axis_not_null;
CREATE TRIGGER trg_hops_axis_not_null
BEFORE INSERT ON hops
WHEN NEW.axis IS NULL OR NEW.exc_set_version IS NULL
BEGIN
  SELECT RAISE(ABORT, 'hop 必须带 axis 与异常集合快照版本，禁止隐式分轴');
END;

DROP TRIGGER IF EXISTS trg_hops_no_exception_on_primary;
CREATE TRIGGER trg_hops_no_exception_on_primary
BEFORE INSERT ON hops
WHEN NEW.axis = 'primary' AND EXISTS (
  SELECT 1 FROM exception_code_versions v
  WHERE v.version = NEW.exc_set_version
    AND EXISTS (
      SELECT 1 FROM json_each(v.codes_json) WHERE value = NEW.event_code
    )
)
BEGIN
  SELECT RAISE(ABORT, '失败闭：异常 event_code 的点禁止进入正式有效轴');
END;

DROP TRIGGER IF EXISTS trg_hops_no_primary_on_exception;
CREATE TRIGGER trg_hops_no_primary_on_exception
BEFORE INSERT ON hops
WHEN NEW.axis = 'exception' AND NOT EXISTS (
  SELECT 1 FROM exception_code_versions v
  WHERE v.version = NEW.exc_set_version
    AND EXISTS (
      SELECT 1 FROM json_each(v.codes_json) WHERE value = NEW.event_code
    )
)
BEGIN
  SELECT RAISE(ABORT, '失败闭：非异常 event_code 的点禁止进入异常轴');
END;

DROP TRIGGER IF EXISTS trg_hops_no_dual_axis;
CREATE TRIGGER trg_hops_no_dual_axis
BEFORE INSERT ON hops
WHEN EXISTS (
  SELECT 1 FROM hops h
  WHERE h.waybill_id = NEW.waybill_id
    AND h.event_ts = NEW.event_ts
    AND h.location_code = NEW.location_code
    AND h.event_code = NEW.event_code
    AND h.axis <> NEW.axis
)
BEGIN
  SELECT RAISE(ABORT, '失败闭：同一轨迹点禁止在正式轴与异常轴同时可见');
END;
"""

EVENT_STATUS = {
    "PICKED": "已揽收",
    "IN_TRANSIT": "运输中",
    "ARRIVED_HUB": "到站",
    "OUT_FOR_DELIVERY": "派送中",
    "DELIVERED": "已签收",
    "EXCEPTION": "异常",
    "DAMAGED": "破损异常",
    "LOST": "丢失异常",
}

# 基线异常集合（夹具对照：EXCEPTION/DAMAGED 为异常码，PICKED 等为正常码）
BASELINE_EXCEPTION_CODES = ["EXCEPTION", "DAMAGED", "LOST"]


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    # 先保证异常集合存在，老库 hop 迁移才拿得到快照版本
    exc.ensure_baseline_set(conn, BASELINE_EXCEPTION_CODES)
    # 老库一次性迁移：补不可变 axis / 快照列并按基线集合回填
    exc.migrate_hops_axis(conn)
    conn.executescript(TRIGGERS)
    n = conn.execute("SELECT COUNT(*) AS c FROM waybills").fetchone()["c"]
    if n == 0:
        _seed_fixtures(conn)
    else:
        # 老库正式投影可能曾被异常点抢末位，按双轴规则重建一次
        exc.reproject_all(conn)
    conn.commit()
    conn.close()


def _seed_fixtures(conn):
    fixture = FIXTURES_DIR / "seed_hops.json"
    if not fixture.exists():
        return
    data = json.loads(fixture.read_text(encoding="utf-8"))
    for wb in data.get("waybills", []):
        conn.execute(
            "INSERT INTO waybills(id, title, status_proj, created_at) VALUES (?,?,?,?)",
            (wb["id"], wb["title"], wb.get("status_proj", "未知"), wb["created_at"]),
        )
    codes, version = exc.get_current_set(conn)
    for hop in data.get("hops", []):
        axis = exc.AXIS_EXCEPTION if hop["event_code"] in codes else exc.AXIS_PRIMARY
        conn.execute(
            """
            INSERT INTO hops(id, waybill_id, event_ts, location_code, event_code,
                             seq, channel, axis, exc_set_version)
            VALUES (?,?,?,?,?,?, 'primary', ?, ?)
            """,
            (
                hop["id"],
                hop["waybill_id"],
                hop["event_ts"],
                hop["location_code"],
                hop["event_code"],
                hop.get("seq"),
                axis,
                version,
            ),
        )
    exc.assert_invariants(conn)
    for wb in data.get("waybills", []):
        exc.refresh_waybill_projection(conn, wb["id"], version)


def map_status(event_code: str) -> str:
    return EVENT_STATUS.get(event_code, "未知")
