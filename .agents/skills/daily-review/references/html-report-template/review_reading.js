/* Offline projections of the report's own evidence. No requests or scoring. */
(function(root, factory){
  var api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root&&root.document){root.ReviewReading=api;api.enhance(root.document);}
})(typeof window==='undefined'?null:window,function(){
  'use strict';
  var COLORS=['#247f76','#5376b7','#aa6848','#8b63b5'];
  var STATES={'满足':'已满足','已满足':'已满足','未满足':'未满足','部分满足':'部分满足',
    '待定义':'待定义','待核验':'待核验','不适用':'不适用'};
  var STATUS_ORDER=['已满足','未满足','部分满足','待定义','待核验','不适用','复合或其他状态'];
  function text(el){return el?el.textContent.trim().replace(/\s+/g,' '):'';}
  function esc(s){return String(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function number(s){
    var v=String(s==null?'':s).trim().replace(/−/g,'-');
    return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(v)&&Number.isFinite(Number(v))?Number(v):null;
  }
  function conditionStatus(s){return STATES[String(s).trim()]||'复合或其他状态';}
  function conditionModel(headers, rows, mode){
    var status=headers.findIndex(function(h){return ['条件结果','结果','状态'].includes(h);});
    var time=headers.indexOf('验证时点');
    if(!headers.length||!/^ID(?:$|[／/])/.test(headers[0])||
       !headers.some(function(h){return /原条件|原话条件/.test(h);})||
       (mode==='past'?status<0:time<0))return null;
    if(rows.some(function(r){return r.length!==headers.length||!r[0];}))return null;
    var seen=new Set();
    return rows.map(function(row,index){
      // Duplicate IDs may describe distinct scope/expiry rows: keep them, never deduplicate.
      var duplicate=seen.has(row[0]);seen.add(row[0]);
      var group=mode==='past'?conditionStatus(row[status]):row[time]||'时点待定义';
      return {id:row[0],group:group,status:mode==='past'?row[status]:'尚待验证',
        fields:headers.map(function(h,i){return [h,row[i]];}),index:index,duplicate:duplicate};
    });
  }
  function element(doc,tag,className,value){
    var e=doc.createElement(tag);if(className)e.className=className;if(value!=null)e.textContent=value;return e;
  }
  function sourceTable(table){
    var head=table.querySelector('thead tr')||table.rows[0];
    if(!head)return null;
    return {headers:Array.from(head.cells).map(text),rows:Array.from(table.rows).filter(function(r){return r!==head;}).map(function(r){return Array.from(r.cells).map(text);})};
  }
  function evidenceTitle(table,index){
    var d=table.closest('details');var summary=d&&d.querySelector('summary');
    return text(summary)||'条件来源组 '+(index+1);
  }
  function addConditions(doc,id,mode){
    var section=doc.getElementById(id);if(!section||section.querySelector('[data-reading-conditions]'))return;
    var tables=Array.from(section.querySelectorAll('table')), box=element(doc,'div','reading-conditions');
    box.setAttribute('data-reading-conditions',mode);
    box.appendChild(element(doc,'p','reading-help',mode==='past'?
      '按原表分别汇总条件结果，不合并系统与老师样本、不计算胜率。待定义和待核验不算未满足；复合状态保留原文。':
      '按原验证时点分组，保留条件ID、观察日期、满足与失效标准。以下均为待观察事项，不代表已发生。'));
    var groups=0;
    tables.forEach(function(t,ti){
      var data=sourceTable(t), model=data&&conditionModel(data.headers,data.rows,mode);if(!model||!model.length)return;
      groups++;
      var group=element(doc,'div','reading-source-group');
      group.appendChild(element(doc,'h3',null,evidenceTitle(t,ti)));
      var by={};model.forEach(function(r){(by[r.group]||(by[r.group]=[])).push(r);});
      var keys=mode==='past'?STATUS_ORDER.filter(function(k){return by[k];}):Object.keys(by);
      var grid=element(doc,'div','reading-condition-grid');
      keys.forEach(function(k){
        var d=element(doc,'details','reading-condition-bucket'),s=element(doc,'summary',null,k+' · '+by[k].length+'项');
        d.setAttribute('data-reading-status',k);d.appendChild(s);
        by[k].forEach(function(r){
          var card=element(doc,'div','reading-condition-card');
          card.appendChild(element(doc,'strong',null,r.id));
          var dl=element(doc,'dl');
          r.fields.slice(1).forEach(function(pair){dl.appendChild(element(doc,'dt',null,pair[0]));dl.appendChild(element(doc,'dd',null,pair[1]||'原表未填写'));});
          card.appendChild(dl);
          var b=element(doc,'button','reading-source-button','查看原表与来源');b.type='button';
          b.setAttribute('data-reading-reveal',id+'|'+ti+'|'+r.index);card.appendChild(b);d.appendChild(card);
        });grid.appendChild(d);
      });group.appendChild(grid);box.appendChild(group);
    });
    if(groups){var heading=section.querySelector('h2');heading.insertAdjacentElement('afterend',box);}
  }
  function gapModel(kind,status,info){
    if(!['partial','source_failed','missing-data','missing','not_computed','pending','unavailable'].includes(status))return null;
    var defs={
      capital:['资金渠道','渠道来源或同日比较尚未齐备','暂不能确认增量资金来源；成交额变化本身不能替代渠道证据。'],
      minutes:['指数分钟结构','分钟结构覆盖不足','仅能判断已核验的指数与周期，不能把缺失周期解释为没有风险。'],
      sector:['板块调整风险','部分板块或周期未核验','已取得的预警仍保留；未出现信号不等于全部板块无风险。'],
      core:['情绪核心反馈','历史或当日行情存在缺口','只解释已核验对象；未知对象不能判成退潮，也不补零。'],
      template:['待补充的复盘条件','部分答案、验证条件或个人信息仍待补充','未填写不等于条件不成立，不能据此统计预测命中率。'],
      teacher:['老师锚点与点位','部分来源、条件或表述尚待核验','保留老师原日期和原条件，不能把待核内容当作今日市场事实。']
    };
    var d=defs[kind];if(!d)return null;
    return {title:d[0],missing:info||d[1],impact:d[2],status:status};
  }
  function addGaps(doc){
    var ops=doc.getElementById('ops');if(!ops||ops.querySelector('[data-reading-gaps]'))return;
    var mappings=[['capital','[data-incremental-capital]'],['minutes','[data-index-intraday-structure]'],
      ['sector','[data-sector-adjustment-risk]'],['core','[data-emotion-leader]'],
      ['template','[data-review-template]'],['teacher','[data-teacher-core-anchors]']];
    var box=element(doc,'div','reading-gap-grid');box.setAttribute('data-reading-gaps','v1');
    mappings.forEach(function(pair){
      var e=doc.querySelector(pair[1]);if(!e)return;
      var info='';
      if(pair[0]==='capital'){
        var section=e.closest('section');
        var labels={etf:'ETF',margin:'融资',institution:'机构席位'};
        var gaps=section?Array.from(section.querySelectorAll('[data-role="gap"][data-source]')):[];
        info=gaps.map(function(g){var source=g.getAttribute('data-source'),day=g.getAttribute('data-as-of');
          return (labels[source]||source)+'：'+({'partial':'部分覆盖','missing-data':'缺少来源','source_failed':'来源失败'}[g.getAttribute('data-source-status')]||'待核验')+(day?'（来源日'+day+'）':'');}).join('；');
      }
      if(pair[0]==='minutes'){
        var n=number(e.getAttribute('data-row-count')),total=number(e.getAttribute('data-expected-rows'));
        if(n!==null&&total!==null&&total>0&&n>=0&&n<=total)info='分钟结构已核验 '+n+'/'+total+' 组。';
      }
      if(pair[0]==='sector'){
        var summary=Array.from(e.children).find(function(c){return c.tagName==='P';});
        info=text(summary).replace(/^\[(?:判断|事实)\]\s*/,'');
      }
      if(pair[0]==='core'){
        var loaded=number(e.getAttribute('data-coverage-loaded')),expected=number(e.getAttribute('data-coverage-expected'));
        if(loaded!==null&&expected!==null)info='历史来源覆盖 '+loaded+'/'+expected+' 日；当日停牌与未解决缺失分别保留在来源中。';
      }
      var m=gapModel(pair[0],e.getAttribute('data-source-status'),info);if(!m)return;
      var card=element(doc,'div','reading-gap-card');card.appendChild(element(doc,'h3',null,m.title));
      card.appendChild(element(doc,'p',null,'缺口：'+m.missing));card.appendChild(element(doc,'p',null,'影响：'+m.impact));
      var section=e.closest('section[id]'),link=element(doc,'a',null,'查看该章节证据');
      if(section){link.href='#'+section.id;card.appendChild(link);}box.appendChild(card);
    });
    if(!box.children.length)return;
    // All original disclosures remain, including gaps not in the impact glossary.
    var originals=Array.from(ops.children).filter(function(e){return ['P','UL','OL'].includes(e.tagName);});
    var full=element(doc,'details','evidence reading-gap-original');
    full.setAttribute('data-items',String(originals.length));
    full.appendChild(element(doc,'summary',null,'完整缺口清单与原说明（'+originals.length+'项）'));
    originals.forEach(function(e){full.appendChild(e);});
    var heading=ops.querySelector('h2');heading.insertAdjacentElement('afterend',box);
    box.insertAdjacentElement('afterend',element(doc,'p','reading-help','上方按主要影响解释；完整缺口、范围和来源仍以下方原说明为准。'));
    if(originals.length)box.parentNode.insertBefore(full,box.nextSibling.nextSibling);
  }
  function addTemplateStatus(doc){
    var labels={'complete':'完整','partial':'部分整理','pending':'待验证','user-input':'待填写','missing-data':'缺少资料','source_failed':'来源失败'};
    doc.querySelectorAll('[data-review-template]').forEach(function(host){
      var summary=Array.from(host.children).find(function(c){return c.tagName==='P';});
      if(!summary||summary.hasAttribute('data-reading-template-summary'))return;
      var fields=Array.from(host.querySelectorAll('[data-template-field]'));
      var expected={sector:11,relay:5}[host.getAttribute('data-review-template')];
      if(fields.length!==expected||new Set(fields.map(function(f){return f.getAttribute('data-template-field');})).size!==expected)return;
      if(fields.some(function(f){return !labels[f.getAttribute('data-source-status')];}))return;
      var counts={};fields.forEach(function(f){var s=f.getAttribute('data-source-status');counts[s]=(counts[s]||0)+1;});
      summary.setAttribute('data-reading-template-summary',summary.textContent);
      summary.textContent='[事实] 完整 '+(counts.complete||0)+'/'+expected+' 项；'+Object.keys(labels).filter(function(s){return s!=='complete'&&counts[s];}).map(function(s){return labels[s]+' '+counts[s]+' 项';}).join('；')+'。待验证、待填写不等于未成立。';
    });
  }
  function validDates(points){
    return points.length>0&&points.every(function(p,i){var d=new Date(p.date+'T00:00:00Z');
      return /^\d{4}-\d{2}-\d{2}$/.test(p.date)&&Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===p.date&&(!i||p.date>points[i-1].date);});
  }
  function chartModel(points,series,options){
    options=options||{};if(!validDates(points))return null;
    var values=[];points.forEach(function(p){series.forEach(function(s){var v=p.values[s.key];if(typeof v==='number'&&Number.isFinite(v))values.push(v);});});
    if(!values.length)return {points:points,series:series,empty:true};
    var lo=options.rate?0:Math.min.apply(null,values.concat(options.zero===false?[]:[0]));
    var hi=options.rate?100:Math.max.apply(null,values.concat(options.zero===false?[]:[0]));
    if(options.domain){lo=options.domain[0];hi=options.domain[1];}
    if(hi===lo){var pad=Math.max(Math.abs(hi)*0.01,1);lo-=pad;hi+=pad;}
    if(options.zero===false&&!options.domain){var margin=(hi-lo)*0.08;lo-=margin;hi+=margin;}
    var x=function(i){return 52+288*i/Math.max(points.length-1,1);};
    var y=function(v){return 170-140*(v-lo)/(hi-lo);};
    var lines=[],dots=[];
    series.forEach(function(s,si){var previous=null;
      points.forEach(function(p,i){var v=p.values[s.key];
        if(typeof v!=='number'||!Number.isFinite(v)){previous=null;return;}
        var source=p.sources?p.sources[s.key]:p.source;
        var dot={date:p.date,key:s.key,x:x(i),y:y(v),value:v,color:s.color||COLORS[si],source:source,trade:/成交|交易/.test(source||''),partial:(p.statuses&&p.statuses[s.key]||p.status)==='partial'};
        if(previous&&!(p.breakBefore&&p.breakBefore[s.key]))lines.push({key:s.key,from:previous.date,to:p.date,x1:previous.x,y1:previous.y,x2:dot.x,y2:dot.y,
          color:dot.color,dashed:previous.partial||dot.partial||dot.trade||!!(source&&previous.source&&source!==previous.source),dash:s.dash||''});
        dots.push(dot);previous=dot;
      });
    });return {points:points,series:series,lo:lo,hi:hi,lines:lines,dots:dots,reference:options.rate?50:(lo<=0&&hi>=0?0:null)};
  }
  function fmt(n){return n==null?'未计算':Math.abs(n)>=1000?n.toFixed(0):Number(n.toFixed(2)).toString();}
  function readingValue(n,series){return n==null?'未计算':series.digits==null?Number(n.toFixed(2)).toString():n.toFixed(series.digits);}
  function panelStatus(point,series){
    if(!point.statuses)return point.status;
    var states=series.map(function(s){return point.statuses[s.key]||point.status;});
    if(states.every(function(s){return s==='ok';}))return 'ok';
    if(states.every(function(s){return s==='not_computed';}))return 'not_computed';
    if(states.every(function(s){return ['missing','not_computed'].includes(s);}))return 'missing';
    return 'partial';
  }
  function svgChart(model,title,unit){
    if(model.empty)return '<p class="reading-help">本窗口无可绘数值，缺失不补零。</p>';
    var y=function(v){return 170-140*(v-model.lo)/(model.hi-model.lo);};
    var out=['<svg viewBox="0 0 360 208" role="img" aria-label="'+esc(title+'，单位'+unit)+'">'];
    var ticks=Array.from(new Set([model.lo,model.hi,model.reference].filter(function(v){return v!==null;}))).sort(function(a,b){return a-b;});
    ticks.forEach(function(v){out.push('<line x1="52" x2="340" y1="'+y(v)+'" y2="'+y(v)+'" stroke="currentColor" opacity=".22"/><text x="47" y="'+(y(v)+4)+'" text-anchor="end">'+esc(fmt(v))+'</text>');});
    model.lines.forEach(function(l){out.push('<line data-reading-line="'+esc(l.key)+'" data-from="'+l.from+'" data-to="'+l.to+'" x1="'+l.x1+'" x2="'+l.x2+'" y1="'+l.y1+'" y2="'+l.y2+'" stroke="'+l.color+'" stroke-width="2"'+((l.dashed||l.dash)?' stroke-dasharray="'+(l.dashed?'4 3':esc(l.dash))+'"':'')+'/>');});
    model.dots.forEach(function(d){out.push('<'+(d.trade?'rect':'circle')+' data-reading-point="'+esc(d.key)+'" data-date="'+d.date+'" '+(d.trade?'x="'+(d.x-2.5)+'" y="'+(d.y-2.5)+'" width="5" height="5"':'cx="'+d.x+'" cy="'+d.y+'" r="2.5"')+' fill="'+(d.partial?'var(--paper,white)':d.color)+'" stroke="'+d.color+'"/>');});
    var dates=model.points;[0,Math.floor((dates.length-1)/2),dates.length-1].filter(function(v,i,a){return a.indexOf(v)===i;}).forEach(function(i){out.push('<text x="'+(52+288*i/Math.max(1,dates.length-1))+'" y="197" text-anchor="middle">'+dates[i].date.slice(5)+'</text>');});
    out.push('</svg>');return out.join('');
  }
  function rowsFor(host){
    var tables=Array.from(host.querySelectorAll('table'));
    for(var i=0;i<tables.length;i++){var d=sourceTable(tables[i]);if(d&&['交易日','来源日','日期'].includes(d.headers[0]))return d;}
    return null;
  }
  function chartData(host,kind){
    var data=rowsFor(host);if(!data)return null;
    var points=[],panels=[],cols=data.headers;
    if(data.rows.some(function(r){return r.length!==cols.length;}))return null;
    if(['top20','board','core'].includes(kind)){
      if(cols.length<7||cols[1]!=='状态'||cols[2]!=='样本/分母')return null;
      points=data.rows.map(function(r){var values={};for(var j=3;j<7;j++)values['v'+j]=['ok','partial'].includes(r[1])?number(r[j]):null;
        return {date:r[0],status:r[1],sample:r[2],values:values};});
      var common=[];points.forEach(function(p){[3,4,5].forEach(function(j){if(p.values['v'+j]!==null)common.push(p.values['v'+j]);});});
      var coreValues=[];points.forEach(function(p){Object.values(p.values).forEach(function(v){if(v!==null)coreValues.push(v);});});
      for(var j=3;j<7;j++)panels.push({title:cols[j],unit:kind==='core'?'只':'%',series:[{key:'v'+j,label:cols[j]}],
        options:kind==='core'?{domain:[0,Math.max(1,...coreValues)]}:j===6?{rate:true}:{domain:[Math.min(0,...common),Math.max(1,...common)]}});
    }else if(kind==='height'){
      if(cols[1]!=='最高连板')return null;
      points=data.rows.map(function(r){var complete=/^完整(?:$|；)/.test(r[2]);return {date:r[0],status:complete?'ok':'missing',sample:r[2],values:{height:complete&&/^\d+板$/.test(r[1])?number(r[1].slice(0,-1)):null}};});
      panels=[{title:'最高连板',unit:'板',series:[{key:'height',label:'最高连板'}]}];
    }else if(kind==='fx'){
      if(cols.join('|')!=='来源日|即期中值|1Y 全价|掉期点|掉期来源')return null;
      points=data.rows.map(function(r){var origin=r[4].split('；')[0];return {date:r[0],status:'ok',statuses:{},sample:r[4],sources:{spot:'即期',forward:origin,swap:origin,changeSpot:'即期',changeForward:origin},values:{spot:number(r[1]),forward:number(r[2]),swap:number(r[3])}};});
      var baseIndex=points.findIndex(function(p){return p.values.spot!==null&&p.values.forward!==null;});
      var base=points[baseIndex];
      points.forEach(function(p,i){p.values.changeSpot=base&&base.values.spot!==0&&i>=baseIndex&&p.values.spot!==null?(p.values.spot/base.values.spot-1)*100:null;
        p.values.changeForward=base&&base.values.forward!==0&&i>=baseIndex&&p.values.forward!==null?(p.values.forward/base.values.forward-1)*100:null;
        Object.keys(p.values).forEach(function(k){p.statuses[k]=p.values[k]!==null?'ok':k.startsWith('change')&&(!base||i<baseIndex||base.values[k==='changeSpot'?'spot':'forward']===0)?'not_computed':'missing';});
        p.status=panelStatus(p,[{key:'spot'},{key:'forward'},{key:'swap'}]);});
      panels=[{title:'即期与1Y全价',unit:'人民币/美元',series:[{key:'spot',label:'即期',digits:5},{key:'forward',label:'1Y全价',digits:4}],options:{zero:false}},
        {title:'1Y掉期点',unit:'Pips',series:[{key:'swap',label:'掉期点',digits:2}],options:{zero:false}},
        {title:'相对共同基准日变化',unit:'%',note:base?'基准日 '+base.date+'；（当日/基准−1）×100%，非投资收益。':'缺少共同有效基准日，未计算。',series:[{key:'changeSpot',label:'即期'},{key:'changeForward',label:'1Y全价'}]}];
    }else if(kind==='low'){
      if(cols.length!==5||!cols[1].includes('≤5元'))return null;
      points=data.rows.map(function(r){var state=r[4].split('／')[0],values={},samples=[];
        for(var j=1;j<=3;j++){var v=r[j].split('／');samples.push(v[0]);values['m'+j]=state==='complete'&&v.length===3?number(v[1]):null;values['a'+j]=state==='complete'&&v.length===3?number(v[2]):null;}
        return {date:r[0],status:state==='complete'?'ok':state,sample:'三组n：'+samples.join(' / '),values:values};});
      var labels=['≤5元','>5且≤10元','全市场'];
      panels=[{title:'涨跌幅中位数',unit:'%',series:labels.map(function(l,i){return {key:'m'+(i+1),label:l,color:COLORS[i],dash:['','5 3','2 3'][i]};})},
        {title:'上涨家数占比',unit:'%',series:labels.map(function(l,i){return {key:'a'+(i+1),label:l,color:COLORS[i],dash:['','5 3','2 3'][i]};}),options:{rate:true}}];
    }else if(kind==='pool'){
      if(cols[1]!=='数量（只）'||cols[2]!=='合计（亿元）')return null;
      points=data.rows.map(function(r){return {date:r[0],status:r[4]==='完整'?'ok':'partial',statuses:{count:number(r[1])===null?'missing':'ok',amount_yi:number(r[2])===null?'missing':'ok'},sample:'金额覆盖 '+r[3]+' · '+r[4],values:{count:number(r[1]),amount_yi:number(r[2])},breakBefore:{}};});
      // Preserve the existing SVG's source-change breaks; the table does not encode provider identity.
      ['count','amount_yi'].forEach(function(key){
        var dots=Array.from(host.querySelectorAll('[data-pool-point="'+key+'"]')),x={};dots.forEach(function(d){x[d.getAttribute('data-date')]=d.getAttribute('cx');});
        var edges=new Set(Array.from(host.querySelectorAll('[data-pool-line="'+key+'"]')).map(function(l){return l.getAttribute('x1')+'|'+l.getAttribute('x2');}));
        points.forEach(function(p,i){if(i)p.breakBefore[key]=!edges.has(x[points[i-1].date]+'|'+x[p.date]);});
      });
      panels=[{title:'连板股数量',unit:'只',series:[{key:'count',label:'数量'}]},
        {title:'连板股成交额',unit:'亿元',series:[{key:'amount_yi',label:'成交额'}]}];
    }
    var fact=host.getAttribute('data-end-date')||host.getAttribute('data-as-of');
    if(!panels.length||!validDates(points)||(fact&&points.some(function(p){return p.date>fact;})))return null;
    return {points:points,panels:panels};
  }
  function addMobileCharts(doc){
    var families=[['top20','[data-top20-feedback-trend="v1"]'],['board','[data-board-feedback-trend="v1"]'],
      ['core','[data-core-feedback-trend="v1"]'],['height','[data-emotion-height-chart="v1"]'],
      ['fx','.fx-chart'],['low','[data-low-price-trend="v1"]'],['pool','[data-consecutive-pool-trend="v1"]']];
    families.forEach(function(pair){doc.querySelectorAll(pair[1]).forEach(function(host){
      if(host.querySelector('[data-reading-mobile]'))return;
      var data=chartData(host,pair[0]);if(!data){
        if(!host.querySelector('[data-reading-mobile-fallback]')){
          var note=element(doc,'p','reading-mobile-fallback','逐日数值暂无法核验，手机端保留原图和完整来源，不生成替代趋势。');
          note.setAttribute('data-reading-mobile-fallback','v1');host.appendChild(note);
        }return;
      }
      var box=element(doc,'div','reading-mobile-charts');box.setAttribute('data-reading-mobile',pair[0]);
      var explanation=pair[0]==='fx'?'报价为圆点，交易为方点与虚线，来源切换用虚线；即期独立标记。':pair[0]==='low'?'三组颜色与线型上下图一致，仅连接完整有效点。':'空心点表示部分覆盖。';
      box.appendChild(element(doc,'p','reading-help','手机分图 · '+data.points[0].date+' → '+data.points[data.points.length-1].date+'；缺值断线。'+explanation+'原图及完整口径保留在本节。'));
      data.panels.forEach(function(p,index){
        var card=element(doc,'div','reading-mobile-panel'),model=chartModel(data.points,p.series,p.options);
        card.appendChild(element(doc,'h4',null,p.title+'（'+p.unit+'）'));
        if(p.note)card.appendChild(element(doc,'p','reading-help',p.note));
        var legend=element(doc,'div','reading-chart-legend');p.series.forEach(function(s,i){var item=element(doc,'span',null,s.label);item.style.color=s.color||COLORS[i];legend.appendChild(item);});card.appendChild(legend);
        var graphic=element(doc,'div','reading-mobile-graphic');graphic.innerHTML=svgChart(model,p.title,p.unit);card.appendChild(graphic);
        var label=element(doc,'label','reading-date-control','查看日期 '),select=element(doc,'select');
        select.setAttribute('aria-label',p.title+'查看日期');select.setAttribute('data-reading-date','v1');
        data.points.forEach(function(point,i){var opt=element(doc,'option',null,point.date);opt.value=String(i);if(i===data.points.length-1){opt.selected=true;opt.setAttribute('selected','');}select.appendChild(opt);});
        label.appendChild(select);card.appendChild(label);
        var readout=element(doc,'div','reading-date-values');readout.setAttribute('aria-live','polite');
        data.points.forEach(function(point,i){var state=panelStatus(point,p.series);
          var value=element(doc,'p',null,point.date+' · '+p.series.map(function(s){return s.label+' '+readingValue(point.values[s.key],s)+(point.values[s.key]==null?'':p.unit);}).join('；')+' · '+(point.sample||'')+' · '+({'ok':'已核验','complete':'已核验','partial':'部分覆盖','empty':'无样本','missing':'缺少数值','missing-data':'缺少来源','source_failed':'来源失败','not_computed':'未计算','skipped':'未计算'}[state]||'待核验'));
          value.setAttribute('data-reading-value',String(i));value.hidden=i!==data.points.length-1;readout.appendChild(value);});
        card.appendChild(readout);box.appendChild(card);
      });
      var originals=Array.from(host.querySelectorAll('svg,img')).filter(function(el){return !el.closest('[data-reading-mobile]');});
      if(!originals.length)return;
      // Hide only wide plot wrappers on phones; title, coverage and disclosure controls remain.
      originals.forEach(function(el){var target=el.closest('.feedback-plots');
        if(!target&&pair[0]==='pool')target=el.parentElement.parentElement;
        (target||el).classList.add('reading-desktop-plot');});
      var first=originals[0].closest('.reading-desktop-plot')||originals[0];first.parentNode.insertBefore(box,first);
    });});
  }
  function bind(doc){
    doc.querySelectorAll('[data-reading-date]').forEach(function(select){if(select._readingBound)return;select._readingBound=true;
      select.addEventListener('change',function(){select.closest('.reading-mobile-panel').querySelectorAll('[data-reading-value]').forEach(function(p){p.hidden=p.getAttribute('data-reading-value')!==select.value;});});});
    doc.querySelectorAll('[data-reading-reveal]').forEach(function(button){if(button._readingBound)return;button._readingBound=true;
      button.addEventListener('click',function(){var spec=button.getAttribute('data-reading-reveal').split('|'),section=doc.getElementById(spec[0]);
        var table=section&&section.querySelectorAll('table')[Number(spec[1])];if(!table)return;
        for(var p=table.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;
        var row=table.rows[Number(spec[2])+1];if(row&&row.scrollIntoView)row.scrollIntoView({block:'center'});
        table.setAttribute('tabindex','-1');table.focus();});});
  }
  function enhance(doc){
    if(!doc.querySelector('#report-document'))return;
    addConditions(doc,'s0','past');addConditions(doc,'s8','future');addGaps(doc);addTemplateStatus(doc);addMobileCharts(doc);bind(doc);
  }
  return {conditionStatus:conditionStatus,conditionModel:conditionModel,gapModel:gapModel,number:number,
    chartModel:chartModel,chartData:chartData,svgChart:svgChart,enhance:enhance};
});
