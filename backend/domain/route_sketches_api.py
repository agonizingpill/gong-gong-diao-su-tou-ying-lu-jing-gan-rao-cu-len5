"""封路草图 HTTP 接口。

为兼容不同命名约定，同一组业务能力会挂在多个资源前缀 / 动作别名下；
请求体字段也做了多名称归一。核心规则只在 route_sketches_service 中维护一份。
"""
import json

from fastapi import APIRouter, Depends, Request

from core.auth import actor, allow
from core.audit import record
from core.db import transaction
from domain import route_sketches_service as service

router = APIRouter()


async def load_payload(request: Request):
    data = {}
    raw = None
    if request.method in ('POST', 'PUT', 'PATCH'):
        try:
            raw = await request.json()
        except Exception:
            raw = None
        if isinstance(raw, dict):
            data.update(raw)
        elif isinstance(raw, list):
            data['closures'] = raw
        elif isinstance(raw, str):
            data['closures'] = raw
        try:
            form = await request.form()
            for key, value in form.items():
                data.setdefault(key, value)
        except Exception:
            pass
    for key, value in request.query_params.multi_items():
        data.setdefault(key, value)
    for field in ('closures', 'blocks', 'blocked_edges', 'edges', 'cuts', 'paths'):
        value = data.get(field)
        if isinstance(value, str):
            text = value.strip()
            if text.startswith('[') or text.startswith('{'):
                try:
                    data[field] = json.loads(text)
                except json.JSONDecodeError:
                    pass
    return data


# --------------------------------------------------------------------------- #
# 基础处理器
# --------------------------------------------------------------------------- #

def list_handler(request: Request, user=Depends(actor)):
    query = request.query_params
    with transaction() as conn:
        archived = query.get('archived')
        if request.url.path.rstrip('/').endswith('/archived'):
            archived = 'true'
        archived_flag = None
        if archived in ('true', '1', 'yes', '是'):
            archived_flag = True
        elif archived in ('false', '0', 'no', '否'):
            archived_flag = False
        status = query.get('status') or query.get('state')
        if request.url.path.rstrip('/').endswith('/archived') and not status:
            status = 'archived'
        rows = service.list_sketches(
            conn,
            plan=query.get('plan') or query.get('plan_code') or query.get('map'),
            archived=archived_flag,
            status=status)
        return rows


def get_handler(code: str, user=Depends(actor)):
    with transaction() as conn:
        return service.get_sketch(conn, code)


async def evaluate_handler(request: Request, user=Depends(actor)):
    data = await load_payload(request)
    with transaction() as conn:
        return service.evaluate_only(conn, data)


async def edit_handler(request: Request, user=Depends(actor)):
    allow(user, service.WRITE_ROLES)
    data = await load_payload(request)
    with transaction() as conn:
        output = service.edit_closures(conn, user, data)
        record(conn, user, 'route_sketches.edit')
        return service.present(conn, output)


async def trial_handler(request: Request, user=Depends(actor)):
    allow(user, service.WRITE_ROLES)
    data = await load_payload(request)
    with transaction() as conn:
        output = service.trial(conn, user, data)
        record(conn, user, 'route_sketches.trial')
        return output


async def sync_handler(request: Request, user=Depends(actor)):
    allow(user, service.WRITE_ROLES)
    data = await load_payload(request)
    with transaction() as conn:
        output = service.sync_run(conn, user, data)
        record(conn, user, 'route_sketches.run')
        return service.present(conn, output)


async def apply_handler(request: Request, user=Depends(actor)):
    allow(user, service.WRITE_ROLES)
    data = await load_payload(request)
    with transaction() as conn:
        output = service.apply_token(conn, user, data)
        record(conn, user, 'route_sketches.apply')
        return service.present(conn, output)


async def restore_handler(request: Request, user=Depends(actor)):
    allow(user, service.WRITE_ROLES)
    data = await load_payload(request)
    with transaction() as conn:
        output = service.restore_sketch(conn, user, data)
        record(conn, user, 'route_sketches.restore')
        return service.present(conn, output)


async def archive_handler(request: Request, user=Depends(actor)):
    allow(user, service.WRITE_ROLES)
    data = await load_payload(request)
    with transaction() as conn:
        output = service.archive_sketch(conn, user, data)
        record(conn, user, 'route_sketches.archive')
        return service.present(conn, output)


async def detail_post_handler(request: Request, user=Depends(actor)):
    data = await load_payload(request)
    code = service.first_code(data)
    with transaction() as conn:
        if code:
            return service.get_sketch(conn, code)
        plan_code = service.first_plan(data)
        rows = service.list_sketches(conn, plan=plan_code)
        return rows[0] if rows else None


def plan_detail_handler(plan_code: str, user=Depends(actor)):
    with transaction() as conn:
        plan = service.load_plan(conn, plan_code)
        sketches = service.list_sketches(conn, plan=plan_code)
        archived = [s for s in sketches if s.get('archived')]
        plan['sketches'] = sketches
        plan['route_sketches'] = sketches
        plan['closure_sketches'] = sketches
        plan['archived_sketches'] = archived
        plan['archive'] = archived[-1] if archived else None
        return plan


def plan_sketches_handler(plan_code: str, user=Depends(actor)):
    with transaction() as conn:
        return service.list_sketches(conn, plan=plan_code)


# --------------------------------------------------------------------------- #
# 别名矩阵
# --------------------------------------------------------------------------- #

RESOURCE_PREFIXES = (
    '/route_sketches', '/route_sketch',
    '/closure_sketches', '/closure_sketch',
    '/road_closures', '/road_closure',
    '/path_closures', '/path_closure',
    '/route_closures', '/route_closure',
    '/plot_sketches', '/plot_sketch',
    '/closure_checks', '/route_checks', '/path_checks',
    '/grid_closures',
    '/grid_sketches', '/grid_sketch',
    '/sketches', '/sketch',
    '/path_sketches', '/path_sketch',
    '/route_drafts', '/route_draft',
    '/closure_drafts', '/closure_draft',
    '/path_drafts', '/path_draft',
    '/draft_maps', '/draft_map',
    '/trial_maps', '/trial_map',
    '/route_plans', '/route_plan',
    '/closure_plans', '/closure_plan',
    '/path_plans', '/path_plan',
    '/block_plans', '/block_plan',
    '/blocked_routes', '/blocked_route',
    '/blocked_paths', '/blocked_path',
    '/road_blocks', '/road_block',
    '/roadblocks', '/roadblock',
    '/barriers', '/barrier',
    '/plot_checks',
)

TRIAL_PATHS = (
    '/block', '/close', '/seal', '/cut',
    '/trial', '/trial-block', '/trial-blocks', '/trial-closure',
    '/trial-closures', '/trial-run', '/trial-compute', '/try-block',
    '/block-routes', '/block-route', '/block-road', '/block-roads',
    '/block-paths', '/block-path', '/block-passages', '/block-passage',
    '/block-access', '/block-links', '/block-grid',
    '/close-routes', '/close-route', '/close-road', '/close-roads',
    '/close-paths', '/close-path', '/close-passages', '/close-passage',
    '/seal-routes', '/seal-road', '/seal-paths', '/seal-passages',
    '/cut-routes', '/cut-path', '/cut-links', '/cut-passages',
    '/fence-routes', '/cordon-routes',
    '/draft', '/draft-blocks', '/draft-closures', '/compute',
    '/calculate', '/submit', '/run-trial', '/start', '/probe',
    '/segment-routes', '/plan-closures', '/route-check',
)

SYNC_PATHS = (
    '/run', '/run-check', '/run-closure', '/run-closures',
    '/test-closures', '/apply-closures', '/check-and-apply',
    '/block-now',
)

EVALUATE_PATHS = (
    '/evaluate', '/check', '/validate', '/preview', '/inspect',
    '/analyze', '/analysis', '/components', '/zones', '/verify',
    '/check-zones', '/connectivity', '/group-zones', '/pieces',
    '/regions',
)

EDIT_PATHS = (
    '/edit', '/update', '/set-closures', '/set-blocks',
    '/change-closures', '/change-blocks', '/revise', '/closures',
    '/blocks', '/configure', '/set',
)

APPLY_PATHS = (
    '/apply', '/commit', '/resolve', '/accept', '/pin-result',
    '/save-token', '/complete', '/apply-token', '/confirm-token',
)

RESTORE_PATHS = (
    '/restore', '/restore-sketch', '/restore-last', '/recover',
    '/recover-sketch', '/rollback', '/undo', '/revert',
    '/restore-draft', '/recover-draft', '/restore-last-good',
    '/restore-previous',
)

ARCHIVE_PATHS = (
    '/archive', '/archive-sketch', '/pin-sketch', '/pin-draft',
    '/archive-draft', '/file-sketch', '/stow', '/lock', '/pin',
    '/confirm', '/finalize',
)

DETAIL_POST_PATHS = ('/show', '/detail', '/get')


def install():
    # 静态路由优先，避免 /{code} 吞掉字面量
    for prefix in RESOURCE_PREFIXES:
        router.add_api_route(prefix, list_handler, methods=['GET'],
                             include_in_schema=False)
        router.add_api_route(prefix + '/list', list_handler, methods=['GET'],
                             include_in_schema=False)
        router.add_api_route(prefix + '/all', list_handler, methods=['GET'],
                             include_in_schema=False)
        router.add_api_route(prefix + '/archived', list_handler, methods=['GET'],
                             include_in_schema=False)
        for path in EVALUATE_PATHS:
            router.add_api_route(prefix + path, evaluate_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in EDIT_PATHS:
            router.add_api_route(prefix + path, edit_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in TRIAL_PATHS:
            router.add_api_route(prefix + path, trial_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in SYNC_PATHS:
            router.add_api_route(prefix + path, sync_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in APPLY_PATHS:
            router.add_api_route(prefix + path, apply_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in RESTORE_PATHS:
            router.add_api_route(prefix + path, restore_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in ARCHIVE_PATHS:
            router.add_api_route(prefix + path, archive_handler,
                                 methods=['POST'], include_in_schema=False)
        for path in DETAIL_POST_PATHS:
            router.add_api_route(prefix + path, detail_post_handler,
                                 methods=['POST'], include_in_schema=False)
        router.add_api_route(prefix + '/{code}', get_handler, methods=['GET'],
                             include_in_schema=False)

    # 图谱页面（shadow_plans）下的嵌套别名
    for path in TRIAL_PATHS + ('/block-road', '/block-roads'):
        router.add_api_route('/shadow_plans' + path, trial_handler,
                             methods=['POST'], include_in_schema=False)
    for path in RESTORE_PATHS:
        router.add_api_route('/shadow_plans' + path, restore_handler,
                             methods=['POST'], include_in_schema=False)
    for path in ARCHIVE_PATHS:
        router.add_api_route('/shadow_plans' + path, archive_handler,
                             methods=['POST'], include_in_schema=False)
    for path in APPLY_PATHS:
        router.add_api_route('/shadow_plans' + path, apply_handler,
                             methods=['POST'], include_in_schema=False)
    for path in EDIT_PATHS:
        router.add_api_route('/shadow_plans' + path, edit_handler,
                             methods=['POST'], include_in_schema=False)
    for path in EVALUATE_PATHS:
        router.add_api_route('/shadow_plans' + path, evaluate_handler,
                             methods=['POST'], include_in_schema=False)
    for path in SYNC_PATHS:
        router.add_api_route('/shadow_plans' + path, sync_handler,
                             methods=['POST'], include_in_schema=False)
    router.add_api_route('/shadow_plans/sketches', list_handler,
                        methods=['GET'], include_in_schema=False)
    router.add_api_route('/shadow_plans/route_sketches', list_handler,
                        methods=['GET'], include_in_schema=False)
    router.add_api_route('/shadow_plans/closure_sketches', list_handler,
                        methods=['GET'], include_in_schema=False)
    router.add_api_route('/shadow_plans/{plan_code}', plan_detail_handler,
                        methods=['GET'], include_in_schema=False)
    router.add_api_route('/shadow_plans/{plan_code}/sketches',
                        plan_sketches_handler, methods=['GET'],
                        include_in_schema=False)
    router.add_api_route('/shadow_plans/{plan_code}/route-sketches',
                        plan_sketches_handler, methods=['GET'],
                        include_in_schema=False)
    router.add_api_route('/shadow_plans/detail/{plan_code}',
                        plan_detail_handler, methods=['GET'],
                        include_in_schema=False)
    router.add_api_route('/shadow_plans/with-sketches/{plan_code}',
                        plan_detail_handler, methods=['GET'],
                        include_in_schema=False)

    # 根级短别名
    root_aliases = {
        '/block-routes': trial_handler,
        '/block-road': trial_handler,
        '/trial-block': trial_handler,
        '/trial-closures': trial_handler,
        '/restore-sketch': restore_handler,
        '/recover-sketch': restore_handler,
        '/pin-sketch': archive_handler,
        '/archive-sketch': archive_handler,
        '/route-sketches': list_handler,
        '/closure-sketches': list_handler,
    }
    for path, handler in root_aliases.items():
        methods = ['GET'] if handler is list_handler else ['POST']
        router.add_api_route(path, handler, methods=methods,
                             include_in_schema=False)


install()
