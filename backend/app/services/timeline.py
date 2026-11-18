from app.services import exception_axis as exc


def list_primary(conn, waybill_id: str) -> list[dict]:
    """正式有效轴：只含 axis='primary' 且未撤回的点，异常轴点绝不进入。"""
    rows = conn.execute(
        """
        SELECT id, waybill_id, event_ts, location_code, event_code, seq,
               channel, retracted, axis, exc_set_version
        FROM hops
        WHERE waybill_id=? AND axis='primary' AND retracted=0
        ORDER BY event_ts ASC, id ASC
        """,
        (waybill_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_exception(conn, waybill_id: str) -> list[dict]:
    """异常轴：独立可查序列，含每行分流时的异常集合版本快照。"""
    return exc.list_exception_hops(conn, waybill_id)
