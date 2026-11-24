from sqlalchemy import Boolean, Column, Integer, JSON, String, Table

from core.db import metadata

# 封路草图：从一张已归档遮挡图谱（原图）出发，维护最新封路、
# 最近一次可用测算结果、上张可用草图以及归档快照。
route_sketches = Table('route_sketches', metadata,
    Column('code', String(64), primary_key=True),
    Column('plan', String(64), nullable=False),
    Column('band', String(64), nullable=True),
    # 每次封路变更 / 试算 / 恢复都会递增，归档时核对草图是否跟得上最新封路
    Column('revision', Integer, nullable=False, default=0),
    Column('closures', JSON, nullable=False),
    Column('edge_keys', JSON, nullable=True),
    Column('closure_sig', String(64), nullable=True),
    Column('status', String(32), nullable=False, default='草稿'),
    Column('result', JSON, nullable=True),
    Column('based_on', Integer, nullable=True),
    Column('token', String(64), nullable=True),
    # 最近一张可用草图（封路 + 结果），供失败后恢复
    Column('usable', JSON, nullable=True),
    Column('previous', JSON, nullable=True),
    Column('history', JSON, nullable=True),
    Column('last_failure', JSON, nullable=True),
    Column('archived', Boolean, nullable=False, default=False),
    Column('archived_snapshot', JSON, nullable=True),
    Column('archived_revision', Integer, nullable=True),
    Column('archive_token', String(64), nullable=True),
    Column('owner', String(160), nullable=True),
)
