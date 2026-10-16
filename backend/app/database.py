from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import settings

connect_args = {"check_same_thread": False, "timeout": 30} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL: leituras não esperam escritas; vários jobs rodam em paralelo.
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Migração simples: adiciona colunas novas em tabelas já existentes."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(engine.dialect)
                default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"
                if default is not None:
                    ddl += f" DEFAULT {int(default) if isinstance(default, bool) else repr(default)}"
                conn.execute(text(ddl))


def init_db() -> None:
    from . import models  # noqa: F401  (registra as tabelas)
    from .quant import models as quant_models  # noqa: F401  (tabelas do motor quant)

    quant_models.migrate(engine)
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    with session_scope() as db:
        if db.get(models.BotConfig, 1) is None:
            db.add(models.BotConfig(id=1))
        if db.get(quant_models.QuantConfig, 1) is None:
            db.add(quant_models.QuantConfig(id=1))
