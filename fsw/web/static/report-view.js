/* Live and standalone report share the same renderer and event rules. */
(function(root){
 const Rules=typeof module!=="undefined"&&module.exports?require('./report-rules.js'):root.ReportRules;
 const Layout=typeof module!=="undefined"&&module.exports?require('./report-layout.js'):root.ReportLayout;
 const states=new Map();
 const P90_LABEL='90%记录不超过（P90）';
 function facade(){const R={...Layout};R.model=(data,audience)=>{const m=Rules.refreshModel(Layout.model(data,audience),audience),meta=Rules.metadata(data);Object.assign(m.names,meta.phaseMeta.resultNames);for(const l of m.lines){l.startName=m.names[l.start_point]||l.startName;l.endName=m.names[l.end_point]||l.endName}return m};configureReferences(R);configurePlot(R);return R;}
 function configureReferences(R){
// Match a cited standard and statistic, never a device or a sample round.
R.referenceMeaning=function(ref){
 const source=String(ref.e.source||'')+' '+String(ref.e.short||'');
 const cesa=/T\s*\/\s*CESA\s*1045\s*[-—–]\s*2019/i.test(source)&&ref.mode==='mean'&&ref.e.fair===2;
 return {label:cesa?'A级平均响应时间参考线':ref.mode==='mean'?'平均用时参考线':'用时参考线',cesa};
};
R.referenceEvidence=function(ref){
 const meaning=R.referenceMeaning(ref);
 if(!meaning.cesa)return '<p>原报告记录的来源：'+R.esc(ref.e.source||ref.e.short||'未保存来源')+'</p>';
 return '<p>T/CESA 1045—2019《智能音箱技术规范》是团体标准。第 6.3.3 条规定典型对话场景的平均响应时间：A级不超过 2 秒，B级不超过 2.5 秒。此处采用 A级参考线比较；单次超过只作逐轮标记。</p><p><a href="https://qxb-img-osscache.qixin.com/standards/ecbd45195595996fc9d51514a92c322c.pdf" target="_blank" rel="noopener">查看标准原文（第 6.3.3 条）</a></p>'+(ref.e.good===.8?'<p>报告保存的 0.8 秒为项目采用的体验目标，依据公开技术参考；它不是上述团体标准的另一档，也不作为单次合格线。</p><p><a href="https://hamming.ai/blog/how-to-optimize-latency-in-voice-agents" target="_blank" rel="noopener">查看 0.8 秒体验目标的技术参考</a></p>':'');
};

}
// Stable result identity across the tabs and subsets of one report.
function configurePlot(R){
 const render=R.roundsOverview,ns='http://www.w3.org/2000/svg';
 const colors=['#2563b8','#b45b08','#8245aa','#007e78','#a73555','#526521'];
 const shapes=['circle','square','triangle','diamond','cross','hexagon'];
 function marker(index,x,y){
  const kind=shapes[(index+Math.floor(index/colors.length))%shapes.length];let el;
  if(kind==='circle'){el=document.createElementNS(ns,'circle');el.setAttribute('cx',x);el.setAttribute('cy',y);el.setAttribute('r',5.6)}
  else if(kind==='square'){el=document.createElementNS(ns,'rect');el.setAttribute('x',x-5.2);el.setAttribute('y',y-5.2);el.setAttribute('width',10.4);el.setAttribute('height',10.4)}
  else {el=document.createElementNS(ns,'polygon');const points=kind==='triangle'?[[0,-7],[6.5,5.5],[-6.5,5.5]]:kind==='diamond'?[[0,-7],[6,0],[0,7],[-6,0]]:kind==='cross'?[[-2,-6],[2,-6],[2,-2],[6,-2],[6,2],[2,2],[2,6],[-2,6],[-2,2],[-6,2],[-6,-2],[-2,-2]]:[[0,-6],[5.2,-3],[5.2,3],[0,6],[-5.2,3],[-5.2,-3]];el.setAttribute('points',points.map(([a,b])=>(x+a)+','+(y+b)).join(' '))}
  el.setAttribute('data-marker-shape',kind);return el;
 }
 R.roundsOverview=function(m,options={}){
  // Result index, not its position in the filtered chart, chooses the symbol.
  const visible=m.visibleLines.map(l=>({...l,color:colors[l.index%colors.length],stat:{...l.stat,exp_std:R.referenceInfo(m,l).usable?l.stat.exp_std:{...(l.stat.exp_std||{}),fair:null}}}));
  const host=document.createElement('div');host.innerHTML=render({...m,audience:'technical',visibleLines:visible},options);
  host.querySelectorAll('[data-result][data-value]').forEach(old=>{
   const el=marker(Number(old.dataset.result),Number(old.dataset.x),Number(old.dataset.y));
   for(const a of old.attributes)if(a.name.startsWith('data-')||['fill','stroke','stroke-width'].includes(a.name))el.setAttribute(a.name,a.value);
   el.setAttribute('stroke-width','1.3');el.setAttribute('tabindex','0');el.setAttribute('role','button');el.setAttribute('aria-label',old.textContent);el.style.cursor='pointer';old.replaceWith(el);
  });
  const lines=visible.filter(l=>options.complete?l.contentDependent:!l.contentDependent);
  host.querySelectorAll('.point-key').forEach((old,i)=>{
   const l=lines[i],svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 18 18');svg.setAttribute('width','16');svg.setAttribute('height','16');svg.setAttribute('aria-hidden','true');svg.style.cssText='flex:none;vertical-align:middle';const el=marker(l.index,9,9);el.setAttribute('fill',l.color);svg.append(el);old.replaceWith(svg);
  });
  const chart=host.querySelector('svg.round-overview-svg');if(!chart){if(!lines.length&&visible.length&&visible.every(l=>l.contentDependent))return '<p>本图只显示响应用时；本次记录均为完整过程用时，逐次记录保留在下方完整数据表中。</p>';return host.innerHTML;}
  // Label the reference on the chart, including which result/statistic it applies to.
  const referenceValues=[...new Set(lines.map(l=>(l.stat.exp_std||{}).fair).filter(v=>Number.isFinite(v)&&v>=0))];
  chart.querySelectorAll('line[stroke-dasharray]:not([data-mean])').forEach((el,i)=>{
   const value=referenceValues[i],matching=lines.filter(l=>l.stat.exp_std?.fair===value&&R.referenceInfo(m,l).usable);
   if(!matching.length){el.remove();return}
   el.setAttribute('data-reference-value',value);el.setAttribute('stroke','#48535f');el.setAttribute('stroke-width','2');el.setAttribute('stroke-dasharray','6 4');el.setAttribute('pointer-events','none');
   const text=document.createElementNS(ns,'text');text.setAttribute('x',el.getAttribute('x1'));text.setAttribute('y','9');text.setAttribute('text-anchor','middle');text.setAttribute('font-size','12');text.setAttribute('font-weight','600');text.setAttribute('fill','#48535f');
   text.textContent=matching.map(l=>l.endName+' · '+R.referenceMeaning(R.referenceInfo(m,l)).label+' '+R.fmt(value)).join(' / ');chart.append(text);
  });
  chart.querySelectorAll('[data-mean]').forEach(el=>{el.style.display='none';el.setAttribute('stroke-width','2');el.setAttribute('stroke-dasharray','8 5');el.setAttribute('opacity','.9');el.setAttribute('pointer-events','none')});
  const legend=host.querySelector('.round-overview-legend');
  [...legend.children].slice(lines.length).forEach(el=>el.remove());
  [...legend.children].forEach((el,i)=>{
   const l=lines[i],mean=document.createElement('small');mean.dataset.meanSummary='';mean.hidden=true;mean.textContent='平均 '+R.fmt(l.stat.avg);mean.style.color=l.color;el.append(mean);
  });
  const controls=document.createElement('div');controls.className='plot-controls';controls.innerHTML='<label><input type="checkbox" data-toggle-means> 显示平均用时线</label>';legend.after(controls);
  host.querySelectorAll('p.small-note').forEach(el=>el.remove());
  // One rule for both tabs: compare each valid sample only with its own reference.
  // Internal segment anomalies remain in the detailed tables, not in this plot's marks.
  chart.querySelectorAll('[data-abnormal-round]').forEach(el=>el.remove());
  const comparable=lines.filter(l=>!l.contentDependent&&R.referenceInfo(m,l).usable);
  const markedRows=new Set(),over=[];
  chart.querySelectorAll('[data-result][data-value]').forEach(point=>{
   const line=comparable.find(l=>l.index===Number(point.dataset.result));if(!line)return;
   const value=m.data.rounds.find(r=>String(r.n)===point.dataset.round)?.ends[line.index];
   const limit=R.referenceInfo(m,line).e.fair;
   if(!Number.isFinite(value)||value<0||value<=limit)return;
   point.dataset.overReference=String(limit);point.setAttribute('aria-label',point.getAttribute('aria-label')+'，超过 '+R.fmt(limit)+' '+R.referenceMeaning(R.referenceInfo(m,line)).label);
   over.push({point,line,limit});
   if(m.audience==='general'&&lines.length===1){
    if(markedRows.has(point.dataset.round))return;markedRows.add(point.dataset.round);
    const rect=document.createElementNS(ns,'rect');rect.setAttribute('data-over-reference-round',point.dataset.round);rect.setAttribute('x','0');rect.setAttribute('y',Number(point.dataset.y)-13);rect.setAttribute('width','760');rect.setAttribute('height','26');rect.setAttribute('fill','#fff3d6');rect.setAttribute('pointer-events','none');chart.prepend(rect);
   }else{
    const ring=document.createElementNS(ns,'circle');ring.setAttribute('data-over-reference-ring',line.index+':'+point.dataset.round);ring.setAttribute('cx',point.dataset.x);ring.setAttribute('cy',point.dataset.y);ring.setAttribute('r','10');ring.setAttribute('fill','none');ring.setAttribute('stroke','#b86e00');ring.setAttribute('stroke-width','2.3');ring.setAttribute('pointer-events','none');point.before(ring);
   }
  });
  if(comparable.length){
   const note=document.createElement('p');note.className='plot-explanation';
   const rowMode=m.audience==='general'&&lines.length===1;
   note.textContent=(rowMode?'黄色底：':'橙色外圈：')+comparable.map(l=>'本次「'+l.endName+'」用时超过 '+R.fmt(R.referenceInfo(m,l).e.fair)+'参考线').join('；')+'。';
   controls.after(note);
  }
  const refs=lines.flatMap(l=>{const r=R.referenceInfo(m,l);if(!r.usable)return [];return [l.endName+'：'+R.referenceMeaning(r).label+'为 '+R.fmt(r.e.fair)+(r.mode==='mean'?'，用于比较整组平均用时。':'。')]});
  if(refs.length){const note=document.createElement('p');note.className='plot-explanation';note.textContent=refs.join(' ');host.append(note)}
  const readout=document.createElement('div');readout.className='point-readout';readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');readout.textContent='点击一个点，查看该次用时。';controls.after(readout);
  return '<div class="identified-plot">'+host.innerHTML+'</div>';
 };
}
function bindPlot(scope){
 function showPoint(event){
  const el=event.target.closest?.('.identified-plot [data-result][data-value]');if(!el)return;
  const root=el.closest('.identified-plot');root.querySelector('.point-readout').textContent=el.querySelector('title')?.textContent||el.getAttribute('aria-label');
  root.querySelectorAll('[data-result][data-value]').forEach(p=>p.setAttribute('stroke-width',p===el?'2.8':'1.3'));
 }
 scope.addEventListener('click',showPoint);
 scope.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){if(e.target.matches?.('.identified-plot [data-result][data-value]')){e.preventDefault();showPoint(e)}}});
 scope.addEventListener('change',e=>{if(!e.target.matches?.('[data-toggle-means]'))return;const root=e.target.closest('.identified-plot');root.querySelectorAll('[data-mean]').forEach(el=>el.style.display=e.target.checked?'':'none');root.querySelectorAll('[data-mean-summary]').forEach(el=>el.hidden=!e.target.checked)});
}
// Technical view: all measured records remain available.
function buildTechnical(scope,payload,R,ReportPolicy,phaseMeta,eventMeta,provenance,state){
 const E=R.esc,F=R.fmt,stageGroups=ReportPolicy.stageGroups;
 const root=scope.querySelector('#v-tech'),m=R.model(payload,'technical'),split=R.sharedPrefix(m);
 root.classList.add('readable-report');
 const exact=v=>Number.isFinite(v)?v.toFixed(6)+' 秒':'未记录';
 const name=p=>phaseMeta.resultNames?.[p]||m.names[p]||'未命名事件';
 for(const l of m.lines){l.startName=name(l.start_point);l.endName=name(l.end_point)}
 m.names={...m.names,...phaseMeta.resultNames};
 let selected=m.lines.some(l=>l.index===state.technical)?state.technical:(m.main?.index??m.lines[0]?.index),round=payload.rounds.some(r=>r.n===state.round)?state.round:payload.rounds[0]?.n;
 const boundary=k=>{const d=m.defs[k]||{};return name(d.from)+' → '+name(d.to)};
 const fold=(title,body)=>'<details class="tech-fold"><summary>'+E(title)+'</summary><div class="tech-fold-body">'+body+'</div></details>';
 const fields=rows=>'<dl class="tech-fields">'+rows.map(([k,v])=>'<div><dt>'+E(k)+'</dt><dd>'+E(v)+'</dd></div>').join('')+'</dl>';
 const event=p=>{const at=m.scenario.points?.[p]?.at,known=eventMeta.events?.[p];if(at&&known?.at===at)return known.label;const a=String(at||'').split('|');return a.length>=3?(a[1]==='before'?'执行前：':a[1]==='after'?'执行后：':a[1]+'：')+a.slice(2).join('|'):'未保存实际程序事件'};
 const knownEvent=p=>{const v=eventMeta.events?.[p];return v&&v.at===m.scenario.points?.[p]?.at?v:null};
 const eventPlain=p=>knownEvent(p)?.plain||event(p);
 const eventNote=p=>knownEvent(p)?.note||'';
 const endsFor=k=>{const d=m.defs[k]||{};return [...new Set(d.to_any?.length?d.to_any:[d.to])].filter(Boolean)};
 const endDescription=l=>{const ps=endsFor(l.segments.at(-1));return ps.length>1?'取最后记录到的事件：'+ps.map(event).join(' / '):event(l.end_point)};
 const occurrence=p=>(m.scenario.last_points||[]).includes(p)?'本轮最后一次记录':'本轮首次记录';
 const timingEntry=(p,label)=>'<div class="timing-entry"><b>'+E(label)+'</b><div><p>'+E(eventPlain(p))+'</p><small>'+E(event(p))+' · '+E(occurrence(p))+'</small>'+(eventNote(p)?'<p class="scope-note">'+E(eventNote(p))+'</p>':'')+'</div></div>';
 const panel=(id,title,body)=>'<section class="panel" id="'+id+'"><h2>'+title+'</h2>'+body+'</section>';
 const binding=p=>{const def=(m.scenario.points||{})[p]||{},parts=String(def.at||'').split('|');return parts.length>=3?'<span class="binding-position">'+E('计时位置对应语句')+'</span><code>'+E(parts.slice(2).join('|'))+'</code>':'未保存绑定语句'};
 const endpoints=points=>'<div class="endpoint-pair">'+[...new Set(points)].map((p,i)=>{const c=payload.code?.[p]||{},def=m.scenario.points?.[p]||{};return '<div class="event-card"><b>'+(i?(points.length>2?'终点候选：':'终点：'):'起点：')+E(name(p))+'</b><span class="event-meaning">'+E(event(p))+' · '+E(occurrence(p))+'</span>'+binding(p)+'<p class="event-location">'+E((c.path||def.f||'未保存文件')+':'+(c.l||def.l||'?'))+(c.func?'<br>'+E(c.func):'')+'</p></div>'}).join('')+'</div>';
 function eventScope(l){return '<div class="event-scope"><div><span>起点</span><b>'+E(l.startName)+'</b><small>'+E(event(l.start_point))+'</small></div><div><span>终点</span><b>'+E(l.endName)+'</b><small>'+E(endDescription(l))+'</small></div></div>'}

 function table(keys,offset=0,expanded=false){
  const host=document.createElement('div');host.innerHTML=R.technicalTable(m,keys,offset);
  host.querySelectorAll('tr.technical-segment').forEach((tr,i)=>{
   const k=tr.dataset.segment,d=m.defs[k],s=m.segments[k]||{},th=tr.querySelector('th');
   th.firstChild.textContent=(offset+i+1)+'. '+boundary(k);
   th.querySelector('.segment-boundaries')?.remove();
   const extraEnds=endsFor(k).filter(p=>p!==d.to);
   if(extraEnds.length){const catalogHost=document.createElement('div');catalogHost.innerHTML=R.pointCatalog(m);for(const p of extraEnds){const sourceRow=[...catalogHost.querySelectorAll('[data-source-point]')].find(row=>row.dataset.sourcePoint===p);const ctx=sourceRow?.nextElementSibling?.querySelector('.source-context');if(ctx)tr.nextElementSibling.querySelector('td').append(ctx)}}
   const more=th.querySelector('details');more.classList.add('tech-fold');more.querySelector('summary').textContent='统计、计时位置与源码';
   more.insertAdjacentHTML('beforeend',endpoints([d.from,...endsFor(k)])+(endsFor(k).length>1?'<p class="fine">此段取上述终点候选中最后记录到的时刻。逐次数据未注明最终采用哪个候选。</p>':'')+'<p class="fine">原分段名称：'+E(d.name||s.name)+'；涉及模块（原报告标注）：'+E(d.owner||s.owner||'未提供')+'。</p>');
   more.querySelectorAll('.endpoint-location').forEach(x=>x.remove());
   more.querySelectorAll('dl>div').forEach(cell=>{const dt=cell.querySelector('dt'),dd=cell.querySelector('dd');if(dd?.textContent==='没有可比记录'||dt?.textContent==='参与方')cell.remove();else if(dt?.textContent==='有效次数')dt.textContent='有效样本数';else if(dt?.textContent==='90%分位')dt.textContent=P90_LABEL});
   const body=document.createElement('div');body.className='tech-fold-body';while(more.children.length>1)body.append(more.children[1]);th.querySelector('button[data-code]')?.remove();const source=tr.nextElementSibling;while(source?.firstElementChild?.firstChild)body.append(source.firstElementChild.firstChild);source?.remove();more.append(body);
   if(expanded){const section=document.createElement('section');section.className='segment-facts segment-more';section.append(body);more.replaceWith(section)}
   const detailRow=document.createElement('tr');detailRow.className='segment-detail-row';const detailCell=document.createElement('td');detailCell.colSpan=5;detailCell.append(expanded?th.querySelector('.segment-facts'):more);detailRow.append(detailCell);tr.after(detailRow);
   const status=tr.lastElementChild;
   const notices=[];
   if(s.bad)notices.push('计时顺序异常'+(s.bad_rounds?.length?'：第 '+s.bad_rounds.join('、')+' 次':''));if(s.thin)notices.push('有效记录 '+(s.count??'未提供')+'/'+(s.of??payload.rounds.length)+' 次');if(s.spiky)notices.push('偏慢：第 '+(s.spiky_rounds||[]).join('、')+' 次');
   if(s.vs?.change)notices.push('历史中位数：'+s.vs.change);
   if(s.contentDependent)notices.push('随内容长度变化');
   if(s.verdict==='推算')notices.push('估算值');
   status.textContent=notices.join('；')||'—';status.dataset.label='需关注的记录';
  });
  const heads=host.querySelectorAll('thead th');if(heads.length)heads[heads.length-1].textContent='需关注的记录';
  return host.innerHTML;
 }
 // All stages share one header; row groups preserve the actual path order.
 function stageRows(keys,offset=0){
  if(!keys.length)return '<tr class="segment-empty"><td colspan="5">'+(split.keys.length?'共用步骤已到达这个结果，没有额外分段。':'此结果没有已记录的分段。')+'</td></tr>';
  const host=document.createElement('div');host.innerHTML=table(keys,offset);
  let groups;try{groups=stageGroups(m,{segments:keys,start_point:m.defs[keys[0]].from,end_point:m.defs[keys.at(-1)].to},phaseMeta)}catch{return host.querySelector('tbody').innerHTML}
  const body=host.querySelector('tbody');
  for(const g of groups){
   const first=[...body.querySelectorAll('tr.technical-segment')].find(row=>row.dataset.segment===g.keys[0]);
   const partial=offset>0&&split.keys.some(k=>{try{return stageGroups(m,m.lines[selected],phaseMeta).some(full=>full.keys.includes(k)&&g.keys.some(t=>full.keys.includes(t)))}catch{return false}});
   const heading=document.createElement('tr');heading.className='segment-stage-heading';
   heading.innerHTML='<th colspan="5"><div><span>'+E(g.label)+(partial?'（后续部分）':'')+'</span><span>'+(partial?'后续步骤平均 ':'阶段平均 ')+F(g.avg)+(g.count<payload.rounds.length?' · '+g.count+'/'+payload.rounds.length+' 次有记录':'')+'</span></div></th>';
   first.before(heading);
  }
  return body.innerHTML;
 }
 function groupedSegments(){
  const host=document.createElement('div');host.innerHTML=table(split.keys.length?split.keys:(m.lines[selected]?.segments||[]));
  const template=host.querySelector('table');if(!template)return '<div id="tech-tail-title" hidden></div><table class="technical-table"><tbody id="tech-tail"></tbody></table>';
  template.classList.add('grouped-segments');template.setAttribute('aria-label','当前结果的分段用时');template.querySelector('tbody').remove();
  if(split.keys.length)template.insertAdjacentHTML('beforeend','<tbody class="segment-group-label"><tr><th colspan="5">所有结果共用的前'+split.keys.length+'步<small class="shared-scope-note">切换结果时，这部分不变</small></th></tr></tbody><tbody id="tech-common-rows">'+stageRows(split.keys)+'</tbody>');
  template.insertAdjacentHTML('beforeend','<tbody class="segment-group-label"><tr><th colspan="5" id="tech-tail-title"></th></tr></tbody><tbody id="tech-tail"></tbody>');
  return host.innerHTML;
 }
 function stats(l){
  const s={...l.stat,...ReportPolicy.stats(payload.rounds.map(r=>r.ends[l.index])),of:payload.rounds.length},values=[['平均用时',F(s.avg)],['最快',F(s.min)],['最慢',F(s.max)],['中位数',F(s.med)],[P90_LABEL,F(s.p90)],['有效记录',String(s.count??0)+'/'+String(s.of??payload.rounds.length)+' 次']];
  const prev=payload.prev?.lines?.[l.name];if(Number.isFinite(prev))values.push(['上次中位数',F(prev)+(s.vs?' · '+s.vs.change:'')]);
  return '<dl class="tech-stats">'+values.map(([k,v])=>'<div><dt>'+E(k)+'</dt><dd>'+E(v)+'</dd></div>').join('')+'</dl>';
 }
 function stageScope(){
  const seen=new Set(),groups=[];
  for(const l of m.lines){let stages;try{stages=stageGroups(m,l,phaseMeta)}catch{continue}
   for(const g of stages){const key=JSON.stringify(g.keys);if(seen.has(key))continue;seen.add(key);
    const b=phaseMeta.boundaries?.[g.to],matched=b&&(!b.fromAt||m.scenario.points?.[g.from]?.at===b.fromAt);
    const range=name(g.from)+' → '+name(g.to);
    groups.push('<div class="scope-stage"><b>'+E(g.label)+'</b>'+(g.label!==range?'<p>'+E(range)+'</p>':'')+(matched&&b.evidence?'<small class="code">'+E(b.evidence)+'</small>':'')+'</div>');
   }
  }
  return groups.length?'<section class="scope-section"><h4>各阶段的范围与依据</h4>'+groups.join('')+'</section>':'';
 }
 function source(){
  const q=payload.quality||{};
  const actualChecks=(payload.checks||[]).filter(c=>c.kind!=='line_changed');
  const sourceLabel=provenance.simulation===true?'模拟数据（基于实测记录修改）':provenance.simulation===false?'实际测试':'来源未注明';
  const sourceRows=[['数据来源',sourceLabel],[provenance.simulation?'原测试时间':'测试时间',String(payload.measured_at||'未记录').replace(/\s*·\s*模拟(?:变体|数据)\s*$/,'')],['采集记录','完整 '+(q.ok??'未记录')+' 次 · 缺失 '+(q.missing??'未记录')+' 次 · 未完成 '+(q.half??'未记录')+' 次']];
  const env=payload.measurement_environment;
  const envRows=(env?.rows||[]).filter(([k,v])=>k&&v!==null&&v!==undefined&&String(v).trim());
  const envDetails=(env?.details||[]).filter(([k,v])=>k&&v!==null&&v!==undefined&&String(v).trim());
  const environment=envRows.length?'<section class="source-section"><h4>'+E(env.title||'测量环境')+'</h4>'+(env.note?'<p class="environment-origin">'+E(env.note)+'</p>':'')+fields(envRows)+(envDetails.length?'<h4>来源与固件标识</h4>'+fields(envDetails):'')+'</section>':'';
  const starts=[...new Set(m.lines.map(l=>l.start_point))];
  const lineEnd=l=>{const ps=endsFor(l.segments.at(-1));return ps.length>1?'<div class="scope-result"><h4>'+E(l.endName)+'</h4><p class="small-note">以以下事件中最后记录到的时刻结束计时：</p>'+ps.map(p=>timingEntry(p,name(p))).join('')+'<p class="small-note">逐次数据只保存用时，未注明每次最终采用哪个事件。</p></div>':timingEntry(l.end_point,l.endName)};
  const boundaries=starts.length===1?'<h4>所有结果的计时起点</h4>'+timingEntry(starts[0],name(starts[0]))+'<h4>各项结果的计时终点</h4>'+m.lines.map(lineEnd).join(''):m.lines.map(l=>'<div class="scope-result"><h4>'+E(l.endName)+'</h4>'+timingEntry(l.start_point,'起点：'+l.startName)+lineEnd(l)+'</div>').join('');
  const scopeBody=boundaries+stageScope()+(adjustment?'<section class="scope-section"><h4>范围与分段连接的处理记录</h4><ul>'+adjustment+'</ul></section>':'');
  return '<div class="tech-intro"><h2>本次测量</h2><p>'+payload.rounds.length+' 次记录 · '+m.lines.length+' 项结果 · '+Object.keys(m.defs).length+' 个分段</p>'+(actualChecks.length?'<ul class="issue-list">'+actualChecks.map(c=>'<li>'+E(c.why||c.name)+'</li>').join(''):'')+fold('数据来源与测量环境','<section class="source-section"><h4>测试记录</h4>'+fields(sourceRows)+'</section>'+environment)+(scopeBody?fold('计时范围与分段说明',scopeBody):'')+'</div>';
 }
 function method(){const references=m.lines.map(l=>R.referenceInfo(m,l)).filter(r=>Number.isFinite(r.e.fair));const unique=[...new Map(references.map(r=>[JSON.stringify([r.e,r.mode]),r])).values()];return fold('统计与标记规则',
 (unique.length?'<section class="method-section"><h4>参考线的含义与出处</h4>'+unique.map(r=>'<p>'+E(R.referenceMeaning(r).label)+'：'+F(r.e.fair)+'；'+E(r.mode==='mean'?'用于平均用时比较':'来源未明确比较哪项统计值')+'。</p>'+R.referenceEvidence(r)).join('')+'</section>':'')+
 '<section class="method-section"><h4>数值如何计算</h4><ul><li>平均值使用有效记录，慢的记录不剔除；缺失值和负用时不计入平均。</li><li>中位数用于观察分布和比较同一步骤的各次记录。90%记录不超过（P90）用于观察较慢时的表现，取升序排列后的第 ⌈有效样本数 × 90%⌉ 个值；它与平均值、中位数分别保留。</li><li>总用时取每次起终点的时间差；阶段用时先逐次合计其连续分段，再求平均。</li><li>同一次记录可核对分段之和；不同分段的中位数不能相加代替总用时。显示值有四舍五入，完整数值表统一用秒。</li></ul></section>'+
 '<section class="method-section"><h4>哪些记录会列出来</h4><ul><li>分段偏慢：该段最慢用时超过中位数 2.5 倍，且多出 0.3 秒以上；随后列出该段超过中位数 2.5 倍的各次记录。原规则标为内容长度相关的分段不参与此项判断。</li><li>单次超过参考值：只表示这一次用时较长；如果参考值用于平均用时，不能据此判定单次不合格。</li><li>计时记录问题：列出负用时、分段缺失或分段合计与总用时不一致的记录，保留原值供核对。</li><li>分段证据以各段中位数为基准，列出各条路径多耗时最多的一段；相同分段只列一次。</li></ul></section>')}
 function plot(){return R.roundsOverview(m)}
 // Presentation rounding never changes threshold decisions or saved measurements.
 function reviewTime(v,unitFrom=v){
  if(!Number.isFinite(v))return '未记录';
  if(v<0)return preciseTime(v);
  const factor=unitFrom>0&&unitFrom<.01?1000:1;
  const scaled=v*factor;
  return (scaled>0&&scaled<.005?scaled.toFixed(factor===1000?3:6):scaled.toFixed(2))+(factor===1000?' 毫秒':' 秒');
 }
 function referenceSentence(ref,v){
  if(!ref.usable||!Number.isFinite(v)||v<=ref.e.fair)return '';
  const delta=Math.round((v-ref.e.fair)*1e6)/1e6,narrow=delta<.005;
  return R.referenceMeaning(ref).label+'为 '+preciseTime(ref.e.fair,1)+'，本次多了 '+(narrow?preciseTime(delta,1):reviewTime(delta,1))+'。';
 }
 function observations(){
  const entries=[];
  for(const row of payload.rounds){
   const results=[],reasons=new Set(),slowSegments=new Set();
   for(const {l,ref,v,slow,bad,over} of ReportPolicy.review(m,row,R).affected){
    if(bad)reasons.add('计时记录需要核对');
    if(over)reasons.add(l.endName+'：'+referenceSentence(ref,v));
    slow.forEach(k=>slowSegments.add(k));
    results.push('<div class="record-result-value">'+E(l.endName)+' <strong>'+F(v)+'</strong></div>');
   }
   if(slowSegments.size)reasons.add('有 '+slowSegments.size+' 个步骤需要核对');
   if(results.length)entries.push('<tr><td><button class="record-toggle" data-tech-round="'+E(row.n)+'" aria-expanded="false" aria-controls="observation-detail-'+E(row.n)+'">第 '+E(row.n)+' 次<span class="expand-chevron" aria-hidden="true">⌄</span></button></td><td>'+results.join('')+'</td><td>'+[...reasons].map(x=>'<div>'+E(x)+'</div>').join('')+'</td></tr><tr class="observation-detail-row" id="observation-detail-'+E(row.n)+'" hidden><td colspan="3"><div class="observation-detail"></div></td></tr>');
  }
  const functional=(payload.labels||[]).filter(l=>l.kind==='problem');
  return '<h3>需要核对的记录</h3>'+(entries.length?'<table class="observation-table"><thead><tr><th>轮次</th><th>结果与用时</th><th>需要核对的情况</th></tr></thead><tbody>'+entries.join('')+'</tbody></table>':'<p class="small-note">本次没有符合下列标记规则的记录。</p>')+(functional.length?'<ul>'+functional.map(l=>'<li>'+E(l.name||l.why)+'：第 '+E((l.rounds||[]).join('、'))+' 次</li>').join('')+'</ul>':'')+method();
 }

 function detailsForLine(l){
  const r=R.referenceInfo(m,l);
  const comparison=r.usable&&r.mode==='mean'?'本次平均用时'+(r.avg<=r.e.fair?'未超过':'超过')+R.referenceMeaning(r).label+' '+F(r.e.fair)+'。':r.usable?'参考值为 '+F(r.e.fair)+'，但来源未明确比较平均值还是其他统计值，暂不判断是否达标。':R.referenceText(m,l,true);
  const summary=stats(l)+'<p class="small-note">'+E(comparison)+'</p>';
  return summary;
 }
 function rawTable(kind){const host=document.createElement('div');host.innerHTML=R.valuesTable(payload,kind);const cols=host.querySelectorAll('thead th');cols.forEach((th,i)=>{if(!i){th.textContent='测试轮次';return}th.textContent=kind==='segments'?boundary(Object.keys(m.defs)[i-1]):m.lines[i-1].endName});return host.innerHTML;}
 function catalog(){const host=document.createElement('div');host.innerHTML=R.pointCatalog(m);host.querySelectorAll('[data-source-point]').forEach(row=>{const label=document.createElement('small');label.className='actual-event-range';label.textContent=event(row.dataset.sourcePoint);row.querySelector('th').append(label)});host.querySelectorAll('[data-code]').forEach(button=>button.remove());host.querySelectorAll('.coderow').forEach(row=>row.hidden=false);return host.innerHTML;}
 const covered=new Set(m.lines.flatMap(l=>l.segments)),independent=Object.keys(m.defs).filter(k=>!covered.has(k));
 const adjustment=(payload.checks||[]).filter(c=>c.kind==='line_changed').map(c=>'<li>'+E(c.why)+'</li>').join('')+(payload.merged||[]).map(x=>'<li>'+E(x.why)+'。</li>').join('');
 const supplementary=(independent.length?'<details class="tech-fold" id="tech-independent"><summary>其他已测分段 · '+independent.length+' 项</summary><div class="tech-fold-body"><p class="small-note">这些分段不在所选结果的连续路径中，单独保留，不加入总用时。</p>'+table(independent,0,true)+'</div></details>':'')+(Object.keys(m.scenario.points||{}).length||Object.keys(payload.code||{}).length?fold('计时位置与源码',catalog()):'')+(payload.rounds.length?fold('完整测量数据','<section class="complete-values" data-values-kind="results"><h3>各次结果用时（秒）</h3>'+rawTable('lines')+'</section><section class="complete-values" data-values-kind="segments"><h3>各次分段用时（秒）</h3>'+rawTable('segments')+'</section>'):'');
 root.innerHTML=source()+panel('tech-observations','每次用了多久',plot()+observations())+
 panel('d-sec-technical-steps','阶段与分段用时','<div class="tech-overview"><div class="result-switcher"><div><p class="fine">选择要核对的结果</p><h3 id="tech-selected-title" class="tech-selected-title"></h3><div id="tech-selected-scope" class="tech-selected-scope"></div></div><div id="tech-choices" class="tech-choices"></div></div><div id="tech-total" class="tech-total"></div></div>'+
 groupedSegments())+
 '<details class="panel process-fold" id="tech-round"'+(state.processOpen?' open':'')+'><summary>单次完整过程</summary><div class="process-body"><div class="tech-round-controls"'+(!payload.rounds.length||!m.lines.length?' hidden':'')+'><div class="tech-round-result-control"'+(m.lines.length<2?' hidden':'')+'><span>查看结果</span><div id="tech-process-results" class="process-buttons" role="group" aria-label="单次过程的结果">'+compactOptions(m.lines,selected,'data-tech-process-line')+'</div></div><div class="tech-round-number-control"><span>测试轮次</span><div id="tech-process-rounds" class="process-buttons" role="group" aria-label="选择测量轮次">'+roundOptions(payload.rounds,round)+'</div></div><select id="tech-round-result" hidden aria-hidden="true" tabindex="-1">'+m.lines.map(l=>'<option value="'+l.index+'">'+E(l.endName)+'</option>').join('')+'</select><select id="tech-round-select" hidden aria-hidden="true" tabindex="-1">'+payload.rounds.map(r=>'<option value="'+E(r.n)+'">第 '+E(r.n)+' 次</option>').join('')+'</select></div><p id="tech-round-line" class="small-note"></p><div id="tech-round-content"></div></div></details>'+
 (supplementary?panel('tech-evidence','补充资料',supplementary):'');
 const processTooltip=document.createElement('div');processTooltip.id='tech-process-tooltip';processTooltip.className='process-tooltip';processTooltip.setAttribute('role','tooltip');processTooltip.hidden=true;root.append(processTooltip);
 function preciseTime(v,unitFrom=v){
  if(!Number.isFinite(v))return '未记录';
  const factor=Math.abs(unitFrom)>0&&Math.abs(unitFrom)<1?1000:1;
  return (v*factor).toFixed(factor===1000?3:6).replace(/(\.[0-9]*?)0+$/,'$1').replace(/\.$/,'')+(factor===1000?' 毫秒':' 秒');
 }
 function roundMarkup(index,n){
  const l=m.lines[index],record=payload.rounds.find(r=>r.n===Number(n)),p=R.pathInfo(m,l,record),host=document.createElement('div');
  host.innerHTML=R.roundHtml(m,index,Number(n)).replaceAll('首尾实测',provenance.simulation?'首尾记录':'首尾实测').replaceAll('真实连续分段','连续分段').replaceAll('按原始精度核对','按完整数值核对').replaceAll('原始精度（秒）','完整数值（秒）').replaceAll('核对精确用时','查看完整用时数值').replaceAll('首尾记录','总用时').replaceAll('首尾实测','总用时').replaceAll('按完整数值核对：分段连接，分段和与首尾用时一致。','分段相接，合计与总用时一致。');
  host.querySelectorAll('details').forEach(el=>el.remove());
  host.querySelectorAll('.round-svg rect').forEach((rect,i)=>{
   const part=p.parts[i];if(!part)return;
   const group=document.createElementNS('http://www.w3.org/2000/svg','g'),next=rect.nextElementSibling;
   const tip='第 '+(i+1)+' 步：'+part.from+' → '+part.to+'\n用时 '+preciseTime(part.v);
   group.dataset.processTip=tip;group.setAttribute('tabindex','0');group.setAttribute('role','img');group.setAttribute('aria-label',tip);group.setAttribute('aria-describedby','tech-process-tooltip');
   rect.querySelector('title')?.remove();rect.before(group);group.append(rect);
   if(next?.tagName.toLowerCase()==='text'&&next.textContent===String(i+1))group.append(next);
  });
  host.querySelectorAll('.round-parts>li b').forEach((el,i)=>{el.textContent=preciseTime(p.parts[i].v);el.removeAttribute('title')});
  host.querySelectorAll('.round-verdict b').forEach(el=>{el.textContent='第 '+E(n)+' 次 · 总用时 '+preciseTime(p.end);el.removeAttribute('title')});
  host.querySelectorAll('p.small-note').forEach(el=>el.remove());
  host.insertAdjacentHTML('beforeend','<div class="process-totals"><span>分段合计 <b>'+E(p.parts.every(x=>Number.isFinite(x.v))?preciseTime(p.total):'记录不完整')+'</b></span><span>总用时 <b>'+E(preciseTime(p.end))+'</b></span></div>');
  return host.innerHTML;
 }

 function hideProcessTip(){processTooltip.hidden=true;}
 function showProcessTip(e){
  const part=e.target.closest?.('[data-process-tip]');if(!part)return;
  processTooltip.textContent=part.dataset.processTip;processTooltip.hidden=false;
  const box=part.getBoundingClientRect(),x=Number.isFinite(e.clientX)?e.clientX:box.left+box.width/2,y=Number.isFinite(e.clientY)?e.clientY:box.bottom;
  const tip=processTooltip.getBoundingClientRect();
  processTooltip.style.left=Math.max(12,Math.min(x+12,window.innerWidth-tip.width-12))+'px';
  processTooltip.style.top=Math.max(12,y+16+tip.height<window.innerHeight?y+16:y-tip.height-12)+'px';
 }
 function paintProcessChoices(){
  scope.querySelectorAll('[data-tech-process-line]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.techProcessLine)===selected)));
  scope.querySelectorAll('[data-tech-process-round]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.techProcessRound)===round)));
  for(const id of ['tech-process-results','tech-process-rounds']){const strip=scope.querySelector('#'+id);if(strip.clientWidth)revealSelected(strip)}
 }
 function drawRound(){
  hideProcessTip();paintProcessChoices();const l=m.lines[selected];
  if(!l||round===undefined){scope.querySelector('#tech-round-result').disabled=!m.lines.length;scope.querySelector('#tech-round-select').disabled=!payload.rounds.length;scope.querySelector('#tech-round-content').innerHTML='<p>本次没有可展示的逐次过程记录。</p>';return;}
  scope.querySelector('#tech-round-line').textContent=l.startName+' → '+l.endName+(endsFor(l.segments.at(-1)).length>1?'。结束规则：'+endDescription(l)+'。':'');
  scope.querySelector('#tech-round-select').value=round;scope.querySelector('#tech-round-result').value=selected;
  for(const id of ['tech-round-select','tech-round-result']){const select=scope.querySelector('#'+id);for(const option of select.options)option.toggleAttribute('selected',option.value===select.value)}
  scope.querySelector('#tech-round-content').innerHTML=roundMarkup(selected,round);scope._updateFloating?.();
 }
 function recordReview(n){
  const record=payload.rounds.find(r=>r.n===n),review=ReportPolicy.review(m,record,R),affected=review.affected.map(x=>x.l);
  function evidenceRow({k,v,med,count}){
   const comparison=!Number.isFinite(v)?'本次缺少这一步的用时记录。':v<0?'本次记录的终点早于起点，需核对计时顺序。':Number.isFinite(med)?'这 '+count+' 次有效记录中，同一步骤的中位数为 '+reviewTime(med,v)+(v>med?'，本次多了 '+reviewTime(Math.round((v-med)*1e6)/1e6,v):'')+'。':'';
   return '<div class="plain-review-step" data-review-segment="'+E(k)+'"><p>'+E(boundary(k)+(Number.isFinite(v)&&v>=0?'，本次用了 '+reviewTime(v)+'。':'。'))+'</p>'+(comparison?'<p>'+E(comparison)+'</p>':'')+'</div>';
  }
  const rows=review.groups.map(group=>{
   if(group.items.length===1)return evidenceRow(group.items[0]);
   const changes=group.items.map(x=>x.v-x.med),allGrowing=changes.every(x=>Number.isFinite(x)&&x>0),display=changes.map(x=>reviewTime(x,1)),sameDisplay=allGrowing&&display.every(x=>x===display[0]);
   const summary=sameDisplay?'这 '+group.items.length+' 项相关计时分别比各自中位数多约 '+display[0]+'。':allGrowing?'这 '+group.items.length+' 项相关计时都比各自中位数更长。':'这 '+group.items.length+' 项计时覆盖的时间有重叠。';
   const data=group.items.map(x=>'<tr data-review-segment="'+E(x.k)+'"><th>到「'+E(name(m.defs[x.k].to))+'」</th><td>'+E(reviewTime(x.v))+'</td><td>'+E(reviewTime(x.med,x.v))+'</td></tr>').join('');
   return '<div class="overlap-review" data-overlap-start="'+E(group.from)+'"><p>从「'+E(name(group.from))+'」开始：</p><p>'+E(summary)+'是否由同一原因引起，现有记录不能确定。</p><table class="overlap-values"><thead><tr><th>计时终点</th><th>本次用时</th><th>本组中位数</th></tr></thead><tbody>'+data+'</tbody></table></div>';
  }).join('');
  const issues=affected.flatMap(l=>{
   const p=R.pathInfo(m,l,record),v=record.ends[l.index];if(p.valid)return [];
   const check=!Number.isFinite(v)?l.endName+'：本次未记录到总用时。':v<0?l.endName+'：总用时记录为 '+preciseTime(v)+'，需核对计时顺序。':p.parts.some(x=>!Number.isFinite(x.v))?l.endName+'：缺少分段记录，无法核对分段合计。':p.parts.some(x=>x.v<0)?l.endName+'：存在终点早于起点的分段，无法核对合计。':l.endName+'：分段合计 '+preciseTime(p.total)+'，总用时 '+preciseTime(p.end)+'，需核对分段连接与计时范围。';
   return ['<p>'+E(check)+'</p>'];
  }).join('');
  return '<section class="plain-review-section">'+(rows||'<p>已有记录尚不能定位到某个具体步骤。</p>')+'</section>'+(issues?'<section class="plain-review-section"><h5>计时记录问题</h5>'+issues+'</section>':'');

 }

 function toggleObservation(button){
  const n=Number(button.dataset.techRound),row=scope.querySelector('#'+'observation-detail-'+n);
  const open=button.getAttribute('aria-expanded')!=='true';
  state.open=state.open||[];state.open=state.open.filter(x=>x!==n);if(open)state.open.push(n);button.setAttribute('aria-expanded',String(open));row.hidden=!open;if(!open)return;
  row.querySelector('.observation-detail').innerHTML='<div class="observation-detail-head"><h4>第 '+E(n)+' 次 · 值得核对的步骤</h4></div>'+recordReview(n);
 }

 // Reserve the tallest overview at this width, regardless of the selected result.
 // The temporary probe uses the same CSS context and is removed before painting.
 let overviewWidth=0;
 const overview=root.querySelector('.tech-overview');
 function sizeOverview(){
  const width=overview.getBoundingClientRect().width;
  if(!m.lines.length||width<=0||width===overviewWidth)return;
  const probe=overview.cloneNode(true);
  probe.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));
  Object.assign(probe.style,{position:'absolute',left:'-100000px',top:'0',width:width+'px',minHeight:'0',visibility:'hidden',pointerEvents:'none'});
  probe.inert=true;overview.parentElement.append(probe);
  const header=probe.querySelector('.result-switcher');header.style.minHeight='0';
  let height=0,headerHeight=0;
  const fill=l=>{probe.querySelector('.tech-selected-title').textContent=l.endName;probe.querySelector('.tech-selected-scope').innerHTML=eventScope(l);probe.querySelector('.tech-total').innerHTML=detailsForLine(l)};
  try{
   for(const l of m.lines){fill(l);headerHeight=Math.max(headerHeight,header.getBoundingClientRect().height)}
   header.style.minHeight=Math.ceil(headerHeight)+'px';
   for(const l of m.lines){fill(l);height=Math.max(height,probe.getBoundingClientRect().height)}
  }finally{probe.remove()}
  overviewWidth=width;overview.querySelector('.result-switcher').style.minHeight=Math.ceil(headerHeight)+'px';overview.style.minHeight=Math.ceil(height)+'px';
 }
 function choose(i){
  const l=m.lines[i];if(!l)return;selected=i;state.technical=i;
  scope.querySelector('#'+'tech-choices').innerHTML=m.lines.map(x=>'<button class="result-button" data-tech-line="'+x.index+'" aria-pressed="'+(i===x.index)+'"><span>'+E(x.endName)+'</span><small>'+(x.internal?'程序内部事件':x.contentDependent?'完整过程':'结果')+'</small></button>').join('');
  scope.querySelector('#'+'tech-selected-title').textContent=l.endName;
  scope.querySelector('#'+'tech-selected-scope').innerHTML=eventScope(l);
  scope.querySelector('#'+'tech-total').innerHTML=detailsForLine(l);
  scope.querySelector('#'+'tech-tail-title').textContent='到「'+l.endName+'」的'+(split.keys.length?'后续':'')+'步骤';
  scope.querySelector('#'+'tech-tail').innerHTML=stageRows(split.tails[i]||l.segments,split.keys.length);
  drawRound();
 }
 root.addEventListener('click',e=>{const b=e.target.closest('[data-tech-line],[data-tech-process-line]'),go=e.target.closest('[data-tech-round]'),pick=e.target.closest('[data-tech-process-round]');if(b)choose(Number(b.dataset.techLine??b.dataset.techProcessLine));if(go)toggleObservation(go);if(pick){round=Number(pick.dataset.techProcessRound);state.round=round;drawRound()}});
 root.addEventListener('toggle',e=>{if(e.target.id==='tech-round'){state.processOpen=e.target.open;if(e.target.open)paintProcessChoices();else hideProcessTip()}},true);
 root.addEventListener('pointerover',showProcessTip);root.addEventListener('pointermove',showProcessTip);
 root.addEventListener('pointerout',e=>{const part=e.target.closest?.('[data-process-tip]');if(part&&!part.contains(e.relatedTarget))hideProcessTip()});
 root.addEventListener('focusin',showProcessTip);root.addEventListener('focusout',e=>{if(e.target.closest?.('[data-process-tip]'))hideProcessTip()});
 root.addEventListener('keydown',e=>{if(e.key==='Escape')hideProcessTip()});
 scope.querySelector('#'+'tech-round-result').addEventListener('change',e=>choose(Number(e.target.value)));
 scope.querySelector('#'+'tech-round-select').addEventListener('change',e=>{round=Number(e.target.value);state.round=round;drawRound()});
 choose(selected);if(!m.lines.length)drawRound();
 for(const n of state.open||[]){const b=root.querySelector('[data-tech-round="'+n+'"]');if(b)toggleObservation(b)}
 return {toggleObservation,recordReview,choose,model:m,sizeOverview,overview,hideProcessTip};
}

 // Both audiences use the same compact controls and bounded floating-state rule.
 function compactOptions(lines,selected,buttonAttribute){
  const E=Layout.esc;
  if(lines.length<2)return '';
  return lines.map(l=>'<button aria-pressed="'+(l.index===selected)+'" '+buttonAttribute+'="'+l.index+'">'+E(l.endName)+'</button>').join('');
 }
 function roundOptions(rounds,selected){
  const E=Layout.esc;return rounds.map(r=>'<button type="button" data-tech-process-round="'+E(r.n)+'" aria-pressed="'+(r.n===selected)+'">第 '+E(r.n)+' 次</button>').join('');
 }
 function revealSelected(host){
  const selected=host.querySelector('[aria-pressed="true"]');if(!selected||!host.clientWidth)return;
  const item=selected.getBoundingClientRect(),box=host.getBoundingClientRect();
  if(item.left<box.left)host.scrollLeft+=item.left-box.left;
  else if(item.right>box.right)host.scrollLeft+=item.right-box.right;
 }
 function paintCompact(host,lines,selected,attribute){
  const key=JSON.stringify(lines.map(l=>[l.index,l.endName]));
  if(host._optionsKey!==key){host.innerHTML=compactOptions(lines,selected,attribute);host._optionsKey=key}
  host.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.getAttribute(attribute))===selected)));
  revealSelected(host);
 }
 function floatingActive({enabled,count,anchorTop,anchorBottom,endBottom,top,height,early=false}){
  return !!enabled&&count>0&&(early?anchorTop<=top:anchorBottom<=top+height+8)&&endBottom>top+height;
 }

 function shell(payload,options={}){
  const E=Layout.esc,json=JSON.stringify(payload).replace(/</g,'\\u003c').replace(/&/g,'\\u0026');
  return `<div class="product-report readable-report" data-origin="${options.real===false?'simulation':options.real===true?'measured':'unknown'}"><script type="application/json" data-report-payload>${json}</script>${payload.simulation===true?'<div class="report-simulation-banner">模拟数据 · 基于真实记录的副本修改，非实测结果</div>':''}<div data-slot="notice">${options.noticeHtml||''}</div><nav class="tabbar" role="tablist" aria-label="报告内容"><button type="button" role="tab" id="tab-measure" data-report-tab="measure" aria-controls="v-measure" aria-selected="true">总结</button><button type="button" role="tab" id="tab-tech" data-report-tab="tech" aria-controls="v-tech" aria-selected="false">技术细节</button><button type="button" role="tab" id="tab-advice" data-report-tab="advice" aria-controls="v-advice" aria-selected="false">优化建议</button></nav><div id="v-measure" role="tabpanel"><div class="summary-grid" id="summary-grid"><section id="metric" class="main-metric"></section><aside id="outcomes" class="outcomes" aria-label="选择测量结果"></aside></div><div id="floating-switch" class="floating-switch"><strong id="floating-current"></strong><div id="compact-options" class="compact-options"></div></div><section class="panel"><h2 id="plot-title">每次测量的用时</h2><div id="plot"></div></section><section class="panel"><div class="step-head"><h2 id="stage-title">时间花在哪</h2><span>阶段平均用时</span></div><div id="steps"></div></section></div><div id="v-tech" role="tabpanel" hidden></div><section id="v-advice" role="tabpanel" hidden><h2>优化建议</h2><div data-slot="advice">${options.adviceHtml||'<p>本报告未附经核对通过的修改建议。</p>'}</div></section><div data-slot="actions" hidden>${options.actionsHtml||''}</div><footer class="report-bottom"><span class="fine">${payload.rounds.length} 次记录 · ${options.real===false?'模拟数据':'按程序记录的事件计时'}</span><div><button class="pill" data-report-data>下载全部数据</button> <button class="pill" data-report-save>保存报告</button></div></footer></div>`;
 }
 function mount(scope,options={}){
  if(!scope||scope._reportMounted)return;
  const payload=JSON.parse(scope.querySelector('[data-report-payload]').textContent),R=facade(),E=R.esc,F=R.fmt;
  const key=JSON.stringify([payload.measured_at,payload.scenario,payload.rounds]);
  let state=states.get(key)||{};
  if(scope.dataset.offline==='true'){
   state={tab:scope.querySelector('[data-report-tab][aria-selected="true"]')?.dataset.reportTab,
    summary:Number(scope.querySelector('#outcomes [aria-pressed="true"]')?.dataset.summaryLine),
    technical:Number(scope.querySelector('[data-tech-line][aria-pressed="true"]')?.dataset.techLine),
    round:Number(scope.querySelector('#tech-round-select')?.value),
    processOpen:scope.querySelector('#tech-round')?.open,
    open:[...scope.querySelectorAll('[data-tech-round][aria-expanded="true"]')].map(b=>Number(b.dataset.techRound))};
  }
  states.set(key,state);
  const provenance={simulation:scope.dataset.origin==='simulation'?true:scope.dataset.origin==='measured'?false:null};
  const {phaseMeta,eventMeta}=Rules.metadata(payload),model=R.model(payload,'general');
  const summaryLines=Rules.choices(model);let selected=summaryLines.some(l=>l.index===state.summary)?state.summary:summaryLines[0]?.index;
  const $=id=>scope.querySelector('#'+id);
  const stageGroups=Rules.stageGroups;
  // In a saved report this slot is already inside the technical view. Preserve it before rebuilding.
  const actions=scope.querySelector('[data-slot="actions"]');actions?.remove();
  const technical=buildTechnical(scope,payload,R,Rules,phaseMeta,eventMeta,provenance,state);
  const inspection=document.createElement('div');inspection.className='tech-inspection';
  const detailSteps=$('d-sec-technical-steps');detailSteps.before(inspection);inspection.append(detailSteps,$('tech-round'));
  const technicalBar=document.createElement('div');technicalBar.id='tech-floating-switch';technicalBar.className='floating-switch';technicalBar.setAttribute('aria-label','技术细节结果切换');
  technicalBar.innerHTML='<strong id="tech-floating-current"></strong><div id="tech-compact-options" class="compact-options"></div>';inspection.prepend(technicalBar);
  function technicalControls(){const l=technical.model.lines[state.technical];$('tech-floating-current').textContent=l?l.endName+' · 平均 '+F(l.stat.avg):'';paintCompact($('tech-compact-options'),technical.model.lines,state.technical,'data-tech-compact-line');updateFloating()}
  if(actions){actions.hidden=false;scope.querySelector('#v-tech').append(actions)}
  function view(which){
   if(!['measure','tech','advice'].includes(which))which='measure';state.tab=which;
   for(const t of ['measure','tech','advice']){$('v-'+t).hidden=t!==which;$('tab-'+t).setAttribute('aria-selected',String(t===which))}
   scope.dataset.view=which;technical.sizeOverview();updateFloating();
  }
  function ref(l){const r=R.referenceInfo(model,l);if(l.contentDependent)return '<span class="ref-chip">时长随处理内容变化</span>';if(!r.usable)return '<span class="ref-chip">暂无适用的快慢参考</span>';const text=r.mode==='mean'?'平均用时'+(r.avg<=r.e.fair?'未超过':'超过')+' '+F(r.e.fair)+' '+R.referenceMeaning(r).label:'参考值 '+F(r.e.fair)+'，比较口径待确认';return '<span class="ref-chip">'+E(text)+'</span>';}
  function updateFloating(){
   if(!scope.isConnected)return;const bar=$('floating-switch'),grid=$('summary-grid'),header=document.querySelector('.apphd');
   const top=header?Math.max(0,header.getBoundingClientRect().bottom):0,height=bar.getBoundingClientRect().height;
   const bounds=scope.getBoundingClientRect(),widthChanged=bar.getBoundingClientRect().width!==bounds.width;for(const b of [bar,technicalBar]){b.style.left=bounds.left+'px';b.style.width=bounds.width+'px';b.style.transform='none'}scope.style.setProperty('--report-top',top+'px');if(height)scope.style.setProperty('--switch-height',height+'px');
   const active=floatingActive({enabled:!$('v-measure').hidden,count:summaryLines.length,anchorBottom:grid.getBoundingClientRect().bottom,endBottom:$('v-measure').getBoundingClientRect().bottom,top,height});
   if(active&&(!bar.classList.contains('is-active')||widthChanged))revealSelected($('compact-options'));
   bar.classList.toggle('is-active',active);grid.classList.toggle('compact-active',active);bar.setAttribute('aria-hidden',String(!active));$('outcomes').setAttribute('aria-hidden',String(active));
   const selector=detailSteps.querySelector('.result-switcher'),techHeight=technicalBar.getBoundingClientRect().height;
   if(techHeight)scope.style.setProperty('--tech-switch-height',techHeight+'px');
   // Compact the detail selector as its top reaches the header, before statistics can be covered.
   const techActive=floatingActive({enabled:!$('v-tech').hidden,count:technical.model.lines.length,anchorTop:selector.getBoundingClientRect().top,endBottom:inspection.getBoundingClientRect().bottom,top,height:techHeight,early:true});
   if(techActive&&(!technicalBar.classList.contains('is-active')||widthChanged))revealSelected($('tech-compact-options'));
   technicalBar.classList.toggle('is-active',techActive);technicalBar.setAttribute('aria-hidden',String(!techActive));inspection.classList.toggle('compact-active',techActive);
   $('tech-choices').setAttribute('aria-hidden',String(techActive));
  }
  function controls(i){
   const choices=summaryLines,name=x=>E(x.endName);$('summary-grid').classList.toggle('single',choices.length===1);
   $('outcomes').innerHTML=choices.map(x=>'<button aria-pressed="'+(x.index===i)+'" data-summary-line="'+x.index+'"><span>'+name(x)+'</span><strong>'+F(x.stat.avg)+'</strong></button>').join('');
   paintCompact($('compact-options'),choices,i,'data-summary-line');
  }
  function pick(i){
   if(!summaryLines.some(l=>l.index===i))return;selected=i;state.summary=i;const l=model.lines[i],s=l.stat;
   $('metric').innerHTML='<div class="muted">从「'+E(l.startName)+'」到</div><h2>「'+E(l.endName)+'」</h2><div class="bigtime"><small>平均 </small>'+F(s.avg)+'</div><div class="metrics"><span>最快 '+F(s.min)+'</span><span>最慢 '+F(s.max)+'</span></div><div class="reference">'+ref(l)+'</div>'+(s.count<payload.rounds.length?'<p>有效记录 '+s.count+'/'+payload.rounds.length+' 次。</p>':'');
   controls(i);$('floating-current').textContent=l.endName+' · 平均 '+F(s.avg);$('plot-title').textContent=l.endName+' · 每次测量用时';$('stage-title').textContent=l.endName+' · 时间花在哪';$('plot').innerHTML=R.roundsOverview({...model,visibleLines:[l]});
   let groups=[];try{groups=stageGroups(model,l,phaseMeta)}catch{ $('steps').innerHTML='<p>分段连接尚不能核对，请在技术细节查看原记录。</p>';updateFloating();return;}
   const all=summaryLines.map(x=>{try{return stageGroups(model,x,phaseMeta)}catch{return []}}),cap=R.axis(Math.max(...all.flatMap(x=>x.map(g=>g.avg||0)))).cap;
   $('steps').style.setProperty('--stage-height',Math.max(0,...all.map(g=>g.length))*84+'px');
   $('steps').innerHTML='<div class="stage-list">'+groups.map(g=>'<div class="stage-row" data-stage-from="'+E(g.from)+'" data-stage-to="'+E(g.to)+'"><div class="step-label"><span>'+E(g.label)+(g.count<payload.rounds.length?'（'+g.count+'/'+payload.rounds.length+' 次有分段记录）':'')+'</span><strong>'+F(g.avg)+'</strong></div><div class="bar"><i style="width:'+Math.max(0,(g.avg||0)/cap*100)+'%"></i></div></div>').join('')+'</div>';updateFloating();
  }
  scope.addEventListener('click',e=>{
   const tab=e.target.closest('[data-report-tab]'),choice=e.target.closest('[data-summary-line]');
   if(tab)view(tab.dataset.reportTab);if(choice)pick(Number(choice.dataset.summaryLine));
   const techChoice=e.target.closest('[data-tech-compact-line]');if(techChoice)technical.choose(Number(techChoice.dataset.techCompactLine));
   if(techChoice||e.target.closest('[data-tech-line],[data-tech-process-line]'))technicalControls();
   if(e.target.closest('[data-report-data]'))download(JSON.stringify(payload,null,2),'measurement-data.json','application/json');
   if(e.target.closest('[data-report-save]')){if(options.onSave)options.onSave();else download('<!doctype html>'+document.documentElement.outerHTML,'固件用时报告.html','text/html');}
  });
  scope.addEventListener('change',e=>{if(e.target.matches('#tech-round-result'))technicalControls()});
  scope.addEventListener('toggle',()=>updateFloating(),true);
  bindPlot(scope);
  if(summaryLines.length)pick(selected);else{$('summary-grid').hidden=true;scope.querySelectorAll('#v-measure>.panel').forEach(el=>el.hidden=true);$('v-measure').insertAdjacentHTML('beforeend','<section class="panel"><p>'+(!payload.rounds.length?'本次没有逐次测量记录。':'本次没有可单独展示的响应用时。完整过程用时可在技术细节中查看。')+'</p><button class="pill" data-report-tab="tech">查看技术细节</button></section>')}
  technicalControls();view(state.tab||'measure');scope._reportMounted=true;scope._show=view;scope._updateFloating=updateFloating;scope._hideProcessTip=technical.hideProcessTip;
  if(typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(()=>{technical.sizeOverview();updateFloating()});observer.observe($('floating-switch'));observer.observe(technicalBar);observer.observe(technical.overview);scope._cleanup=()=>observer.disconnect()}
 }
 function update(container,html,options={}){
  const next=document.createElement('div');next.innerHTML=html;const incoming=next.querySelector('.product-report'),old=container.querySelector('.product-report');
  if(old&&old.dataset.origin===incoming.dataset.origin&&old.querySelector('[data-report-payload]').textContent===incoming.querySelector('[data-report-payload]').textContent){
   for(const slot of ['advice','notice','actions']){const a=old.querySelector('[data-slot="'+slot+'"]'),b=incoming.querySelector('[data-slot="'+slot+'"]');if(a&&b&&a.innerHTML!==b.innerHTML){const key=d=>(d.closest('[id]')?.id||'')+'|'+(d.querySelector('summary')?.textContent||'').replace(/[▸▾›⌄]/g,'').trim(),open=new Map(Array.from(a.querySelectorAll('details'),d=>[key(d),d.open]));a.innerHTML=b.innerHTML;a.querySelectorAll('details').forEach(d=>{if(open.has(key(d)))d.open=open.get(key(d))});}}
   old._updateFloating?.();
   return old;
  }
  old?._cleanup?.();container.innerHTML=html;const scope=container.querySelector('.product-report');mount(scope,options);return scope;
 }
 function download(text,name,type){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 function show(scope,tab,target){scope?._show?.(tab);if(target){const el=scope.querySelector('#'+target)||scope.querySelector('#d-'+target);el?.scrollIntoView({block:'start'})}}
 function install(){
  const reposition=()=>document.querySelectorAll('.product-report').forEach(scope=>{scope._hideProcessTip?.();scope._updateFloating?.()});
  window.addEventListener('scroll',reposition,{passive:true});window.addEventListener('resize',reposition);
 }
 const api={shell,mount,update,show,install,compactOptions,roundOptions,revealSelected,floatingActive,model:(data,audience)=>facade().model(data,audience)};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ReportView=api;
})(typeof globalThis!=='undefined'?globalThis:this);
