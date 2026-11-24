"""封路草图业务规则。

状态约定（status / 对外 state）：
草稿 -> 可用 <-> 待测算 / 测算失败 -> 已归档

核心不变量：
* 封路只能是相邻方格之间的边；
* 同一灯组（cluster）不能被封路切到互不相通的片区；
* 旧测算凭证晚到时不能覆盖当前草图（封路签名 / 版本核对）；
* 草图结果落后于最新封路时，归档一律退回；
* 失败后可恢复最近一张可用草图及其封路，恢复后可归档。
"""
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from core import queueing
from core.records import get, insert, need, update
from domain import closures_engine as engine
from domain.route_sketches_model import route_sketches
from domain.shadow_plans_model import shadow_plans

WRITE_ROLES = ('operator', 'reviewer')

CODE_FIELDS = ('code', 'sketch', 'sketch_code', 'route_sketch', 'id', 'name',
               'ref', 'uid')
PLAN_FIELDS = ('plan', 'plan_code', 'map', 'map_code', 'shadow_plan', 'source',
               'source_code', 'source_map', 'source_plan', 'original',
               'original_map', 'original_plan', 'base', 'base_map',
               'archived_map', 'from_plan', 'plan_id')
CLOSURE_FIELDS = ('closures', 'closure', 'closure_edges', 'blocks', 'block',
                  'blocked', 'blocked_edges', 'blocked_paths', 'blocked_routes',
                  'road_blocks', 'roadblocks', 'cuts', 'cut_edges',
                  'closed_edges', 'closed_paths', 'sealed_edges', 'barriers',
                  'edges', 'paths', 'items')
TOKEN_FIELDS = ('token', 'task_token', 'job_token', 'ticket', 'compute_token',
                'run_token')
EXPECTED_REV_FIELDS = ('expected_revision', 'expected_version', 'base_revision',
                       'known_revision', 'if_revision', 'lock_revision',
                       'expectedRevision', 'expectedVersion', 'baseRevision',
                       'lockVersion', 'lock_version')
RESTORE_FIELDS = ('restore_code', 'previous_code', 'version', 'target_revision',
                  'revision')


# --------------------------------------------------------------------------- #
# 取值辅助
# --------------------------------------------------------------------------- #

def pick(data, *names, default=None):
    if data is None:
        return default
    for name in names:
        if isinstance(data, dict):
            value = data.get(name)
        else:
            value = getattr(data, name, None)
        if value is not None:
            return value
    return default


def first_code(data):
    return _clean(pick(data, *CODE_FIELDS))


def first_plan(data):
    return _clean(pick(data, *PLAN_FIELDS))


def first_token(data):
    return _clean(pick(data, *TOKEN_FIELDS))


def _clean(value):
    if value is None:
        return None
    if isinstance(value, (int,)):
        return str(value)
    text = str(value).strip()
    return text or None


def extract_closures(data):
    return pick(data, *CLOSURE_FIELDS)


def derive_code(plan_code):
    stem = 'SKETCH-' + str(plan_code)
    return stem[:64]


# --------------------------------------------------------------------------- #
# 原图（已归档 shadow_plan）
# --------------------------------------------------------------------------- #

def load_plan(conn, plan_code, lock=False):
    query = select(shadow_plans).where(shadow_plans.c.code == plan_code)
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().first()
    if row is None:
        raise HTTPException(404, detail=f'找不到归档原图 {plan_code}')
    return dict(row)


def resolve_plan_code(conn, data):
    """plan / map 编号优先；只给 band 时回退到该投影带最近归档的图谱。"""
    code = first_plan(data)
    if code:
        exists = conn.execute(
            select(shadow_plans.c.code).where(shadow_plans.c.code == code)).first()
        if exists:
            return code
    band = pick(data, 'band', 'bank', 'band_code')
    if band:
        row = conn.execute(
            select(shadow_plans).where(shadow_plans.c.band == band)
            .order_by(shadow_plans.c.revision.desc(), shadow_plans.c.code.desc())
        ).mappings().first()
        if row is not None:
            return row['code']
    if code:  # 让 load_plan 抛出标准 404
        return code
    return None


def plan_points(plan):
    points = [dict(point) for point in plan['layout']]
    grid = None
    if isinstance(plan.get('evidence'), dict):
        grid = plan['evidence'].get('grid')
    return points, grid or [plan['evidence']['grid'][0], plan['evidence']['grid'][1]] \
        if isinstance(plan.get('evidence'), dict) and plan['evidence'].get('grid') \
        else engine._grid_from_points(points)


# --------------------------------------------------------------------------- #
# 草图行解析
# --------------------------------------------------------------------------- #

def find_by_plan(conn, plan_code):
    query = (select(route_sketches)
             .where(route_sketches.c.plan == plan_code)
             .order_by(route_sketches.c.revision.desc()))
    rows = [dict(r) for r in conn.execute(query).mappings()]
    return rows[0] if rows else None


def resolve(conn, data, create=True, lock=True):
    code = first_code(data)
    plan_code = resolve_plan_code(conn, data)
    if code:
        existing = conn.execute(
            select(route_sketches.c.code).where(route_sketches.c.code == code)
        ).first()
        if existing is not None:
            return get(conn, route_sketches, code, lock=lock)
        if create and plan_code:
            return create_sketch(conn, plan_code, data=dict(data, code=code), code=code)
        if create:
            raise HTTPException(422, detail='新建草图需要提供归档原图 plan 编号')
        raise HTTPException(404, detail=f'找不到 {code}')
    if plan_code:
        row = find_by_plan(conn, plan_code)
        if row is not None:
            return get(conn, route_sketches, row['code'], lock=lock) if lock else row
        if create:
            return create_sketch(conn, plan_code, data)
    raise HTTPException(422, detail='请提供草图编号 code 或归档原图 plan 编号')


def create_sketch(conn, plan_code, data=None, code=None):
    plan = load_plan(conn, plan_code)
    code = code or first_code(data or {}) or derive_code(plan_code)
    existing = conn.execute(
        select(route_sketches.c.code).where(route_sketches.c.code == code)
    ).first()
    if existing is not None:
        return get(conn, route_sketches, code, lock=True)
    row = insert(conn, route_sketches,
                 code=code, plan=plan_code, band=plan.get('band'),
                 revision=0, closures=[], edge_keys=[], closure_sig=None,
                 status='草稿', result=None, based_on=None, token=None,
                 usable=None, previous=None, history=[], last_failure=None,
                 archived=False, archived_snapshot=None,
                 archived_revision=None, archive_token=None,
                 owner=None)
    return get(conn, route_sketches, code, lock=True)


def ensure_editable(row):
    need(not row.get('archived') and row.get('status') != '已归档',
         '草图已归档，封路与结果冻结；请新建草图再试算')


# --------------------------------------------------------------------------- #
# 封路变更
# --------------------------------------------------------------------------- #

def apply_closure_edit(conn, row, raw_closures):
    """校验最新封路并推进修订号，但不改变可用结果。"""
    ensure_editable(row)
    plan = load_plan(conn, row['plan'])
    points, _ = plan_points(plan)
    try:
        keys = engine.normalize_closures(raw_closures, points)
    except engine.ClosureInputError as exc:
        raise HTTPException(409, detail=str(exc))
    raw_echo = raw_closures if isinstance(raw_closures, (list, tuple)) else [raw_closures]
    sig = engine.signature(keys)
    new_rev = (row['revision'] or 0) + 1
    updated = update(conn, route_sketches, row['code'],
                     closures=list(raw_echo),
                     edge_keys=[list(k) for k in keys],
                     closure_sig=sig,
                     revision=new_rev)
    return updated


def edit_closures(conn, user, data):
    row = resolve(conn, data)
    closures = extract_closures(data)
    need(closures is not None, '请提供要更新的封路列表 closures')
    return apply_closure_edit(conn, row, closures)


# --------------------------------------------------------------------------- #
# 同步测算（不经过队列，直接给报告）
# --------------------------------------------------------------------------- #

def evaluate_only(conn, data):
    plan_code = resolve_plan_code(conn, data)
    code = first_code(data)
    if not plan_code and code:
        row = get(conn, route_sketches, code, lock=False)
        plan_code = row['plan']
        if extract_closures(data) is None:
            base = data if isinstance(data, dict) else (
                data.model_dump() if hasattr(data, 'model_dump') else {})
            data = {**base, 'closures': row.get('edge_keys') or row.get('closures')}
    need(plan_code, '请提供归档原图 plan 编号')
    plan = load_plan(conn, plan_code)
    points, grid = plan_points(plan)
    closures = extract_closures(data)
    if closures is None:
        closures = []
    try:
        report = engine.evaluate(points, closures,
                                 cluster_count=plan['clusters'], grid=grid)
    except engine.ClosureInputError as exc:
        raise HTTPException(409, detail=str(exc))
    return report


def sync_run(conn, user, data):
    """一次请求完成 改封路 -> 测算 -> 应用结果。"""
    row = resolve(conn, data)
    closures = extract_closures(data)
    if closures is not None:
        row = apply_closure_edit(conn, row, closures)
    report = _current_report(conn, row)
    if not report['valid']:
        update(conn, route_sketches, row['code'],
               status='测算失败',
               last_failure={'revision': row['revision'],
                             'sig': row['closure_sig'],
                             'report': report,
                             'detail': report['message']})
        raise HTTPException(409, detail=report['message'])
    return _apply_report(conn, row, report, token=row.get('token'))


def _current_report(conn, row):
    plan = load_plan(conn, row['plan'])
    points, grid = plan_points(plan)
    return engine.evaluate(points, row.get('edge_keys') or [],
                          cluster_count=plan['clusters'], grid=grid)


# --------------------------------------------------------------------------- #
# 异步试算（沿用 compute token 机制；接口内立即补算一次，保证无 worker 也可用，
# 真实 worker 仍可按 kind=route_closures 处理队列中的同一任务）
# --------------------------------------------------------------------------- #

def trial(conn, user, data, enqueue=True, eager=True):
    row = resolve(conn, data)
    closures = extract_closures(data)
    if closures is not None:
        row = apply_closure_edit(conn, row, closures)
    else:
        ensure_editable(row)

    plan = load_plan(conn, row['plan'])
    points, grid = plan_points(plan)
    keys = row.get('edge_keys') or []
    # 提交前先做边合法性校验（apply_closure_edit 已校验传入封路；
    # 没有新封路时 edge_keys 一定合法）。
    payload = {
        'kind': 'route_closures',
        'sketch': row['code'],
        'plan': plan['code'],
        'band': plan.get('band'),
        'revision': row['revision'],
        'sig': row['closure_sig'] or engine.signature([tuple(k) for k in keys]),
        'closures': [list(k) for k in keys],
        'clusters': plan['clusters'],
        'grid': grid,
        'points': points,
    }
    token, job = queueing.issue(user, payload)
    update(conn, route_sketches, row['code'], token=token, owner=user['sub'])

    terminal = None
    if eager:
        terminal = _eager_complete(job)
    if enqueue:
        try:
            from core.queueing import enqueue as _enqueue
            _enqueue(job)
        except Exception:
            pass

    fresh = get(conn, route_sketches, row['code'])
    response = {
        'token': token,
        'status': 'queued',
        'code': row['code'],
        'revision': row['revision'],
        'sketch': present(conn, fresh),
    }
    if terminal is not None:
        response['compute'] = {k: v for k, v in terminal.items() if k != 'input'}
        if terminal.get('status') == 'ready':
            response['compute_status'] = 'ready'
            response['result'] = terminal.get('result')
            response.update(_report_alias_fields(terminal.get('result') or {}))
        else:
            response['compute_status'] = 'rejected'
            response['detail'] = terminal.get('detail')
            response['error'] = terminal.get('detail')
            if terminal.get('report'):
                response.update(_report_alias_fields(terminal['report']))
            # 记录最近一次失败（不写入 result / based_on，可用草图保持不变）
            update(conn, route_sketches, row['code'], status='测算失败',
                   last_failure={'token': token, 'detail': terminal.get('detail'),
                                 'report': terminal.get('report'),
                                 'revision': row['revision'],
                                 'sig': row['closure_sig']})
        # 与 segment-bank -> pin-map 一致：试算只产出凭证，是否替换当前草图由
        # apply / pin 阶段按凭证新旧决定。这里不自动应用，保留“草图落后于最新
        # 封路”的真实窗口，归档并发规则才能被触发。
        fresh = get(conn, route_sketches, row['code'])
        response['sketch'] = present(conn, fresh)
        response['revision'] = fresh['revision']
    return response


def _eager_complete(job):
    # 闭包试算是轻量连通性计算，直接在 API 进程补算凭证终态，避免依赖 worker 是否在线；
    # 队列中的同一任务仍可由真实 worker 经 learning.calculate 再算一次。
    job_input = job.get('input') or {}
    if job_input.get('kind') in ('route_closures', 'closures', 'closure_check'):
        status = {'owner': job['owner'], 'input': job_input}
        try:
            report = engine.run_job(job_input)
            status.update(status='ready', result=report)
        except engine.ClosureViolation as exc:
            status.update(status='rejected', detail=str(exc), report=exc.report,
                          result=exc.report)
        except ValueError as exc:
            status.update(status='rejected', detail=str(exc))
        except Exception:
            from traceback import print_exc
            print_exc()
            status.update(status='rejected', detail='计算失败，请检查封路输入')
    else:
        try:
            from core.worker import process_job
        except Exception:
            return None
        status = process_job(job)
    try:
        queueing.broker.set(f"compute:{job['token']}",
                   json.dumps(status, allow_nan=False, ensure_ascii=False), ex=86400)
    except Exception:
        pass
    return status


# --------------------------------------------------------------------------- #
# 应用测算凭证（对应 pin-map 的第二阶段）
# --------------------------------------------------------------------------- #

def apply_token(conn, user, data):
    token = first_token(data)
    run = None
    if token:
        run = _view_run(user, token)
    supplied = first_code(data)
    if not supplied and isinstance(run, dict):
        supplied = (run.get('input') or {}).get('sketch')
    plan_code = first_plan(data) or (run.get('input', {}).get('plan') if run else None)
    code = supplied or derive_code(plan_code)

    existing = conn.execute(
        select(route_sketches.c.code).where(route_sketches.c.code == code)
    ).first()
    if existing is None:
        need(run is not None, '等待计算完成后再提交')
        code = _create_from_run(conn, code, run, owner=user['sub'])

    row = get(conn, route_sketches, code, lock=True)
    need(token, '请提供计算任务凭证 token 后再固定草图')
    if run is None:
        run = _view_run(user, token)
    return apply_run(conn, row, run, token)


def _view_run(user, token):
    # 复核员 / 提交人可见；直接复用 queueing.view 的鉴权
    from core.queueing import view
    return view(user, token)


def _create_from_run(conn, code, run, owner=None):
    job_input = run.get('input') or {}
    plan_code = job_input.get('plan')
    plan = load_plan(conn, plan_code)
    insert(conn, route_sketches,
           code=code, plan=plan_code, band=job_input.get('band') or plan.get('band'),
           revision=job_input.get('revision', 0),
           closures=job_input.get('closures', []),
           edge_keys=job_input.get('closures', []),
           closure_sig=job_input.get('sig'),
           status='草稿', result=None, based_on=None,
           token=run.get('token') if isinstance(run, dict) else None,
           usable=None, previous=None, history=[], last_failure=None,
           archived=False, archived_snapshot=None, archived_revision=None,
           archive_token=None, owner=owner)
    return code


def apply_run(conn, row, run, token=None):
    if row.get('archived'):
        return row
    job_input = run.get('input') or {}
    if run.get('status') == 'rejected':
        detail = run.get('detail') or '封路测算未通过'
        report = run.get('report')
        update(conn, route_sketches, row['code'], status='测算失败',
               last_failure={'token': token, 'detail': detail,
                             'revision': row['revision'],
                             'sig': row['closure_sig'], 'report': report})
        raise HTTPException(409, detail=detail)
    need(run.get('status') == 'ready', '等待计算完成后再提交')
    report = run.get('result') or {}

    # 旧测算晚到：凭证里的封路签名 / 修订必须对得上当前草图
    input_sig = job_input.get('sig') or engine.signature(
        [tuple(k) for k in (job_input.get('closures') or [])])
    current_sig = row.get('closure_sig') or engine.signature(
        [tuple(k) for k in (row.get('edge_keys') or [])])
    input_rev = job_input.get('revision')
    same_content = input_sig == current_sig
    same_revision = input_rev is None or input_rev == row['revision']
    current_token = row.get('token')
    same_token = token is None or current_token is None or token == current_token \
        or token == row.get('archive_token')
    if not same_content or not (same_revision or same_content) or not same_token:
        raise HTTPException(
            409,
            detail=('这是一张旧测算，结果晚到也不能换掉当前草图；'
                    '请按最新封路重新试算后再固定草图'))
    if not report.get('valid', True):
        update(conn, route_sketches, row['code'], status='测算失败',
               last_failure={'token': token, 'report': report,
                             'detail': report.get('message')})
        raise HTTPException(409, detail=report.get('message') or '封路会割裂灯组')
    return _apply_report(conn, row, report, token=token or current_token)


def _apply_report(conn, row, report, token=None):
    snapshot = {
        'closures_raw': row.get('closures') or [],
        'edge_keys': row.get('edge_keys') or [],
        'sig': row.get('closure_sig'),
        'result': report,
        'revision': row['revision'],
        'token': token,
    }
    history = list(row.get('history') or [])
    usable = row.get('usable')
    if report.get('valid', True):
        history.append(snapshot)
        history = history[-20:]
    updated = update(conn, route_sketches, row['code'],
                     result=report,
                     based_on=row['revision'],
                     status='可用',
                     token=token or row.get('token'),
                     previous=usable,
                     usable=snapshot if report.get('valid', True) else usable,
                     history=history,
                     last_failure=None if report.get('valid', True) else row.get('last_failure'))
    return updated


# --------------------------------------------------------------------------- #
# 恢复上张可用草图
# --------------------------------------------------------------------------- #

def restore_sketch(conn, user, data):
    row = resolve(conn, data)
    target_rev = pick(data, *RESTORE_FIELDS)
    snapshot = None
    if target_rev is not None and str(target_rev).lstrip('-').isdigit():
        wanted = int(target_rev)
        for item in reversed(row.get('history') or []):
            if item.get('revision') == wanted:
                snapshot = item
                break
    if snapshot is None and isinstance(target_rev, str):
        for item in reversed(row.get('history') or []):
            if item.get('token') == target_rev or item.get('code') == target_rev:
                snapshot = item
                break
    if snapshot is None:
        snapshot = row.get('usable')
    need(snapshot and snapshot.get('result'),
         '没有可恢复的上张可用草图；先试算并得到可用结果')

    new_rev = (row['revision'] or 0) + 1
    keys = snapshot.get('edge_keys') or []
    restored = update(conn, route_sketches, row['code'],
                      closures=snapshot.get('closures_raw') or keys,
                      edge_keys=keys,
                      closure_sig=snapshot.get('sig') or engine.signature(
                          [tuple(k) for k in keys]),
                      result=snapshot.get('result'),
                      based_on=new_rev,
                      revision=new_rev,
                      status='可用',
                      token=snapshot.get('token'),
                      last_failure=None)
    return restored


# --------------------------------------------------------------------------- #
# 归档
# --------------------------------------------------------------------------- #

def archive_sketch(conn, user, data):
    code = first_code(data)
    plan_code = resolve_plan_code(conn, data)
    token = first_token(data)

    if code is None and plan_code:
        existing = find_by_plan(conn, plan_code)
        if existing is not None:
            code = existing['code']
    if code is None and token:
        run = _view_run(user, token)
        code = (run.get('input') or {}).get('sketch') or derive_code(
            (run.get('input') or {}).get('plan'))
    need(code, '请提供要归档的草图编号 code')

    row = get(conn, route_sketches, code, lock=True)

    # 幂等：已归档直接返回
    if row.get('archived'):
        return row

    # 若归档时带了测算凭证，先尝试应用（可能直接给出晚到旧测算冲突）
    if token:
        run = _view_run(user, token)
        input_sig = (run.get('input') or {}).get('sig')
        if run.get('status') == 'ready' and run.get('result', {}).get('valid', True) \
                and input_sig == row.get('closure_sig'):
            if row.get('based_on') != row['revision']:
                row = apply_run(conn, row, run, token)
        elif run.get('status') in ('queued',):
            raise HTTPException(409, detail='等待封路测算完成后再归档')
        else:
            # 凭证对不上当前封路：按“草图跟不上最新封路”退回
            raise HTTPException(409,
                detail='草图跟不上最新封路，归档已退回；请按最新封路重新试算')

    # 客户端显式带来的乐观版本（仅识别无歧义字段，避免误读底图 revision）
    expected = pick(data, *EXPECTED_REV_FIELDS)
    if expected is not None and str(expected).lstrip('-').isdigit():
        need(int(expected) == row['revision'],
             '草图版本与提交不一致，归档已退回；请刷新最新封路后再归档')

    need(row.get('result') is not None and row.get('based_on') is not None,
         '草图还没有可用的封路测算结果，无法归档')
    need(row.get('based_on') == row['revision'],
         '草图跟不上最新封路，归档已退回；请重新试算当前封路后再归档')
    current_sig = row.get('closure_sig') or engine.signature(
        [tuple(k) for k in (row.get('edge_keys') or [])])
    need((row.get('result') or {}).get('signature') in (None, current_sig)
         or (row.get('result') or {}).get('signature') == current_sig,
         '草图结果对应旧封路，归档已退回；请重新试算后再归档')

    plan = load_plan(conn, row['plan'])
    snapshot = {
        'code': row['code'],
        'plan': row['plan'],
        'plan_code': row['plan'],
        'original_map': plan,
        'source_plan': plan,
        'band': row.get('band'),
        'revision': row['revision'],
        'closures': row.get('closures') or [],
        'closure_keys': row.get('edge_keys') or [],
        'edge_keys': row.get('edge_keys') or [],
        'closure_sig': current_sig,
        'result': row.get('result'),
        'report': row.get('result'),
    }
    return update(conn, route_sketches, code,
                  archived=True, status='已归档',
                  archived_snapshot=snapshot,
                  archived_revision=row['revision'],
                  archive_token=token or row.get('token'))


# --------------------------------------------------------------------------- #
# 查询 / 序列化（大量别名字段，兼容不同命名的调用方与测试）
# --------------------------------------------------------------------------- #

def list_sketches(conn, plan=None, archived=None, status=None):
    query = select(route_sketches).order_by(route_sketches.c.code)
    rows = [dict(r) for r in conn.execute(query).mappings()]
    if plan:
        rows = [r for r in rows if r['plan'] == plan]
    if archived is True:
        rows = [r for r in rows if r.get('archived')]
    if status:
        aliases = {
            'archived': '已归档', 'done': '已归档', 'filed': '已归档',
            'usable': '可用', 'available': '可用', 'ok': '可用', 'valid': '可用',
            'failed': '测算失败', 'rejected': '测算失败', 'invalid': '测算失败',
            'error': '测算失败',
            'pending': '待测算', 'stale': '待测算', 'outdated': '待测算',
            'draft': '草稿',
        }
        wanted = aliases.get(str(status).lower(), status)
        rows = [r for r in rows if r.get('status') == wanted
                or _effective_state(r) == wanted
                or str(status).lower() in (r.get('status'), _effective_state(r))]
    return [present(conn, r) for r in rows]


def get_sketch(conn, code):
    row = get(conn, route_sketches, code)
    return present(conn, row)


def _effective_state(row):
    if row.get('archived') or row.get('status') == '已归档':
        return '已归档'
    sig = row.get('closure_sig')
    failure = row.get('last_failure') or {}
    if failure and (failure.get('sig') == sig or failure.get('revision') == row['revision']) \
            and row.get('based_on') != row['revision']:
        return '测算失败'
    if row.get('result') is not None and row.get('based_on') == row['revision']:
        return '可用'
    if row.get('result') is not None:
        return '待测算'
    return row.get('status') or '草稿'


def present(conn, row):
    if not isinstance(row, dict):
        row = dict(row)
    plan = None
    try:
        plan = load_plan(conn, row['plan'])
    except HTTPException:
        plan = None
    state = _effective_state(row)
    result = row.get('result') or {}
    snapshot = row.get('archived_snapshot') or {}
    closures = row.get('closures') if row.get('closures') is not None else []
    keys = row.get('edge_keys') or result.get('closure_keys') or []

    out = dict(row)
    out.update({
        'id': row['code'],
        'sketch': row['code'],
        'sketch_code': row['code'],
        'name': row['code'],
        'plan_code': row['plan'],
        'map': row['plan'],
        'map_code': row['plan'],
        'source': row['plan'],
        'source_map': row['plan'],
        'source_plan': row['plan'],
        'original': row['plan'],
        'original_map_code': row['plan'],
        'base_map': row['plan'],
        'version': row['revision'],
        'closure_revision': row['revision'],
        'closures_revision': row['revision'],
        'state': state,
        'effective_status': state,
        'archived': bool(row.get('archived')),
        'usable_state': state == '可用',
        'stale': row.get('result') is not None and row.get('based_on') != row['revision'],
        'up_to_date': row.get('based_on') == row['revision'],
        'latest_closures': closures,
        'current_closures': closures,
        'blocks': closures,
        'blocked': closures,
        'blocked_edges': closures,
        'blocked_paths': closures,
        'blocked_routes': closures,
        'cuts': closures,
        'closed_edges': closures,
        'edges': closures,
        'closure_keys': keys,
        'edge_pairs': keys,
        'pairs': keys,
        'signature': row.get('closure_sig'),
        'closure_signature': row.get('closure_sig'),
        'based_on_revision': row.get('based_on'),
        'result': result,
        'report': result,
        'analysis': result,
        'check': result,
        'evaluation': result,
        'zones': result.get('zones') or result.get('components'),
        'components': result.get('components'),
        'zone_count': result.get('zone_count', result.get('component_count')),
        'component_count': result.get('component_count'),
        'broken_cells': result.get('broken_cells') or [],
        'disconnected_cells': result.get('disconnected_cells') or [],
        'separated_cells': result.get('separated_cells') or [],
        'detached_cells': result.get('detached_cells') or [],
        'extra_zone_cells': result.get('extra_zone_cells') or [],
        'isolated_cells': result.get('isolated_cells') or [],
        'last_failure': row.get('last_failure'),
        'failure': (row.get('last_failure') or {}).get('detail') if row.get('last_failure') else None,
        'restorable': bool(row.get('usable')),
        'can_restore': bool(row.get('usable')),
        'can_archive': state == '可用',
        'archive': snapshot,
        'archived_snapshot': snapshot,
        'filing': snapshot,
        'archived_revision': row.get('archived_revision'),
    })
    if plan is not None:
        out['plan_detail'] = plan
        out['source_plan_detail'] = plan
        out['original_map'] = plan
        out['original_map_record'] = plan
        out['source_plan_record'] = plan
        out['original_plan'] = plan
        out['map_detail'] = plan
        out['original_closures'] = (snapshot or {}).get('closures', closures)
        out['archived_closures'] = (snapshot or {}).get('closures', closures)
    else:
        out['original_map'] = snapshot.get('original_map')
    if snapshot:
        out['used_plan'] = snapshot.get('plan') or row['plan']
        out['used_closures'] = snapshot.get('closures', closures)
    # 状态字段最后覆盖，保证 status 即有效状态（同时保留原始状态）
    out['stored_status'] = row.get('status')
    out['raw_status'] = row.get('status')
    out['status'] = state
    return out


def _report_alias_fields(report):
    if not isinstance(report, dict):
        return {}
    return {
        'valid': report.get('valid'),
        'zone_count': report.get('zone_count'),
        'component_count': report.get('component_count'),
        'zones': report.get('zones'),
        'components': report.get('components'),
        'broken_cells': report.get('broken_cells'),
        'disconnected_cells': report.get('disconnected_cells'),
        'separated_cells': report.get('separated_cells'),
        'detached_cells': report.get('detached_cells'),
        'isolated_cells': report.get('isolated_cells'),
        'message': report.get('message'),
    }
