import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from dotenv import load_dotenv   # ✅ 추가
load_dotenv()  

from app.models import Base  # <-- add this import

target_metadata = Base.metadata  # <-- enable autogenerate from ORM

# Interpret the config file for Python logging.
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Pull database URL from env (SYNC driver)
SYNC_DATABASE_URL = os.getenv("SYNC_DATABASE_URL")

if not SYNC_DATABASE_URL:
    # fallback for local dev
    SYNC_DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql+psycopg://")
if not SYNC_DATABASE_URL:
    raise RuntimeError("Set SYNC_DATABASE_URL or DATABASE_URL in environment for Alembic.")

config.set_main_option("sqlalchemy.url", SYNC_DATABASE_URL)

# Import target metadata only when models exist (next step).
# target_metadata = None  # will be set in later steps


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
