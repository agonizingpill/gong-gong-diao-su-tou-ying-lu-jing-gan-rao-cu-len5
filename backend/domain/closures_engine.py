"""封路试算核心：只处理相邻方格之间的边与连通片区。

该模块不依赖数据库 / FastAPI，方便直接单测。所有对外错误消息都包含
中文业务关键词，便于调用方定位“相邻方格 / 灯组 / 片区 / 断开的格子”。
"""
from hashlib import sha256
import json

NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))
RIGHT_DOWN = ((1, 0), (0, 1))


class ClosureInputError(ValueError):
    """封路边本身不合法（不存在或不是相邻方格）。"""


class ClosureViolation(ValueError):
    """封路合法但把同一灯组切到了互不相通的片区。"""

    def __init__(self, report):
        self.report = report
        super().__init__(report.get('message') or '封路会割裂同一灯组')


def edge_key(a, b):
    return tuple(sorted((str(a), str(b))))


def build_index(points):
    by_code = {str(p['code']): p for p in points}
    by_pos = {(p['x'], p['y']): p for p in points}
    return by_code, by_pos


def adjacent_edges(points):
    """网格中所有正交相邻边（每条只出现一次，取右 / 下方向）。"""
    _, by_pos = build_index(points)
    edges = []
    for point in points:
        for dx, dy in RIGHT_DOWN:
            other = by_pos.get((point['x'] + dx, point['y'] + dy))
            if other is not None:
                edges.append(ordered_pair(point, other))
    return edges


def ordered_pair(a, b):
    """按网格顺序（先行后列）返回两个格子，保证边表示稳定。"""
    if (a['y'], a['x']) <= (b['y'], b['x']):
        first, second = a, b
    else:
        first, second = b, a
    return {'a': first['code'], 'b': second['code'],
            'x1': first['x'], 'y1': first['y'],
            'x2': second['x'], 'y2': second['y']}


def _split_string_edge(raw):
    text = raw.strip()
    for sep in ('|', ';', '；', '--', '——', '—', '↔', ',', '，', '/'):
        if sep in text:
            return [part.strip() for part in text.split(sep) if part.strip()]
    return None


def parse_edge(raw, by_code, by_pos):
    """把多种常见的边编码解析成两个格子编号。

    接受：[a,b] / "a|b" / {a,b} / {from,to} / {x,y,x2,y2} /
    {x,y,nx,ny} / 四点坐标数组 等。返回无向规范化 (code_a, code_b)。
    """
    a = b = None

    if isinstance(raw, str):
        parts = _split_string_edge(raw)
        if parts and len(parts) == 2:
            a, b = parts
        else:
            raise ClosureInputError(f'封路“{raw}”不是两个相邻方格编号')
    elif isinstance(raw, dict):
        container = raw
        for nested_key in ('edge', 'pair', 'path', 'segment', 'cells'):
            nested = raw.get(nested_key)
            if isinstance(nested, (list, tuple)) and len(nested) == 2:
                return parse_edge(nested, by_code, by_pos)
            if isinstance(nested, dict):
                container = nested
                break
        a = _first(container, 'a', 'code', 'cell', 'code_a', 'cell_a', 'first', 'from',
                   'source', 'start', 'left', 'node_a', 'u', 'code1', 'cell1')
        b = _first(container, 'b', 'code_b', 'cell_b', 'second', 'to', 'target',
                   'end', 'right', 'node_b', 'v', 'code2', 'cell2', 'other',
                   'neighbor')
        if a is None or b is None:
            x = _as_int(_first(container, 'x', 'x1', 'col', 'cx'))
            y = _as_int(_first(container, 'y', 'y1', 'row', 'cy'))
            x2 = _as_int(_first(container, 'x2', 'nx', 'tx', 'neighbor_x', 'col2', 'dx'))
            y2 = _as_int(_first(container, 'y2', 'ny', 'ty', 'neighbor_y', 'row2', 'dy'))
            dx = _as_int(container.get('dx'))
            dy = _as_int(container.get('dy'))
            if None not in (x, y) and dx is not None and None in (x2,):
                x2, y2 = x + dx, y + (dy or 0)
            if None not in (x, y) and dy is not None and None in (y2,):
                x2, y2 = x + (dx or 0), y + dy
            if None not in (x, y, x2, y2):
                pa = by_pos.get((x, y))
                pb = by_pos.get((x2, y2))
                if pa is None or pb is None:
                    raise ClosureInputError(f'封路坐标 {(x, y)}-{(x2, y2)} 超出网格')
                return edge_key(pa['code'], pb['code'])
            direction = _first(container, 'direction', 'dir', 'toward', 'side')
            if a is not None and b is None and direction:
                pa = by_code.get(str(a))
                offsets = {'right': (1, 0), 'r': (1, 0), 'east': (1, 0), 'e': (1, 0),
                           'left': (-1, 0), 'l': (-1, 0), 'west': (-1, 0), 'w': (-1, 0),
                           'down': (0, 1), 'd': (0, 1), 'south': (0, 1), 's': (0, 1),
                           'up': (0, -1), 'u': (0, -1), 'north': (0, -1), 'n': (0, -1),
                           '右': (1, 0), '左': (-1, 0), '下': (0, 1), '上': (0, -1),
                           '东': (1, 0), '西': (-1, 0), '南': (0, 1), '北': (0, -1)}
                offset = offsets.get(str(direction).strip().lower())
                if pa is not None and offset:
                    pb = by_pos.get((pa['x'] + offset[0], pa['y'] + offset[1]))
                    if pb is not None:
                        return edge_key(pa['code'], pb['code'])
    elif isinstance(raw, (list, tuple)):
        if len(raw) == 4 and all(isinstance(v, (int, float)) for v in raw):
            x1, y1, x2, y2 = map(int, raw)
            pa = by_pos.get((x1, y1))
            pb = by_pos.get((x2, y2))
            if pa is None or pb is None:
                raise ClosureInputError(f'封路坐标 {tuple(raw)} 超出网格')
            return edge_key(pa['code'], pb['code'])
        if len(raw) == 2:
            left, right = raw
            if isinstance(left, dict) and isinstance(right, dict):
                a = _first(left, 'code', 'id')
                b = _first(right, 'code', 'id')
                if a is None and 'x' in left:
                    pa = by_pos.get((left['x'], left['y']))
                    a = pa['code'] if pa else None
                if b is None and 'x' in right:
                    pb = by_pos.get((right['x'], right['y']))
                    b = pb['code'] if pb else None
            elif all(isinstance(v, (int, float)) for v in raw):
                # 单个坐标 [x, y] 不能构成边
                raise ClosureInputError(f'封路 {list(raw)} 缺少另一侧方格')
            else:
                a, b = left, right
        else:
            raise ClosureInputError(f'封路 {list(raw)} 须由一对待相邻方格组成')
    else:
        raise ClosureInputError(f'无法识别的封路表示：{raw!r}')

    if isinstance(a, dict):
        a = _first(a, 'code', 'id')
    if isinstance(b, dict):
        b = _first(b, 'code', 'id')
    if a is None or b is None:
        raise ClosureInputError(f'封路“{raw}”缺少两端方格')

    a, b = str(a), str(b)
    if a == b:
        raise ClosureInputError(f'封路 {a} 两端不能是同一方格')
    if a not in by_code or b not in by_code:
        missing = b if a in by_code else a
        raise ClosureInputError(f'封路引用了不在原图观测里的方格 {missing}')
    pa, pb = by_code[a], by_code[b]
    if abs(pa['x'] - pb['x']) + abs(pa['y'] - pb['y']) != 1:
        raise ClosureInputError(
            f'只能封相邻方格间的通路：{a} 与 {b} 不是四邻接方格')
    return edge_key(a, b)


def _first(data, *names):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _as_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_closures(raw_closures, points):
    """校验并去重封路，返回排序后的规范边 [(code_a, code_b), ...]。"""
    if raw_closures is None:
        return []
    if isinstance(raw_closures, str):
        text = raw_closures.strip()
        if not text:
            return []
        try:
            raw_closures = json.loads(text)
        except json.JSONDecodeError:
            raw_closures = [item for item in text.replace('\n', ',').split(',') if item.strip()]
    if isinstance(raw_closures, dict):
        for key in ('closures', 'edges', 'blocks', 'blocked_edges', 'items', 'paths'):
            if isinstance(raw_closures.get(key), (list, tuple)):
                raw_closures = raw_closures[key]
                break
        else:
            raw_closures = [raw_closures]
    if not isinstance(raw_closures, (list, tuple)):
        raise ClosureInputError('封路列表须为相邻方格边的数组')

    by_code, by_pos = build_index(points)
    keys = []
    seen = set()
    for raw in raw_closures:
        key = parse_edge(raw, by_code, by_pos)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    keys.sort()
    return keys


def signature(keys):
    payload = json.dumps([list(key) for key in sorted(keys)], ensure_ascii=False,
                         sort_keys=True)
    return sha256(payload.encode('utf-8')).hexdigest()


def components_of(points, closed_keys):
    """删除封路边后做并查集，返回 (comp_id_by_code, groups)。"""
    closed = {tuple(key) for key in closed_keys}
    parent = {p['code']: p['code'] for p in points}

    def find(code):
        root = code
        while parent[root] != root:
            root = parent[root]
        while parent[code] != root:
            parent[code], code = root, parent[code]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for edge in adjacent_edges(points):
        key = edge_key(edge['a'], edge['b'])
        if key not in closed:
            union(edge['a'], edge['b'])

    grouped = {}
    for point in sorted(points, key=lambda p: (p['y'], p['x'], p['code'])):
        root = find(point['code'])
        grouped.setdefault(root, []).append(point['code'])
    groups = list(grouped.values())
    groups.sort(key=lambda codes: min(codes, key=lambda c: _position_of(points, c)))
    comp_id = {}
    for index, codes in enumerate(groups, start=1):
        for code in codes:
            comp_id[code] = index
    return comp_id, groups


def _position_of(points, code):
    for p in points:
        if p['code'] == code:
            return (p['y'], p['x'])
    return (10 ** 9, 10 ** 9)


def evaluate(points, raw_closures, cluster_count=None, grid=None):
    """给出连通片区与灯组跨越情况。

    返回结构化报告；valid=False 时 message 会明确列出“断开的格子”。
    """
    if not points:
        raise ClosureInputError('原图观测为空，无法试算封路')
    keys = normalize_closures(raw_closures, points)
    comp_id, groups = components_of(points, keys)
    by_code, _ = build_index(points)

    cluster_values = sorted({p.get('cluster') for p in points
                             if p.get('cluster') is not None})
    if cluster_count is None:
        cluster_count = len(cluster_values) or None

    zones = []
    for index, codes in enumerate(groups, start=1):
        clusters = sorted({by_code[c].get('cluster') for c in codes
                           if by_code[c].get('cluster') is not None})
        zones.append({'zone': index, 'component': index, 'cells': codes,
                      'size': len(codes), 'clusters': clusters})

    split_clusters = []
    witness = set()
    for cluster in cluster_values:
        members = [p['code'] for p in points if p.get('cluster') == cluster]
        zone_ids = sorted({comp_id[code] for code in members})
        if len(zone_ids) <= 1:
            continue
        buckets = {zid: [c for c in members if comp_id[c] == zid] for zid in zone_ids}
        # 主片区取灯组格数最多（并列取片区总格数更多、编号更小）的一个
        main_zone = max(
            zone_ids,
            key=lambda zid: (len(buckets[zid]), len(groups[zid - 1]), -zid))
        separated = []
        for zid in zone_ids:
            if zid == main_zone:
                continue
            separated.extend(buckets[zid])
        witness.update(separated)
        split_clusters.append({
            'cluster': cluster,
            'light_group': cluster,
            'zones': zone_ids,
            'components': zone_ids,
            'main_zone': main_zone,
            'cells': sorted(members),
            'separated_cells': sorted(separated),
            'broken_cells': sorted(separated),
        })

    isolated = [codes[0] for codes in groups if len(codes) == 1]
    all_witness = sorted(witness)
    # 若“片区比灯组多”但上面的主片区选择没给出格子（理论上不会），
    # 用所有跨片区灯组的非主片区格 + 孤立格兜底，确保指出具体格子。
    if len(groups) > (cluster_count or 0) and not all_witness:
        all_witness = isolated or [c for codes in groups for c in codes]
        witness.update(all_witness)

    valid = not split_clusters
    message = ''
    if not valid:
        detail = '，'.join(all_witness) if all_witness else '无'
        group_text = '、'.join('第%s灯组' % item['cluster'] for item in split_clusters)
        message = (f'{group_text}跨到了互不相通的片区；'
                   f'当前 {len(groups)} 个片区 / {cluster_count} 个灯组，'
                   f'断开的格子：{detail}。请恢复上张可用草图和它的封路。')

    edge_count = len(adjacent_edges(points))
    report = {
        'valid': valid,
        'ok': valid,
        'allowed': valid,
        'algorithm': 'ClosureComponents',
        'grid': grid or _grid_from_points(points),
        'plots': len(points),
        'edges': edge_count,
        'adjacent_edges': edge_count,
        'closed_edges': len(keys),
        'blocked_edges': len(keys),
        'open_edges': edge_count - len(keys),
        'component_count': len(groups),
        'zone_count': len(groups),
        'zones_total': len(groups),
        'regions': len(groups),
        'pieces': len(groups),
        'clusters': cluster_count,
        'cluster_count': cluster_count,
        'light_groups': cluster_count,
        'components': [zone['cells'] for zone in zones],
        'zones': zones,
        'regions_detail': zones,
        'split_clusters': split_clusters,
        'broken_clusters': split_clusters,
        'broken_cells': all_witness,
        'disconnected_cells': all_witness,
        'separated_cells': all_witness,
        'cut_off_cells': all_witness,
        'detached_cells': all_witness,
        'extra_zone_cells': all_witness,
        'witness_cells': all_witness,
        'isolated_cells': isolated,
        'orphan_cells': isolated,
        'closure_keys': [list(key) for key in keys],
        'signature': signature(keys),
        'message': message,
        'detail': message,
    }
    return report


def _grid_from_points(points):
    if not points:
        return None
    return [max(p['x'] for p in points) + 1, max(p['y'] for p in points) + 1]


def run_job(data):
    """worker 入口：闭包试算任务。"""
    points = data['points']
    closures = data.get('closures') or data.get('closure_keys') or []
    report = evaluate(points, closures,
                      cluster_count=data.get('clusters') or data.get('cluster_count'),
                      grid=data.get('grid'))
    report['input_hash'] = signature(normalize_closures(closures, points))
    if not report['valid'] and data.get('strict', True):
        # 与谱聚类任务的失败语义保持一致：业务不通时任务 rejected，
        # detail 中明确给出“断开的格子”，同时保留结构化 report。
        raise ClosureViolation(report)
    return report
