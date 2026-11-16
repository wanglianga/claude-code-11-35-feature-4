from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 轻量演进：旧库已存在的表只补齐新增列（默认值/可空），不删除、不重建历史数据。
def _ensure_columns(bind) -> None:
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    type_sql = {
        "JSON": "JSON",
        "Boolean": "BOOLEAN",
        "DateTime": "TIMESTAMP WITH TIME ZONE",
        "Float": "DOUBLE PRECISION",
        "Integer": "INTEGER",
        "String": "VARCHAR(256)",
        "Text": "TEXT",
    }
    from . import models  # noqa: F401  确保模型已注册

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {col["name"]: col for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            col_type = type_sql.get(type(column.type).__name__, "VARCHAR(256)")
            default = ""
            if not column.nullable and not column.primary_key:
                if col_type == "JSON":
                    arg_name = getattr(getattr(column.default, "arg", None), "__name__", "")
                    default = " DEFAULT '[]'::json" if arg_name == "list" else " DEFAULT '{}'::json"
                elif col_type == "BOOLEAN":
                    default = " DEFAULT false"
                elif col_type in ("INTEGER", "DOUBLE PRECISION"):
                    default = " DEFAULT 0"
                elif col_type.startswith("VARCHAR") or col_type == "TEXT":
                    default = " DEFAULT ''"
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}{default}'
            with bind.begin() as conn:
                conn.execute(text(ddl))


def init_db():
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)
    _ensure_columns(engine)
