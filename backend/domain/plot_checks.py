from sqlalchemy import select

from core.records import get, insert, need, update
from domain.light_handoffs_model import light_handoffs
from domain.sculpture_bands_model import sculpture_bands
from domain.shadow_plans_model import shadow_plans
from domain.sight_cells_model import sight_cells

FIGURATIVE = '虚构人物雕塑'
SCOPES = (FIGURATIVE, '抽象几何雕塑')
TEAMS = ('NORTH', 'SOUTH', 'WEST')


def observations(conn, band):
    query = select(sight_cells).where(sight_cells.c.band == band).order_by(sight_cells.c.y, sight_cells.c.x)
    return [dict(row) for row in conn.execute(query).mappings()]


def bump_revision(conn, band):
    plan_codes = select(shadow_plans.c.code).where(shadow_plans.c.band == band['code'])
    conn.execute(light_handoffs.update().where(
        light_handoffs.c.plan.in_(plan_codes),
        light_handoffs.c.status == '待二签',
    ).values(status='版本失效'))
    update(conn, sculpture_bands, band['code'], revision=band['revision']+1)


def record_beam(conn, data):
    band = get(conn, sculpture_bands, data.band, lock=True)
    need(0<=data.x<band['cols'] and 0<=data.y<band['rows'], '观测格超出网格边界')
    need(0<=data.shade<=1 and 0<=data.spill<=1, '面积比须在 0 到 1')
    code = f'{band["code"]}-{data.x}-{data.y}'
    query = select(sight_cells).where(sight_cells.c.code == code)
    existing = conn.execute(query).mappings().first()
    if existing is not None and (data.shade,data.spill)==(existing['shade'],existing['spill']):
        return dict(existing)
    if existing is None:
        result = insert(conn, sight_cells, code=code, **data.model_dump(),
                        owner='NORTH' if data.x<band['cols']/2 else 'SOUTH', protected=False)
    else:
        result = update(conn, sight_cells, code, shade=data.shade, spill=data.spill)
    bump_revision(conn, band)
    return result


def protect_station(conn, code):
    cell = get(conn, sight_cells, code)
    band = get(conn, sculpture_bands, cell['band'], lock=True)
    need(band['scope']==FIGURATIVE, '仅虚构人物雕塑可登记细部保护观察位')
    cell = get(conn, sight_cells, code)
    if cell['protected']:
        return cell
    result = update(conn, sight_cells, code, protected=True)
    bump_revision(conn, band)
    return result


def offer_station(conn, user, data):
    plan = get(conn, shadow_plans, data.plan)
    band = get(conn, sculpture_bands, plan['band'], lock=True)
    need(plan['revision']==band['revision'], '图谱版本失效，归档当前观测图谱后再移交')
    cell = get(conn, sight_cells, data.cell, lock=True)
    plotted = next((point for point in plan['layout'] if point['code']==cell['code']), None)
    need(cell['band']==band['code'] and plotted is not None, '观察位不在当前图谱')
    need(data.target in TEAMS and data.target!=cell['owner'], '选择与现任不同的在岗灯组')
    pending = select(light_handoffs.c.code).where(light_handoffs.c.cell==cell['code'],
                                                  light_handoffs.c.status=='待二签')
    need(conn.execute(pending).scalar() is None, '该观察位已有待二签移交单')
    draft = insert(conn, light_handoffs, **data.model_dump(), cluster=plotted['cluster'], source=cell['owner'],
                   status='待二签' if cell['protected'] else '已移交', first_sign=user['sub'], second_sign=None)
    if not cell['protected']:
        update(conn, sight_cells, cell['code'], owner=data.target)
    return draft


def check_station(conn, user, code):
    draft = get(conn, light_handoffs, code)
    plan = get(conn, shadow_plans, draft['plan'])
    band = get(conn, sculpture_bands, plan['band'], lock=True)
    draft = get(conn, light_handoffs, code, lock=True)
    need(draft['status']=='待二签', '仅待二签移交单可由复核员签收')
    need(band['revision']==plan['revision'], '底图版本失效，请按最新观测图重新签发')
    cell = get(conn, sight_cells, draft['cell'], lock=True)
    need(cell['protected'] and cell['owner']==draft['source'], '观察位责任状态发生变化')
    need(draft['first_sign']!=user['sub'], '保护位二签必须来自另一身份')
    update(conn, sight_cells, cell['code'], owner=draft['target'])
    return update(conn, light_handoffs, code, status='已移交', second_sign=user['sub'])


# --------------------------------------------------------------------------- #
# 封路草图：函数名兼容垫片，真正规则在 route_sketches_service / closures_engine
# --------------------------------------------------------------------------- #

def _as_dict(data):
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if hasattr(data, 'model_dump'):
        return data.model_dump()
    return {k: getattr(data, k) for k in dir(data) if not k.startswith('_') and not callable(getattr(data, k, None))}


def _split_user_data(args):
    user = next((a for a in args if isinstance(a, dict) and 'sub' in a), None)
    data = next((_as_dict(a) for a in args if a is not user), {})
    return user or {'sub': 'plot-checks'}, data


def evaluate_closures(conn, *args):
    from domain import route_sketches_service as svc
    _, data = _split_user_data(args)
    return svc.evaluate_only(conn, data)


def block_routes(conn, *args):
    from domain import route_sketches_service as svc
    user, data = _split_user_data(args)
    return svc.sync_run(conn, user, data)


def trial_closures(conn, *args):
    from domain import route_sketches_service as svc
    user, data = _split_user_data(args)
    return svc.trial(conn, user, data)


def restore_sketch(conn, *args):
    from domain import route_sketches_service as svc
    user, data = _split_user_data(args)
    return svc.restore_sketch(conn, user, data)


def pin_sketch(conn, *args):
    from domain import route_sketches_service as svc
    user, data = _split_user_data(args)
    if data.get('token'):
        row = svc.apply_token(conn, user, data)
        data = {**data, 'code': row['code']}
    return svc.archive_sketch(conn, user, data)


def archive_sketch(conn, *args):
    from domain import route_sketches_service as svc
    user, data = _split_user_data(args)
    return svc.archive_sketch(conn, user, data)
