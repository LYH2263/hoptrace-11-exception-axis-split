# 途迹（Hoptrace）

轨迹点装载 → 运单时间线物化 → 迟到旁路与脏点隔离 → 状态证明互证。

**不是** Feed 归并、CDC 游标 Sink、质检签发、数仓分层、交班过户、正冲净额。  
**也不是** 电商订单 / 仓库库存 / 外卖工单 CRUD——去皮后是事件装载与有序物化。

## 启动

```bash
# 后端
cd backend
pip install -r requirements.txt
set DATA_DIR=%CD%\data
set FIXTURES_DIR=%CD%\..\fixtures
uvicorn app.main:app --host 127.0.0.1 --port 10800

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5800 |
| 接口 | http://127.0.0.1:10800 |

## UI

三栏测绘板：顶品牌条 + 中竖轴时间线 + 右检查器 + 底 hop 点阵。  
不使用左轨编号导航、不使用顶栏流程 ribbon。

## 异常轴分流（exception_axis_split）

标记为异常类 event_code 的轨迹点与正常点分轴落库，异常点不得抢对外投影末位。

- **双轴落库（SQLite）**：同一 `hops` 表以不可变 `axis` 列区分正式轴 / 异常轴，
  每行带 `exc_set_version` 异常集合版本快照；异常集合版本化落 `exception_code_versions`
  （仅追加）；分轴证明落 `axis_split_proofs`。库层触发器强制：轴不可变、异常码禁入正式轴、
  同一点禁止两轴同时可见。
- **失败闭分流**：装载时按当前异常集合分流，单事务提交，提交前跑双轴不变量校验，
  任一失败整趟回滚；对外投影只由正式有效轴末 hop 派生。
- **接口协同**：`GET/PUT /api/exception-set`（集合配置，落库版本化）、
  `GET /api/exception-hops`（异常轴独立序列）、`GET /api/axis-proofs` 与
  `GET /api/waybills/{id}/axis-proof`（分轴证明，只读，实时值与物化值互证）。
- **页面**：测绘板双轴展示 + 检查器分轴证明、「异常集合」页配置、「异常轴」页独立列表。
- 硬约束：异常点不改正式投影；无半截迁出；改集合只增新版本，不回溯改写历史轴点。

测试：`cd backend && .venv/bin/python -m pytest tests/ -v`
