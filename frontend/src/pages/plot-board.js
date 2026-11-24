import {el} from '../dom.js';
import {request, awaitCompute} from '../api.js';

const percent = share => new Intl.NumberFormat('zh-CN', {style:'percent',maximumFractionDigits:1}).format(share);
const keyOf = (a, b) => [a, b].sort().join('|');
const pairsOfKeys = keys => keys.map(key => key.split('|'));

function planEdges(plan) {
  const byPos = new Map(plan.layout.map(p => [p.x + ',' + p.y, p]));
  const edges = [];
  for (const p of plan.layout) {
    for (const [dx, dy] of [[1, 0], [0, 1]]) {
      const q = byPos.get((p.x + dx) + ',' + (p.y + dy));
      if (q) edges.push({a: p.code, b: q.code, key: keyOf(p.code, q.code),
                         x1: p.x, y1: p.y, x2: q.x, y2: q.y,
                         horizontal: dx === 1});
    }
  }
  return edges;
}

function edgeLabel(edge) {
  return edge.a.replace(/^.*-(\d+-\d+-\d+)$/, '$1') + ' ↔ ' +
         edge.b.replace(/^.*-(\d+-\d+-\d+)$/, '$1');
}

export function plotBoard(plans, role) {
  if (!plans.length) return el('p',{class:'empty'},'试投格录齐后测算。展陈复核员归档图谱后会显示灯影方格与封路试算。');
  const canWrite = role === 'operator' || role === 'reviewer';
  const lookup = new Map(plans.map(plan => [plan.code, plan]));
  const selector = el('select',{'aria-label':'遮挡图谱',onchange:event => draw(lookup.get(event.target.value))},
    plans.map(plan => el('option',{value:plan.code},plan.code)));

  const chosen = el('div',{class:'plot-sheet'});
  const archiveBox = el('section',{class:'sketch-archive','aria-label':'归档草图查询'});
  let sketches = [];
  let loadSeq = 0;

  async function loadSketches(preferCode) {
    const seq = ++loadSeq;
    const wantedAtStart = (preferCode || codeInput.value || '').trim();
    try {
      sketches = await request('/route_sketches');
    } catch { sketches = []; }
    // 只允许最近一次加载落编辑器，避免旧响应晚到覆盖当前草图
    if (seq !== loadSeq) return undefined;
    renderArchive();
    const plan = selector.value ? lookup.get(selector.value) : plans[0];
    const mine = sketches.filter(s => s.plan === plan.code);
    const wanted = wantedAtStart;
    const target = wanted ? mine.find(s => s.code === wanted) : mine[0];
    if (target) {
      codeInput.value = target.code;
      setClosures(target.closure_keys || target.edge_pairs || []);
      renderReport(target);
    } else {
      if (wanted) codeInput.value = wanted;
      closureKeys = new Set();
      syncArea();
      reportBox.replaceChildren();
    }
    return target;
  }

  // ---- 封路编辑器状态 ----
  const codeInput = el('input',{name:'sketch_code','aria-label':'草图编号',value:''});
  const closuresArea = el('textarea',{name:'closures','aria-label':'封路编号对（JSON 数组）',
    placeholder:'[["BANK-01-0-0","BANK-01-1-0"]]'});
  const workStatus = el('p',{class:'status sketch-status','aria-live':'polite','data-testid':'sketch-status'});
  const reportBox = el('div',{class:'sketch-report','data-testid':'sketch-report'});
  let closureKeys = new Set();
  let pickMode = false;
  let picked = null;

  function currentPairs() { return pairsOfKeys([...closureKeys]); }
  function syncArea() { closuresArea.value = JSON.stringify(currentPairs()); }
  function setClosures(pairs) {
    closureKeys = new Set((pairs || []).map(([a, b]) => keyOf(a, b)));
    syncArea();
    draw(lookup.get(selector.value), true);
  }
  function toggleEdge(key) {
    if (closureKeys.has(key)) closureKeys.delete(key); else closureKeys.add(key);
    syncArea();
    draw(lookup.get(selector.value), true);
  }

  function renderReport(sketch) {
    if (!sketch) { reportBox.replaceChildren(); return; }
    const r = sketch.result || sketch.report || {};
    const broken = sketch.broken_cells || r.broken_cells || [];
    reportBox.replaceChildren(
      el('p',{class:'sketch-state'},'草图 '+sketch.code+' · 状态 '+sketch.status+
        ' · 封路版本 '+(sketch.revision ?? '—')+
        (sketch.zone_count != null ? ' · 片区 '+sketch.zone_count : '')),
      broken.length ? el('p',{class:'status error'},'断开的格子：'+broken.join('、')) :
        (r.valid ? el('p',{class:'status ok'},'当前封路下各灯组仍在同一片区内。') : null));
  }

  function actionButton(label, path, buildPayload, variant) {
    const button = el('button',{type:'button', class:variant || ''}, label);
    button.addEventListener('click', async () => {
      button.disabled = true; workStatus.className = 'status sketch-status'; workStatus.textContent = '处理中…';
      try {
        const payload = buildPayload();
        let out = await request(path, payload);
        if (out && out.token) {
          const compute = await awaitCompute(out.token, current => {
            workStatus.textContent = '计算状态：' + current.status;
          });
          if (compute.status === 'rejected') throw new Error(compute.detail || '测算失败');
          try { out = await request('/route_sketches/apply',{code:payload.code, token:out.token}); }
          catch { /* eager 部署已自动应用；重复固定失败不影响结果 */ }
        }
        workStatus.classList.add('ok');
        workStatus.textContent = label + '完成。';
        if (out && out.closure_keys) setClosures(out.closure_keys);
        renderReport(out);
        await loadSketches(payload.code);
      } catch (error) {
        workStatus.classList.add('error');
        workStatus.textContent = error.message;
        const code = codeInput.value.trim();
        if (code) {
          const latest = sketches.find(s => s.code === code);
          if (latest) renderReport(latest);
        }
      } finally { button.disabled = false; }
    });
    return button;
  }

  function renderArchive() {
    const archived = sketches.filter(s => s.archived || s.status === '已归档');
    const all = sketches;
    archiveBox.replaceChildren(
      el('h2',{},'归档草图查询'),
      el('p',{class:'plot-detail'},'复核员可在图谱页面查到每张归档草图所用的原图（归档遮挡图谱）与封路。'),
      archived.length ? el('div',{class:'archived-grid','data-testid':'archived-sketches'},
        archived.map(archivedCard)) : el('p',{class:'empty'},'还没有归档草图。领班试算封路、恢复可用草图后可在此归档并查询。'),
      all.length ? el('div',{class:'scroll'}, el('table',{class:'sketch-table'},
        el('thead',{},el('tr',{},el('th',{},'草图'),el('th',{},'原图'),el('th',{},'状态'),
          el('th',{},'封路数'),el('th',{},'版本'))),
        el('tbody',{}, all.map(s => el('tr',{},
          el('td',{},s.code), el('td',{},s.plan), el('td',{},s.status),
          el('td',{},String((s.closure_keys||[]).length)), el('td',{},String(s.revision))))))) : null);
  }

  function archivedCard(s) {
    const original = s.original_map || s.plan_detail || {};
    const grid = original.evidence && original.evidence.grid ? original.evidence.grid.join('×') : '';
    const closures = s.archived_closures || s.used_closures || s.closures || [];
    const keys = s.closure_keys || [];
    const r = s.result || {};
    return el('article',{class:'archived-card record','data-code':s.code},
      el('h3',{},s.code),
      el('dl',{},
        el('dt',{},'原图（归档图谱）'),
        el('dd',{},String(s.plan)+(original.band ? ' · 投影带 '+original.band : '')+
          (original.revision != null ? ' · 底图版本 '+original.revision : '')+(grid ? ' · 网格 '+grid : '')),
        el('dt',{},'封路'),
        el('dd',{}, keys.length ? el('ul',{class:'closure-list','data-testid':'archived-closures'},
          keys.map(pair => el('li',{},String(pair[0])+' ↔ '+String(pair[1])))) : '未封相邻方格通路'),
        el('dt',{},'片区 / 灯组'),
        el('dd',{},'片区 '+(s.zone_count ?? r.zone_count ?? '—')+' · 灯组 '+(r.cluster_count ?? original.clusters ?? '—')),
        (s.broken_cells && s.broken_cells.length) ? [el('dt',{},'断开的格子'),
          el('dd',{class:'broken-cells'},s.broken_cells.join('、'))] : []));
  }

  function draw(plan, preserveEditor) {
    if (!plan) return;
    if (!preserveEditor && !codeInput.value) codeInput.value = 'SKETCH-' + plan.code;
    const edges = planEdges(plan);
    const edgeByKey = new Map(edges.map(e => [e.key, e]));
    const note = el('p',{class:'plot-detail','aria-live':'polite'},
      '选择方格查看试投覆盖。带虚线边框的格子需要二签。封路只可切换相邻方格之间的通路。');

    const cols = plan.evidence.grid[0];
    const grid = el('div',{class:'plot-grid closure-grid','aria-label':'灯影网格',style:'--cols:'+cols},
      plan.layout.map(point => {
        const classes = ['plot-point','cluster-'+point.cluster, point.protected ? 'protected' : ''];
        for (const key of closureKeys) {
          const edge = edgeByKey.get(key);
          if (!edge) continue;
          if (edge.a === point.code && edge.horizontal) classes.push('bar-right');
          if (edge.a === point.code && !edge.horizontal) classes.push('bar-bottom');
        }
        return el('button',{
          type:'button', class:classes.join(' '),
          'aria-label':point.code+'，第 '+point.cluster+' 组'+(point.protected?'，保护位':''),
          'data-code':point.code, 'aria-pressed':'false',
          onclick:event => {
            if (pickMode) {
              if (!picked) { picked = point.code; note.textContent = '已选 '+point.code+'，再点一个相邻方格封路。'; return; }
              if (picked === point.code) { picked = null; note.textContent = '已取消选择。'; return; }
              const key = keyOf(picked, point.code);
              if (!edgeByKey.has(key)) { note.textContent = '只能封相邻方格间的通路，请重选。'; picked = point.code; return; }
              toggleEdge(key); picked = null;
              note.textContent = '已切换 '+key.replace('|',' 与 ')+' 之间的通路。';
              return;
            }
            const old = grid.querySelector('[aria-pressed=true]');
            if (old) old.setAttribute('aria-pressed','false');
            event.currentTarget.setAttribute('aria-pressed','true');
            note.textContent = point.code+' · 第 '+point.cluster+' 组 · 遮挡 '+percent(point.shade)+
              ' · 串光 '+percent(point.spill)+(point.protected?' · 保护观察位需二签':'');
          }
        },String(point.cluster));
      }));

    const edgeChips = el('div',{class:'edge-list','aria-label':'相邻方格通路列表'},
      edges.map(edge => el('button',{
        type:'button',
        class:'edge-chip'+(closureKeys.has(edge.key)?' edge-on':''),
        'aria-pressed':closureKeys.has(edge.key)?'true':'false',
        'data-edge-key':edge.key,
        title:edge.a+' 与 '+edge.b,
        'aria-label':(closureKeys.has(edge.key)?'取消封路 ':'封路 ')+edge.a+' 与 '+edge.b+' 之间的通路',
        onclick:() => toggleEdge(edge.key)
      }, edgeLabel(edge) + (closureKeys.has(edge.key)?' 封':''))));

    const pickToggle = el('button',{type:'button',class:'quiet'+(pickMode?' pick-on':''),
      'aria-pressed':pickMode?'true':'false',
      onclick:() => { pickMode = !pickMode; picked = null;
        pickToggle.setAttribute('aria-pressed',String(pickMode));
        pickToggle.classList.toggle('pick-on',pickMode); }},
      pickMode ? '点选模式：开启（点两个相邻格）' : '点选方格封路');

    const applyJson = el('button',{type:'button',class:'quiet',onclick:() => {
      try {
        const parsed = JSON.parse(closuresArea.value || '[]');
        setClosures(Array.isArray(parsed) ? parsed : []);
        workStatus.classList.remove('error'); workStatus.textContent = '已读取封路 JSON。';
      } catch (e) {
        workStatus.classList.add('error'); workStatus.textContent = '封路 JSON 格式不正确：'+e.message;
      }
    }},'读取封路 JSON');

    const loadCode = el('button',{type:'button',class:'quiet',onclick:() => loadSketches(codeInput.value.trim())},'读取草图');

    const tools = canWrite ? el('div',{class:'closure-tools','data-testid':'closure-tools'},
      el('div',{class:'closure-head'},
        el('label',{},el('span',{},'草图编号'),codeInput), loadCode, pickToggle),
      el('p',{class:'plot-detail'},'也可直接切换下列相邻通路，或在文本框填写编号对后读取。'),
      edgeChips,
      el('label',{},el('span',{},'封路 JSON（相邻方格编号对）'),closuresArea),
      applyJson,
      el('div',{class:'closure-actions'},
        actionButton('试算封路','/route_sketches/trial-block',
          () => ({code:codeInput.value.trim(), plan:plan.code, closures:currentPairs()})),
        actionButton('恢复上张可用草图','/route_sketches/restore-sketch',
          () => ({code:codeInput.value.trim(), plan:plan.code}), 'quiet'),
        actionButton('归档草图','/route_sketches/archive-sketch',
          () => ({code:codeInput.value.trim(), plan:plan.code})),
      ),
      workStatus, reportBox) : el('div',{}, reportBox);

    chosen.replaceChildren(
      el('p',{class:'plot-caption'},plan.band+' · 观测版本 '+plan.revision+' · '+
        plan.evidence.plots+' 格 · '+plan.evidence.cut_edges+'/'+plan.evidence.edges+' 条边跨区'),
      grid, note, tools);
    if (!preserveEditor) syncArea();
  }

  selector.addEventListener('change', () => { closureKeys = new Set(); draw(lookup.get(selector.value)); loadSketches(); });
  draw(plans[0]);
  loadSketches();

  return el('section',{class:'plan-picker','data-testid':'plot-board'},
    el('label',{},'归档平面',selector), chosen, archiveBox);
}
