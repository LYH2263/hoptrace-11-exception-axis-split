"""exception_axis_split 功能实现入口。

双轴分流（正式有效轴 / 异常轴）的领域门面，具体逻辑在 app.services.exception_axis。

落库形态：同一 hops 表以不可变 axis 列区分双轴 + exc_set_version 异常码快照列，
异常集合版本化落 exception_code_versions（仅追加）。
分流路径：装载时按集合分流，单事务 + 提交前不变量校验，失败整趟回滚。
"""
from app.services.exception_axis import (
    AXIS_EXCEPTION,
    AXIS_PRIMARY,
    SplitInvariantError,
    assert_invariants,
    build_live_proof,
    classify_axis,
    get_exception_set,
    list_axis_proofs,
    list_exception_hops,
    list_versions,
    refresh_waybill_projection,
    save_new_version,
)

STUB = False

__all__ = [
    "STUB",
    "AXIS_PRIMARY",
    "AXIS_EXCEPTION",
    "SplitInvariantError",
    "assert_invariants",
    "build_live_proof",
    "classify_axis",
    "get_exception_set",
    "list_axis_proofs",
    "list_exception_hops",
    "list_versions",
    "refresh_waybill_projection",
    "save_new_version",
]
