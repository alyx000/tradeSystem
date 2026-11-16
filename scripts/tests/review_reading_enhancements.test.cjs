const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const {JSDOM} = require(require.resolve('jsdom', {paths: [path.join(root, 'web')]}));
const api = require(path.join(root, '.agents/skills/daily-review/references/html-report-template/review_reading.js'));
const dom = body => new JSDOM(`<article id="report-document">${body}</article>`);
const table = (headers, rows) => `<table><thead><tr>${headers.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(x=>`<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const point = (date, value, status='ok', more={}) => ({date, status, values:{v:value}, ...more});

test('condition states remain independent, composite and unknown never become failure', () => {
  const h=['ID／来源日','原条件','事实','条件结果'];
  const rows=[['A','条件','事实','部分满足'],['B','条件','事实','待定义'],['B','条件2','事实','待核验／不作单日输赢'],['C','条件','事实','未满足']];
  const before=JSON.stringify(rows), model=api.conditionModel(h,rows,'past');
  assert.deepEqual(model.map(r=>r.group),['部分满足','待定义','复合或其他状态','未满足']);
  assert.equal(model[2].duplicate,true);
  assert.equal(JSON.stringify(rows),before);
  assert.equal(api.conditionModel(['股票','状态'],[['股票','未满足']],'past'),null);
  assert.equal(api.conditionModel(h,[['A','短行']],'past'),null);
});

test('two sources keep separate counts; all original fields survive; source reveal works', () => {
  const h=['ID／来源日','原条件','事实','条件结果'];
  const d=dom(`<section id="s0"><h2>验证</h2><details><summary>系统条件</summary>${table(h,[['N1/11-15','原条件','事实','未满足']])}</details><details><summary>老师条件</summary>${table(h,[['T1/11-15','老师原条件','事实','待定义']])}</details></section>`);
  const doc=d.window.document,before=[...doc.querySelectorAll('table')].map(t=>t.innerHTML);
  api.enhance(doc);api.enhance(doc);
  assert.equal(doc.querySelectorAll('[data-reading-conditions]').length,1);
  assert.equal(doc.querySelectorAll('.reading-source-group').length,2);
  assert.equal(doc.querySelectorAll('.reading-condition-card').length,2);
  assert.deepEqual([...doc.querySelectorAll('table')].map(t=>t.innerHTML),before);
  assert.ok(doc.querySelector('.reading-condition-card').textContent.includes('原条件'));
  doc.querySelector('[data-reading-reveal]').click();
  assert.equal(doc.querySelector('#s0>details').open,true);
});

test('future conditions retain exact timing and expiry, no inferred success', () => {
  const h=['ID','对象／日期','原条件','验证时点','满足标准','不满足／失效','缺口'];
  const rows=[['N1','11-17指数','原条件','收盘','收盘>MA5','收盘≤MA5','无'],['T1','原期限','中期条件','事件发布后','发布后验证','未落地待核','时点未知']];
  const d=dom(`<section id="s8"><h2>次日</h2><details><summary>观察</summary>${table(h,rows)}</details></section>`);
  api.enhance(d.window.document);
  assert.equal(d.window.document.querySelectorAll('.reading-condition-card').length,2);
  assert.ok(d.window.document.querySelector('[data-reading-conditions]').textContent.includes('事件发布后'));
  assert.equal(d.window.document.querySelector('[data-reading-conditions]').textContent.includes('已满足'),false);
});

test('impact cards do not assert missing data for complete modules and retain all diagnostics', () => {
  assert.equal(api.gapModel('minutes','complete','18/18'),null);
  const d=dom('<section id="s1"><p data-incremental-capital="verdict" data-source-status="complete">已核验</p><table data-index-intraday-structure="v1" data-source-status="partial" data-row-count="17" data-expected-rows="18"></table></section><section id="ops"><h2>缺口</h2><p>分钟结构缺1组</p><p>其他未映射的重要缺口</p><details><summary>来源</summary>原始来源</details></section>');
  api.enhance(d.window.document);api.enhance(d.window.document);
  assert.equal(d.window.document.querySelectorAll('.reading-gap-card').length,1);
  assert.ok(d.window.document.querySelector('.reading-gap-card').textContent.includes('17/18'));
  assert.ok(d.window.document.querySelector('.reading-gap-original').textContent.includes('其他未映射的重要缺口'));
  assert.ok(d.window.document.getElementById('ops').textContent.includes('原始来源'));
});

test('chart gaps break lines, partial points dash, no interpolation or trailing fallback', () => {
  const points=[point('2026-11-10',1),point('2026-11-11',null),point('2026-11-12',-2,'partial'),point('2026-11-13',0),point('2026-11-16',null)];
  const m=api.chartModel(points,[{key:'v',label:'数值'}]);
  assert.equal(m.lines.length,1);
  assert.equal(m.lines[0].from,'2026-11-12');assert.equal(m.lines[0].to,'2026-11-13');assert.equal(m.lines[0].dashed,true);
  assert.equal(m.dots.length,3);assert.equal(m.dots[2].value,0);
  assert.equal(m.points.at(-1).values.v,null);
  assert.equal(api.chartModel([point('2026-02-31',1)],[{key:'v'}]),null);
  assert.equal(api.chartModel([point('2026-11-10',1),point('2026-11-10',2)],[{key:'v'}]),null);
});

test('source changes preserve dashed FX connections and pool line breaks; 50% reference', () => {
  let p=[point('2026-11-12',6.5,'ok',{source:'报价'}),point('2026-11-13',6.6,'ok',{source:'成交'})];
  assert.equal(api.chartModel(p,[{key:'v'}],{zero:false}).lines[0].dashed,true);
  p[1].breakBefore={v:true};assert.equal(api.chartModel(p,[{key:'v'}]).lines.length,0);
  const r=api.chartModel([point('2026-11-12',0),point('2026-11-13',70)],[{key:'v'}],{rate:true});
  assert.deepEqual([r.lo,r.hi,r.reference],[0,100,50]);
});

test('mobile charts keep exact dates, independent rate axis and touch-accessible missing last date', () => {
  const h=['交易日','状态','样本/分母','开盘均值（%）','收盘均值（%）','收盘中位（%）','收涨率（%）'];
  const rows=[['2026-11-13','ok','20/20','1','2','1.5','60'],['2026-11-16','missing-data','—/20','—','—','—','—']];
  const d=dom(`<figure data-top20-feedback-trend="v1" data-as-of="2026-11-16"><div class="feedback-plots"><svg viewBox="0 0 920 270"><circle cx="2"/></svg></div><details><summary>数据</summary>${table(h,rows)}</details></figure>`);
  const doc=d.window.document, original=doc.querySelector('svg').innerHTML;
  api.enhance(doc);api.enhance(doc);
  assert.equal(doc.querySelectorAll('[data-reading-mobile]').length,1);
  assert.equal(doc.querySelectorAll('.reading-mobile-panel').length,4);
  assert.equal(doc.querySelector('.reading-desktop-plot svg').innerHTML,original);
  const select=doc.querySelector('[data-reading-date]');
  assert.equal(select.value,'1');
  assert.ok(doc.querySelector('.reading-date-values p:not([hidden])').textContent.includes('未计算'));
  select.value='0';select.dispatchEvent(new d.window.Event('change'));
  assert.ok(doc.querySelector('.reading-date-values p:not([hidden])').textContent.includes('开盘均值（%） 1%'));
  const saved=new JSDOM(doc.documentElement.outerHTML);api.enhance(saved.window.document);
  assert.equal(saved.window.document.querySelectorAll('[data-reading-mobile]').length,1);
});

test('missing inline source retains original chart; corrupt rows cannot silently produce mobile data', () => {
  const d=dom('<figure data-top20-feedback-trend="v1"><svg viewBox="0 0 920 270"></svg></figure>');
  api.enhance(d.window.document);
  assert.equal(d.window.document.querySelectorAll('.reading-desktop-plot').length,0);
  assert.equal(d.window.document.querySelectorAll('[data-reading-mobile]').length,0);
  assert.ok(d.window.document.querySelector('[data-reading-mobile-fallback]'));
});

test('template summary distinguishes partial work from genuinely missing answers', () => {
  const states=['partial','partial','pending','user-input','missing-data'];
  const d=dom(`<div data-review-template="relay"><h3>模板</h3><p>已整理0/5</p>${states.map((s,i)=>`<div data-template-field="relay.${i}" data-source-status="${s}">原答案${i}</div>`).join('')}</div>`);
  api.enhance(d.window.document);
  const summary=d.window.document.querySelector('[data-reading-template-summary]');
  assert.ok(summary.textContent.includes('完整 0/5'));
  assert.ok(summary.textContent.includes('部分整理 2 项'));
  assert.ok(summary.textContent.includes('待验证 1 项'));
  assert.ok(summary.textContent.includes('待填写 1 项'));
  assert.ok(summary.textContent.includes('缺少资料 1 项'));
  assert.equal(summary.getAttribute('data-reading-template-summary'),'已整理0/5');
});

test('low-price charts share series colors and dashes, reject partial points rather than fill zero', () => {
  const h=['日期','≤5元：n／中位%／上涨%','>5且≤10元：n／中位%／上涨%','全市场：n／中位%／上涨%','状态／来源'];
  const rows=[['2026-11-13','700／-1／40','1300／-2／30','5300／-3／20','complete／source'],['2026-11-16','700／-1／40','1300／-2／30','5300／-3／20','partial／source']];
  const d=dom(`<div data-low-price-trend="v1" data-as-of="2026-11-16"><img src="data:image/png;base64,AA==">${table(h,rows)}</div>`);
  const host=d.window.document.querySelector('[data-low-price-trend]'),data=api.chartData(host,'low');
  assert.equal(data.points[1].values.m1,null);assert.equal(data.points[1].values.a1,null);
  assert.deepEqual(data.panels[0].series.map(s=>[s.color,s.dash]),data.panels[1].series.map(s=>[s.color,s.dash]));
  api.enhance(d.window.document);
  assert.equal(d.window.document.querySelectorAll('.reading-mobile-panel').length,2);
});

test('FX keeps trade squares/dashes, independent spot source and common change baseline', () => {
  const h=['来源日','即期中值','1Y 全价','掉期点','掉期来源'];
  const rows=[['2026-11-11','6','—','—','缺失'],['2026-11-12','6','5','-100','报价；历史补采 2026-11-17'],['2026-11-13','6.06','5.1','-110','交易'],['2026-11-16','6.12','5.2','-120','交易']];
  const d=dom(`<div class="fx-chart"><svg></svg>${table(h,rows)}</div>`);
  const data=api.chartData(d.window.document.querySelector('.fx-chart'),'fx');
  assert.equal(data.panels.length,3);
  assert.equal(data.points[0].values.changeSpot,null);
  assert.equal(data.points[1].values.changeSpot,0);
  assert.ok(Math.abs(data.points[2].values.changeForward-2)<1e-10);
  assert.ok(data.panels[2].note.includes('2026-11-12'));
  const model=api.chartModel(data.points,data.panels[0].series,data.panels[0].options);
  assert.ok(model.lines.filter(l=>l.key==='spot').every(l=>!l.dashed));
  assert.ok(model.lines.filter(l=>l.key==='forward').every(l=>l.dashed));
  assert.equal(model.dots.filter(l=>l.key==='forward'&&l.trade).length,2);
  assert.ok(api.svgChart(model,'外汇','元').includes('<rect data-reading-point="forward"'));
});

test('FX readouts preserve quote precision and diagnose missing legs per panel', () => {
  const h=['来源日','即期中值','1Y 全价','掉期点','掉期来源'];
  const rows=[['2026-11-12','6.66710','6.4619','-2058.99','报价'],['2026-11-13','6.67020','—','—','缺失'],['2026-11-16','—','—','—','缺失']];
  const d=dom(`<div class="fx-chart"><svg></svg>${table(h,rows)}</div>`);api.enhance(d.window.document);
  const panels=[...d.window.document.querySelectorAll('.reading-mobile-panel')];
  assert.ok(panels[0].querySelector('[data-reading-value="0"]').textContent.includes('6.66710人民币/美元'));
  assert.ok(panels[0].querySelector('[data-reading-value="0"]').textContent.includes('6.4619人民币/美元'));
  assert.ok(panels[1].querySelector('[data-reading-value="0"]').textContent.includes('-2058.99Pips'));
  assert.ok(panels[0].querySelector('[data-reading-value="1"]').textContent.endsWith('部分覆盖'));
  assert.ok(panels[1].querySelector('[data-reading-value="1"]').textContent.endsWith('缺少数值'));
  for(const p of panels)assert.ok(p.querySelector('[data-reading-value="2"]').textContent.endsWith('缺少数值'));
  const noBase=dom(`<div class="fx-chart"><svg></svg>${table(h,[rows[1],rows[2]])}</div>`);api.enhance(noBase.window.document);
  assert.ok(noBase.window.document.querySelectorAll('.reading-mobile-panel')[2].querySelector('[data-reading-value="1"]').textContent.endsWith('未计算'));
});

test('narrow print restores original plots with at least mobile selector specificity', () => {
  const css=require('node:fs').readFileSync(path.join(root,'.agents/skills/daily-review/references/html-report-template/review_reading.css'),'utf8');
  const d=dom('<style>'+css+'</style><div class="report-document"><svg class="reading-desktop-plot"></svg><div class="reading-mobile-charts"></div></div>');
  const rules=[...d.window.document.styleSheets[0].cssRules];
  function display(element,print){
    let winner=null;
    for(const media of rules){
      if(!media.cssRules||!(media.conditionText==='(max-width:700px)'||(print&&media.conditionText==='print')))continue;
      for(const rule of media.cssRules){
        if(!rule.selectorText||!element.matches(rule.selectorText)||!rule.style.getPropertyValue('display'))continue;
        const score=(rule.style.getPropertyPriority('display')==='important'?1000:0)+(rule.selectorText.match(/\./g)||[]).length;
        if(!winner||score>=winner.score)winner={score,value:rule.style.getPropertyValue('display')};
      }
    }
    return winner.value;
  }
  const doc=d.window.document;
  assert.equal(display(doc.querySelector('.reading-desktop-plot'),false),'none');
  assert.equal(display(doc.querySelector('.reading-desktop-plot'),true),'block');
  assert.equal(display(doc.querySelector('.reading-mobile-charts'),true),'none');
});
