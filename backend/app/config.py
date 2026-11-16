import os


class Settings:
    """应用配置：全部可通过环境变量覆盖，默认指向容器内预装 PostgreSQL。"""

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/ccdev",
    )
    # 是否在启动时写入演示种子数据（幂等）
    SEED_DEMO: bool = os.getenv("SEED_DEMO", "false").lower() == "true"
    # 会话令牌有效期（小时）
    SESSION_TTL_HOURS: int = int(os.getenv("SESSION_TTL_HOURS", "72"))
    # 前端构建产物目录（存在时由后端直接托管）
    FRONTEND_DIST: str = os.getenv(
        "FRONTEND_DIST",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "frontend", "dist"),
    )


settings = Settings()
