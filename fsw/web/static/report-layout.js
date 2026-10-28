/* Report presentation. Paths, statistics and values are supplied by the report API. */
(function (root) {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const fmt = v => !finite(v) ? '未测到' : v === 0 ? '0 秒' : v < .001 ? `${(v * 1000).toFixed(3)} 毫秒` : v < .01 ? `${(v * 1000).toFixed(2)} 毫秒` : `${v.toFixed(2)} 秒`;
  const palette = ['#6e8792', '#64877b', '#16796e', '#8a7753'];
  const median = values => { const a=values.filter(finite).sort((a,b)=>a-b),n=a.length; return n ? (a[Math.floor((n-1)/2)]+a[Math.floor(n/2)])/2 : null; };
  // One primary statistic for both audiences; never relabel a saved median as a mean.
  const mean = values => {const a=values.filter(v=>finite(v)&&v>=0);return a.length?a.reduce((x,y)=>x+y,0)/a.length:null;};
  function axis(max) {
    if (!(max>0)) max=1;
    const raw=max/5, power=10**Math.floor(Math.log10(raw)), f=raw/power;
    const step=(f<=1?1:f<=2?2:f<=5?5:10)*power;
    const cap=Math.ceil(max/step)*step;
    return {step,cap,ticks:Array.from({length:Math.round(cap/step)+1},(_,i)=>i*step)};
  }
  function model(data, audience='technical') {
    const scenario=data.scenario, names={...(scenario.point_names||{})}, defs=scenario.segments;
    names[scenario.start.point]=names[scenario.start.point]||scenario.start.name;
    Object.values(defs).forEach(d=>{if(!names[d.to])names[d.to]=`完成「${d.name}」`;});
    const lines=scenario.lines.map((original,i)=>{const line={...original};
      line.start_point=line.start_point||(defs[line.segments[0]]||{}).from;
      line.end_point=line.end_point||(defs[line.segments[line.segments.length-1]]||{}).to;
      return { ...line, index:i, stat:{...((data.lines||[]).find(x=>x.name===line.name)||{}),avg:mean(data.rounds.map(r=>r.ends[i]))},
      color:palette[i%palette.length], startName:names[line.start_point]||(scenario.start||{}).name||'起点',
      endName:(scenario.point_names||{})[line.end_point]||line.end||line.name };});
    if(audience==='general'){
      Object.keys(names).forEach(k=>names[k]=humanName(names[k]));
      lines.forEach(l=>{l.startName=names[l.start_point]||l.startName;l.endName=names[l.end_point]||humanName(l.endName);});
    }
    lines.forEach(l=>{l.contentDependent=l.stat.verdict==='看回答长短';l.internal=!!(l.actual&&l.was)||l.audience==='technical';});
    const visibleLines=audience==='general'?lines.filter(l=>!l.internal&&!l.stat.bad):lines;
    const segments=Object.fromEntries((data.segments||[]).map(s=>[s.k,{...s,avg:mean(data.rounds.map(r=>r.segs[s.k]))}]));
    const main=visibleLines.find(x=>x.first)||visibleLines.find(x=>x.measure&&!x.contentDependent)||[...visibleLines].reverse().find(x=>!x.contentDependent)||visibleLines[visibleLines.length-1];
    return {data,scenario,lines,visibleLines,segments,main,names,defs,audience};
  }
  function pathInfo(m,line,round) {
    let prev=line.start_point,total=0,valid=true;
    const parts=line.segments.map(k=>{
      const def=m.defs[k]||{},v=round.segs[k],connected=def.from===prev;
      const part={k,def,v,from:m.names[def.from]||'起点',to:m.names[def.to]||'终点',offset:total};
      if(!connected||!finite(v)||v<0)valid=false;
      if(finite(v))total+=v;
      prev=def.to;return part;
    });
    const end=round.ends[line.index];
    valid=valid && prev===line.end_point && finite(end) && Math.abs(total-end)<.000001;
    return {parts,total,end,valid};
  }
  function summarySvg(m) {
    const lines=m.visibleLines.filter(x=>!x.contentDependent),a=axis(Math.max(...lines.map(x=>finite(x.stat.avg)?x.stat.avg:0)));
    if(!lines.length)return '<p class="small-note">没有可展示的完整结果，暂不绘制概览图。</p>';
    const x0=260,w=470,h=lines.length*90+70;
    let s=`<svg class="measure-svg" viewBox="0 0 900 ${h}" role="img" aria-label="各种回应平均要等多久，同一比例">`;
    a.ticks.forEach(t=>{let x=x0+t/a.cap*w;s+=`<line x1="${x}" x2="${x}" y1="15" y2="${h-36}" stroke="#e5ebed"/><text x="${x}" y="${h-12}" text-anchor="middle" fill="#7a8991" font-size="13">${esc(fmt(t))}</text>`;});
    lines.forEach((l,i)=>{
      const y=50+i*90,v=l.stat.avg,x=x0+(finite(v)?v/a.cap*w:0);
      s+=`<foreignObject x="4" y="${y-32}" width="240" height="82"><div xmlns="http://www.w3.org/1999/xhtml" style="font-family:inherit;line-height:1.4"><b style="font-size:16px;color:#263e46;overflow-wrap:anywhere">${esc(l.endName)}</b><div style="font-size:11px;color:#74818e;margin-top:5px">从「${esc(l.startName)}」开始</div></div></foreignObject><line x1="${x0}" x2="${x0+w}" y1="${y}" y2="${y}" stroke="#e3ebea" stroke-width="3"/>`;
      if(finite(v)&&v>=0)s+=`<line x1="${x0}" x2="${x}" y1="${y}" y2="${y}" stroke="${l.color}" stroke-width="4"/><circle cx="${x}" cy="${y}" r="6" fill="${l.color}"/><text x="${x+14}" y="${y+6}" font-size="19" font-weight="700" fill="${l.color}">${esc(fmt(v))}</text>`;
      else s+=`<text x="${x0+14}" y="${y+6}" font-size="16" fill="#74818e">${finite(v)?'计时先后异常':'未测到'}</text>`;
    });return s+'</svg>';
  }
  function roundsOverview(m,options={}) {
    const lines=options.complete?m.visibleLines.filter(l=>l.contentDependent):m.visibleLines.filter(l=>!l.contentDependent),rounds=m.data.rounds;
    const values=rounds.flatMap(r=>lines.map(l=>r.ends[l.index])).filter(v=>finite(v)&&v>=0);
    if(!values.length)return '<p class="small-note">这份报告暂时没有逐次用时记录，不补造图上的数据。</p>';
    const references=m.audience==='general'?[]:[...new Set(lines.map(l=>(l.stat.exp_std||{}).fair).filter(v=>finite(v)&&v>0))];
    const bad=new Map();(m.data.labels||[]).filter(v=>v.kind==='problem'||v.kind==='spiky'||v.kind==='line').forEach(v=>(v.rounds||[]).forEach(n=>bad.set(n,[bad.get(n),v.name||v.why||'这次需要留意'].filter(Boolean).join('；'))));
    const used=new Set(lines.flatMap(l=>l.segments));
    used.forEach(k=>{const s=m.segments[k]||{};if(s.spiky)(s.spiky_rounds||[]).forEach(n=>bad.set(n,'这次有步骤明显偏慢'));});
    const a=axis(Math.max(...values,...references)),left=82,top=45,width=620,rowHeight=26,bottom=top+rounds.length*rowHeight;
    const x=v=>left+v/a.cap*width,y=i=>top+i*rowHeight+rowHeight/2;
    let svg=`<svg class="round-overview-svg" viewBox="0 0 760 ${bottom+22}" role="img" aria-label="逐次点位图：横轴为用时，纵向为测量次数">`;
    a.ticks.forEach(t=>{svg+=`<line x1="${x(t)}" x2="${x(t)}" y1="${top-9}" y2="${bottom}" stroke="#e5ecea"/><text x="${x(t)}" y="${top-20}" text-anchor="middle" font-size="13" fill="#7c8e85">${Number(t.toPrecision(8))} 秒</text>`;});
    rounds.forEach((r,i)=>{
      if(bad.has(r.n))svg+=`<rect data-abnormal-round="${r.n}" x="0" y="${y(i)-13}" width="760" height="26" fill="#fff0e8"><title>${esc(bad.get(r.n))}</title></rect>`;
      const available=lines.map(l=>r.ends[l.index]).filter(v=>finite(v)&&v>=0);
      svg+=`<text x="${left-14}" y="${y(i)+5}" text-anchor="end" font-size="13" fill="#7c8e85">第 ${esc(r.n)} 次</text>`;
      if(available.length)svg+=`<line x1="${x(0)}" x2="${x(Math.max(...available))}" y1="${y(i)}" y2="${y(i)}" stroke="#e4ebe7" stroke-width="2"/>`;
      else svg+=`<text x="${left+12}" y="${y(i)+5}" font-size="12" fill="#8b9790">没测到</text>`;
      lines.forEach((l,j)=>{
        const v=r.ends[l.index];if(!finite(v)||v<0)return;
        const attrs=`data-result="${l.index}" data-round="${r.n}" data-value="${v.toFixed(6)}" data-x="${x(v)}" data-y="${y(i)}" fill="${l.color}" stroke="#fff" stroke-width="1"`;
        const tip=`<title>${esc(l.endName)} · 第 ${r.n} 次：${v.toFixed(6)} 秒</title>`;
        svg+=j%2===0?`<circle ${attrs} cx="${x(v)}" cy="${y(i)}" r="4.7">${tip}</circle>`:`<rect ${attrs} x="${x(v)-4.5}" y="${y(i)-4.5}" width="9" height="9" rx="1">${tip}</rect>`;
      });
    });
    lines.forEach(l=>{if(finite(l.stat.avg)&&l.stat.avg>=0)svg+=`<line data-mean="${l.index}" x1="${x(l.stat.avg)}" x2="${x(l.stat.avg)}" y1="${top-9}" y2="${bottom}" stroke="${l.color}" stroke-dasharray="3 4" opacity=".55"/>`;});
    references.forEach(v=>{svg+=`<line x1="${x(v)}" x2="${x(v)}" y1="${top-9}" y2="${bottom}" stroke="#b08b60" stroke-width="1.5" stroke-dasharray="7 4"/>`;});
    svg+='</svg>';
    const referenceDescription=lines.filter(l=>finite((l.stat.exp_std||{}).fair)).map(l=>`${l.endName}的参考值为 ${l.stat.exp_std.fair} 秒${referenceInfo(m,l).mode==='mean'?'（用于平均值比较）':''}`).join('；');
    const ranges=lines.map(l=>{
      const rows=rounds.filter(r=>finite(r.ends[l.index])&&r.ends[l.index]>=0);
      if(!rows.length)return `<li>${esc(l.endName)}：没有有效记录。</li>`;
      const min=Math.min(...rows.map(r=>r.ends[l.index])),max=Math.max(...rows.map(r=>r.ends[l.index]));
      const slow=rows.filter(r=>r.ends[l.index]===max).map(r=>r.n);
      return `<li><b>${esc(l.endName)}</b>：${rows.length} 次记录，${fmt(min)} ～ ${fmt(max)}${rows.length>1&&min!==max?'；第 '+esc(slow.join('、'))+' 次用时最长':''}。</li>`;
    }).join('');
    return `<div class="round-overview-legend">${lines.map((l,j)=>`<span><i class="point-key ${j%2===0?'point-circle':'point-square'}" style="background:${l.color}"></i>${esc(l.endName)}${l.first?'<small>主要回应</small>':''}</span>`).join('')}<span><i class="median-key"></i>细虚线：平均用时</span>${bad.size?'<span>浅橙底：记录提示需留意的轮次</span>':''}</div><div class="scroll">${svg}</div><p class="small-note">横向越靠右，等待越久；同一种点的横向位置越集中，这几次越稳定，左右跨度表示用时范围。每行是一次测量，缺数留空，不跨轮连线。${m.audience==='technical'?'悬停点位查看原始精度；按轮次在上方完整过程核对分段，代码及全部数据见下方。':''}${references.length?'长虚线：'+esc(referenceDescription)+'。单次点超过此线只说明这一回较慢，不表示整体不达标。':''}</p>${m.audience==='general'?'<ul class="plot-ranges">'+ranges+'</ul><p class="small-note">这里只描述本次记录的波动，不代表长期稳定性。</p>':''}`;
  }
  function sharedPrefix(m) {
    const lines=m.visibleLines;
    const result=(status,keys=[],note='')=>({status,keys,note,tails:Object.fromEntries(lines.map(l=>[l.index,l.segments.slice(keys.length)]))});
    if(lines.length<2)return result('single',[],lines.length?'这里只有一项结果，直接查看完整步骤。':'还没有可比较的结果。');
    for(const line of lines){
      if(!line.start_point||!line.end_point||!line.segments.length)return result('unknown',[],'有些步骤没有记全，暂时无法确认共用部分。');
      let cursor=line.start_point;const visited=new Set([cursor]);
      for(const k of line.segments){
        const d=m.defs[k];
        if(!d||d.from!==cursor||!d.to||visited.has(d.to))return result('unknown',[],'步骤的连接还不能确认，先保留完整过程分别查看。');
        cursor=d.to;visited.add(cursor);
      }
      if(cursor!==line.end_point)return result('unknown',[],'步骤还没有连接到所选结果，暂时不合并。');
    }
    if(lines.some(l=>l.start_point!==lines[0].start_point))return result('different-starts',[],'这些结果从不同位置开始计时，分别展示完整过程。');
    const keys=[];
    const shortest=Math.min(...lines.map(l=>l.segments.length));
    for(let i=0;i<shortest;i++){
      const k=lines[0].segments[i],d=m.defs[k];
      // Identity and directed connection must match. Names and measured times are not evidence.
      if(lines.some(l=>l.segments[i]!==k))break;
      // An ambiguous alternative endpoint is not a confirmed shared boundary.
      if(d.to_any&&new Set(d.to_any).size>1)break;
      keys.push(k);
    }
    return keys.length?result('confirmed',keys):result('none',[],'没有确认一致的开头步骤，分别展示完整过程。');
  }
  // Only explicit source wording or structured metadata selects a comparison statistic.
  // Unknown/percentile requirements are not silently treated as mean requirements.
  function referenceInfo(m,l) {
    const e=l.stat.exp_std||{},values=m.data.rounds.map(r=>r.ends[l.index]).filter(v=>finite(v)&&v>=0);
    const avg=mean(values);
    const mode=e.statistic||(e.source&&/平均\s*(?:用时|响应时间)?\s*(?:≤|<=|不超过|小于等于)/.test(e.source)?'mean':null);
    const usable=!l.stat.bad&&!l.stat.thin&&!l.contentDependent&&finite(e.fair)&&e.fair>=0&&values.length>0;
    return {e,avg,count:values.length,over:values.filter(v=>v>e.fair).length,mode,usable};
  }
  function referenceText(m,l,compact=false) {
    const r=referenceInfo(m,l);
    if(l.stat.bad||l.stat.thin)return '记录有异常或不足，暂不比较快慢。';
    if(l.contentDependent)return '用时受处理内容多少影响，只记录，不判快慢。';
    if(!r.usable)return '没有适用的用时参考，暂不按标准判快慢。';
    if(r.mode==='mean')return `按来源的平均值口径，本次${compact?'':'平均 '+fmt(r.avg)+'，'}${r.avg<=r.e.fair?'未超过':'超过'} ${fmt(r.e.fair)} 的参考值。`;
    return `本报告引用的参考值为 ${fmt(r.e.fair)}；统计口径尚未明确匹配，不作整体达标结论。`;
  }
  function issueRows(m,keys) {
    return keys.filter(k=>{const s=m.segments[k]||{};return s.spiky||s.bad||s.thin;}).map(k=>{
      const s=m.segments[k],d=m.defs[k]||{},title=`${m.names[d.from]||'起点'} → ${m.names[d.to]||'终点'}`;
      const ns=s.bad?s.bad_rounds:s.spiky?s.spiky_rounds:[];
      const detail=s.bad?'记录的先后顺序异常，暂不比较快慢':s.thin?`只记录到 ${s.count||0}/${s.of||m.data.n} 次，数据不足`:`平均 ${fmt(s.avg)}，最慢 ${fmt(s.max)}，有时明显更慢`;
      return `<li><b>${esc(title)}</b><span>${esc(detail)}${ns&&ns.length?'；第 '+esc(ns.join('、'))+' 次':''}</span></li>`;
    }).join('');
  }
  function findings(m,advice) {
    const keys=[...new Set(m.visibleLines.flatMap(l=>l.segments))],issues=issueRows(m,keys);
    const missing=m.lines.filter(l=>l.internal&&l.was).length;
    const quality=(m.data.checks||[]).some(c=>['bad','bad_line','thin','thin_line'].includes(c.kind));
    const msg=advice==='done'?'建议分析已完成，具体方案和依据见下方。':advice==='running'?'修改建议正在生成，测量结果已可查看。':advice==='failed'?'修改建议尚未就绪，具体状态见下方。':'尚未生成修改建议。';
    return `<div class="report-findings"><p><b>参考判断：</b>${m.main?esc(referenceText(m,m.main,true)):'还没有可比较的完整结果。'}</p><p><b>需要留意：</b>${issues?'有步骤出现波动或记录异常，见下方用时拆解。':quality?'部分记录有异常或不足，不能据此认定整体正常。':'按现有分段检查，未标出明显波动；这不等于所有功能都已验证。'}${missing?'另有 '+missing+' 项原定结果没有直接测到。':''}</p><p><b>修改建议：</b>${esc(msg)} <a href="#sec-advice">查看建议状态</a></p>${issues?'<a href="#sec-tl">查看需要留意的步骤 ↓</a>':''}</div>`;
  }
  function comparison(m) {
    return `<div class="reference-list">${m.visibleLines.filter(l=>!l.contentDependent).map(l=>{
      const r=referenceInfo(m,l),s=l.stat,vs=s.vs;
      return `<article class="reference-item"><h3>${esc(l.endName)}</h3><p>${esc(referenceText(m,l))}</p>${r.usable?`<p class="small-note">单次观察：${r.count} 次记录中，${r.over} 次超过 ${fmt(r.e.fair)}。未超过的有 ${r.count-r.over}/${r.count} 次（${Math.round((r.count-r.over)/r.count*100)}%）。这只是本组记录的分布，不是标准通过率。</p>`:''}${vs?`<details><summary>历史比较（原记录采用中位数）</summary><p>${esc((finite(vs.prev)?fmt(vs.prev)+' → '+fmt(s.med)+'，':'')+(vs.change||'已有比较'))}</p><p class="small-note">未保存上次平均值，不与本次平均用时混比。</p></details>`:'<p class="small-note">没有上次的可比记录。</p>'}</article>`;
    }).join('')}</div>`;
  }
  function costFocus(m,keys) {
    const valid=keys.filter(k=>{const s=m.segments[k]||{};return !s.bad&&!s.thin&&finite(s.avg)&&s.avg>=0;});
    if(!valid.length)return '<p class="small-note">这部分还没有足够的有效记录，暂不判断主要用时。</p>';
    const max=Math.max(...valid.map(k=>m.segments[k].avg));
    const largest=valid.filter(k=>m.segments[k].avg===max&&!m.segments[k].spiky);
    if(!largest.length)return ''; // The anomaly summary already shows these same steps and values.
    return `<div class="cost-focus"><b>平均用时较长的步骤</b><ul>${largest.map(k=>{const d=m.defs[k];return `<li>${esc(m.names[d.from]||'起点')} → ${esc(m.names[d.to]||'终点')}：<strong>单步平均 ${fmt(m.segments[k].avg)}</strong></li>`;}).join('')}</ul><p class="small-note">用时较长不能据此确定原因。</p></div>`;
  }
  function sharedHtml(m,split,prefix='') {
    if(!split.keys.length)return `<p class="shared-status small-note">${esc(split.note)}</p>`;
    const first=m.visibleLines[0],last=m.defs[split.keys[split.keys.length-1]],end=m.names[last.to]||'分开的位置';
    const issues=issueRows(m,split.keys);
    return `<section class="shared-steps"><div class="shared-heading"><h3>共同经过的步骤</h3><span>${split.keys.length} 步 · 切换结果时不变</span></div><p class="small-note">当前展示的 ${m.visibleLines.length} 项结果，都先从「${esc(first.startName)}」经过相同的步骤，到「${esc(end)}」为止。${m.audience==='technical'?'本页还包括内部记录，共同范围可能与结论页不同。':''}</p>${m.audience==='general'?costFocus(m,split.keys):''}${issues?'<div class="shared-alert"><b>共同部分需要留意</b><ul class="issue-list">'+issues+'</ul></div>':''}<details id="${prefix}shared-fold"><summary>${issues?'查看相关分段及全部':'展开'} ${split.keys.length} 个共同步骤</summary>${m.audience==='technical'?technicalTable(m,split.keys):costHtml(m,first.index,{keys:split.keys})}</details></section>`;
  }

  function costHtml(m,index,options={}) {
    const line=m.lines[index],keys=options.keys||line.segments,startAt=options.startAt||0,values=keys.map(k=>m.segments[k]||{});
    const used=new Set(m.visibleLines.flatMap(l=>l.segments));
    const scale=axis(Math.max(...[...used].map(k=>finite((m.segments[k]||{}).avg)?m.segments[k].avg:0)));
    if(!keys.length)return '<p class="empty-tail">经过上面的共同步骤，就已经到达这个结果，没有额外步骤。</p>';
    return `<p class="small-note">横条越长，花的时间越多。每段按该段有记录的次数取平均；只有同批记录、路径连续完整时才能相加。</p><div class="cost-scale">横条满宽 = ${esc(fmt(scale.cap))}</div>`+keys.map((k,i)=>{
      const s=values[i],d=m.defs[k]||{},name=`${m.names[d.from]||'起点'} → ${m.names[d.to]||'终点'}`;
      const caption=m.audience==='general'?`${s.count||0}/${s.of||m.data.n} 次有记录${s.bad?' · 记录先后异常':s.thin?' · 记录不足':s.spiky?' · 有时明显更慢，最慢 '+fmt(s.max):''}`:`${esc(d.name||s.name||'')} · 涉及${esc(d.owner||s.owner||'未确认')} · ${s.count||0}/${s.of||m.data.n} 轮有记录${s.spiky?' · 有一次或多次明显偏慢':''}`;
      return `<div class="cost-row"><div class="cost-heading"><span>${startAt+i+1}. ${esc(name)}</span><b>单步平均 ${esc(fmt(s.avg))}</b></div><div class="duration-track">${finite(s.avg)&&s.avg>=0?`<i style="width:${s.avg/scale.cap*100}%"></i>`:''}</div><div class="cost-caption">${caption}</div></div>`;
    }).join('');
  }
  function codeContext(m,point) {
    const c=(m.data.code||{})[point];
    if(!c||!Array.isArray(c.ctx))return '';
    return `<div class="source-context"><b>${esc(m.names[point]||'计时位置')}</b><p class="code">${esc(c.path)}:${esc(c.l)}${c.func?' · '+esc(c.func):''}</p><pre class="codectx">${c.ctx.map(([n,t])=>`<span class="${n===c.l?'hit':''}"><i>${esc(n)}</i>${esc(t)}</span>`).join('\n')}</pre></div>`;
  }
  function pointLocation(m,point) {
    const c=(m.data.code||{})[point],p=(m.scenario.points||{})[point];
    if(c)return `<span class="code">${esc(c.path)}:${esc(c.l)}</span>${c.func?`<span class="location-function">${esc(c.func)}</span>`:''}`;
    return p?`<span class="code">${esc(p.f||'')}：${esc(p.l||'未提供行号')}</span>`:'未保存代码位置';
  }
  function technicalTable(m,keys,startAt=0) {
    if(!keys.length)return '<p class="empty-tail">共同步骤已到达这条线的终点，没有额外分段。</p>';
    const rows=keys.map((k,i)=>{
      const d=m.defs[k]||{},s=m.segments[k]||{},points=[...new Set([d.from,d.to].filter(Boolean))];
      const context=points.map(p=>codeContext(m,p)).join('');
      const previous=((m.data.prev||{}).segs||{})[k];
      const verdict=[s.bad?'数据有问题':null,s.thin?'数据不足':null,s.spiky?'偶发特别慢':null,(s.vs&&s.vs.change?'历史中位数：'+s.vs.change:s.verdict)].filter(Boolean).join(' · ')||'未判';
      const locations=points.map((p,j)=>`<div class="endpoint-location"><small>${j?'终点':'起点'}：${esc(m.names[p]||'未命名')}</small>${pointLocation(m,p)}</div>`).join('');
      const extra=`<details class="segment-more"><summary>更多数据与位置</summary><dl><div><dt>最快</dt><dd>${fmt(s.min)}</dd></div><div><dt>90%分位</dt><dd>${fmt(s.p90)}</dd></div><div><dt>有效次数</dt><dd>${s.count==null?'未提供':s.count}/${s.of==null?m.data.n:s.of}</dd></div><div><dt>上次（中位数）${m.data.prev&&m.data.prev.measured_at?' · '+esc(m.data.prev.measured_at):''}</dt><dd>${finite(previous)?fmt(previous):'没有可比记录'}${s.vs?' · '+esc(s.vs.change||s.vs):''}</dd></div><div><dt>参与方</dt><dd>${esc(d.owner||s.owner||'未确认')}</dd></div></dl>${locations}</details>`;
      return `<tr class="technical-segment" data-segment="${esc(k)}"><th>${startAt+i+1}. ${esc(d.name||s.name||'未命名分段')}<span class="segment-boundaries">${esc(m.names[d.from]||'起点')} → ${esc(m.names[d.to]||'终点')}</span><button class="pill codebtn" data-code="1" ${context?'':'disabled'}>展开代码</button>${context?'':'<small>还差：源码上下文。</small>'}${extra}</th><td data-label="平均用时">${fmt(s.avg)}</td><td data-label="最慢">${fmt(s.max)}</td><td data-label="中位数（补充）">${fmt(s.med)}</td><td data-label="判断">${esc(verdict)}${s.spiky_rounds&&s.spiky_rounds.length?'<br>第 '+esc(s.spiky_rounds.join('、'))+' 次':''}</td></tr><tr class="coderow" hidden><td colspan="5">${context}</td></tr>`;
    }).join('');
    return `<table class="technical-table"><colgroup><col class="segment-col"><col><col><col><col></colgroup><thead><tr><th>分段与代码</th><th>平均用时</th><th>最慢</th><th>中位数<br>（补充）</th><th>判断</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  function lineSummary(m,index) {
    const l=m.lines[index],s=l.stat,previous=((m.data.prev||{}).lines||{})[l.name];
    const medians=l.segments.map(k=>(m.segments[k]||{}).med),sum=medians.every(finite)?medians.reduce((a,b)=>a+b,0):null;
    const cells=[['中位数（补充）',fmt(s.med)],['最慢',fmt(s.max)],['最快',fmt(s.min)],['90%分位',fmt(s.p90)],['有效次数',`${s.count==null?'未提供':s.count}/${s.of==null?m.data.n:s.of}`],['和上次比（中位数）',finite(previous)?fmt(previous)+(s.vs?' · '+(s.vs.change||s.vs):''):'没有可比记录']];
    return `<div class="line-summary"><h4>${esc(l.startName)} → ${esc(l.endName)} · 补充统计</h4><dl>${cells.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl><p>${esc(referenceText(m,l,true))}</p><details><summary>原始规则判定与统计口径</summary><p>以下保留后台已有判定；其中体验描述按中位数，不能替代上面的平均值比较或完整标准验收。</p><p>${esc(s.combo||s.verdict||'未判')}${s.exp?' · 体验描述：'+esc(s.exp):''}</p><p class="small-note">${finite(sum)?`各段中位数相加 ${fmt(sum)}；整条线路按每次首尾计时后取中位数，不能用分段中位数的和替代。`:'有些分段缺少用时，整条线路仍按可用的首尾记录展示。'}</p></details></div>`;
  }
  function pointCatalog(m) {
    const points=[...new Set([...Object.keys(m.scenario.points||{}),...Object.keys(m.data.code||{})])];
    return `<div class="scroll"><table class="point-table"><thead><tr><th>计时位置</th><th>文件与函数</th><th>原始绑定</th></tr></thead><tbody>${points.map(p=>{const def=(m.scenario.points||{})[p]||{},ctx=codeContext(m,p);return `<tr data-source-point="${esc(p)}"><th>${esc(m.names[p]||def.n||'未命名')}</th><td>${pointLocation(m,p)}<button class="pill codebtn" data-code="1" ${ctx?'':'disabled'}>展开代码</button>${ctx?'':'<small>还差：源码上下文。</small>'}</td><td class="code">${esc(def.at||'未保存绑定语句')}</td></tr><tr class="coderow" hidden><td colspan="3">${ctx}</td></tr>`;}).join('')}</tbody></table></div>`;
  }
  function roundHtml(m,index,n) {
    const line=m.lines[index],round=m.data.rounds.find(r=>r.n===n);
    if(!round)return '<p class="small-note">本次没有可回放的逐轮记录，不绘制过程。</p>';
    const p=pathInfo(m,line,round);
    const a=axis(Math.max(...m.data.rounds.map(r=>r.ends[index]).filter(finite))),x0=18,w=820;
    let svg='';
    if(p.valid){
      svg=`<svg class="measure-svg round-svg" viewBox="0 0 900 102" role="img" aria-label="第${n}次该条线路的真实连续分段">`;
      a.ticks.forEach(t=>{const x=x0+t/a.cap*w;svg+=`<line x1="${x}" x2="${x}" y1="10" y2="73" stroke="#e3ebed"/><text x="${x}" y="94" text-anchor="${t===0?'start':'middle'}" font-size="12" fill="#74818e">${esc(fmt(t))}</text>`;});
      p.parts.forEach((part,i)=>{const x=x0+part.offset/a.cap*w,width=part.v/a.cap*w;svg+=`<rect x="${x}" y="28" width="${width}" height="25" fill="${palette[i%palette.length]}"><title>${esc(part.from+' → '+part.to+'：'+fmt(part.v))}</title></rect>`;if(width>28)svg+=`<text x="${x+width/2}" y="45" text-anchor="middle" font-size="12" fill="white">${i+1}</text>`;});svg+='</svg>';
    }
    const exact=v=>finite(v)?v.toFixed(6)+' 秒':'未测到';
    if(m.audience==='general')return `<div class="round-verdict"><b>第 ${n} 次 · 共等待 ${esc(fmt(p.end))}</b><span>${p.valid?'这一轮的各步用时已核对。':'这次有些步骤没有记全，暂不画完整过程。'}</span></div>${svg}<ol class="round-parts">${p.parts.map((s,i)=>`<li><i style="background:${palette[i%palette.length]}">${i+1}</i><span>${esc(s.from)} → ${esc(s.to)}</span><b>${esc(fmt(s.v))}</b></li>`).join('')}</ol><p class="small-note">这是同一次测量。显示值四舍五入，相加可能差几毫秒。</p><button class="btn" data-report-tech="1">核对详细数据</button>`;
    return `<div class="round-verdict"><b title="${exact(p.end)}">第 ${n} 次 · 首尾实测 ${esc(fmt(p.end))}</b><span>${p.valid?'按原始精度核对：分段连接，分段和与首尾用时一致。':'分段缺失、连接不完整或与首尾不符，暂不画连续时间线。'}</span></div>${svg}<ol class="round-parts">${p.parts.map((s,i)=>`<li><i style="background:${palette[i%palette.length]}">${i+1}</i><span>${esc(s.from)} → ${esc(s.to)}</span><b title="${exact(s.v)}">${esc(fmt(s.v))}</b></li>`).join('')}</ol><p class="small-note">这是同一次测量。短段按真实比例绘制；显示值四舍五入，相加可能差几毫秒。</p><details><summary>核对精确用时</summary><table><thead><tr><th>分段</th><th>原始精度（秒）</th></tr></thead><tbody>${p.parts.map(s=>`<tr><td>${esc(s.from)} → ${esc(s.to)}</td><td>${exact(s.v)}</td></tr>`).join('')}<tr><th>分段和</th><td>${p.valid?exact(p.total):'无法完整核对'}</td></tr><tr><th>首尾实测</th><td>${exact(p.end)}</td></tr></tbody></table></details>`;
  }
  function resultOptions(m) {
    return m.visibleLines.map(l=>({index:l.index,label:l.endName,available:(finite(l.stat.med)&&l.stat.med>=0)||m.data.rounds.some(r=>finite(r.ends[l.index])&&r.ends[l.index]>=0)}));
  }
  function resultChoices(m,selected,prefix='') {
    const options=resultOptions(m);
    if(!options.length)return '<p class="small-note">这份报告还没有可展示的结果。</p>';
    return options.map(o=>`<div class="result-choice"><button type="button" class="btn result-button" data-line="${o.index}" aria-pressed="${o.index===selected}" ${o.available?'':'disabled'}><span class="result-card-label">${esc(o.label)}</span><span class="result-card-note">${o.index===selected?'正在查看':o.available?'查看单步用时':'还没有记录'}</span></button>${o.available?'':'<small>还差：这项结果的有效用时记录。</small>'}</div>`).join('');
  }
  // Only vocabulary is translated. Point identity, endpoints and measured values are untouched.
  function humanName(name) {
    return String(name||'').replace(/语音活动上报/g,'程序发出说话状态通知').replace(/上传首包/g,'上传第一部分数据').replace(/上传末包/g,'上传最后一部分数据').replace(/\bUDP\b\s*/gi,'数据').replace(/\bASR\b/gi,'语音识别').replace(/\bTTS\b/gi,'语音合成').replace(/\bVAD\b/gi,'说话状态判断').replace(/报文/g,'消息').replace(/首包/g,'第一部分数据').replace(/末包/g,'最后一部分数据').replace(/上屏/g,'显示在屏幕上').replace(/缓冲/g,'待处理数据').replace(/固件/g,'设备程序');
  }
  const states=new Map();
  function stateKey(data,audience){return JSON.stringify([audience,data.measured_at,data.scenario.start,data.scenario.lines.map(l=>[l.name,l.segments]),data.rounds.map(r=>r.n)]);}
  function selectedState(m){
    const key=stateKey(m.data,m.audience),options=resultOptions(m),available=options.filter(o=>o.available);
    let state=states.get(key)||{};
    if(!available.some(o=>o.index===state.selected))state.selected=(available.find(o=>m.main&&o.index===m.main.index)||available[0]||{}).index;
    if(!m.data.rounds.some(r=>r.n===state.round))state.round=(m.data.rounds[0]||{}).n;
    states.set(key,state);return state;
  }
  function tailHtml(m,split,i){
    if(i==null)return '<p>还没有可展示的分段记录。</p>';
    const keys=split.tails[i];
    if(m.audience==='technical')return technicalTable(m,keys,split.keys.length);
    const costs=costHtml(m,i,{keys,startAt:split.keys.length});
    if(!keys.length)return costs;
    const issues=issueRows(m,keys);
    return `${costFocus(m,keys)}${issues?'<ul class="issue-list">'+issues+'</ul>':''}<details><summary>查看这部分全部 ${keys.length} 步的用时</summary>${costs}</details>`;
  }
  function resultHeading(m,i){
    if(i==null)return '查看其中各步';
    const l=m.lines[i],s=l.stat,label=l.contentDependent?'完整过程':m.audience==='technical'?'首尾用时':'完整等待';
    const value=s.bad?'计时先后异常':s.thin?'记录不足':finite(s.avg)&&s.avg>=0?fmt(s.avg):'未测到';
    return `${l.endName}｜${label} · 平均 ${value}`;
  }
  function resultScope(m,i){
    if(i==null)return '';
    const l=m.lines[i];return `总用时范围：「${l.startName}」→「${l.endName}」。`;
  }
  function tailNote(split,i){return split.keys.length?(split.tails[i].length?`共用步骤已在上方列出；下面是其余 ${split.tails[i].length} 步。`:'完成共同步骤，就已经到达这个结果。'):'下面是到达所选结果的全部步骤。';}
  function panel(data,audience='general') {
    const m=model(data,audience),split=sharedPrefix(m),state=selectedState(m),i=state.selected;
    const json=JSON.stringify(data).replace(/</g,'\\u003c').replace(/&/g,'\\u0026');
    return `<div class="report-inspect" data-audience="${audience}"><script type="application/json" data-role="report-data">${json}</script>${audience==='general'?`<h3>各个结果，平均要等多久</h3><p class="small-note">从各自起点到结果的平均用时，按同一比例绘制。图中名称表示程序记下的事件，是否实际完成可在技术细节核对。</p><div class="scroll">${summarySvg(m)}</div>`:''}${sharedHtml(m,split)}<section class="different-steps"><div class="different-heading"><h3>查看其中各步</h3><p class="small-note">点右侧卡片切换结果。两版均以平均用时为主；单次实测与补充统计另行标明。</p></div><div class="result-switcher"><div class="result-heading"><h3 data-role="cost-title">${esc(resultHeading(m,i))}</h3><p data-role="total-scope" class="small-note">${esc(resultScope(m,i))}</p><p data-role="different-note" class="switch-hint">${i==null?'有记录后可查看。':tailNote(split,i)}</p></div><div class="result-choices" data-role="choices">${resultChoices(m,i)}</div></div><div data-role="cost-content">${tailHtml(m,split,i)}</div>${audience==='technical'?`<div data-role="line-summary">${i==null?'':lineSummary(m,i)}</div>`:''}</section><details class="single-round"><summary>查看某一次的完整过程</summary><label class="round-select">选择测量次数 <select data-role="round-select" aria-label="选择测量次数">${data.rounds.map(r=>`<option value="${r.n}" ${r.n===state.round?'selected':''}>第 ${esc(r.n)} 次</option>`).join('')}</select></label><div data-role="round-content">${i==null?'':roundHtml(m,i,state.round)}</div></details></div>`;
  }
  function valuesTable(data,kind='lines',audience='technical') {
    const m=model(data,audience),cols=kind==='lines'?m.visibleLines.map(l=>({key:l.index,name:l.endName})):Object.keys(m.defs).map(k=>({key:k,name:m.defs[k].name}));
    return `<div class="scroll"><table class="raw-values"><thead><tr><th>测量次数</th>${cols.map(c=>`<th>${esc(c.name)}</th>`).join('')}</tr></thead><tbody>${data.rounds.map(r=>`<tr><th>第 ${esc(r.n)} 次</th>${cols.map(c=>{const v=kind==='lines'?r.ends[c.key]:r.segs[c.key];return `<td>${finite(v)?v.toFixed(6):'未测到'}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function extra(data){
    const m=model(data),covered=new Set(m.lines.flatMap(l=>l.segments)),keys=Object.keys(m.defs).filter(k=>!covered.has(k));
    return `${keys.length?'<section class="panel"><h2>独立记录的步骤</h2><p class="small-note">这些步骤同时发生，但不在上述结果的完整路径中，不加入线路合计。</p>'+technicalTable(m,keys)+'</section>':''}<section class="panel"><h2>完整数据与代码位置</h2><details><summary>全部计时位置与源码上下文</summary>${pointCatalog(m)}</details><details><summary>每次完整结果（秒，保留原始精度）</summary>${valuesTable(data)}</details><details><summary>每次全部分段（秒，保留原始精度）</summary>${valuesTable(data,'segments')}</details><button class="pill" data-report-download="1">下载完整测量数据</button><script type="application/json" data-role="download-data">${JSON.stringify(data).replace(/</g,'\\u003c').replace(/&/g,'\\u0026')}</script></section>`;
  }
  function recordOnly(data,record){
    if(!record||!(record.lines.length||record.segments.length||(record.parallel||[]).length))return '';
    const m=model(data,'general'),all=model(data),rows=[];
    record.lines.forEach(name=>{const line=all.lines.find(l=>l.name===name);if(line)rows.push({name:humanName(line.startName)+' → '+humanName(line.endName),stat:line.stat});});
    [...new Set([...record.segments,...(record.parallel||[])])].forEach(k=>{const d=m.defs[k]||{};rows.push({name:humanName(d.plain||d.short||d.name||'另外记录的一步'),stat:m.segments[k]||{}});});
    return `<section class="panel" id="sec-rec"><h2>只记录、不判快慢</h2><p class="small-note">这些记录帮助了解完整过程和同时发生的其他步骤，不加入上面的回应时间合计。受处理内容多少影响的记录，不直接判断快慢。</p><details><summary>查看完整过程及独立步骤的全部统计</summary>${rows.map(row=>`<div class="line-summary"><h4>${esc(row.name)}</h4><dl><div><dt>平均用时</dt><dd>${fmt(row.stat.avg)}</dd></div><div><dt>最快</dt><dd>${fmt(row.stat.min)}</dd></div><div><dt>最慢</dt><dd>${fmt(row.stat.max)}</dd></div><div><dt>有记录的次数</dt><dd>${row.stat.count==null?'未提供':row.stat.count}/${row.stat.of||data.n}</dd></div></dl></div>`).join('')}</details></section>`;
  }
  function paint(box,changeLine){
    const data=JSON.parse(box.querySelector('[data-role="report-data"]').textContent),m=model(data,box.dataset.audience),split=sharedPrefix(m),state=selectedState(m),i=state.selected;
    if(i==null)return;
    if(changeLine){
      box.querySelector('[data-role="cost-title"]').textContent=resultHeading(m,i);
      box.querySelector('[data-role="total-scope"]').textContent=resultScope(m,i);
      box.querySelector('[data-role="different-note"]').textContent=tailNote(split,i);
      box.querySelectorAll('[data-line]').forEach(b=>{const active=Number(b.dataset.line)===i;b.setAttribute('aria-pressed',String(active));b.querySelector('.result-card-note').textContent=active?'正在查看':b.disabled?'还没有记录':'查看单步用时';});
      box.querySelector('[data-role="cost-content"]').innerHTML=tailHtml(m,split,i);
      const total=box.querySelector('[data-role="line-summary"]');if(total)total.innerHTML=lineSummary(m,i);
    }
    box.querySelector('[data-role="round-content"]').innerHTML=roundHtml(m,i,state.round);
  }
  // An exported file starts with a fresh script state; hydrate from its saved controls.
  function controlState(box,m){
    const state=selectedState(m),active=box.querySelector('[data-line][aria-pressed="true"]'),select=box.querySelector('[data-role="round-select"]');
    if(active)state.selected=Number(active.dataset.line);
    if(select&&m.data.rounds.some(r=>r.n===Number(select.value)))state.round=Number(select.value);
    return state;
  }
  function install(onTech){
    document.addEventListener('click',e=>{
      const tech=e.target.closest('[data-report-tech]');if(tech){onTech(tech.dataset.reportTarget||'');return;}
      const download=e.target.closest('[data-report-download]');if(download){
        const raw=download.parentElement.querySelector('[data-role="download-data"]').textContent,url=URL.createObjectURL(new Blob([raw],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='measurement-data.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;
      }
      const b=e.target.closest('[data-line]'),box=b&&b.closest('.report-inspect');if(!box||b.disabled)return;
      const data=JSON.parse(box.querySelector('[data-role="report-data"]').textContent),m=model(data,box.dataset.audience),state=controlState(box,m);state.selected=Number(b.dataset.line);paint(box,true);
    });
    document.addEventListener('change',e=>{const select=e.target.closest('[data-role="round-select"]'),box=select&&select.closest('.report-inspect');if(!box)return;const m=model(JSON.parse(box.querySelector('[data-role="report-data"]').textContent),box.dataset.audience);controlState(box,m).round=Number(select.value);paint(box,false);});
  }
  const api={esc,fmt,finite,median,mean,axis,model,pathInfo,summarySvg,roundsOverview,costHtml,roundHtml,resultOptions,resultChoices,sharedPrefix,sharedHtml,referenceInfo,referenceText,issueRows,costFocus,resultHeading,resultScope,findings,comparison,technicalTable,lineSummary,pointCatalog,humanName,panel,valuesTable,extra,recordOnly,install};
  if(typeof module!=='undefined')module.exports=api;
  root.ReportLayout=api;
})(typeof globalThis!=='undefined'?globalThis:this);
