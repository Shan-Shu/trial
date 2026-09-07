"""看板 Web 服务（FastAPI）。

启动:
    uv run research-agent-dashboard --port 8000
打开 http://127.0.0.1:8000 查看动态本体图谱与智能体工作状态。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
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
        version="0.0.2",
    )
    app.state.db_path = _db

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "db": _db}

    @app.get("/api/overview")
    def overview() -> dict:
        return dbapi.overview(_db)

    @app.get("/api/papers")
    def papers() -> list[dict]:
        return dbapi.list_papers(_db)

    @app.get("/api/papers/{key}")
    def paper_detail(key: str) -> dict | None:
        return dbapi.get_paper_detail(key, _db)

    @app.get("/api/agents")
    def agents(recent: int = Query(40, ge=1, le=200)) -> dict:
        agents_list, events = dbapi.agent_status(_db, recent=recent)
        return {"agents": agents_list, "recent": events}

    @app.get("/api/logs")
    def logs(limit: int = Query(80, ge=1, le=500)) -> list[dict]:
        return dbapi.activity_log(limit=limit, db_path=_db)

    @app.get("/api/ontology")
    def ontology(
        min_confidence: float = Query(0.0, ge=0.0, le=1.0),
        types: Optional[str] = Query(None, description="逗号分隔的节点类型"),
        q: Optional[str] = Query(None, description="名称搜索"),
        limit: int = Query(800, ge=1, le=5000),
    ) -> dict:
        type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
        return dbapi.ontology_graph(
            _db, min_confidence=min_confidence, types=type_list,
            query=q, limit=limit,
        )

    @app.get("/api/ontology/nodes/{node_id}")
    def node_detail(node_id: int) -> dict | None:
        return dbapi.ontology_node_detail(node_id, _db)

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
