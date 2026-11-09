/* Evidence-only progressive charts. No network, storage, scoring or trading actions. */
(function () {
  'use strict';
  const num = value => {
    if (value == null || !String(value).trim()) return null;
    const s = String(value).trim().replace(/[,，]/g, '').replace(/−/g, '-');
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:%|pp|亿)?$/.test(s)) return null;
    const n = Number(s.replace(/%|pp|亿/g, ''));
    return Number.isFinite(n) ? n : null;
  };
  const fmt = (v, digits = 2, signed = false) => v == null ? '未计算' :
    (signed && v > 0 ? '+' : '') + v.toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const compare = (a, b) => a == null || b == null ? 'missing' : a > b ? 'above' : a < b ? 'below' : 'equal';
  // Displayed prices are rounded: equality cannot prove a precise touch.
  const compareDisplayed = (a, b) => compare(a, b) === 'equal' ? 'rounded-equal' : compare(a, b);
  const percentItems = text => {
    // Only explicit name + signed percentage fragments, never infer values from prose.
    const result = [];
    for (const m of text.matchAll(/(?:^|[，,、；：])\s*([\u4e00-\u9fffⅡ]{2,16})\s*([+\-−]\d+(?:\.\d+)?)%/g)) {
      result.push({label: m[1], value: num(m[2])});
    }
    return result;
  };
  const scale = (values, signed = false) => {
    const valid = values.filter(v => v != null && Number.isFinite(v));
    const max = Math.max(1, ...valid.map(Math.abs));
    return {min: signed ? -max : 0, max};
  };
  const sectorFacts = (meta, sourceText, reportDate) => {
    if (meta.vizMeasure !== 'sector-daily-return-pct' || meta.vizAsOf !== reportDate ||
        meta.vizKind !== 'fact' || !['complete', 'partial'].includes(meta.vizSourceStatus) ||
        !sourceText.startsWith('[事实]')) return [];
    return percentItems(sourceText.split('[判断]')[0]);
  };
  const stats = values => {
    const v = values.filter(x => x != null && Number.isFinite(x)).sort((a, b) => a - b);
    if (!v.length) return {n: 0, mean: null, median: null};
    const m = Math.floor(v.length / 2);
    return {n: v.length, mean: v.reduce((a, b) => a + b, 0) / v.length, median: v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2};
  };
  const coverageState = states => states.some(s => s === 'complete' || s === 'partial') ?
    (states.every(s => s === 'complete') ? 'ready' : 'partial') :
    (states.includes('source_failed') ? 'source_failed' : 'missing-data');
  // Report date governs calendars/plans; market charts require a proven fact date.
  const chartDate = (context, reportDate) => {
    if (context == null) return reportDate;
    if (context.schema !== 'review-visual-context-v1' || context.report_date !== reportDate || context.status !== 'complete') return null;
    const fact = context.fact_date;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(fact || '')) return null;
    if (fact === reportDate) return context.review_context == null ? fact : null;
    const span = (Date.parse(reportDate + 'T00:00:00Z') - Date.parse(fact + 'T00:00:00Z')) / 86400000;
    const proof = context.review_context;
    if (!Number.isInteger(span) || span < 1 || span > 31 || proof?.report_date !== reportDate || proof.calendar?.length !== span + 1) return null;
    return proof.calendar.every((r, i) => Array.isArray(r) && r.length === 2 &&
      r[0] === new Date(Date.parse(fact + 'T00:00:00Z') + i * 86400000).toISOString().slice(0, 10) && r[1] === (i === 0 ? 1 : 0)) ? fact : null;
  };
  const capacityChange = value => {
    const text = String(value || '');
    const match = text.includes('→') ? text.match(/→\s*([+\-−]?[\d.]+)%/) : text.match(/^\s*([+\-−]?[\d.]+)%/);
    return match ? num(match[1]) : null;
  };
  const capacityValues = value => {
    const parts = String(value || '').split(/[；;]/);
    const close = num(parts[0].trim().replace(/^收盘\s*/, ''));
    const averages = (parts[1] || '').split('/');
    return {close, averages: [5,10,20,60,144,233].map((window, i) =>
      num((averages[i] || '').trim().replace(new RegExp('^MA' + window + '\\s+'), '')))};
  };
  const capacityColumn = headers => {
    const index = headers.findIndex(h => /前复权.*MA|收盘.*MA/.test(String(h)));
    return index >= 0 ? index : 7; // Legacy template's documented fixed column.
  };
  // Only the institution main table is accepted; do not infer net flow from a missing leg.
  const institutionFlow = (meta, headers, records, factDate) => {
    const empty = reason => ({status: 'missing-data', groups: {single: [], multi: []}, issues: [reason]});
    const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
    const expected = ['榜段/股票', '机构买／卖／净亿', '当日涨幅%／换手%／成交亿'];
    if (!factDate || meta?.institutionFlow !== 'v1' || meta.asOf !== factDate ||
        (meta.sourceCount != null && meta.sourceCount !== 1) ||
        (meta.sourceStatus != null && !['complete', 'partial', 'ok'].includes(meta.sourceStatus)) ||
        !Array.isArray(headers) || expected.some((value, i) => clean(headers[i]) !== value) || !Array.isArray(records)) {
      return empty('机构主表缺失、日期不符或字段契约不完整');
    }
    const missing = /^(?:—|–|-|未计算|未留存|买卖分项未留存)$/;
    const scalar = token => {
      const s = clean(token).replace(/−/g, '-');
      if (!s || missing.test(s)) return {value: null, tolerance: 0};
      if (!/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(s)) return null;
      const value = Number(s);
      return Number.isFinite(value) ? {value, tolerance: .5 * 10 ** -(s.split('.')[1]?.length || 0)} : null;
    };
    const triple = value => {
      const parts = clean(value).split(/[／/]/);
      if (parts.length !== 3) return null;
      const values = parts.map(scalar);
      return values.every(v => v !== null) ? values : null;
    };
    const parsed = [], issues = [];
    records.forEach((cells, rowIndex) => {
      if (!Array.isArray(cells) || cells.length < 3) {issues.push('第' + (rowIndex + 1) + '行字段不足'); return;}
      const match = clean(cells[0]).match(/^(单日|多日)[／/]\s*(.+?)\s+(\d{6}\.(?:SH|SZ|BJ))$/);
      if (!match || /\d{6}(?:\.(?:SH|SZ|BJ))?|[／/]/.test(match[2])) {issues.push('第' + (rowIndex + 1) + '行榜段或股票身份不明确'); return;}
      const flow = triple(cells[1]), market = triple(cells[2]);
      if (!flow || !market) {issues.push(match[2] + '：字段格式不明确，未绘制'); return;}
      const [buy, sell, net] = flow.map(v => v.value);
      const [change, turnover, amount] = market.map(v => v.value);
      if ((buy != null && buy < 0) || (sell != null && sell < 0) ||
          (turnover != null && turnover < 0) || (amount != null && amount < 0) ||
          (flow.every(v => v.value != null) && Math.abs(buy - sell - net) > flow.reduce((sum, v) => sum + v.tolerance, 0) + 1e-9)) {
        issues.push(match[2] + '：买卖净额或行情字段冲突，未绘制'); return;
      }
      parsed.push({segment: match[1] === '单日' ? 'single' : 'multi', name: match[2], code: match[3],
        buy, sell, net, change, turnover, amount, rowIndex,
        incomplete: [...flow, ...market].some(v => v.value == null)});
    });
    const counts = new Map();
    parsed.forEach(r => {const key = r.segment + ':' + r.code; counts.set(key, (counts.get(key) || 0) + 1);});
    const groups = {single: [], multi: []};
    parsed.forEach(r => {
      if (counts.get(r.segment + ':' + r.code) !== 1) {issues.push(r.name + '：同榜段身份重复，未绘制'); return;}
      groups[r.segment].push(r);
    });
    const all = [...groups.single, ...groups.multi];
    const status = !all.some(r => r.net != null) ? 'missing-data' :
      (issues.length || all.some(r => r.incomplete) || !groups.single.length || meta.sourceStatus === 'partial' ? 'partial' : 'ready');
    return {status, groups, issues};
  };

  // Board labels are source facts: never infer them from a stock code here.
  const sectorBoardCounts = data => {
    const keys = ['10', '20', '30', 'unknown'];
    const integer = n => Number.isInteger(n) && n >= 0;
    const counts = (value, total) => value && integer(total) &&
      keys.every(k => integer(value[k])) && keys.reduce((n,k) => n + value[k], 0) === total;
    const sectors = Array.isArray(data?.sectors) ? data.sectors : [];
    const result = {current: {valid: false}, previous: {valid: false}, sectors: []};
    result.sectors = sectors.map(s => {
      const out = {code: s.code};
      for (const day of ['current', 'previous']) {
        const c = s[day === 'current' ? 'board_counts' : 'previous_board_counts'];
        const list = s[day], n = s[day + '_count'];
        const tally = Object.fromEntries(keys.map(k => [k, 0]));
        const seen = new Set();
        const rowsValid = Array.isArray(list) && list.length === n && list.every(r => {
          if (!r || !keys.includes(r.board) || !r.code || seen.has(r.code)) return false;
          seen.add(r.code); tally[r.board]++; return true;
        });
        const valid = !!(counts(c, n) && rowsValid && keys.every(k => tally[k] === c[k]));
        out[day] = {valid, counts: valid ? {...c} : null};
      }
      return out;
    });
    for (const day of ['current', 'previous']) {
      const sum = Object.fromEntries(keys.map(k => [k, 0]));
      const codes = new Set(), stocks = new Set();
      let valid = counts(data?.board_totals?.[day], data?.totals?.[day]) && sectors.length > 0;
      sectors.forEach((s,i) => {
        if (!s.code || codes.has(s.code) || !result.sectors[i][day].valid) valid = false;
        codes.add(s.code);
        if (result.sectors[i][day].valid) keys.forEach(k => {sum[k] += result.sectors[i][day].counts[k];});
        if (Array.isArray(s[day])) s[day].forEach(r => {if (stocks.has(r?.code)) valid = false; stocks.add(r?.code);});
      });
      valid = !!(valid && keys.every(k => sum[k] === data.board_totals[day][k]));
      result[day] = {valid, counts: valid ? sum : null};
    }
    return result;
  };
  // Read only the exact, dated comparison sentence in the verified emotion evidence.
  const emotionComparison = (value, asOf, factDate, previousDate) => {
    if (!factDate || asOf !== factDate || !/^\d{4}-\d{2}-\d{2}$/.test(previousDate || '') || previousDate >= factDate) return null;
    const m = String(value || '').match(/^\[事实\] 非ST (\d{1,2})月(\d{1,2})日→(\d{1,2})月(\d{1,2})日：涨停(\d+)→(\d+)；跌停(\d+)→(\d+)；炸板(\d+)→(\d+)；首板(\d+)→(\d+)；连板(\d+)→(\d+)；最高板(\d+)→(\d+)；封板率([\d.]+)%→([\d.]+)%[。；]/);
    if (!m || Number(previousDate.slice(5,7)) !== +m[1] || Number(previousDate.slice(8)) !== +m[2] || Number(factDate.slice(5,7)) !== +m[3] || Number(factDate.slice(8)) !== +m[4]) return null;
    const values=m.slice(5).map(Number);
    if (values.some(v=>!Number.isFinite(v)) || values.slice(0,12).some(v=>!Number.isSafeInteger(v))) return null;
    if (values[0] !== values[6]+values[8] || values[1] !== values[7]+values[9]) return null;
    for (let i=0;i<2;i++) if (values[12+i]<0 || values[12+i]>100 || values[i]+values[4+i]<=0 || Math.abs(100*values[i]/(values[i]+values[4+i])-values[12+i])>.011) return null;
    return {previousDate, factDate, rows:['涨停','跌停','炸板','首板','连板','最高板'].map((label,i)=>({label,before:values[i*2],after:values[i*2+1],unit:i===5?'板':'只'})), seal:values.slice(12)};
  };
  const visualStatus = (contentStatus, sourceStatus) => contentStatus && contentStatus !== 'ready' ? contentStatus : sourceStatus === 'partial' ? 'partial' : 'ready';
  const api = {visualStatus, emotionComparison, num, fmt, compare, compareDisplayed, percentItems, sectorFacts, scale, stats, coverageState, chartDate, capacityValues, capacityChange, capacityColumn, institutionFlow, sectorBoardCounts};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  const doc = document.querySelector('.report-document');
  if (!doc || doc.dataset.visualsReady) return;
  doc.dataset.visualsReady = 'v1';
  const reportDate = doc.dataset.reportDate;
  let context;
  try { context = JSON.parse(document.getElementById('review-visual-context')?.textContent || 'null'); }
  catch (_) { context = {}; }
  const date = chartDate(context, reportDate);
  let identity = {};
  try {
    const parsed = JSON.parse(document.getElementById('review-identity-data')?.textContent || '{}');
    if (parsed.schema === 'review-identity-flows-v1' && parsed.report_date === reportDate && (parsed.fact_date || reportDate) === date) identity = parsed;
  } catch (_) { /* Missing evidence is displayed per panel below. */ }
  const classificationDate = identity.classification_as_of || date;
  const text = el => el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
  const q = (selector, root = doc) => root.querySelector(selector);
  const qa = (selector, root = doc) => Array.from(root.querySelectorAll(selector));
  const el = (tag, cls, value) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (value != null) e.textContent = value;
    return e;
  };
  const rows = table => table ? qa('tbody > tr', table).map(r => ({node: r, cells: Array.from(r.cells).map(text)})) : [];
  const valid = e => !!e && date != null && e.dataset.asOf === date && ['complete', 'partial', 'ok'].includes(e.dataset.sourceStatus);
  const tableWith = (section, headers) => qa(section + ' table').find(t => {
    const h = qa('thead th', t).map(text); return headers.every((v, i) => h[i] === v);
  });
  const gaps = [];
  let sourceId = 0;
  function sourceLink(card, source) {
    if (!source) return;
    if (!source.id) source.id = 'viz-source-' + (++sourceId);
    const a = el('a', 'rv-source', '查看依据 ↗'); a.href = '#' + source.id;
    a.addEventListener('click', event => {
      event.preventDefault();
      let p = source.parentElement;
      while (p && p !== doc) {if (p.tagName === 'DETAILS') p.open = true; p = p.parentElement;}
      source.scrollIntoView({block: 'center', behavior: 'smooth'});
      source.setAttribute('tabindex', '-1'); source.focus({preventScroll: true});
    });
    card.append(a);
  }
  function panel(section, key, title, note, source, build) {
    const parent = q('#' + section); if (!parent) return;
    let host = q(':scope > .rv-host', parent);
    if (!host) {
      host = el('div', 'rv-host');
      const heading = q(':scope > h2', parent);
      (heading || parent.firstElementChild).after(host);
    }
    const card = el('figure', 'rv-card'); card.dataset.reviewVisual = key;
    card.append(el('figcaption', 'rv-title', title));
    const legend = {
      'index-matrix':'分钟：收盘比 MA5；日线：MA5 比 MA10。↑ 上方 · ↓ 下方 · — 未计算',
      'sector-heatmap':'颜色深浅表示数量；未录＝未进入榜单，缺＝数据缺失。题材可重叠，不可相加。',
      'style-feedback':'● 开盘均值 / ◇ 开盘中位数（%）；仅为开盘反馈。',
      'institution-flow':'已列机构席位净额（亿元）；单日与多日分开看。',
      'capacity':'↑ 线上 · ↓ 线下 · ≈ 显示值相等 · — 未计算',
      'new-highs':'60 / 120 / 250 日窗口独立统计，名单重叠。',
      'new-high-flow':'退出＝今日未再次创新高；并非趋势破坏。',
      'calendar':'计划事件，发布后仍需核验。',
      'checks':'[判断] 待验证条件',
    }[key];
    if (legend) card.append(el('p','rv-note',legend));
    const method=el('details','rv-subdetail rv-method');
    method.append(el('summary','','统计口径与来源'),el('p','rv-note',note));
    if (!['calendar','checks','coverage'].includes(key)) {
      card.append(el('p','rv-note',date ? '[事实] '+date+(date===reportDate?'':' · 复核 '+reportDate) : '交易日历未核验，未采用历史行情。'));
    }
    if (classificationDate && classificationDate !== date && ['sector-limits','sector-heatmap','sector-breadth'].includes(key)) {
      card.append(el('p','rv-note','行业按 '+classificationDate+' 成分快照回看，非历史成分回放；分类时点差异标记 partial。'));
    }
    if (source?.dataset.sourceStatus) method.append(el('p', 'rv-note', '来源状态：' + source.dataset.sourceStatus + ' · 事实日 ' + (source.dataset.asOf || '未提供')));
    try {
      const content = build();
      if (!content) throw new Error('missing-data');
      card.append(content); card.dataset.visualStatus = visualStatus(content.dataset.visualStatus, source?.dataset.sourceStatus);
      if (card.dataset.visualStatus !== 'ready') gaps.push(title + '（' + card.dataset.visualStatus + '）');
    } catch (_) {
      card.append(el('p', 'rv-empty', '未生成图形：来源缺失、日期不符或字段不完整。请查看原始证据；缺失不等于零。'));
      card.dataset.visualStatus = 'missing-data'; gaps.push(title);
    }
    if (card.dataset.visualStatus === 'partial') card.append(el('p','rv-note','部分数据缺失，图中保留可核验值。'));
    sourceLink(method, source); card.append(method); host.append(card); return card;
  }
  function bars(items, {unit = '%', signed = true, digits = 2, limit = null} = {}) {
    if (!items.length || !items.some(r => r.value != null)) return null;
    const bound = limit || scale(items.map(r => r.value), signed);
    const wrap = el('div', 'rv-bars');
    const axis = el('div', 'rv-axis');
    axis.append(el('span', '', fmt(bound.min, digits) + unit), el('span', '', signed ? '0' : ''), el('span', '', fmt(bound.max, digits) + unit));
    wrap.append(axis);
    items.forEach(item => {
      const row = el('div', 'rv-bar-row'); row.dataset.value = item.value == null ? '' : item.value;
      row.append(el('span', 'rv-label', item.label));
      const track = el('div', 'rv-track');
      const zero = 100 * (0 - bound.min) / (bound.max - bound.min);
      const line = el('i', 'rv-zero'); line.style.left = zero + '%'; track.append(line);
      if (item.value != null) {
        const endpoint = 100 * (item.value - bound.min) / (bound.max - bound.min);
        const mark = el('i', 'rv-bar ' + (item.tone || (item.value < 0 ? 'rv-negative' : 'rv-positive')));
        mark.style.left = Math.min(endpoint, zero) + '%'; mark.style.width = Math.abs(endpoint - zero) + '%'; track.append(mark);
      } else track.append(el('span', 'rv-no-value', '—'));
      row.append(track, el('strong', 'rv-number', fmt(item.value, digits, signed) + (item.value == null ? '' : unit)));
      wrap.append(row);
    });
    return wrap;
  }
  function matrix(headers, records) {
    if (!records.length) return null;
    const scroller = el('div', 'rv-matrix-scroll'); scroller.tabIndex = 0;
    scroller.setAttribute('aria-label', '状态矩阵，可横向滚动查看全部指标');
    const grid = el('div', 'rv-matrix'); grid.style.setProperty('--rv-cols', headers.length);
    grid.setAttribute('role', 'table');
    const head = el('div', 'rv-matrix-row'); head.setAttribute('role', 'row');
    headers.forEach(h => {const c = el('div', 'rv-matrix-head', h); c.setAttribute('role', 'columnheader'); head.append(c);}); grid.append(head);
    records.forEach(record => {
      const row = el('div', 'rv-matrix-row'); row.setAttribute('role', 'row');
      record.forEach((value, i) => {
        const cell = typeof value === 'object' ? value : {text: value};
        const c = el('div', 'rv-cell rv-' + (cell.state || 'neutral'), cell.text);
        c.setAttribute('role', i === 0 ? 'rowheader' : 'cell'); row.append(c);
      }); grid.append(row);
    }); scroller.append(grid);
    const hint = el('small', 'rv-matrix-hint', '窄屏可左右滑动，查看全部指标 →');
    scroller.append(hint); return scroller;
  }
  function choices(items, initial, onChange) {
    const wrap = el('div', 'rv-choices'); wrap.setAttribute('role', 'group');
    items.forEach(([key, label]) => {
      const button = el('button', '', label); button.type = 'button';
      button.setAttribute('aria-pressed', String(key === initial));
      button.addEventListener('click', () => {
        qa('button', wrap).forEach(b => b.setAttribute('aria-pressed', String(b === button)));
        onChange(key);
        const search = document.querySelector('.reader-search input');
        if (search?.value) search.dispatchEvent(new Event('input', {bubbles: true}));
      }); wrap.append(button);
    }); return wrap;
  }
  function unavailable(payload) {
    const status = payload?.status === 'source_failed' ? 'source_failed' : 'missing-data';
    const box = el('div', 'rv-empty', (status === 'source_failed' ? '来源失败' : '缺少数据') + '（' + status + '）：' + (payload?.reason || '未提供身份核验证据') + '。未绘制，不等于0只。');
    box.dataset.visualStatus = status; return box;
  }
  function codeList(title, codes, names = {}) {
    const d = el('details', 'rv-subdetail');
    d.append(el('summary', '', title + ' · ' + codes.length + '只'));
    d.append(el('p', 'rv-code-list', codes.length ? codes.map(code => (names[code] || '名称待核') + ' ' + code).join(' · ') : '本组0只（已核验名单为空）。'));
    return d;
  }
  const stateCell = state => ({state, text: ({above: '↑ 线上', below: '↓ 线下', equal: '= 贴线', 'rounded-equal': '≈ 待核原值', missing: '— 未计算'})[state] || '— 未计算'});
  const kpi = name => qa('#tldr .kpi').find(e => text(q('.k', e)) === name);
  panel('tldr', 'breadth', '市场宽度与量能', '[事实] 昨日与今日对照；上涨占比采用固定 0–100% 刻度，数量与成交额分别展示。', q('#tldr .tldr-grid'), () => {
    if (!date) return null;
    const advance = kpi('上涨家数'), up = kpi('非ST涨停'), down = kpi('非ST跌停');
    const rate = text(advance).match(/占([\d.]+)%.*昨([\d.]+)%/);
    const pair = e => {
      if (!e) return null;
      const v = q('.v', e).cloneNode(true); qa('small', v).forEach(n => n.remove());
      const prev = text(q('small', e)).match(/昨\s*(\d+)/); return prev ? [num(prev[1]), num(text(v))] : null;
    };
    const u = pair(up), d = pair(down);
    if (!rate || !u || !d || [...u, ...d].some(v => v == null || v < 0 || !Number.isInteger(v)) ||
        [num(rate[1]), num(rate[2])].some(v => v == null || v < 0 || v > 100)) return null;
    const box = el('div', 'rv-grid-two');
    const left = el('div'); left.append(el('h4', '', '上涨占比'), bars([
      {label: '上一交易日', value: num(rate[2]), tone: 'rv-previous'}, {label: date.slice(5), value: num(rate[1])}
    ], {signed: false, limit: {min: 0, max: 100}}));
    const right = el('div'); right.append(el('h4', '', '非 ST 涨跌停家数'), bars([
      {label: '昨日涨停', value: u[0], tone: 'rv-previous'}, {label: '今日涨停', value: u[1]},
      {label: '昨日跌停', value: d[0], tone: 'rv-previous'}, {label: '今日跌停', value: d[1], tone: 'rv-negative'}
    ], {signed: false, unit: '只', digits: 0}));
    box.append(left, right);
    const amount = kpi('两市成交额'); if (amount) box.append(el('p', 'rv-amount', text(amount) + ' · 成交额变化不代表资金净流入。'));
    return box;
  });
  const intra = q('table[data-index-intraday-structure=v1]');
  panel('s1', 'index-matrix', '指数 × 周期：均线位置', '[事实] 分钟列以该周期收盘相对 MA5 标记；日线单列 MA5 相对 MA10。二者不是同一指标，不合成评分。', intra, () => {
    if (!valid(intra)) return null;
    const records = new Map();
    rows(intra).forEach(({node, cells}) => {
      const id = node.dataset.indexId; if (!id) return;
      if (!records.has(id)) records.set(id, {label: text(node.cells[0].childNodes[0]), periods: {}});
      const r = records.get(id); const period = node.dataset.period;
      r.periods[period] = node.dataset.status === 'ok' && (node.dataset.lastBarAt || '').startsWith(date) ?
        stateCell(node.dataset.closeVsMa5) : stateCell('missing');
    });
    const box = el('div');
    box.append(matrix(['指数', '15分钟', '30分钟', '60分钟'], Array.from(records.values()).map(r =>
      [r.label, ...['15m', '30m', '60m'].map(p => r.periods[p] || stateCell('missing'))])));
    const daily = tableWith('#s1', ['指数', '收盘/涨跌', '今日MA5/10']) || tableWith('#s1', ['指数', '收盘/涨跌%', 'MA5/MA10']);
    if (daily && daily.closest('details')?.dataset.asOf === date) {
      const dailyRows = rows(daily).map(({cells}) => {
        const m = cells[2].split('/').map(num);
        return [cells[0].replace(/\s*\d{6}\.[A-Z]+/, ''), stateCell(compareDisplayed(m[0], m[1]))];
      });
      const details = el('details', 'rv-subdetail'); details.append(el('summary', '', '展开日线 MA5 / MA10 对照（含更多宽基）'), matrix(['指数', 'MA5 相对 MA10'], dailyRows)); box.append(details);
    }
    return box;
  });
  const concentration = q('table[data-sector-concentration=v1]');
  const scan = q('[data-viz-measure="sector-daily-return-pct"]');
  panel('s2', 'sectors', '板块涨跌与成交集中度', '[事实] 左侧仅展示原文明确给出的板块当日涨跌；右侧为成交额 Top20 内部份额变化（百分点），两个口径分别阅读。', concentration, () => {
    if (!valid(concentration)) return null;
    const box = el('div', 'rv-grid-two');
    const returns = scan ? sectorFacts(scan.dataset, text(scan), date) : [];
    const left = el('div'); left.append(el('h4', '', '当日涨跌 · 涨跌各前5'));
    returns.sort((a, b) => b.value - a.value);
    const leaders = returns.filter(r => r.value > 0).slice(0,5);
    const laggards = returns.filter(r => r.value < 0).reverse().slice(0,5);
    const preview = [...leaders, ...laggards];
    const returnScale = scale(returns.map(r => r.value), true);
    left.append(bars(preview, {limit:returnScale}) || el('p', 'rv-empty', returns.length ? '已核验板块均收平，完整名单见展开。' : '未提供可核验的板块当日涨跌，未绘制。'));
    const remaining = returns.filter(r => !preview.includes(r));
    if (remaining.length) {
      const more = el('details','rv-subdetail');
      more.append(el('summary','',`展开其余${remaining.length}个板块`), bars(remaining,{limit:returnScale})); left.append(more);
    }
    if (!returns.length) {gaps.push('板块当日涨跌'); box.dataset.visualStatus = 'partial';}
    if (scan?.dataset.vizSourceStatus === 'partial') {
      left.append(el('p', 'rv-note', '板块涨跌来源：partial，部分覆盖。')); box.dataset.visualStatus = 'partial';
    }
    if (scan) sourceLink(left, scan);
    const right = el('div'); right.append(el('h4', '', 'Top20 内部份额 · 变化最大6项'));
    const items = rows(concentration).map(({cells}) => ({label: cells[0], value: num(cells[5])}));
    items.sort((a,b) => (b.value == null ? -Infinity : Math.abs(b.value)) - (a.value == null ? -Infinity : Math.abs(a.value)));
    const changeScale = scale(items.map(r => r.value), true);
    right.append(bars(items.slice(0,6), {unit:'pp',limit:changeScale}) || el('p', 'rv-empty', items.length ? items.slice(0,6).map(r=>r.label+'：未计算').join('；') : '份额变化未计算'));
    if (items.length > 6) {
      const more = el('details','rv-subdetail');
      const rest = bars(items.slice(6),{unit:'pp',limit:changeScale});
      more.append(el('summary','',`展开其余${items.length-6}项`),rest || el('p','rv-empty',items.slice(6).map(r=>r.label+'：未计算').join('；'))); right.append(more);
    }
    box.append(left, right); return box;
  });
  const sectorLimits = identity.sector_limits;
  panel('s2', 'sector-limits', '热门板块：涨停数量前5', '[事实] 申万二级·非ST、非退市涨停前5；按今日数量排序，全部板块可展开查看。', null, () => {
    if (!sectorLimits || !['complete','partial'].includes(sectorLimits.status)) return unavailable(sectorLimits);
    if (sectorLimits.date !== date || sectorLimits.classification_as_of !== classificationDate) return unavailable({status:'source_failed',reason:'板块涨停统计日期不符'});
    const box=el('div','rv-sector-limits'); box.dataset.visualStatus=sectorLimits.status==='partial'?'partial':'ready';
    box.append(el('p','rv-statline', sectorLimits.previous_date+' → '+date+' · 来源涨停样本 '+sectorLimits.totals.previous+' → '+sectorLimits.totals.current+'只'));
    box.append(el('p','rv-note','空心：昨日；实心：今日板型。仅统计原始涨停名单。'));
    const boards=sectorBoardCounts(sectorLimits), boardKeys=['10','20','30','unknown'];
    const boardLabel=k=>k==='unknown'?'未知':k+'cm';
    const boardText=c=>boardKeys.filter(k=>k==='10'||k==='20'||c[k]>0).map(k=>boardLabel(k)+' '+c[k]+'只').join(' · ');
    if(boards.current.valid) box.append(el('p','rv-statline','今日名单：'+boardText(boards.current.counts)));
    const legend=el('div','rv-board-legend');boardKeys.forEach(k=>{const item=el('span','');item.append(el('i','rv-board-'+k),document.createTextNode(boardLabel(k)));legend.append(item);});box.append(legend);
    if(!boards.current.valid||!boards.previous.valid){box.dataset.visualStatus='partial';box.append(el('p','rv-note',(boards.current.valid?'':'今日板型未核验；')+(boards.previous.valid?'':'昨日板型未核验；')+'缺字段或合计不一致，不按代码推定板型。'));}
    const selected=sectorLimits.sectors.filter(s=>s.code!=='UNCLASSIFIED' && s.current_count>0).slice(0,5);
    const max=Math.max(1,...selected.flatMap(s=>[s.previous_count,s.current_count]));
    selected.forEach(s=>{
      const verified=boards.sectors.find(r=>r.code===s.code);
      const currentOK=boards.current.valid&&verified?.current.valid, previousOK=boards.previous.valid&&verified?.previous.valid;
      const row=el('div','rv-sector-count-row'); row.dataset.sector=s.code;
      row.dataset.currentCount=s.current_count;row.dataset.previousCount=s.previous_count;
      row.dataset.boardStatus=currentOK?'verified':'unverified';
      const name=el('div','rv-label',s.name);
      name.append(el('span','rv-board-counts',currentOK?boardText(verified.current.counts):'板型未核验'));
      name.append(el('small','','首板'+s.first_count+' / 连板'+s.consecutive_count));
      const tracks=el('div','rv-count-tracks');
      const previous=el('div','rv-track');const old=el('i','rv-count-previous');old.style.width=100*s.previous_count/max+'%';previous.append(old);
      previous.title='昨日 '+s.previous_count+'只 · '+(previousOK?boardText(verified.previous.counts):'板型未核验');
      const current=el('div','rv-track');
      if(currentOK){let offset=0;boardKeys.forEach(k=>{const n=verified.current.counts[k];if(n){const mark=el('i','rv-count-board rv-board-'+k);mark.dataset.board=k;mark.dataset.count=n;mark.style.left=100*offset/max+'%';mark.style.width=100*n/max+'%';mark.title=boardLabel(k)+' '+n+'只';current.append(mark);}offset+=n;});}
      else current.append(el('span','rv-no-value','板型未核验'));
      current.setAttribute('aria-label','今日 '+s.current_count+'只 · '+(currentOK?boardText(verified.current.counts):'板型未核验'));
      tracks.append(previous,current);row.append(name,tracks,el('strong','rv-number',s.previous_count+' → '+s.current_count+'只'));box.append(row);
    });
    if(!selected.length) box.append(el('p','rv-empty','完整来源中没有可归类的当日涨停板块；未分类样本见下方。'));
    const missing=sectorLimits.unclassified;
    box.append(el('p','rv-note','未分类/分类冲突：昨日'+missing.previous.length+'只、今日'+missing.current.length+'只；不重复分配到多个行业。'));
    const all=el('details','rv-subdetail');all.append(el('summary','','展开全部板块统计与逐股名单（含昨日有、今日为0的板块）'));
    all.append(el('p','rv-note','两日均按'+classificationDate+'申万二级成分快照归类，非历史成分回放；同数按名称排序。热门只表示涨停数量，不等于主线判断。10/20/30cm为常规板型，涨停由原始名单认定，不按涨幅阈值筛选；30cm为0仅指现有样本，不证明全市场无30cm涨停。'));
    for(const day of ['previous','current']) all.append(el('p','rv-note',(day==='current'?'今日':'昨日')+'名单：'+(boards[day].valid?boardKeys.map(k=>boardLabel(k)+' '+boards[day].counts[k]+'只').join(' · '):'板型未核验')));
    sectorLimits.sectors.forEach(s=>{
      const d=el('details','rv-subdetail');d.append(el('summary','',s.name+' · '+s.previous_count+' → '+s.current_count+'只 · 今日首板'+s.first_count+'/连板'+s.consecutive_count));
      d.append(el('p','rv-code-list','今日：'+(s.current.map(r=>r.name+' '+r.code+'（'+r.height+'板；'+(boardKeys.includes(r.board)?boardLabel(r.board):'板型未核验')+'）').join(' · ')||'0只（来源完整）')));
      d.append(el('p','rv-code-list','昨日：'+(s.previous.map(r=>r.name+' '+r.code+'（'+r.height+'板；'+(boardKeys.includes(r.board)?boardLabel(r.board):'板型未核验')+'）').join(' · ')||'0只（来源完整）')));all.append(d);
    });box.append(all);sourceLink(box, all.querySelector('summary'));return box;
  });
  const heat = identity.sector_heatmap, themeHeat = identity.theme_heatmap;
  panel('s2', 'sector-heatmap', '行业与热门题材：近5日涨停热力图', '[事实] 行业与题材使用各自来源。题材可以重叠，不能把各行相加作为全市场涨停数；未进入题材归档榜单不等于0。', null, () => {
    const box=el('div'), holder=el('div');
    const state=(data,isTheme)=>!data?'missing-data':!['complete','partial'].includes(data.status)?data.status:
      data.date!==date||data.dates?.length!==5||(!isTheme&&data.classification_as_of!==classificationDate)?'source_failed':data.status;
    box.dataset.visualStatus=coverageState([state(heat,false),state(themeHeat,true)]);
    function drawKind(kind){
      holder.replaceChildren();holder.dataset.heatKind=kind;
      const isTheme=kind==='theme',data=isTheme?themeHeat:heat;
      if(!data||!['complete','partial'].includes(data.status)){holder.append(unavailable(data));return;}
      if(data.date!==date||data.dates?.length!==5||(!isTheme&&data.classification_as_of!==classificationDate)){holder.append(unavailable({status:'source_failed',reason:'热力图日期或五日窗口不符'}));return;}
      holder.append(el('p','rv-statline',isTheme?'热门题材榜 · 今日原榜'+data.sectors.length+'项':'申万二级行业 · 固定今日涨停靠前板块'));
      holder.append(el('p','rv-note',isTheme?'题材使用各日原榜的涨停家数，未重算非ST口径；原榜包含泛概念及少量行业，与申万行业图统计口径不同。点击格子查看股票名称与代码。当日成员快照确认的部分名单、涨停原因涉及的股票分别列示；原因标签不是完整题材归属，名单不能用于重算原榜总数。':'统一按'+classificationDate+'申万二级成分快照回看五日，不是逐日历史成分；默认固定今日涨停数前10板块。点击格子查看股票名称与代码。'));
      const maximum=Math.max(1,...data.days.flatMap(d=>Object.values(d.counts||{})));
      holder.append(el('p','rv-note','统一色阶 0–'+maximum+'只；颜色越深表示涨停数越多。'+(isTheme?'“未录”表示当日归档榜单未收录、数量未知；“缺”表示来源失败。':'灰色“缺”表示来源失败；“≥”表示存在未分类股票，只能确认已归类数量。')));
      const content=el('div'),detail=el('div','rv-picked-detail',isTheme?'点击格子查看原榜统计及有来源的股票名称。':'点击格子查看股票名称与代码。');
      function drawRows(range){
        content.replaceChildren();detail.textContent=isTheme?'点击格子查看原榜统计及有来源的股票名称。':'点击格子查看股票名称与代码。';
        const selected=data.sectors.filter(s=>range==='all'||data.selected.includes(s.code));
        const grid=el('div','rv-heat-grid');grid.setAttribute('role','group');grid.setAttribute('aria-label',isTheme?'热门题材近五日涨停数':'行业近五日涨停数');
        grid.append(el('strong','rv-heat-label',(isTheme?'题材':'行业')+' / 涨停数'));data.dates.forEach(day=>grid.append(el('strong','rv-heat-head',day.slice(5))));
        selected.forEach(s=>{
          grid.append(el('span','rv-heat-label',s.name));
          data.days.forEach(d=>{
            const good=['complete','partial'].includes(d.status)&&d.counts!==null;
            const unlisted=isTheme&&good&&!Object.prototype.hasOwnProperty.call(d.counts,s.code);
            const count=good&&!unlisted?(d.counts[s.code]||0):null;
            const lower=!isTheme&&d.status==='partial';
            const label=count===null?(unlisted?'未录':'缺'):(lower?'≥':'')+count;
            const cell=el('button','rv-heat-cell'+(count===null?' rv-heat-missing':''),label);cell.type='button';
            cell.dataset.sector=s.code;cell.dataset.date=d.date;cell.dataset.count=count===null?'':count;cell.dataset.cellState=unlisted?'unlisted':good?'known':'source_failed';
            cell.setAttribute('aria-label',s.name+' '+d.date+' '+(unlisted?'未收录，数量未知':count===null?'来源失败':(lower?'至少':'')+count+'只涨停'));
            if(count!==null)cell.style.backgroundColor='rgba(0,124,105,'+(count===0?0:0.10+0.65*count/maximum)+')';
            if(count!==null&&count/maximum>.55)cell.style.color='white';
            cell.addEventListener('click',()=>{
              detail.replaceChildren(el('strong','',s.name+' · '+d.date+' · '+(unlisted?'未收录':count===null?'来源失败':(lower?'至少':'')+count+'只涨停')));
              let description;
              if(unlisted)description='当日归档榜单未收录该题材，涨停数未知，不能当作0。';
              else if(count===null)description=d.reason;
              else if(isTheme){const entry=d.entries[s.code];description='当日原榜：'+entry.name+' · 排名'+entry.rank+' · 涨停'+entry.count+'只 · 连板'+entry.consecutive+'只。完整题材成员尚未归档，以下只展示有证据的名称。';}
              else {const records=(d.records||[]).filter(r=>r.sector===s.code);description=records.length?records.map(r=>r.name+' '+r.code).join(' · '):d.status==='complete'?'0只（完整来源）。':'当前已归类记录为0；尚有未分类股票，不能确认本行业为0。';}
              detail.append(el('p','rv-code-list',description));
              if(isTheme){
                const evidence=d.stock_details;
                const members=evidence?.members?.[s.code]||[],reasons=evidence?.reasons?.[s.code]||[];
                detail.append(el('p','rv-statline','已确认题材成员 · 部分名单'));
                if(members.length)detail.append(el('p','rv-code-list',members.map(r=>r.name+' '+r.code).join(' · ')));
                else detail.append(el('p','rv-note','尚无该题材可确认的成员名单；不代表没有涨停股。'));
                detail.append(el('p','rv-note',evidence?.membership_status==='partial'?'当日成员快照仅抽样覆盖'+evidence.sampled_stocks+'/'+evidence.up_total+'只涨停股，并非完整题材成分。':evidence?.membership_status==='source_failed'?'成员来源核验失败：'+evidence.membership_reason:'缺少该日成员快照。'));
                const unverified=evidence?.unverified_members?.[s.code]||[];
                if(unverified.length){detail.append(el('p','rv-statline','成员接口关联 · 有效性未核验'),el('p','rv-code-list',unverified.map(r=>r.name+' '+r.code).join(' · ')),el('p','rv-note','当日归档未保存成员有效状态（is_new），仅作关联线索，不计入已确认成员。'));}
                detail.append(el('p','rv-statline','涨停原因涉及 · '+(reasons.length?reasons.length+'只（补充线索）':'暂无明确匹配')));
                if(reasons.length){const list=el('ul','rv-theme-stocks');reasons.forEach(r=>{const item=el('li');item.append(el('strong','',r.name+' '+r.code),el('p','rv-note','原始原因：'+r.cause));list.append(item);});detail.append(list);}
                detail.append(el('p','rv-note',evidence?.status==='source_failed'?'涨停身份来源核验失败：'+evidence.reason:evidence?.reason_status==='source_failed'?'涨停原因来源失败：'+evidence.cause_reason:evidence?.reason_status==='missing-data'||!evidence?'尚无当日涨停原因归档。':'按原因标签匹配；不代表原榜全部成员，未匹配也不等于无相关股票。'));
                if(evidence?.no_cause_codes?.length)detail.append(el('p','rv-note','另有'+evidence.no_cause_codes.length+'只当日涨停股未匹配到原因来源。'));
                const rule=el('details','rv-subdetail');rule.append(el('summary','','名称来源与匹配规则'),el('p','rv-note',evidence?.reason_rule||'未取得匹配依据。'));
                const link=el('a','rv-source','查看逐股涨停原因字段说明 ↗');link.href='https://tushare.pro/document/2?doc_id=355';link.target='_blank';link.rel='noopener noreferrer';rule.append(link);detail.append(rule);
              }
              const search=document.querySelector('.reader-search input');if(search?.value)search.dispatchEvent(new Event('input',{bubbles:true}));
            });grid.append(cell);
          });
        });content.append(grid);
        if(isTheme)grid.querySelector('button[data-date="'+date+'"]')?.click();
      }
      holder.append(choices([['top',isTheme?'原榜前10':'今日前10'],['all',isTheme?'今日原榜全部题材':'今日全部有涨停板块']], 'top',drawRows),content,detail);drawRows('top');
      const receipt=el('details','rv-subdetail'),summary=el('summary','','查看五日来源覆盖与口径');receipt.append(summary);
      data.days.forEach(d=>receipt.append(el('p','rv-note',d.date+' · '+d.status+' · '+(isTheme?(d.row_count===null?d.reason:'归档原榜'+d.row_count+'项；未收录项未知'):(d.total===null?d.reason:'非ST/非退市涨停'+d.total+'只，未分类'+(d.unclassified||[]).length+'只')))));
      if(isTheme){const link=el('a','rv-source','查看题材统计字段说明 ↗');link.href='https://tushare.pro/document/2?doc_id=357';link.target='_blank';link.rel='noopener noreferrer';receipt.append(link);}
      holder.append(receipt);sourceLink(holder,summary);
    }
    box.append(choices([['industry','申万行业'],['theme','热门题材']], 'industry',drawKind),holder);drawKind('industry');return box;
  });
  const breadthBySector=identity.sector_breadth;
  panel('s2','sector-breadth','板块内部：上涨、平盘与下跌','[事实] 涨跌方向由当日收盘相对昨收计算；比例分母仅为价格、成交量和成交额有效的交易样本。无有效行情单列，不算平盘。',null,()=>{
    const data=breadthBySector;
    if(!data||!['complete','partial'].includes(data.status))return unavailable(data);
    if(data.date!==date||data.classification_as_of!==classificationDate)return unavailable({status:'source_failed',reason:'板块分布日期不符'});
    const box=el('div');box.dataset.visualStatus=data.status==='complete'?'ready':'partial';const content=el('div');
    box.append(el('p','rv-note','青＝上涨 · 灰＝平盘 · 红＝下跌；仅有效交易样本。'));
    const missing=data.sectors.reduce((n,s)=>n+s.unknown,0);
    if(missing) box.append(el('p','rv-note',missing+'只无有效行情，未计入比例。'));
    function draw(mode){
      content.replaceChildren();
      data.sectors.filter(s=>mode==='all'?(s.sample>0):data.selected.includes(s.code)).forEach(s=>{
        const d=el('details','rv-subdetail rv-breadth-detail');d.dataset.sector=s.code;
        const summary=el('summary');const row=el('div','rv-breadth-row');
        row.append(el('span','rv-label',s.name+' · 有效n='+s.valid));const track=el('div','rv-breadth-track');
        if(s.valid) ['up','flat','down'].forEach(k=>{const seg=el('span','rv-breadth-'+k);seg.style.width=100*s[k]/s.valid+'%';track.append(seg);});
        else track.append(el('span','rv-no-value','无有效样本'));
        row.append(track,el('strong','rv-number',s.valid?'上涨 '+(100*s.up/s.valid).toFixed(1)+'%':'比例未计算'));summary.append(row);
        summary.append(el('small','rv-note','涨停 '+s.limit_up+'只'+(s.unknown?' · 无行情 '+s.unknown+'只':'')));
        const counts=el('p','rv-note','上涨'+s.up+' / 平盘'+s.flat+' / 下跌'+s.down+' · 有效样本'+s.valid+'只');
        d.append(summary,counts,el('p','rv-note','快照符合过滤口径的成员'+s.sample+'只；其中有效交易'+s.valid+'只。展开名单按股票代码排列，不代表优先级。'));
        const states={up:'上涨',flat:'平盘',down:'下跌',unknown:'无有效行情，原因未核实'};
        d.append(el('p','rv-code-list',s.records.map(r=>r.name+' '+r.code+' · '+states[r.outcome]+(r.pct===null?'':' '+fmt(r.pct,2,true)+'%')+(r.limit_up?'（涨停）':'')).join('；')));content.append(d);
      });
    }
    box.append(choices([['top','今日涨停前10板块'],['all','全部有成员板块']], 'top',draw),content);draw('top');
    const receipt=el('details','rv-subdetail'), summary=el('summary','','查看过滤口径与覆盖缺口');receipt.append(summary);
    receipt.append(el('p','rv-note','剔除事实日ST名单、成员名称含ST/退市标识及B股；成员快照日 '+classificationDate+'，行情日 '+date+'，不能据无行情断定停牌或退市。青＝上涨、灰＝平盘、红＝下跌；涨停数是独立计数，不另叠加到比例。'));
    receipt.append(el('p','rv-note','全量覆盖：有效交易'+data.sectors.reduce((n,s)=>n+s.valid,0)+'只；快照成员中无有效行情'+data.sectors.reduce((n,s)=>n+s.unknown,0)+'只；ST名单与名称标识差异'+(data.eligibility.st_list_only.length+data.eligibility.name_st_only.length)+'只，已按并集排除。'));
    const e=data.eligibility;
    receipt.append(el('p','rv-note','当日日线'+data.quote_count+'只；ST名单与名称标识取并集剔除'+e.st_union_count+'只，其中仅ST名单命中'+e.st_list_only.length+'只、仅名称ST命中'+e.name_st_only.length+'只；来源身份存在差异，采用并集保守排除。未成功归类行情'+data.unmapped_quotes.length+'只。'));
    receipt.append(el('p','rv-code-list','仅ST名单：'+(e.st_list_only.join(' · ')||'0只')+'；仅名称ST：'+(e.name_st_only.join(' · ')||'0只')+'；未归类：'+(data.unmapped_quotes.join(' · ')||'0只')));
    box.append(receipt);sourceLink(box,summary);return box;
  });
  const feedback = q('table[data-board-break-feedback=v1]');
  panel('s3', 'break-distribution', '断板股：个股收益分布', '[事实] 每行一只股票，点位表示反馈涨跌幅；可切换开盘/收盘，固定同一横轴范围。均值与中位数仅统计有值样本。', feedback, () => {
    const archived = identity.board_feedback;
    const useTable = valid(feedback) && feedback.dataset.outcomeDate === date;
    if (!useTable && (!date || !archived || archived.date !== date || !['complete','partial'].includes(archived.status))) return unavailable(archived);
    const points = useTable ? rows(feedback).map(({node, cells}) => ({code: node.dataset.code, label: cells[0], open: num(node.dataset.feedbackOpenPct), close: num(node.dataset.feedbackClosePct)})) : archived.points;
    const sampleCount = useTable ? num(feedback.dataset.sampleCount) : archived.sample;
    if (!useTable && archived.empty === true && points.length === 0) return el('p','rv-note',archived.note+' · 完整来源确认无断板反馈样本；未计算收益分布。');
    if (!points.length) return unavailable({status:'missing-data',reason:'来源部分覆盖，尚无可核验的逐股反馈'});
    if (!points.length || points.length !== sampleCount || new Set(points.map(r => r.code)).size !== points.length) return null;
    const all = points.flatMap(r => [r.open, r.close]).filter(v => v != null);
    if (!all.length) return null;
    const lo = Math.floor(Math.min(0, ...all)) - 1, hi = Math.ceil(Math.max(0, ...all)) + 1;
    const pct = v => 100 * (v - lo) / (hi - lo);
    const box = el('div');
    if (points.some(r => r.open == null || r.close == null) || (useTable ? feedback.dataset.sourceStatus === 'partial' : archived.status === 'partial')) box.dataset.visualStatus = 'partial';
    const content = el('div', 'rv-distribution');
    function draw(mode) {
      content.replaceChildren(); content.dataset.feedbackMode = mode;
      const summary = stats(points.map(r => r[mode]));
      content.append(el('p', 'rv-statline', (mode === 'open' ? '开盘' : '收盘') + ' · 有值 ' + summary.n + '/' + points.length + ' · 均值 ' + fmt(summary.mean, 2, true) + '% · 中位 ' + fmt(summary.median, 2, true) + '%'));
      content.append(el('p', 'rv-note', '实线＝零轴 · 蓝色虚线＝均值 · 金色虚线＝中位数；均值/中位按表内展示精度复算。'));
      const axis = el('div', 'rv-distribution-axis');
      [lo, 0, hi].forEach(v => {const tick = el('span', '', v + '%'); tick.style.left = pct(v) + '%'; axis.append(tick);}); content.append(axis);
      points.forEach(r => {
        const row = el('div', 'rv-dist-row'); row.dataset.code = r.code;
        row.append(el('span', 'rv-label', r.label));
        const track = el('div', 'rv-track');
        [[0, 'rv-zero'], [summary.mean, 'rv-reference-mean'], [summary.median, 'rv-reference-median']].forEach(([v, cls]) => {
          if (v == null) return; const line = el('i', cls); line.style.left = pct(v) + '%'; track.append(line);
        });
        if (r[mode] != null) {const dot = el('span', 'rv-dist-dot', '●'); dot.style.left = pct(r[mode]) + '%'; track.append(dot);}
        else track.append(el('span', 'rv-no-value', '—'));
        row.dataset.value = r[mode] == null ? '' : r[mode];
        row.append(track, el('strong', 'rv-number', fmt(r[mode], 2, true) + (r[mode] == null ? '' : '%'))); content.append(row);
      });
    }
    box.append(el('p', 'rv-note', useTable ? text(q('caption', feedback)) : archived.note+' · 来源：'+archived.source), choices([['close','收盘反馈'],['open','开盘反馈']], 'close', draw), content);
    draw('close'); return box;
  });
  const ladder = identity.ladder;
  panel('s3', 'ladder-flow', '连板晋级：各梯队成功延续比例', '[事实] 固定昨日非ST、非退市涨停名单，按同股身份追踪；晋级须当日涨停且高度加一。缺行情或高度冲突保留待核，资格变化另列。', tableWith('#s3', ['日期','层级','晋级/分母']), () => {
    if (!ladder || !['complete','partial'].includes(ladder.status)) return unavailable(ladder);
    if (ladder.date !== date || !Array.isArray(ladder.records)) return unavailable({status:'source_failed',reason:'梯队日期或逐股记录不符'});
    const box = el('div', 'rv-ladder'); box.dataset.visualStatus = ladder.status === 'partial' ? 'partial' : 'ready';
    box.append(el('p', 'rv-statline', ladder.previous_date + ' → ' + date + ' · 固定样本 ' + ladder.sample + '只' + (ladder.status==='partial'?' · 部分待核':'')));
    const names = {promoted:'晋级',broken:'断板',unknown:'待核',excluded:'资格变化'};
    ladder.groups.forEach(group => {
      const row = el('div', 'rv-ladder-row'); row.dataset.height = group.height;
      const start = el('div', 'rv-flow-node'); start.append(el('span','', '昨日' + group.height + '板'), el('strong','', group.total + '只'));
      const outcomes = el('div', 'rv-flow-outcomes');
      Object.keys(names).forEach(key => {
        const cell = el('div', 'rv-flow-outcome rv-flow-' + key); cell.dataset.outcome = key; cell.dataset.count = group[key];
        cell.append(el('span', '', names[key]), el('strong', '', group[key] + '只')); outcomes.append(cell);
      }); row.append(start, el('span','rv-flow-arrow','→'), outcomes);
      const detail = el('details', 'rv-subdetail');
      const summary=el('summary','rv-promotion');summary.dataset.height=group.height;
      const ratio=group.total>0?100*group.promoted/group.total:null;
      summary.append(el('span','',group.height+'进'+(group.height+1)));
      const track=el('span','rv-promotion-track');const fill=el('i');fill.style.width=(ratio||0)+'%';track.append(fill);
      summary.append(track,el('strong','',group.promoted+'/'+group.total+' · '+(ratio===null?'未计算':ratio.toFixed(1)+'%')));
      if(group.unknown||group.excluded) summary.append(el('small','rv-note','待核'+group.unknown+' · 资格变化'+group.excluded));
      detail.append(summary,row);
      const list = el('ul', 'rv-identity-list');
      ladder.records.filter(r => r.previous_height === group.height).forEach(r => list.append(el('li','', r.name + ' ' + r.code + ' · ' + names[r.outcome] + (r.current_height ? '（今日'+r.current_height+'板）' : '') + ' · ' + r.reason)));
      detail.append(list); box.append(detail);
    });
    box.append(el('p','rv-note','点击各梯队查看名单。未晋级不等于下跌；今日新首板不计入分母。'));
    return box;
  });
  const emotionSources=qa('#s3 > details.evidence[data-as-of] > .evidence-body > p').map(p=>({p,data:emotionComparison(text(p),p.closest('details').dataset.asOf,date,identity.ladder?.previous_date)})).filter(x=>x.data);
  if(emotionSources.length===1){
    const {p,data}=emotionSources[0],card=el('figure','rv-card rv-emotion-comparison');card.dataset.emotionComparison='v1';
    card.append(el('figcaption','rv-title','情绪变化：前一交易日 → 今日'),el('p','rv-note','[事实] '+data.previousDate+' → '+data.factDate+' · 空心＝前日，实心＝今日；每项独立刻度。'));
    const grid=el('div','rv-comparison-grid');
    data.rows.forEach(r=>{const cell=el('div','rv-comparison-cell');cell.dataset.metric=r.label;cell.dataset.before=r.before;cell.dataset.after=r.after;
      cell.append(el('span','',r.label),el('strong','',r.before+' → '+r.after+r.unit));
      const bound=Math.max(1,r.before,r.after);
      [r.before,r.after].forEach((value,i)=>{const track=el('div','rv-compare-track');const bar=el('i',i?'rv-compare-today':'rv-compare-before');bar.style.width=100*value/bound+'%';track.append(bar);cell.append(track);});grid.append(cell);
    });card.append(grid);sourceLink(card,p);
    const anchor=q('#claim-emotion');if(anchor)anchor.before(card);else q('#s3').append(card);
    // Only fold the duplicate factual list after the replacement chart exists.
    const list=anchor?.nextElementSibling;
    if(list?.tagName==='UL'&&Array.from(list.children).every(li=>text(li).startsWith('[事实]'))){const detail=el('details','rv-subdetail');detail.append(el('summary','','查看变化与晋级原始文字'));list.before(detail);detail.append(list);}
  }
  const style = tableWith('#s4', ['组别', '样本', '开盘均值', '中位', '高开率']);
  const labels = {capacity_top10: '容量前10', first_board_10cm: '10cm 首板', first_board_20cm: '20cm 首板', first_board_30cm: '30cm 首板', second_board: '二板', third_board_plus: '三板及以上'};
  panel('s4', 'style-feedback', '不同风格的隔夜承接', '[事实] ● 开盘均值 / ◇ 开盘中位数；共享零轴。样本少的组仅作样本描述，开盘反馈不是收盘收益。', style, () => {
    const archived = identity.style_feedback;
    const useTable = date && style && style.closest('details')?.dataset.asOf === date;
    if (!useTable && (!date || !archived || archived.date !== date || !['complete','partial'].includes(archived.status))) return unavailable(archived);
    const items = useTable ? rows(style).map(({cells}) => ({label: labels[cells[0]] || cells[0], n: num(cells[1]), mean: num(cells[2]), median: num(cells[3])})) : archived.rows.map(r => ({...r, label: labels[r.key] || r.key}));
    if (!items.some(r => r.mean != null || r.median != null)) return null;
    const bounds = scale(items.flatMap(r => [r.mean, r.median]), true);
    const box = el('div', 'rv-dots'); if (items.some(r => r.mean == null || r.median == null)) box.dataset.visualStatus = 'partial';
    box.append(el('p', 'rv-note', useTable ? text(q('caption', style)) : archived.note+' · 来源：'+archived.source));
    const axis = el('div', 'rv-axis'); axis.append(el('span', '', fmt(bounds.min, 2) + '%'), el('span', '', '0'), el('span', '', fmt(bounds.max, 2) + '%')); box.append(axis);
    items.forEach(r => {
      const row = el('div', 'rv-dot-row'); row.append(el('span', 'rv-label', r.label + ' · n=' + fmt(r.n, 0)));
      const track = el('div', 'rv-track'); const zero = el('i', 'rv-zero'); zero.style.left = '50%'; track.append(zero);
      [['mean', '●'], ['median', '◇']].forEach(([key, symbol]) => {
        if (r[key] == null) return;
        const dot = el('span', 'rv-dot rv-dot-' + key, symbol);
        dot.style.left = 100 * (r[key] - bounds.min) / (bounds.max - bounds.min) + '%'; track.append(dot);
      });
      row.append(track, el('strong', 'rv-number', fmt(r.mean, 2, true) + '% / ' + fmt(r.median, 2, true) + '%')); box.append(row);
    }); return box;
  });
  const institutionTables = qa('#s4 table[data-institution-flow=v1]');
  const institution = institutionTables[0];
  panel('s4', 'institution-flow', '机构席位：净买与净卖', '[事实] 仅展示机构主表已列股票，不代表全机构合计。“机构专用”是席位标签，不是可识别的机构家数。', institution, () => {
    const data = institutionFlow({...institution?.dataset, sourceCount: institutionTables.length},
      institution ? qa('thead th', institution).map(text) : [], rows(institution).map(r => r.cells), date);
    const box = el('div', 'rv-institution'); box.dataset.visualStatus = data.status;
    const body = el('div'), detail = el('div', 'rv-picked-detail');
    detail.setAttribute('aria-live', 'polite'); detail.setAttribute('aria-atomic', 'true');
    const describe = r => r.name + '（' + r.code + '） · 机构买入 ' + fmt(r.buy, 3) + ' 亿 / 卖出 ' + fmt(r.sell, 3) +
      ' 亿 / 净额 ' + fmt(r.net, 3, true) + ' 亿；当日涨跌 ' + fmt(r.change, 2, true) + '% / 换手 ' +
      fmt(r.turnover, 2) + '% / 成交 ' + fmt(r.amount, 3) + ' 亿。';
    const draw = segment => {
      const selected = data.groups[segment]; body.replaceChildren();
      body.append(el('p', 'rv-statline', segment === 'single' ? '单日榜 · 机构净额（亿元）' : '多日累计榜 · 与单日榜分开阅读，不可相加'));
      detail.textContent = '悬停、点选或用键盘选择股票，查看机构买入、卖出和净额。';
      const chart = bars(selected.map(r => ({label: r.name + ' ' + r.code, value: r.net})), {unit: '亿', digits: 3});
      if (chart) {
        qa('.rv-bar-row', chart).forEach((row, i) => {
          const r = selected[i]; row.tabIndex = 0; row.setAttribute('role', 'button');
          row.dataset.stockCode = r.code; row.dataset.segment = segment;
          row.title = describe(r); row.setAttribute('aria-label', describe(r));
          const show = () => {detail.textContent = describe(r);};
          row.addEventListener('mouseenter', show); row.addEventListener('focus', show); row.addEventListener('click', show);
          row.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); show();}});
        });
        body.append(chart);
      } else {
        body.append(el('p', 'rv-empty', '本榜段净额未计算或没有可核验行；不记为零。'));
        if (selected.length) {
          const list = el('ul', 'rv-institution-missing');
          selected.forEach(r => list.append(el('li', '', describe(r)))); body.append(list);
        }
      }
      if (selected.some(r => r.incomplete)) body.append(el('p', 'rv-note', '部分字段未计算；仅绘制原表明确给出的净额，不根据买卖单侧金额补算。'));
    };
    box.append(choices([['single', '单日榜'], ['multi', '多日累计榜']], 'single', draw), body, detail);
    if (data.issues.length) box.append(el('p', 'rv-note', '未绘制的歧义或缺口：' + [...new Set(data.issues)].join('；')));
    draw('single'); return box;
  });
  const capacity = q('table[data-capacity-health=v1]');
  panel('s5', 'capacity', '容量核心健康矩阵', '[事实] 前复权收盘与均线比较；↑ 线上 / ↓ 线下 / — 未计算。显示值相等用≈待核原值，不断言精确贴线。Top50 为近5日入榜次数，不是趋势分。', capacity, () => {
    if (!valid(capacity)) return null;
    const records = rows(capacity).filter(r => r.node.dataset.tier === 'core').map(({node, cells}) => {
      const column = capacityColumn(qa('thead th', capacity).map(text));
      const {close, averages} = capacityValues(cells[column]);
      const pct = capacityChange(cells[5]);
      return [cells[2].includes(node.dataset.code) ? cells[2] : cells[2] + ' ' + (node.dataset.code || '代码待核'), {text: pct == null ? '未计算' : fmt(pct, 2, true) + '%', state: compare(pct, 0)},
        ...Array.from({length: 6}, (_, i) => {
          const cell = stateCell(compareDisplayed(close, averages[i] ?? null));
          if (cell.state === 'missing' && /历史不足/.test(cells.join(' '))) cell.text = '— 历史不足';
          return cell;
        }),
        {text: cells[4], state: 'neutral'}];
    });
    const chart = matrix(['股票', '今日涨跌', 'MA5', 'MA10', 'MA20', 'MA60', 'MA144', 'MA233', 'Top50'], records);
    if (!chart) return null;
    if (records.some(r => r.some(c => c?.state === 'missing'))) chart.dataset.visualStatus = 'partial';
    const reasons = rows(capacity).filter(r => r.node.dataset.tier === 'core' && /历史不足/.test(r.cells.join(' ')))
      .map(r => r.cells[2] + '：' + r.cells.filter(c => /历史不足/.test(c)).join('；'));
    if (reasons.length) chart.append(el('p','rv-note',reasons.join('；')));
    return chart;
  });
  const highs = q('table[data-new-high-structure=v1]');
  panel('s5', 'new-highs', '滚动新高：短窗与长窗', '[事实] 前复权盘中高点突破此前窗口最高值；三个窗口分别统计、名单重叠，不能相加。', highs, () => {
    if (!valid(highs) || !highs.dataset.prevAsOf || highs.dataset.prevAsOf >= date) return null;
    const records = [60, 120, 250].flatMap(w => [
      {label: w + '日 · ' + highs.dataset.prevAsOf.slice(5), value: num(highs.getAttribute('data-prev-' + w + '-count')), tone: 'rv-previous'},
      {label: w + '日 · ' + date.slice(5), value: num(highs.getAttribute('data-current-' + w + '-count'))}
    ]);
    return bars(records, {unit: '只', signed: false, digits: 0});
  });
  const highFlow = identity.new_high;
  panel('s5', 'new-high-flow', '新高名单：延续、退出与新增', '[事实] 同一窗口、同一股票身份的双日交集；退出仅指今日没有再次创新高，不代表退出某个交易池或趋势已经破坏。', highs, () => {
    if (!highFlow || highFlow.status !== 'complete') return unavailable(highFlow);
    if (!valid(highs) || highFlow.date !== date || highFlow.previous_date !== highs.dataset.prevAsOf) return unavailable({status:'source_failed',reason:'新高来源或双日日期不符'});
    if (['60','120','250'].some(w => !highFlow.windows[w] || highFlow.windows[w].previous.length !== num(highs.getAttribute('data-prev-'+w+'-count')) || highFlow.windows[w].current.length !== num(highs.getAttribute('data-current-'+w+'-count')))) return null;
    const box = el('div'), content = el('div', 'rv-high-flow');
    if (highFlow.name_status !== 'complete') box.dataset.visualStatus = 'partial';
    function draw(window) {
      content.replaceChildren(); content.dataset.window = window;
      const data = highFlow.windows[window];
      if (!data || data.previous.length !== num(highs.getAttribute('data-prev-'+window+'-count')) || data.current.length !== num(highs.getAttribute('data-current-'+window+'-count'))) {
        content.append(el('p','rv-empty','名单与正式计数不一致，未绘制。')); return;
      }
      content.append(el('p','rv-statline', window+'日窗口 · '+highFlow.previous_date+' → '+date));
      const nodes = el('div','rv-flow-nodes');
      [['昨日新高',data.previous.length,'previous'],['连续两日新高',data.retained.length,'retained'],['今日新高',data.current.length,'current']].forEach(([label,count,key], i) => {
        if(i) nodes.append(el('span','rv-flow-arrow','→'));
        const node=el('div','rv-flow-node rv-flow-'+key); node.dataset.flow = key; node.dataset.count = count;
        node.append(el('span','',label),el('strong','',count+'只')); nodes.append(node);
      }); content.append(nodes);
      const branches = el('div','rv-flow-branches');
      [['昨日名单中退出',data.exited,'exited'],['今日名单中新出现',data.added,'added']].forEach(([label,codes,key]) => {
        const node=el('div','rv-flow-node rv-flow-'+key); node.dataset.flow=key;node.dataset.count=codes.length;
        node.append(el('span','',label),el('strong','',codes.length+'只')); branches.append(node);
      }); content.append(branches);
      content.append(el('p','rv-note','昨日 = 延续 + 退出；今日 = 延续 + 新增。节点面积与箭头宽度不编码数量。'));
      content.append(codeList('延续名单',data.retained,highFlow.names),codeList('退出名单',data.exited,highFlow.names),codeList('新增名单',data.added,highFlow.names));
      content.append(el('p','rv-note','股票名称使用'+(highFlow.name_as_of || date)+'成员快照；'+(highFlow.name_status==='complete'?'名称覆盖完整。':(highFlow.name_reason || '名称未核实，保留代码。'))));
    }
    box.append(choices([['60','60日窗口'],['120','120日窗口'],['250','250日窗口']], '60', draw), content); draw('60'); return box;
  });
  const events = q('table[data-event-window=v1]');
  panel('s6', 'calendar', '未来七日事件时间轴', '[事实] 沿用来源日历展示的每日最多3项；时间按原归档保留。计划与报道不代表已发生，发布后仍需核验。', events, () => {
    if (!events || events.dataset.asOf !== reportDate || !['complete','partial','ok'].includes(events.dataset.sourceStatus)) return null;
    const list = el('ol', 'rv-timeline');
    rows(events).forEach(({node, cells}) => {
      const item = el('li', 'rv-event ' + (node.dataset.marketStatus === 'closed' ? 'rv-closed' : ''));
      item.append(el('strong', '', cells[0] + ' · ' + cells[1]));
      const content = el('div');
      node.cells[2].innerHTML.split(/<br\s*\/?\s*>/i).forEach(fragment => {
        const holder = document.createElement('div'); holder.innerHTML = fragment;
        content.append(el('p', '', text(holder).replace(/^\[事实\]\s*/, '')));
      });
      item.append(content, el('small', '', '待发布 / 待核验')); list.append(item);
    }); return list.children.length ? list : null;
  });
  const plan = q('#s8');
  panel('s8', 'checks', '下一开放日：验证条件', '[判断] 沿用本节核对清单；尚未发生的结果统一保留待验证，不自动勾选或合成分数。', plan, () => {
    if (!plan) return null;
    const items = qa(':scope > ul > li, :scope > ol > li', plan);
    if (!items.length) return null;
    const box = el('div', 'rv-checks');
    items.forEach((item, i) => {const c = el('div', 'rv-check'); c.append(el('span', 'rv-step', String(i + 1).padStart(2, '0')), el('p', '', text(item)), el('strong', '', '○ 待验证')); box.append(c);});
    return box;
  });
  panel('ops', 'coverage', '数据覆盖状态', '[事实] 下列为报告已标注来源状态的分项，不计算综合完整率；不同来源的样本不可相加。', q('#ops'), () => {
    const specs = [
      ['分钟结构', 'table[data-index-intraday-structure=v1]'], ['容量排名', 'table[data-capacity-health=v1]'],
      ['滚动新高', 'table[data-new-high-structure=v1]'], ['跨资产', 'table[data-cross-asset-context]'],
      ['增量资金', '[data-incremental-capital=verdict], [data-incremental-capital=missing-data]'],
      ['低价股', '[data-style-low-price-effect]'], ['事件日历', 'table[data-event-window=v1]'],
      ['板块风险', '[data-sector-adjustment-risk]']
    ];
    const states = {complete: '完整', ok: '完整', partial: '部分缺失', source_failed: '来源失败', 'missing-data': '未提供', skipped: '跳过'};
    const grid = el('div', 'rv-coverage');
    specs.forEach(([name, selector]) => {
      const e = q(selector); const state = e?.dataset.sourceStatus || 'missing-data';
      const stale = e?.dataset.asOf && e.dataset.asOf !== reportDate;
      const card = el('div', 'rv-coverage-item ' + (['complete', 'ok'].includes(state) && !stale ? 'rv-above' : 'rv-missing'));
      card.append(el('span', '', name), el('strong', '', stale ? '历史 · ' + e.dataset.asOf : states[state] || state));
      sourceLink(card, e); grid.append(card);
    });
    const funding = qa('#ops > p').find(e => /ETF/.test(text(e)) && /融资|两融/.test(text(e)));
    if (funding) grid.append(el('p', 'rv-empty', text(funding)));
    if (gaps.length) grid.append(el('p', 'rv-empty', '图形数据缺口：' + gaps.join('、') + '。原文证据仍完整保留。'));
    return grid;
  });
})();
