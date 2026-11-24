"""命名兼容垫片：业务实现统一在 route_sketches_service。"""
from domain.route_sketches_service import *  # noqa: F401,F403
from domain import route_sketches_service as _impl  # noqa: F401

for _name in dir(_impl):
    if not _name.startswith('_'):
        globals()[_name] = getattr(_impl, _name)
