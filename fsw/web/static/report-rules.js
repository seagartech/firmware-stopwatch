/* Report rules: source-bound labels, current statistics and evidence. No sample data. */
(function(root){
 const valid=v=>Number.isFinite(v)&&v>=0;
 function stats(values){const a=values.filter(valid).sort((a,b)=>a-b),n=a.length;return {count:n,avg:n?a.reduce((s,v)=>s+v,0)/n:null,min:n?a[0]:null,max:n?a[n-1]:null,med:n?(a[Math.floor((n-1)/2)]+a[Math.floor(n/2)])/2:null,p90:n?a[Math.ceil(n*.9)-1]:null}}
 function refreshModel(m,audience){
  const rounds=m.data.rounds;
  for(const [k,d] of Object.entries(m.defs))if(!m.segments[k])m.segments[k]={k,name:d.name};
  for(const [k,s] of Object.entries(m.segments)){Object.assign(s,stats(rounds.map(r=>r.segs?.[k])),{of:rounds.length});s.bad_rounds=rounds.filter(r=>Number.isFinite(r.segs?.[k])&&r.segs[k]<0).map(r=>r.n);s.bad=s.bad_rounds.length>0;s.thin=rounds.length===0||s.count/rounds.length<.8;s.contentDependent=typeof m.defs[k]?.content_dependent==='boolean'?m.defs[k].content_dependent:s.verdict==='看回答长短';s.spiky=!s.contentDependent&&!s.bad&&!s.thin&&s.max>s.med*2.5&&s.max-s.med>.3;s.spiky_rounds=s.spiky?rounds.filter(r=>valid(r.segs?.[k])&&r.segs[k]>s.med*2.5).map(r=>r.n):[]}
  for(const l of m.lines){Object.assign(l.stat,stats(rounds.map(r=>r.ends[l.index])),{of:rounds.length});l.stat.bad_rounds=rounds.filter(r=>Number.isFinite(r.ends[l.index])&&r.ends[l.index]<0).map(r=>r.n);l.stat.bad=l.stat.bad_rounds.length>0;l.stat.thin=rounds.length===0||l.stat.count/rounds.length<.8;
   const explicit=l.content_dependent??l.stat.content_dependent;l.contentDependent=typeof explicit==='boolean'?explicit:l.contentDependent;
  }
  m.visibleLines=audience==='general'?m.lines.filter(l=>!l.internal&&!l.stat.bad):m.lines;
  m.main=m.visibleLines.find(l=>l.first)||m.visibleLines.find(l=>l.measure&&!l.contentDependent)||m.visibleLines[0];return m;
 }
 function choices(model){const a=model.visibleLines.filter(l=>!l.contentDependent),primary=a.find(l=>l.first===true)||a.find(l=>l.measure===true);return primary?[primary,...a.filter(l=>l!==primary)]:a}
 function semantics(report,phases,events){
  const scenario=report.scenario,points=scenario.points||{},last=new Set(scenario.last_points||[]),mapped=new Map(),result={boundaries:{},resultNames:{}},resolved={events:{}};
  for(const [id,event] of Object.entries(events.events||{})){
   if(typeof event.at!=='string'||!event.at.trim()||!['first','last'].includes(event.occurrence))continue;
   const expectedLast=event.occurrence==='last';
   const candidates=Object.entries(points).filter(([p,d])=>d.at===event.at&&last.has(p)===expectedLast);
   if(candidates.length===1){mapped.set(id,candidates[0][0]);resolved.events[candidates[0][0]]=event}
  }
  for(const [id,b] of Object.entries(phases.boundaries||{})){const p=mapped.get(id);if(p)result.boundaries[p]={...b}}
  for(const [id,n] of Object.entries(phases.resultNames||{})){const p=mapped.get(id);if(p)result.resultNames[p]=n}
  for(const [p,event] of Object.entries(resolved.events))if(event.lastOccurrenceName&&last.has(p))result.resultNames[p]=event.lastOccurrenceName;
  return {phases:result,events:resolved};
 }
 function review(m,row,R){
  const affected=[],evidence=new Map();
  const med=k=>stats(m.data.rounds.map(r=>r.segs?.[k])).med;
  const add=k=>{const d=m.defs[k];if(d&&!evidence.has(k))evidence.set(k,{k,from:d.from,to:d.to,v:row.segs?.[k],med:med(k),count:stats(m.data.rounds.map(r=>r.segs?.[k])).count})};
  for(const l of m.lines){
   const ref=R.referenceInfo(m,l),v=row.ends[l.index],slow=l.segments.filter(k=>m.segments[k]?.spiky_rounds?.includes(row.n)),path=R.pathInfo(m,l,row),bad=l.stat.bad_rounds?.includes(row.n)||!path.valid,over=ref.usable&&valid(v)&&v>ref.e.fair;
   if(!slow.length&&!bad&&!over)continue;
   affected.push({l,ref,v,slow,bad,over,path});slow.forEach(add);
   // Choose the observed excess from current samples; saved cause prose/values cannot override data.
   const candidates=l.segments.filter(k=>!m.segments[k]?.bad&&!m.segments[k]?.thin&&!m.segments[k]?.contentDependent&&m.defs[k]?.basis!=='estimated').map(k=>({k,more:valid(row.segs?.[k])&&Number.isFinite(med(k))?row.segs[k]-med(k):-Infinity})).sort((a,b)=>b.more-a.more);
   if(candidates[0]?.more>0)add(candidates[0].k);
   if(bad)path.parts.filter(p=>!valid(p.v)).forEach(p=>add(p.k));
  }
  const groups=[];
  for(const item of evidence.values()){
   // A common recorded start event establishes overlapping intervals. Equal values alone do not.
   const group=valid(item.v)&&item.v>0?groups.find(g=>g.from===item.from&&g.items.every(x=>valid(x.v)&&x.v>0)):null;
   if(group)group.items.push(item);else groups.push({from:item.from,items:[item]});
  }
  return {affected,evidence:[...evidence.values()],groups};
 }
function stageGroups(m,line,meta){
 const groups=[];let keys=[];
 const hasBoundaries=line.segments.some(k=>meta.boundaries[m.defs[k]?.to]),declaredEnds=new Set(m.lines.map(l=>l.end_point));
 const flush=()=>{if(!keys.length)return;const first=m.defs[keys[0]],last=m.defs[keys[keys.length-1]],boundary=meta.boundaries[last.to];
 const values=m.data.rounds.map(r=>keys.every(k=>Number.isFinite(r.segs[k])&&r.segs[k]>=0)?keys.reduce((s,k)=>s+r.segs[k],0):null).filter(v=>v!==null);
 groups.push({keys:[...keys],from:first.from,to:last.to,label:(boundary?.label&&(!boundary.fromAt||m.scenario.points?.[first.from]?.at===boundary.fromAt)?boundary.label:null)||(m.names[first.from]+' → '+m.names[last.to]),avg:values.length?values.reduce((a,b)=>a+b,0)/values.length:null,count:values.length});keys=[]};
 let cursor=line.start_point;for(const k of line.segments){const d=m.defs[k];if(!d||d.from!==cursor)throw Error('Disconnected stage path');
 // Without reviewed phases, group by declared result endpoints; do not infer device/external ownership.
 keys.push(k);cursor=d.to;if(meta.boundaries[d.to]||(!hasBoundaries&&declaredEnds.has(d.to)))flush()}flush();if(cursor!==line.end_point)throw Error('Wrong endpoint');return groups;
}

function metadata(report){
 const saved=report.presentation||report.scenario.presentation||{};
 const resolved=semantics(report,saved.phases||{boundaries:{},resultNames:{}},saved.events||{events:{}});
 return {phaseMeta:resolved.phases,eventMeta:resolved.events};
}
 const api={stats,refreshModel,choices,semantics,review,stageGroups,metadata};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ReportRules=api;
})(typeof globalThis!=='undefined'?globalThis:this);
