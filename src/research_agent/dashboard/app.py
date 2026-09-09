"""看板 Web 服务（FastAPI）。

启动:
    uv run research-agent-dashboard --port 8000
打开 http://127.0.0.1:8000 查看动态本体图谱与智能体工作状态。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from research_agent.config import settings as default_settings
from research_agent.dashboard import api as dbapi

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """构建看板应用。db_path 缺省用 config.Settings.db_path。"""
    _db = str(db_path) if db_path else str(default_settings.db_path)

    app = FastAPI(
        title="research-agent 看板",
        description="动态本体图谱 + 智能体工作状态 + 输入输出",
        version="0.2.0",
    )
    app.state.db_path = _db

    @app.get("/api/health")
    def health(request: Request) -> dict:
        return {"ok": True, "db": request.app.state.db_path}

    @app.get("/api/databases")
    def databases() -> list[dict]:
        return dbapi.list_databases()

    @app.post("/api/db/select")
    def select_db(payload: dict = Body(...)) -> dict:
        path = str(payload.get("path") or "").strip()
        if not path:
            return {"ok": False, "error": "path 为空"}
        if not Path(path).is_file():
            return {"ok": False, "error": f"数据库不存在: {path}"}
        app.state.db_path = path
        return {"ok": True, "db": path}

    @app.get("/api/status/nodes")
    def status_nodes(request: Request = None) -> dict:
        return dbapi.node_status(request.app.state.db_path)

    @app.get("/api/reviews")
    def reviews(request: Request = None,
                history: bool = Query(False)) -> dict:
        return dbapi.human_review_items(
            request.app.state.db_path, include_history=history)

    @app.post("/api/reviews/submit")
    def reviews_submit(payload: dict = Body(...),
                       request: Request = None) -> dict:
        return dbapi.submit_human_review(
            request.app.state.db_path, payload)

    @app.post("/api/planner/run")
    def planner_run(payload: dict = Body(...),
                    request: Request = None) -> dict:
        text = str(payload.get("request") or "").strip()
        if not text:
            return {"ok": False, "error": "指令为空"}
        db_path = payload.get("db") or request.app.state.db_path
        try:
            result = dbapi.run_planner_request(text, db_path)
            return {"ok": True, **result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @app.post("/api/study/run")
    def study_run(payload: dict = Body(...),
                  request: Request = None) -> dict:
        """后台启动一次确定性研究流程，便于在指令台发送完整任务。"""
        text = str(payload.get("request") or "").strip()
        if not text:
            return {"ok": False, "error": "指令为空"}
        db_path = payload.get("db") or request.app.state.db_path
        import threading
        import uuid
        job_id = uuid.uuid4().hex[:8]

        def _run() -> None:
            from research_agent.config import Settings
            from research_agent.study.graph import StudyServices, run_study

            try:
                settings = Settings(db_path=Path(db_path))
                run_study(
                    text,
                    StudyServices(settings=settings),
                    max_results_override=None,
                )
            except Exception as exc:  # noqa: BLE001
                app.state.last_study_error = {
                    "job_id": job_id, "error": str(exc),
                }

        thread = threading.Thread(target=_run, daemon=True, name="study-run")
        thread.start()
        return {"ok": True, "job_id": job_id, "db": str(db_path)}

    @app.get("/api/overview")
    def overview(request: Request) -> dict:
        return dbapi.overview(request.app.state.db_path)

    @app.get("/api/papers")
    def papers(request: Request) -> list[dict]:
        return dbapi.list_papers(request.app.state.db_path)

    @app.get("/api/papers/{key}")
    def paper_detail(key: str, request: Request) -> dict | None:
        return dbapi.get_paper_detail(key, request.app.state.db_path)

    @app.get("/api/agents")
    def agents(recent: int = Query(40, ge=1, le=200),
               request: Request = None) -> dict:
        agents_list, events = dbapi.agent_status(
            request.app.state.db_path, recent=recent)
        return {"agents": agents_list, "recent": events}

    @app.get("/api/study/status")
    def study_status(request: Request) -> dict:
        return dbapi.study_status(request.app.state.db_path)

    @app.get("/api/logs")
    def logs(limit: int = Query(80, ge=1, le=500),
             node: str | None = Query(None),
             event: str | None = Query(None),
             search: str | None = Query(None),
             request: Request = None) -> list[dict]:
        return dbapi.activity_log(limit=limit, db_path=request.app.state.db_path,
                                  node=node, event=event, search=search)

    @app.get("/api/ontology")
    def ontology(
        min_confidence: float = Query(0.0, ge=0.0, le=1.0),
        types: Optional[str] = Query(None, description="逗号分隔的节点类型"),
        q: Optional[str] = Query(None, description="名称搜索"),
        limit: int = Query(800, ge=1, le=5000),
        request: Request = None,
    ) -> dict:
        type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
        return dbapi.ontology_graph(
            request.app.state.db_path,
            min_confidence=min_confidence, types=type_list,
            query=q, limit=limit,
        )

    @app.get("/api/ontology/nodes/{node_id}")
    def node_detail(node_id: int, request: Request) -> dict | None:
        return dbapi.ontology_node_detail(node_id, request.app.state.db_path)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="research-agent 图形化看板")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", help="SQLite 数据库路径（默认 data/research_agent.db）")
    args = ap.parse_args(argv)

    import uvicorn

    app = create_app(args.db)
    print(f"看板已启动: http://{args.host}:{args.port}  (DB: {app.state.db_path})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
