import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, batches, catalogs, evidence, performance, reports
from .config import settings
from .db import init_db

app = FastAPI(
    title="收运证据审计与承运商 SLA 绩效服务",
    version="2.0.0",
    description=(
        "计量域：来源证据库、批次关系册、计量裁定器、费率编译器与差异发布器；"
        "绩效域：排班承诺、车辆到场、处理场签收、居民投诉、申辩裁定与双模式月度 SLA 评分。"
        "本服务不派车、不卸料、不审批付款。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(evidence.router)
app.include_router(batches.router)
app.include_router(catalogs.router)
app.include_router(reports.router)
app.include_router(performance.router)


@app.on_event("startup")
def startup():
    init_db()
    if settings.SEED_DEMO:
        import seed  # backend/seed.py（从 backend/ 目录启动时可导入）
        import seed_sla

        seed.seed()
        seed_sla.seed_sla()


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 生产模式：若前端构建产物存在，由后端直接托管（本地单端口验证用）
_dist = os.path.abspath(settings.FRONTEND_DIST)
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        target = os.path.join(_dist, full_path)
        if full_path and os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(_dist, "index.html"))
