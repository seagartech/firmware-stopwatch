// 演示数据（一个起点 + 几条线 + 每条线的段等）从后台 /api/demo 读，代码里不写死任何场景
let SCENES={},FF={},COLLECT={},RUNS={},RANGE={},RT={},PROJ=[];
// 原型底部"演示栏"的开关，真软件里改用网址参数：?demo= 选演示数据（选源码后面的页面先用它），?fail= 演示出错（nodev/build/burn/try/slowbg），?retest= 改完再测的情况，?settings=first|done 设置页状态
const QS=new URLSearchParams(location.search);
const FAIL=QS.get('fail')||'',DEMO=QS.get('demo')||'',RTCASE=QS.get('retest')||'same';
// ===== 通用计算 =====
const sorted=a=>[...a].sort((x,y)=>x-y);
const med=a=>{const s=sorted(a),m=s.length/2;return s.length%2?s[m|0]:(s[m-1]+s[m])/2};
const p90=a=>sorted(a)[Math.ceil(a.length*.9)-1];
const mx=a=>Math.max(...a);
const f=(x,d=2)=>x.toFixed(d);
const LCOL=['var(--face)','var(--speak)','var(--warn)','var(--muted)'];
const LSOFT=['var(--face-soft)','var(--speak-soft)','var(--soft)','var(--soft)'];
function build(S){S.lines.forEach(l=>{if(l.slow==null)l.slow=Infinity});  // 没有算慢线：不算慢
  const n=Object.values(S.segs)[0].v.length;
  const use={};S.lines.forEach(l=>l.segs.forEach(k=>use[k]=(use[k]||0)+1));
  const lines=S.lines.map((l,i)=>{const ends=[...Array(n)].map((_,r)=>l.segs.reduce((t,k)=>t+S.segs[k].v[r],0));
    return {...l,i,col:LCOL[i],soft:LSOFT[i],ends,med:med(ends),p90:p90(ends),max:mx(ends),segMed:l.segs.map(k=>med(S.segs[k].v))}});
  const rounds=[...Array(n)].map((_,r)=>({n:r+1,ends:lines.map(l=>l.ends[r])}));
  let reversed=[];
  if(S.firstBeforeSecond&&lines.length>1)reversed=rounds.filter(r=>r.ends[0]>r.ends[1]);
  const slowRounds=rounds.filter(r=>lines.some((l,i)=>r.ends[i]>l.slow));
  const allSegs=Object.entries(S.segs).map(([k,s])=>({k,...s,med:med(s.v),max:mx(s.v)}));
  const biggest=[...allSegs].sort((a,b)=>b.med-a.med)[0];
  const spiky=allSegs.filter(s=>s.max>s.med*2.5&&s.max-s.med>.3).sort((a,b)=>(b.max-b.med)-(a.max-a.med))[0];
  const main=lines[lines.length-1];
  const verdict=l=>l.med>l.slow?['慢','c-bad']:l.med>l.ok[1]?['偏慢','c-warn']:['正常','c-ok'];
  const exp=S.exp?(main.med>S.exp.fair?['差','c-bad']:['可以','c-ok']):null;
  // 计时图画"真实的一轮"：选各条线都最接近中位数的那一轮（2026-10-13 用户问"是某一次还是平均值"后改）
  const rep=[...rounds].map(r=>({r,d:r.ends.reduce((t,e,i)=>t+Math.abs(e-lines[i].med)/lines[i].med,0)})).sort((a,b)=>a.d-b.d)[0].r;
  const out={S,n,use,lines,rounds,reversed,slowRounds,allSegs,biggest,spiky,main,verdict,exp,rep};
  return S.J?withJudge(out,S.J):out;
}
// 报告用这次多轮实测真收到的数（演示数据回放时和演示数据一样）；选了真源码时，段、线、范围都按真步骤和对上的标准
let JREP=null,ADV=null,advT=null;
// 为什么、怎么改（任务 17）：选了真源码、测完后，让 AI 读代码和这次的数据写（问题是码表按规则算的），这里每秒问一次
const D0ok=()=>okRounds().length>0;
async function advStart(){ADV={status:'running',read:''};fixPick=null;render();
 const r=await fetch('/api/advise/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({work:REAL.work,steps:IDENT.page.steps,what:hSay,project_dir:REAL.project_dir})});
 const j=await r.json();if(!r.ok){ADV={status:'failed',message:j.detail};render();return}ADV=j;clearInterval(advT);
 advT=setInterval(async()=>{try{const x=await (await fetch('/api/advise')).json();if(x.status!=='running'){clearInterval(advT);advT=null;fixPick=null}ADV=x;if(page==='report')render();if(x.status==='done'&&EXP.state==='wait'){EXP.state='saving';render();exportReport(EXP.dir)}else if(x.status==='failed'&&EXP.state==='wait'){EXP.state='';render()}}catch(e){}},1000)}
function advBox(){if(!ADV||ADV.status==='done')return '';
 if(ADV.status==='failed')return `<div class="errbox"><b>为什么、怎么改还没写出来</b><p style="margin:6px 0">${ADV.message||''}</p><div class="acts" style="margin:8px 0 0"><button class="btn pri" data-advretry="1">重试</button></div></div>`;
 return `<div class="panel"><p style="margin:0"><span class="spin"></span> AI 正在读代码和这次的数据，写"为什么、怎么改"……（调用一次大模型，一般 1～3 分钟）</p>${ADV.read?`<p class="note" style="margin:6px 0 0">最近在看：<span class="code">${htmlEsc(ADV.read)}</span></p>`:''}<p class="note" style="margin:6px 0 0">要改哪些问题是码表按规则从数据算出来的；AI 只写原因和方案，码表只给建议，不改代码。</p></div>`}
const repKey=()=>(realFlash()&&FSWJ&&FSWJ.status==='done'?'real':'demo:'+scene)+':'+okRounds().length;
async function fetchReport(){const key=repKey(),u=key.startsWith('real')?'/api/report':'/api/report?demo='+encodeURIComponent(scene);
 try{const r=await fetch(u);JREP=r.ok?{...(await r.json()),_key:key}:{_key:key,_none:true}}catch(e){JREP={_key:key,_none:true}}if(page==='report')render()}
function repScene(){const J=JREP,S0=SCENES[scene],real=realFlash()&&FSWJ&&FSWJ.status==='done';
 const val=k=>J.rounds.map(r=>r.segs[k]);
 if(!real){const S={...S0,segs:Object.fromEntries(Object.entries(S0.segs).map(([k,g])=>[k,{...g,v:J.rounds.map(r=>r.segs[k]??g.v[0])}])),J};return S}
 const sc=J.scenario,st=Object.fromEntries((IDENT.page.steps||[]).map(x=>[x.p,x])),rng=r=>r?`${r[0]}～${r[1]??'不限'}`:'—';
 const segs=Object.fromEntries(Object.entries(sc.segments).map(([k,g])=>[k,{name:g.name,what:g.what,owner:g.owner,code:`${g.from}→${g.to}`,ok:rng(g.range),v:val(k)}]));
 const P=IDENT.page,[sShort,mEnd]=(P.measure||'起点 → 终点').split('→').map(x=>x.trim()),sEnd=P.sideAsk?P.sideAsk.q:'';
 const lines=sc.lines.map((l,i)=>{const side=i<sc.lines.length-1,end=side?(sEnd||l.end):mEnd;return {name:l.name,end,phrase:(side?'先':'后')+end,segs:l.segments,ok:l.range,slow:l.slow??Infinity}});
 const exp=sc.experience?{fair:sc.experience.fair,label:`${sc.experience.fair} 秒：超过算体验差（${sc.experience.source||'体验标准'}）`}:null;
 const start=st[sc.start.point];
 return {crumb:`${htmlEsc(pName)} · ${htmlEsc(sShort)}到${htmlEsc(mEnd)} · ${J.n} 轮`,real:true,startShort:sShort,start:`${sShort}（${start?start.n:'起点'}）`,est:{label:'推算段',note:'起点之前设备看不见的那段，不计入'},exp,segs,lines,
  firstBeforeSecond:lines.length>1,fixes:ADV&&ADV.status==='done'?ADV.fixes:[],fine:ADV&&ADV.status==='done'&&ADV.fine?htmlEsc(ADV.fine):(()=>{const ok=J.segments.filter(g=>!g.outside&&g.verdict==='正常'&&!g.spiky);return ok.length?`设备自己的几段（${ok.map(g=>`${htmlEsc(g.name)} ${f(g.med)} 秒`).join('、')}）都正常，不用改。`:'设备自己的几段还没有能判的。'})(),
  tech:{cond:[['项目',`${htmlEsc(REAL.name)}，${CHIP()}`],['合理范围',sc.standard?`码表自带的标准：${htmlEsc(sc.standard)}（按代码位置对上 ${Object.keys(sc.matched||{}).length} 个计时点）`:'还没有这个设备的合理范围']],pts:(IDENT.page.steps||[]).length,
   loc:Object.fromEntries(Object.entries(sc.segments).map(([k,g])=>[k,st[g.to]?`${st[g.to].f} 第 ${st[g.to].l} 行`:''])),log:J.raw_log||S0.tech.log},J}}
// 判快慢、两把尺子、结论标签：后台按固定规则算（任务 16，fsw/judge.py），这里只照着显示
const VCLS={'慢':'c-bad','偏慢':'c-warn','正常':'c-ok','推算':'c-gray','没有范围':'c-gray'};
function withJudge(D,J){const L=n=>J.lines.find(x=>x.name===n)||{};
  D.verdict=l=>{const v=L(l.name).verdict||'正常';return [v==='没有范围'?'还没有合理范围':v,VCLS[v]||'c-gray']};
  const e=L(D.main.name).exp;D.exp=e?[e,e==='差'?'c-bad':'c-ok']:null;
  const sp=J.labels.find(x=>x.kind==='spiky'&&!x.with);  // 和对错问题同一根因的，算进那条
  D.probs=J.labels.filter(x=>x.kind==='problem'&&!x.order);D.spiky=sp?D.allSegs.find(s=>s.k===sp.segment)||null:null;
  const ord=J.labels.find(x=>x.kind==='problem'&&x.order);D.reversed=ord?D.rounds.filter(r=>ord.rounds.includes(r.n)):[];
  if(J.biggest&&D.allSegs.find(s=>s.k===J.biggest))D.biggest=D.allSegs.find(s=>s.k===J.biggest);
  D.J=J;return D}
const roundList=a=>'第 '+a.map(r=>r.n).join('、')+' 轮';
// 分段用时的范围：某轮后一个点比前一个点先到（如回答开头被吞），算出来是负数，不写负数，写这一轮先后反了
function rangeTxt(v,ns){const pos=v.filter(x=>x>=0),neg=ns.filter((n,i)=>v[i]<0);
  return (pos.length?`${f(Math.min(...pos))}～${f(Math.max(...pos))}`:'—')+(neg.length?`（第 ${neg.join('、')} 轮先后反了）`:'')}
function headline(D){
  const L=D.lines;
  if(L.length===1)return `${D.S.startShort} ${f(L[0].med,1)} 秒${L[0].phrase}`;
  return `${D.S.startShort} ${f(L[0].med,1)} 秒${L[0].phrase}，${f(L[1].med,1)} 秒${L[1].phrase}`;
}

const LV=[['必须改','lv-must'],['建议改','lv-should'],['可以不改','lv-ok']];
const lvTag=lv=>`<span class="lvtag ${LV[lv][1]}">${LV[lv][0]}</span>`;
const jump=(id,t)=>`<button class="lnk" data-jump="${id}">${t}</button>`;
// 要你拍板的一条 → 讲它的那张卡片：按问题编号对；早先的卡片把几段"偶尔特别慢"都叫 spiky，再按段对
const cardOf=(FX,d)=>FX.findIndex(F=>F.when.includes(d.k)||(d.seg&&F.when.includes('spiky')&&F.seg===d.seg));
function decisionsJ(D){const J=D.J,out=[];
  const src={line:['tiers','「三档用时」里“一般”那一列；各段占多少见「一次的计时图」'],spiky:['rounds',`「${D.n} 轮逐轮」图里红底的几轮`],order:['rounds',`「${D.n} 轮逐轮」图里红底、标"${D.lines[0].end}比${D.lines[1]?D.lines[1].end:''}晚"的几轮`],problem:['rounds',`「${D.n} 轮逐轮」里出这个问题的几轮`]};
  for(const x of J.labels){const lv=['必须改','建议改','可以不改'].indexOf(x.level);
   if(x.kind==='line'){const l=D.lines.find(y=>y.name===x.line);if(!l)continue;const isMain=l===D.main,b=D.biggest,share=Math.round(b.med/l.med*100),[v,c]=D.verdict(l),ex=isMain?D.exp:null;
    out.push({k:isMain?'main':'line:'+l.name,lv,src:src.line,tag:ex?[`体验${ex[0]}`,ex[1]]:[v,c],t:isMain?`${l.end}要 ${f(l.med,1)} 秒，大头是"${b.name}"`:`${l.end}要 ${f(l.med,1)} 秒`,
     d:isMain?`这一段 ${f(b.med)} 秒，占 ${share}%。${D.exp?'':'这个场景还没有体验标准，只按合理范围判。'}`:`${x.why}。`,who:x.level==='可以不改'?(v==='还没有合理范围'?'还没有合理范围，先只记用时':'在合理范围里，不用改'):`找${b.owner}`})}
   else if(x.kind==='spiky'){const s=D.allSegs.find(y=>y.k===x.segment);if(!s||x.with)continue;  // 和对错问题同一根因的，合并到那条里
    out.push({k:x.k,seg:x.segment,lv,src:src.spiky,tag:['偶发','c-warn'],t:`"${s.name}"偶尔特别慢`,d:`平时 ${f(s.med)} 秒，最慢 ${f(s.max)} 秒（${roundList(x.rounds.map(n=>({n})))}）。`,who:`找${s.owner}`})}
   else if(x.order&&D.lines.length>1)out.push({k:'reversed',lv,src:src.order,tag:['要查','c-bad'],t:`${x.rounds.length} 轮${D.lines[0].end}反而比${D.lines[1].end}晚`,d:`${roundList(x.rounds.map(n=>({n})))}。"先给反应"这个设计在这几轮白做了。`,who:'先查是哪一段变慢'});
   else{const also=J.labels.filter(y=>y.kind==='spiky'&&y.with===x.k).map(y=>D.allSegs.find(s=>s.k===y.segment)).filter(s=>s&&!x.name.includes(s.name)).map(s=>`同一轮"${s.name}"也特别慢（最慢 ${f(s.max)} 秒，平时 ${f(s.med)} 秒），是同一个问题。`).join('');
    out.push({k:x.k,lv,src:src.problem,tag:['要查','c-bad'],t:`${D.n} 轮里 ${x.rounds.length} 轮${x.name}`,d:`${roundList(x.rounds.map(n=>({n})))}。${x.why}。${also}`,who:'先查是哪里出的问题'})}}
  return out}
function decisions(D){if(D.J)return decisionsJ(D);
  const out=[];
  if(D.reversed.length)out.push({k:'reversed',tag:['要查','c-bad'],t:`${D.reversed.length} 轮${D.lines[0].end}反而比${D.lines[1].end}晚`,d:`${roundList(D.reversed)}。"先给反应"这个设计在这几轮白做了。`,who:'先查是哪一段变慢'});
  if(D.spiky)out.push({k:'spiky',tag:['偶发','c-warn'],t:`"${D.spiky.name}"偶尔特别慢`,d:`平时 ${f(D.spiky.med)} 秒，最慢 ${f(D.spiky.max)} 秒（${roundList(D.allSegs.find(s=>s.k===D.spiky.k).v.map((x,i)=>({x,n:i+1})).filter(o=>o.x>D.spiky.med*2.5))}）。`,who:`找${D.spiky.owner}`});
  const b=D.biggest,share=Math.round(b.med/D.main.med*100);
  const mv=D.verdict(D.main)[0],mainOk=!(D.exp&&D.exp[0]==='差')&&mv==='正常';
  out.push({k:'main',tag:D.exp?[`体验${D.exp[0]}`,D.exp[1]]:D.verdict(D.main),t:`${D.main.end}要 ${f(D.main.med,1)} 秒，大头是"${b.name}"`,d:`这一段 ${f(b.med)} 秒，占 ${share}%。${D.exp?'':'这个场景还没有体验标准，只按合理范围判。'}`,who:mainOk?'在合理范围里，不用改':`找${b.owner}`});
  // 等级按固定规则算，不由 AI 判断（2026-10-13 用户提出加标签）
  const L=out.map(x=>{let lv;
    if(x.k==='main')lv=(D.exp&&D.exp[0]==='差')||mv==='慢'?0:mv==='偏慢'?1:2;
    else if(x.k==='spiky')lv=D.lines.filter(l=>l.segs.includes(D.spiky.k)).some(l=>l.p90>l.slow)?0:1;
    else lv=D.reversed.length/D.n>.1?0:1;
    const src=x.k==='main'?['tiers','「三档用时」里“一般”那一列；各段占多少见「一次的计时图」']:x.k==='spiky'?['rounds',`「${D.n} 轮逐轮」图里红底的几轮`]:['rounds',`「${D.n} 轮逐轮」图里红底、标"${D.lines[0].end}比${D.lines[1].end}晚"的几轮`];
    return {...x,lv,src}});
  return L.sort((a,b)=>a.lv-b.lv);
}

// ===== 计时图：同一起点、多条横线（按数据画）=====
function niceT(x){return Math.max(.5,Math.ceil(x*1.2/.5)*.5)}
function step(T){return T<=1?.1:T<=2?.25:T<=4?.5:1}
function timeline(D){
  const R=D.rep.n-1,T=niceT(Math.max(...D.rep.ends));const W=900,X0=150,X1=820,sx=t=>X0+(X1-X0)*t/T,h=22,gap=64,top=60;
  const H=top+D.lines.length*gap+30;
  let g='';const st=step(T);
  for(let t=0;t<=T+1e-9;t+=st){g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="30" y2="${H-30}" stroke="var(--soft)"/><text x="${sx(t)}" y="${H-10}" text-anchor="middle" font-size="12" fill="var(--muted)" class="num">${+t.toFixed(2)} 秒</text>`}
  if(D.S.exp&&D.S.exp.fair<=T)g+=`<line x1="${sx(D.S.exp.fair)}" x2="${sx(D.S.exp.fair)}" y1="30" y2="${H-30}" stroke="var(--warn)" stroke-dasharray="3 4"/><text x="${sx(D.S.exp.fair)+4}" y="40" font-size="11.5" fill="var(--warn)">${D.S.exp.label}</text>`;
  g+=`<text x="${sx(0)}" y="22" text-anchor="middle" font-size="12" fill="var(--muted)">${D.S.start}</text>`;
  g+=`<line x1="${sx(0)-60}" x2="${sx(0)}" y1="${top+h/2}" y2="${top+h/2}" stroke="var(--est)" stroke-dasharray="4 4" stroke-width="2"/><text x="${sx(0)-64}" y="${top-6}" font-size="11.5" fill="var(--est)">推算段</text>`;
  D.lines.forEach((l,i)=>{const y=top+i*gap;let t0=0;
    g+=`<text x="${X0-14}" y="${y+h/2+5}" text-anchor="end" font-size="14" font-weight="700" fill="${l.col}">${l.name}</text>`;
    l.segs.forEach((k,j)=>{const d=D.S.segs[k].v[R],a=t0,b=t0+d;t0=b;const last=j===l.segs.length-1;const shared=D.use[k]>1;
      const c=last?l.col:shared?'var(--soft)':l.soft;
      g+=`<rect x="${sx(a)}" y="${y}" width="${Math.max(2,sx(b)-sx(a))}" height="${h}" fill="${c}" rx="2"><title>${D.S.segs[k].name} ${f(d)} 秒</title></rect>`;
      if(sx(b)-sx(a)>90&&!last){const room=Math.floor((sx(b)-sx(a)-40)/12),nm=D.S.segs[k].name,t=nm.length>room?nm.slice(0,Math.max(2,room-1))+'…':nm;g+=`<text x="${(sx(a)+sx(b))/2}" y="${y+h/2+5}" text-anchor="middle" font-size="12" fill="var(--fg)">${t} ${f(d)}<title>${nm}</title></text>`}});
    g+=`<circle cx="${sx(t0)}" cy="${y+h/2}" r="6" fill="${l.col}"/><text x="${sx(t0)+10}" y="${y+h/2+5}" font-size="13" font-weight="700" fill="${l.col}" class="num">${l.end} ${f(D.rep.ends[i])} 秒</text>`;});
  if(D.lines.length>1){const a=D.rep.ends[0],b=D.rep.ends[1],y=top+gap-12;
    g+=`<line x1="${sx(a)}" x2="${sx(a)}" y1="${top+h}" y2="${top+gap+h/2}" stroke="var(--muted)" stroke-dasharray="2 3"/><line x1="${sx(a)}" x2="${sx(b)}" y1="${y}" y2="${y}" stroke="var(--muted)"/><text x="${(sx(a)+sx(b))/2}" y="${y-6}" text-anchor="middle" font-size="12" fill="var(--muted)">早 ${f(b-a,2)} 秒</text>`;}
  const segLegend=Object.entries(D.S.segs).filter(([k])=>D.lines.some(l=>l.segs.slice(0,-1).includes(k))).map(([k,s])=>{const c=D.use[k]>1?'var(--soft)':D.lines.find(l=>l.segs.includes(k)).soft;return `<span><i class="sw" style="background:${c}"></i>${s.name}${D.use[k]>1?'（共用）':''}</span>`}).join('');
  return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" style="min-width:640px;width:100%" role="img" aria-label="计时图">${g}</svg></div>
  <div class="legend">${segLegend}<span><i class="sw" style="background:none;border-top:2px dashed var(--est);height:0"></i>${D.S.est.label}（${D.S.est.note}）</span></div>`;
}
function roundsChart(D){
  const T=niceT(Math.max(...D.rounds.flatMap(r=>r.ends))/1.2);const W=900,X0=70,X1=820,sx=t=>X0+(X1-X0)*t/T,rh=20,top=34,st=step(T);
  const bad=new Set([...D.reversed,...D.slowRounds].map(r=>r.n));
  let g='';
  for(let t=0;t<=T+1e-9;t+=st){g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="${top-8}" y2="${top+D.n*rh}" stroke="var(--soft)"/><text x="${sx(t)}" y="${top-14}" text-anchor="middle" font-size="12" fill="var(--muted)" class="num">${+t.toFixed(2)}</text>`}
  D.rounds.forEach((r,i)=>{const y=top+i*rh;const isBad=bad.has(r.n);
    if(isBad)g+=`<rect x="0" y="${y}" width="${W}" height="${rh}" fill="var(--bad-soft)"/>`;
    g+=`<text x="${X0-12}" y="${y+14}" text-anchor="end" font-size="12" fill="${isBad?'var(--bad)':'var(--muted)'}" class="num">第 ${r.n} 轮</text>`;
    g+=`<line x1="${sx(0)}" x2="${sx(Math.max(...r.ends))}" y1="${y+10}" y2="${y+10}" stroke="var(--line)" stroke-width="2"/>`;
    r.ends.forEach((e,j)=>{g+=j===0?`<circle cx="${sx(e)}" cy="${y+10}" r="5" fill="${D.lines[j].col}"/>`:`<rect x="${sx(e)-5}" y="${y+5}" width="10" height="10" fill="${D.lines[j].col}"/>`});
    const why=D.reversed.some(x=>x.n===r.n)?`${D.lines[0].end}比${D.lines[1].end}晚`:D.slowRounds.some(x=>x.n===r.n)?'超过算慢线':'';
    if(why)g+=`<text x="${sx(Math.max(...r.ends))+10}" y="${y+14}" font-size="12" font-weight="700" fill="var(--bad)">${why}</text>`;});
  D.lines.forEach(l=>{g+=`<line x1="${sx(l.med)}" x2="${sx(l.med)}" y1="${top-8}" y2="${top+D.n*rh}" stroke="${l.col}" stroke-dasharray="3 3"/>`});
  return `<div class="scroll"><svg viewBox="0 0 ${W} ${top+D.n*rh+6}" style="min-width:640px;width:100%" role="img" aria-label="逐轮计时">${g}</svg></div>
  <div class="legend">${D.lines.map((l,j)=>`<span><i class="sw" style="background:${l.col};width:10px;height:10px;${j===0?'border-radius:50%':''}"></i>${l.end}</span>`).join('')}<span>竖虚线是中位数</span><span style="color:var(--bad)">红底：有问题的轮</span></div>`;
}
function tiers(D){const few=D.n<10;  // 少于 10 轮："10 次里最慢"没意义，不显示
  return `<div class="tiers num" ${few?'style="grid-template-columns:auto repeat(2,1fr)"':''}><span></span><span class="h">一般（中位数）</span>${few?'':'<span class="h">10 次里最慢</span>'}<span class="h">最差</span>
  ${D.lines.map(l=>`<b style="color:${l.col};font-family:var(--sans)">${l.end}</b><span>${f(l.med)} 秒</span>${few?'':`<span>${f(l.p90)} 秒</span>`}<span>${f(l.max)} 秒</span>`).join('')}</div>`;
}
// 两把尺子（2026-10-13）：体验标准问"用户等得了吗"（看结果，给负责人）；合理范围问"这套做法有没有做坏"（看实现，给研发）。
// 体验标准只挂在有公认标准的那条线上，没有就只用合理范围。
function lineCards(D){
  const CB={'每段都没做坏，是做法本身慢，要换做法才会快':'每段都没做坏，是这套做法本身就慢：要换做法（如换更快的模型）才会快，不是修毛病。','先查哪段做坏了':'既让人等太久，也比这套做法该有的慢：先查哪段做坏了。','可能有隐患':'用户还等得了，但比这套做法该有的慢，可能有隐患。','正常':'用户等得了，技术上也正常。','只按合理范围判：正常':'这个场景没有体验标准，只按合理范围判：正常。','只按合理范围判：先查哪段做坏了':'这个场景没有体验标准，只按合理范围判：比这套做法该有的慢，先查哪段做坏了。','还没有合理范围：只记下用时，先不判快慢':'还没有这个设备的合理范围：只记下用时，先不判快慢。'};
  return D.lines.map(l=>{const [v,c]=D.verdict(l),isMain=l===D.main,ex=isMain&&D.exp?D.exp:null,inR=v==='正常',jl=D.J&&D.J.lines.find(x=>x.name===l.name);
    const concl=jl?CB[jl.combo]||jl.combo:!ex?(inR?'这个场景没有体验标准，只按合理范围判：正常。':'这个场景没有体验标准，只按合理范围判：比这套做法该有的慢，先查哪段做坏了。')
      :ex[0]==='差'?(inR?'每段都没做坏，是这套做法本身就慢：要换做法（如换更快的模型）才会快，不是修毛病。':'既让人等太久，也比这套做法该有的慢：先查哪段做坏了。')
      :(inR?'用户等得了，技术上也正常。':'用户还等得了，但比这套做法该有的慢，可能有隐患。');
    const head=ex?`<span class="chip ${ex[1]}">体验${ex[0]}</span>`:`<span class="chip ${c}">${v}</span>`;
    return `<div class="panel card"><h3>${l.name} ${head}</h3><p class="num" style="color:var(--fg)">一般 ${f(l.med)} 秒</p>
     ${ex?`<p>体验标准：超过 ${D.S.exp.fair} 秒算差 → <b class="${ex[1]==='c-bad'?'up':'down'}">${ex[0]}</b></p>`:''}
     <p>合理范围：${l.ok?`${l.ok[0]}～${l.ok[1]??'不限'} 秒 → <b class="${inR?'down':'up'}">${inR?'在范围内':v}</b>`:'还没有'}</p>
     <p style="margin-top:6px;color:var(--fg)">${concl}</p></div>`}).join('');
}
// 负责人版写短（决策 98）：必须改在前、建议改在后，都展开；可以不改的收起来。内容不删，只是默认收起
function bossSplit(ds){const by=lv=>ds.filter(x=>x.lv===lv);return {open:[...by(0),...by(1)],fold:by(2)}}
// 要你拍板每条加一行：建议做哪个、预计省多少（取卡片里标了"建议"的方案）
function recLine(F){const o=F&&F.opts?(F.opts.find(o=>o.rec)||F.opts[0]):null;return o?`建议：${o.name}，预计${o.effect}。`:''}
function decideList(D){const FX=activeFixes(D),{open,fold}=bossSplit(decisions(D));
  const li=x=>{const n=cardOf(FX,x),r=n>=0?recLine(FX[n]):'';return `<li>${lvTag(x.lv)}<span><b>${x.t}</b>　${x.d}${x.who}。${r?`<br><b>${r}</b>`:''}<br><span class="src">出处：${jump('sec-'+x.src[0],'见下方'+x.src[1])}${n>=0?`　为什么、怎么改：${jump('fix-'+n,'问题 '+(n+1))}`:''}</span></span></li>`};
  return `<ul class="decide">${open.map(li).join('')}</ul>${fold.length?`<details class="lvwhy"><summary>另有 ${fold.length} 条在合理范围里，不用改</summary><ul class="decide">${fold.map(li).join('')}</ul></details>`:''}
  <details class="lvwhy"><summary>数字从哪来、标签规则</summary><ul class="notes"><li>这里的数字都是码表按这次 ${D.n} 轮数据直接算出来的，不经过 AI。</li><li>"为什么、怎么改"的文字是 AI 读代码写的。</li><li>要做哪些方案：在下方每个问题的方案表里勾选，结果汇总在 ${jump('my-pick','你的决定')}。</li></ul>
  <ul class="notes"><li>标签按固定规则从数据算出，不由 AI 判断；只有一条时也照规则标。</li>
  <li><b class="lvtag lv-must">必须改</b>一般用时（中位数）超过"算慢"线，或体验标准判"差"，或"10 次里最慢"超过"算慢"线（每 10 次就有 1 次让人等太久）。</li>
  <li><b class="lvtag lv-should">建议改</b>偶尔特别慢、先后顺序反了，但不到每 10 次一次；或一般用时超出合理范围、没到"算慢"。</li>
  <li><b class="lvtag lv-ok">可以不改</b>在合理范围里，只是占时最多。</li></ul></details>`}
// 数据检查（决策 122）：这份报告用了几轮、丢了几轮、为什么丢，让人自己判断准不准
function qualityLine(Q){if(!Q)return '';const drop=[Q.missing?`${Q.missing} 轮没收全`:'',Q.stale?`${Q.stale} 轮记到的是上一轮剩下的`:'',Q.half?`${Q.half} 次开头没收到`:'',Q.nostart?`${Q.nostart} 次没记到起点「${Q.startName||'起点'}」`:''].filter(Boolean);
 return `<p class="note" style="margin:4px 0 0">数据检查：用了 ${Q.ok} 轮，都是完整收齐、计时点先后正常的${drop.length?`；另有 ${drop.join('、')}，这些不算进来，已经自动补测`:'，没有丢掉的'}。数字都是码表按这些轮算的，不经过 AI。</p>`}
function subline(D){
  const bits=[];
  if(D.lines.length>1)bits.push(`${D.lines[0].end}平均比${D.lines[1].end}早 ${f(D.lines[1].med-D.lines[0].med,1)} 秒`);
  if(D.exp)bits.push(`按体验标准算<span class="chip ${D.exp[1]}">${D.exp[0]}</span>`);else bits.push('这个场景还没有体验标准');
  if(D.reversed.length)bits.push(`${roundList(D.reversed)}${D.lines[0].end}反而晚了`);
  for(const x of D.probs||[])bits.push(`${roundList(x.rounds.map(n=>({n})))}${x.name}`);
  if(D.spiky)bits.push(`"${D.spiky.name}"偶尔特别慢`);
  return bits.join('；')+'。';
}

// 这次测试本身用了多久（决策 99）：码表自己记的每段用时，按四笔账分类，对照用时标准判"好 / 能忍 / 超了"。只在技术细节里
let TIMING=null,timingAt=0;
function timingLoad(){if(Date.now()-timingAt<3000)return;timingAt=Date.now();fetch('/api/timing').then(r=>r.ok?r.json():null).then(j=>{if(j){const ch=JSON.stringify(j)!==JSON.stringify(TIMING);TIMING=j;if(ch&&page==='report'&&mode==='dev')render()}}).catch(()=>{})}
const mmssT=x=>x==null?'没记下':x<60?`${x.toFixed(1)} 秒`:`${Math.floor(x/60)} 分 ${Math.round(x%60)} 秒`;
const VTAG={'好':'c-ok','能忍':'c-warn','超了':'c-bad'};
function timingBox(t){if(!t)return '';const B=t.books,N=t.names,c=t.counts,stdT=s=>s?`好 ≤${mmssT(s[0])}${s[1]!=null?`，能忍 ≤${mmssT(s[1])}`:''}`:'—';
 const chip=v=>v?`<span class="chip ${VTAG[v]}">${v}</span>`:'';
 const rows=t.stages.map(s=>`<tr><td style="font-family:var(--sans)">${s.name}</td><td class="r">${mmssT(s.secs)}${s.per_round!=null?`<br><span class="note">每轮 ${s.per_round} 秒</span>`:''}</td><td style="font-family:var(--sans)">${{tool:N.tool,human:N.human,collect:'人配合 + 被测设备'}[s.book]}</td><td style="font-family:var(--sans)">${s.key==='collect'?'每轮 ':''}${stdT(s.std)}</td><td style="font-family:var(--sans)">${chip(s.verdict)}${s.note?` <span class="note">${s.note}</span>`:''}</td></tr>`).join('');
 return `<section class="panel scroll"><h2>这次测试本身用了多久</h2><p class="note" style="margin:-4px 0 8px">码表自己记的每段开始、结束时间；按${t.first?'第一次测':'再测（能用上次结果）'}的标准判。等人的时间不算进各段。</p>
  <table class="num"><thead><tr><th>四笔账</th><th class="r">合计</th><th>标准</th><th>判断</th></tr></thead><tbody>
  <tr><td style="font-family:var(--sans)"><b>${N.tool}</b></td><td class="r">${mmssT(B.tool)}</td><td style="font-family:var(--sans)">${stdT(t.std_total.tool)}</td><td>${chip(t.verdict.tool)}</td></tr>
  <tr><td style="font-family:var(--sans)">${N.device}</td><td class="r">${mmssT(B.device)}</td><td style="font-family:var(--sans)">不考码表（设备反应、放完回答）</td><td></td></tr>
  <tr><td style="font-family:var(--sans)">${N.human}</td><td class="r">${mmssT(B.human)}</td><td style="font-family:var(--sans)">${stdT(t.std_total.human)}</td><td>${chip(t.verdict.human)}</td></tr>
  <tr><td style="font-family:var(--sans)">${N.wait}</td><td class="r">${mmssT(B.wait)}</td><td style="font-family:var(--sans)">不算</td><td></td></tr></tbody></table>
  <p style="margin:8px 0">说了 ${c.said} 句（试一次 + 多轮实测）· 补测 ${c.redo} 轮 · 弹提示 ${c.false_hints} 次 · 点了 ${c.clicks} 次 · 调用大模型 ${c.llm_calls} 次</p>
  <details class="lvwhy"><summary>每段用时</summary><table class="num"><thead><tr><th>段</th><th class="r">用时</th><th>记在</th><th>标准</th><th>判断</th></tr></thead><tbody>${rows}</tbody></table>
  ${t.waits.length?`<ul class="notes">${t.waits.map(w=>`<li>等人：${w.why} ${mmssT(w.secs)}</li>`).join('')}</ul>`:''}</details></section>`}
function dev(D){const T=D.S.tech;if(D.S.real)timingLoad();
  const segRows=D.allSegs.map(s=>{const over=s.max>s.med*2.5&&s.max-s.med>.3;return `<tr><td style="font-family:var(--sans)">${s.name}（${s.code}）</td><td><span class="code">${T.loc[s.k]}</span></td><td style="font-family:var(--sans)">${s.owner}</td><td class="r">${f(s.med)}</td><td class="r">${rangeTxt(s.v,s.v.map((x,i)=>(D.rounds[i]||{}).n||i+1))}</td><td class="r">${s.ok}</td><td style="font-family:var(--sans)">${D.J?(()=>{const [v,c]=segJudge(D,s),j=D.J.segments.find(x=>x.k===s.k);return `<span class="chip ${c}">${v}</span>${j&&j.outside?' <span class="chip c-gray">设备外</span>':''}`})():over?'<span class="chip c-warn">偶发特别慢</span>':'<span class="chip c-ok">正常</span>'}</td></tr>`}).join('');
  const lineRows=D.lines.map(l=>{const [v,c]=D.verdict(l);return `<tr><td style="font-family:var(--sans)"><b>${l.name}合计</b>：${l.segs.map(k=>D.S.segs[k].name).join(' → ')}</td><td></td><td></td><td class="r">${f(l.med)}</td><td class="r">${rangeTxt(l.ends,l.ends.map((x,i)=>(D.rounds[i]||{}).n||i+1))}</td><td class="r">${l.ok?`${l.ok[0]}～${l.ok[1]??'不限'}`:'—'}</td><td style="font-family:var(--sans)"><span class="chip ${c}">${v}</span>${l===D.main&&D.exp?` <span class="chip ${D.exp[1]}">体验${D.exp[0]}</span>`:''}</td></tr>`}).join('');
  const bad=[...new Set([...D.reversed,...D.slowRounds])].sort((a,b)=>a.n-b.n);
  const ovLo=(T.pts*.002).toFixed(3),ovHi=(T.pts*.003).toFixed(3);
  return `<div class="stack">${expDone()}${staleBar()}<div><h1>技术细节</h1><p class="sub">在代码哪里、具体怎么改、怎么验证。单位秒。<br>${realFlash()?'段名和代码位置来自找关键步骤的结果，合理范围来自码表自带的标准文件。':'段名、代码位置和合理范围来自 AI 识别结果和这个设备的标准文件；这里只演示样子。'}</p></div>
  <section class="panel"><h2>测量条件</h2><dl class="kv" style="grid-template-columns:6em 1fr">${T.cond.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}
   <dt>计时点开销</dt><dd>每个计时点往调试口打印一行，约 0.002～0.003 秒（按调试口速度推算，未实测）。<br>一轮最多经过 ${T.pts} 个计时点，设备那几段的数字最多偏大约 ${ovLo}～${ovHi} 秒；服务器、网络那几段不受影响。<br>想确认开销，可以在"插计时点和烧录"页切回原版，各收一次比较。</dd></dl></section>
  <section class="panel scroll"><h2 style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">分段用时<button class="pill" data-go="range">合理范围依据</button></h2><table class="num"><thead><tr><th>段（计时点）</th><th>代码位置${T.locNote||''}</th><th>谁负责</th><th class="r">中位数</th><th class="r">最小～最大</th><th class="r">合理</th><th>判断</th></tr></thead><tbody>${segRows}${lineRows}</tbody></table></section>
  <section class="panel scroll"><h2>有问题的轮</h2>${bad.length?`<table class="num"><thead><tr><th>轮</th>${D.lines.map(l=>`<th class="r">${l.end}</th>`).join('')}</tr></thead><tbody>${bad.map(r=>`<tr class="late"><td>第 ${r.n} 轮</td>${r.ends.map(e=>`<td class="r">${f(e)}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'<p class="note">没有。</p>'}</section>${fixDev(D)}
  ${D.S.real?timingBox(TIMING||(JREP&&JREP.timing)):''}
  <section class="panel"><h2>原始日志</h2><p style="margin:0 0 8px"><span class="code">${T.log}</span></p><ul class="notes" style="margin:0 0 8px"><li>设备调试口输出的原样记录，没有删改：固件原有的日志和码表插的计时点行（FSW 开头）都在里面。</li><li>报告里所有数字都从这份文件算出来。</li><li>合理范围改过以后，报告顶上会提示用新范围更新，不用重测。</li></ul><div class="acts"><button class="pill" data-openlog="1">打开所在文件夹</button></div></section>${retestEntry()}${restoreBox(scene)}</div>`;
}


// ===== 页面 =====
const PAGES=[
 {id:'home',name:'首页',done:'已定'},
 {id:'settings',name:'设置',done:'已定'},
 {id:'steps',name:'找关键步骤',done:'已定'},
 {id:'flash',name:'插计时点和烧录',done:'已定'},
 {id:'collect',name:'多轮实测',done:'已定'},
 {id:'report',name:'报告',done:'补充中'},
 {id:'retest',name:'改完再测',done:'新加'},
 {id:'compare',name:'前后对比',done:'已定'},
 {id:'range',name:'合理范围',done:'已定'}];

const arrow=(now,prev)=>{const d=now-prev;if(Math.abs(d)<.02)return '<span class="note">持平</span>';return d<0?`<span class="down">↓ 快了 ${f(-d)} 秒</span>`:`<span class="up">↑ 慢了 ${f(d)} 秒</span>`};
// 已保存的设置（/api/state 读设置文件夹；密钥只知道有没有，不回传）、本机 Claude Code 登没登录、插着的设备（/api/env、/api/ports 真查）
let SAVED={llm_provider:null,has_key:{},data_dir:'',idf_path:''},CCST=null,PORTS=[],dataDraft=null,idfErr='',portT=null;
const P2UI={'claude-api':'claude','claude-code':'cc',deepseek:'deepseek'},UI2P={claude:'claude-api',cc:'claude-code',deepseek:'deepseek'};
const ccOK=()=>!!(CCST&&CCST.logged_in),hasKey=ui=>!!SAVED.has_key[UI2P[ui]];
const keyOK=()=>setLLM==='cc'?ccOK():keyTested||hasKey(setLLM);
const tilde=p=>String(p||'').replace(/^\/Users\/[^/]+|^\/home\/[^/]+/,'~');
// 每 2 秒查一次设备和编译工具（不跑 claude 命令）：插上设备、自己装好编译工具，灯和设置页都自动变
async function portsPoll(){try{const e=await (await fetch('/api/env?cc=0')).json(),n=e.ports||[],idf=(e.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);const ch=n.join()!==PORTS.join()||idf.map(i=>i.path).join()!==IDFS.map(i=>i.path).join();PORTS=n;IDFS=idf;if(!ch)return;if(page==='flash'&&fl.st===4&&PORTS.length&&!realFlash()){fl.st=1;flRun(3,fl.to);return}  // 真插计时点时后台自己接着烧
 if(page==='home'){const lt=document.querySelector('.lights');if(lt)lt.outerHTML=envStatus();return}  // 首页只换灯，不打断正在输入的
 if(page==='steps'){const bj=document.querySelector('.bgjob');if(bj)bj.outerHTML=bgBox();return}  // 找关键步骤页只换"查设备"那块
 if(page==='settings'){const k=document.getElementById('set-key');if(k&&k.value.trim()&&!k.value.startsWith('•'))keyDraft=k.value.trim();render()}else if(page==='flash')render()}catch(e){}}
// 设备插没插：各页都看这一个（真流程看后台每 2 秒查到的端口；演示数据照原型模拟）
const devNow=()=>REAL?{on:PORTS.length>0,port:PORTS[0]||''}:{on:!flNoDev,port:PORTS[0]||(setOS==='mac'?'/dev/cu.usbmodem1101':'COM3')};
function portsWatch(on){if(on&&!portT)portT=setInterval(portsPoll,2000);if(!on&&portT){clearInterval(portT);portT=null}}
// 电脑上真装了的乐鑫编译工具（后台 /api/env 查的）；够项目要求的版本就算装好了
let IDFS=[];
const vt=v=>String(v||'0').split('.').map(Number).concat([0,0,0]).slice(0,3),vge=(a,b)=>{const x=vt(a),y=vt(b);for(let i=0;i<3;i++){if(x[i]!==y[i])return x[i]>y[i]}return true};
const realIdf=()=>{const min=REAL?REAL.idf_min:null;const ok=IDFS.filter(i=>!min||vge(i.version,min));return ok.sort((a,b)=>vge(a.version,b.version)?-1:1)[0]||null};
const shortV=v=>String(v).replace(/\.0$/,'');
// 首页灯和"用到时再检查"都按已保存的设置和真查的结果：没保存就回首页，缺的灯仍是红的
const envKey=()=>{const p=SAVED.llm_provider;return p==='claude-code'?ccOK():!!(p&&SAVED.has_key[p])},envComp=()=>!!realIdf();
function missing(only){const m=[];if(!envKey()&&only!=='comp')m.push(['set-llm',SAVED.llm_provider==='claude-code'?'大模型：本机 Claude Code 登录':'大模型密钥','找关键步骤要用大模型读代码，<b>不填就没法往下走</b>。']);if(hSrc&&!envComp()&&only!=='key')m.push(['set-idf',`乐鑫编译工具 ESP-IDF ${IDF()}`,inst.st===1?'正在后台安装，装好就能编译。':'编译这个固件要用（从源码里读到的要求）。<b>不挡找关键步骤</b>，烧录前装好就行。']);return m}
let envBack='home',compPending=false;
function envStatus(){const L=(c,t)=>`<span class="light"><i class="dot" style="background:var(--${c})"></i>${t}</span>`;
 const sp=P2UI[SAVED.llm_provider],llmName={claude:'Claude',deepseek:'DeepSeek',cc:'本机 Claude Code'}[sp];
 const a=[envKey()?L('ok',`大模型：${llmName}，${sp==='cc'?'已登录':'密钥已填'}`):L('bad',sp==='cc'?(CCST&&!CCST.found?'大模型：没找到本机 Claude Code':'大模型：本机 Claude Code 还没登录'):'大模型：还没填密钥'),PORTS.length?L('ok',`设备：已插上 ${PORTS[0]}`):L('muted','设备：没插上（烧录前插上就行）')];
 if(hSrc&&!hReading)a.push(!envComp()&&inst.st===1?L('warn',`乐鑫 ESP-IDF ${IDF()}：正在后台装 ${Math.round(((inst.step||0)+(inst.pct||0)/100)/5*100)}%`):envComp()?L('ok',`乐鑫 ESP-IDF ${IDF()}：已装 v${realIdf()?shortV(realIdf().version):'6.1'}`):L('bad',`乐鑫 ESP-IDF ${IDF()}：没装`));
 return `<div class="lights">${a.join('<span class="lsep" aria-hidden="true">|</span>')}</div>`}
// 提示（决策 129）：成功（✓）绿色、自己消失；出错（✕）红色、一直在，点 × 才关；其他黄色，按字数多留一会儿
let toastT=null;function toast(m){const t=document.getElementById('toast');m=String(m||'');const kind=m.startsWith('✓')?'ok':m.startsWith('✕')?'bad':'warn';
 t.className='toast t-'+kind;t.innerHTML=`<span>${htmlEsc(m)}</span>${kind==='bad'?'<button class="tclose" aria-label="关掉这个提示">×</button>':''}`;t.hidden=false;clearTimeout(toastT);
 if(kind!=='bad')toastT=setTimeout(()=>{t.hidden=true},Math.min(8000,1800+m.length*80))}
document.addEventListener('click',e=>{if(e.target.closest('#toast .tclose'))document.getElementById('toast').hidden=true});
// 首页"以前测过的"：真测过的记录（后台按结果文件夹里的原始日志重算）；演示数据只在演示入口（地址里写 ?demo=）出现
let HIST=[];async function histLoad(){try{HIST=await (await fetch('/api/history')).json()}catch(e){}}
const histRow=h=>`<div class="lrow"><button data-hopen="${htmlEsc(h.project_dir)}"><span><b>${htmlEsc(h.name)}</b>　<span class="note">${htmlEsc(h.what)} · ${htmlEsc(h.last)}</span></span><span class="num">${htmlEsc(h.metric)} ${f(h.now)} 秒　${h.few?`<span class="note">只测了 ${h.rounds} 轮，跑流程，不比较</span>`:h.prev!=null?arrow(h.now,h.prev):'<span class="note">第一次测</span>'}</span></button></div>${h.fsw_on?`<div class="devrow"><span class="note"><i class="dot" style="background:var(--warn)"></i>设备上现在是计时版，交货、演示前请还原</span><button class="pill" data-hrestore="${htmlEsc(h.project_dir)}">还原成原版</button></div>`:''}`;
const recentList=()=>DEMO?PROJ_LIST():HIST.length?`<div class="list">${HIST.map(histRow).join('')}</div>`:'<p class="note" style="margin:0">还没有测过。选一份固件源码开始，测完会出现在这里。</p>';
const PROJ_LIST=()=>`<div class="list">${PROJ.map(p=>`<div class="lrow"><button data-open="${p.scene}"><span><b>${p.name}</b>　<span class="note">${p.what} · ${p.last}</span></span><span class="num">${p.metric} ${f(p.now)} 秒　${arrow(p.now,p.prev)}</span></button><button class="pill" data-retest="${p.scene}">改完了，再测一次</button></div>${p.fswOnDevice&&!restored[p.scene]?`<div class="devrow"><span class="note"><i class="dot" style="background:var(--warn)"></i>设备上现在是计时版，交货、演示前请还原</span><button class="pill" data-restore="${p.scene}">还原成原版</button></div>`:''}`).join('')}</div>`;
let pName='',restored={},hNeed=false,hSrc='',hReading=false,hErr='',hScan=0,hScanT=null;
// ===== 选固件源码（真读）=====：后台只看项目文件、不用大模型，认出是不是固件、芯片、要的编译工具；复制到纯英文路径，原文件夹不动
let REAL=null,hBoardQ='',hBoardNeed=false;
const IDF=()=>REAL?(REAL.idf_min?REAL.idf_min+' 或更新':'（项目没写要哪个版本）'):FF[scene].idf+' 或更新';
const CHIP=()=>REAL?(REAL.chip?`芯片 ${REAL.chip}${REAL.board?`，板子 ${REAL.board}`:''}`:'还没选板子'):FF[scene].chip;
async function api(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
async function realPick(body,label,board){const quiet=!!board&&!!REAL;hErr='';envBack='home';hBoardNeed=false;hSrc=label||body.path||'';if(!quiet){hReading=true;hStage=1;if(!board)hSay='';render()}  // 只是选板子：页面不跳回"正在读源码"，不闪（决策 113）
 let r;try{r=await api('/api/source/inspect',{...body,board:board||null})}catch(e){r={ok:false,message:'码表后台没有回应，看看运行码表的窗口是不是关了。'}}
 hReading=false;
 if(!r.ok){REAL=null;hSrc='';hStage=0;hErr=r.kind==='multiple'?`<b>${r.message}</b><div class="acts" style="margin:8px 0 0">${r.projects.map(p=>`<button class="btn" data-pickroot="${p.root.replace(/"/g,'&quot;')}">${p.name}<span class="note">　${p.root}</span></button>`).join('')}</div>`:r.message;render();return}
 REAL={...r.info,body,label:hSrc,work:r.work,project_dir:r.project_dir,fingerprint:r.fingerprint,ai:null};if(REAL.board&&!hBoardOpen)hBoardQ=REAL.board;  // 再测时板子按上次选的填好（决策 130）pName=REAL.name+(REAL.board?`（${REAL.board}）`:'');IDENT=null;
 hScan=1;if(r.status!=='needs_board'&&missing().length)pop='env';render();clearTimeout(hScanT);
 // AI 推荐：只给大模型项目概况（目录、说明文件），一般十几秒；没设置大模型就不扫
 if(r.status!=='needs_board'&&envKey()){const me=REAL;fetch('/api/suggest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({work:r.work,name:REAL.name,chip:REAL.chip,project_dir:REAL.project_dir,fingerprint:REAL.fingerprint})}).then(x=>x.ok?x.json():{phrases:[]}).catch(()=>({phrases:[]})).then(j=>{if(REAL!==me)return;REAL.ai=j.phrases||[];REAL.aiCalled=!!j.called;hScan=2;if(page==='home')render()})}
 else{REAL.ai=[];hScan=2;render()}}
// 选板子（决策 104）：一个输入框，边输边在下面列出匹配的板子；模糊匹配（cores3、core s3、m5 cs3 都能找到 m5stack/core-s3）；
// 方向键选、回车定、Esc 收起，也能直接点
const bNorm=x=>String(x||'').toLowerCase().replace(/[^a-z0-9]/g,'');
function boardMatch(boards,q){const qn=bNorm(q),toks=String(q||'').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);if(!qn)return boards.slice();
 const sub=(h,n)=>{let k=0;for(const c of h)if(c===n[k])k++;return k===n.length};
 const sc=b=>{const id=bNorm(b.id),last=bNorm(b.id.split('/').pop()),chip=bNorm(b.chip);
  return last===qn?6:last.startsWith(qn)?5:id.includes(qn)?4:toks.length>1&&toks.every(t=>id.includes(t))?3:chip.includes(qn)?2:sub(id,qn)?1:0};
 return boards.map(b=>[sc(b),b]).filter(x=>x[0]>0).sort((a,b)=>b[0]-a[0]||a[1].id.length-b[1].id.length||a[1].id.localeCompare(b[1].id)).map(x=>x[1])}
let hBoardOpen=false,hBoardHi=0;
function boardRow(){if(!REAL||!REAL.boards||!REAL.boards.length)return '';
 const list=boardMatch(REAL.boards,hBoardQ),hi=Math.min(hBoardHi,Math.max(0,list.length-1));
 const drop=hBoardOpen?`<div class="bdrop" id="h-blist" role="listbox" aria-label="匹配的板子">${list.length?list.slice(0,50).map((b,k)=>`<div class="bopt${k===hi?' hi':''}" role="option" aria-selected="${REAL.board===b.id}" data-pickboard="${b.id}">${b.id}<span class="note">　${b.chip}</span></div>`).join('')+(list.length>50?`<div class="note" style="padding:6px 10px">还有 ${list.length-50} 块，多输几个字缩小范围</div>`:''):'<div class="note" style="padding:8px 10px">没有匹配的板子，换个说法试试（比如只输型号里的几个字）</div>'}</div>`:'';
 return `<div class="field" style="margin-top:14px"><label for="h-boardq">这个项目支持 ${REAL.boards.length} 块板子，你用的是哪块？</label>
  <div class="bpick${REAL.board&&hBoardQ===REAL.board&&!hBoardOpen?' picked':''}"><input type="text" id="h-boardq" role="combobox" aria-expanded="${hBoardOpen}" aria-controls="h-blist" autocomplete="off" placeholder="输入板子型号里的几个字，比如 core-s3" value="${htmlEsc(hBoardQ)}">${drop}</div>
  ${REAL.chip?`<p class="note" style="margin:6px 0 0"><i class="dot" style="background:var(--ok)"></i>已选 ${REAL.board||''}，芯片 ${REAL.chip}${REAL.board_from==='last'?'（按上次选的；想换就在框里重新输入）':''}</p>`:hBoardNeed?'<p class="note" style="color:var(--warn);margin:6px 0 0">先选你用的板子：芯片跟着板子走，编译和烧录要用。</p>':'<p class="note" style="margin:6px 0 0">源码里没写用哪块板子（没编译过），芯片跟着板子走。</p>'}</div>`}
function boardPick(id){const w=document.getElementById('h-what');hSay=w?w.value:hSay;hBoardQ=id;hBoardOpen=false;hBoardHi=0;const q=document.getElementById('h-boardq');if(q){q.value=id;q.blur()}realPick(REAL.body,REAL.label,id)}
async function uploadDrop(items){const tok=Math.random().toString(36).slice(2,12),files=[];
 const skip=new Set(['.git','build','managed_components','node_modules']);
 const walk=async(entry,pre)=>{if(entry.isFile){files.push([pre+entry.name,await new Promise((ok,no)=>entry.file(ok,no))])}
  else if(entry.isDirectory&&!skip.has(entry.name)){const rd=entry.createReader();let batch;do{batch=await new Promise((ok,no)=>rd.readEntries(ok,no));for(const e of batch)await walk(e,pre+entry.name+'/')}while(batch.length)}};
 const roots=[...items].map(i=>i.webkitGetAsEntry&&i.webkitGetAsEntry()).filter(Boolean);if(!roots.length)return;
 const label=roots.length===1?roots[0].name+'（拖进来的，已复制到码表工作区）':`${roots.length} 个拖进来的东西`;
 hErr='';hSrc=label;hReading=true;hStage=1;render();
 for(const r of roots)await walk(r,'');
 let i=0;const put=async()=>{while(i<files.length){const [rel,file]=files[i++];await fetch(`/api/source/upload/${tok}/`+rel.split('/').map(encodeURIComponent).join('/'),{method:'PUT',body:file})}};
 await Promise.all([...Array(8)].map(put));
 realPick({upload:tok},label)}
// 粘贴的 ~/… 路径：换成码表认出来的完整路径（源码根目录），再去对"以前测过的"
const expandHome=p=>REAL&&REAL.root&&/^~(\/|$)/.test(String(p||''))?REAL.root:p;
function chips(){const C=(t,x)=>`<button class="pill" data-fill="${x}">${x}</button>`;
 const seen=new Set(),uniq=a=>a.filter(x=>x&&!seen.has(x)&&seen.add(x));  // 同一句只出现一次：测过的 > AI 推荐 > 通用例子
 const hist=uniq((DEMO?PROJ:HIST).filter(p=>[hSrc,expandHome(hSrc),REAL&&REAL.root].includes(p.path)).map(p=>p.what)),ai=uniq(REAL&&REAL.ai?REAL.ai:FF[scene].ai),gen=uniq(['从按键到屏幕亮','从开机到连上网','从收到指令到动作完成']);
 const row=(lbl,arr)=>`<div class="chiprow"><span class="note">${lbl}</span><div class="pills">${arr.map(x=>C(0,x)).join('')}</div></div>`;
 return (hist.length?row('这个项目测过的',hist):'')+(hScan===2?(ai.length?row('AI 推荐',ai):`<div class="chiprow"><span class="note">AI 推荐</span><span class="note">${REAL&&REAL.needs_board?'选好板子后，AI 会扫一遍这个固件。':envKey()?'这次没扫出来，直接写就行。':'设置好大模型后，选完固件会自动扫一遍。'}</span></div>`):`<div class="chiprow"><span class="note">AI 推荐</span><span class="note"><span class="spin"></span> 正在扫这个固件有哪些流程，约 10～20 秒（估算）。不用等，可以直接写。</span></div>`)+row('通用例子',gen)+`<p class="note" style="margin:8px 0 0">不知道测什么，就从「AI 推荐」里挑一个。</p>`}
const examples=`<div class="pills"><span class="note">比如：</span>${['从按键到屏幕亮','从开机到连上网','从收到指令到动作完成'].map(x=>`<button class="pill" data-fill="${x}">${x}</button>`).join('')}</div>`;

const HOME={
m:{label:'合并版',idea:'首页（A 的表单 + C 的两点）：选完文件夹马上显示读到的芯片和编译工具；出发前用一句话复述要测什么，你点"对"再去找关键步骤。',
 html:()=>{
  const st=hStage;
  const busy=hReading;
  return `<div class="stack"><section class="panel"><h1 style="font-size:22px">开始一次测量</h1>
  ${envStatus()}
  ${st===0||busy?`<div class="field"><label for="h-paste">固件源码位置</label>
    ${busy?`<div class="drop"><span class="spin"></span> 正在读项目……${hSrc.endsWith('.zip')?'先解开压缩包，':''}只看配置文件，不用大模型</div>`:`<div class="drop" id="dropzone"><b>把固件源码文件夹或压缩包拖到这里</b><span class="note">压缩包支持 .zip、.tar.gz；中文路径也可以</span>
     <div class="acts" style="justify-content:center;margin:8px 0 0"><button class="btn pri" data-pick="dir">选文件夹</button><button class="btn" data-pick="zip">选压缩包</button></div></div>
     <div class="inrow" style="margin-top:8px"><input type="text" id="h-paste" placeholder="或者粘贴路径，比如 ~/projects/my-firmware"><button class="btn" data-pick="paste">用这个路径</button></div>
     ${hErr?`<div class="errbox" style="margin-top:10px">${hErr}</div>`:''}`}</div>`
  :`<div class="field"><label for="h-path">固件源码位置</label><div class="inrow"><input type="text" id="h-path" value="${hSrc}" readonly><button class="btn" data-pick="reset">换一个</button></div></div>${boardRow()}
   <div class="field" style="margin-top:14px"><label for="h-what">你想测从什么时候到什么时候？用一句话说</label>
    <div class="inrow"><input type="text" id="h-what" placeholder="比如：从按下按键到屏幕亮" value="${hSay}"><button class="btn pri" data-stage="2">下一步</button></div>${hNeed?'<p class="note" style="color:var(--warn);margin:6px 0 0">先写一句，或者点下面的一个。</p>':''}${chips()}</div>`}
  </section>
  <section class="panel"><h2>以前测过的</h2>${recentList()}</section></div>`}}
};

let keyErr='',keyDraft='',setOS=/Win/i.test(navigator.platform)?'win':'mac',setLLM='claude',keyTested=false,setFirst=true,envMark=false;
// 帮装（决策 48）：0 没开始，1 正在装，2 装好，3 装失败；样子里用定时器假装进度
let inst={st:0,step:0,pct:0,log:'',timer:null};
const INSTALL={
 mac:{path:'~/esp/esp-idf-v6.1',steps:[
  ['检查前提','看 git、Python 在不在；缺"开发者工具"时系统会弹窗，点"安装"就行','$ xcode-select -p → /Library/Developer/CommandLineTools'],
  ['下载乐鑫编译工具 v6.1','从乐鑫官方 GitHub 下载，约 1.5 GB','Receiving objects: {p}% (412873/…)'],
  ['下载编译器等工具','共 9 个，用官方脚本 install.sh 装','Downloading xtensa-esp-elf-14.2.0 … {p}%'],
  ['装 Python 依赖','官方脚本自己建独立环境，不动你电脑上原有的 Python','Installing collected packages: esp-idf-monitor, esptool …'],
  ['试编译一次','编个官方小例子，确认真的能用','Project build complete.']]},
 win:{path:'C:\\esp\\v6.1',steps:[
  ['下载乐鑫官方安装管理器','EIM 命令行版，约 20 MB','eim.exe {p}%'],
  ['装前提','缺 Git、Python 时安装管理器自己装','Installing prerequisites: git, python …'],
  ['下载乐鑫编译工具 v6.1','装到纯英文路径 C:\\esp，避开中文用户名','Downloading esp-idf v6.1 … {p}%'],
  ['下载编译器等工具','共 9 个','Downloading xtensa-esp-elf-14.2.0 … {p}%'],
  ['试编译一次','编个官方小例子，确认真的能用','Project build complete.']]}};
function compOK(){return inst.st===2||!!realIdf()}
function instStop(){if(inst.timer){clearInterval(inst.timer);inst.timer=null}if(typeof instPoll!=='undefined'&&instPoll){clearInterval(instPoll);instPoll=null}}
// 帮装（真的）：后台跑乐鑫官方安装方式，这里每 0.8 秒问一次；点"保存"回首页也照样在装
let instPoll=null;
const INSTV=()=>inst.version||'v6.1',instPath=()=>(inst.path||INSTALL[setOS].path).replace(/^\/Users\/[^/]+/,'~');
function instApply(j){inst.step=j.step||0;inst.pct=j.pct||0;inst.log=j.log||'';inst.version=j.version;inst.path=j.path;inst.msg=j.message;
 if(j.state==='done'){inst.st=2;clearInterval(instPoll);instPoll=null;if(compPending)compPending=false
  fetch('/api/env').then(r=>r.json()).then(e=>{IDFS=(e.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);render()});toast('✓ 编译工具装好了')}
 else if(j.state==='failed'){inst.st=3;clearInterval(instPoll);instPoll=null}
 const b=document.getElementById('instbox');if(page==='settings'&&b)morphHTML(b,installBox());
 if(page==='flash')render();else if(page==='home'){const lt=document.querySelector('.lights');if(lt)lt.outerHTML=envStatus()}  // 别的页也跟着显示"正在装 xx%" 
 const lt=document.querySelector('.lights');if(lt&&page==='home')lt.outerHTML=envStatus();
 if(j.state!=='running'&&page==='settings')render()}
async function instStart(retry){instStop();inst={st:1,step:0,pct:0,log:'',timer:null,version:inst.version,path:inst.path};render();
 const r=await fetch(retry?'/api/install/retry':'/api/install/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({idf_min:REAL?REAL.idf_min:null})});
 const j=await r.json();if(!r.ok){inst.st=3;inst.msg=j.detail;render();return}
 instApply(j);instPoll=setInterval(async()=>{try{instApply(await (await fetch('/api/install')).json())}catch(e){}},800)}
function installBox(){const I=INSTALL[setOS],S=I.steps,n=S.length;
 const list=S.map(([t,d],i)=>{const done=inst.st===2||i<inst.step,cur=inst.st!==2&&i===inst.step,bad=inst.st===3&&cur;
  return `<li class="istep ${done?'ok':cur?(bad?'bad':'cur'):''}"><b>${done?'✓':bad?'✕':i+1}</b><span>${t.replace('v6.1',INSTV())}<small>${d}</small>${cur&&inst.st===1&&S[i][2].includes('{p}')?`<span class="pbar"><i style="width:${inst.pct}%"></i></span>`:''}</span></li>`}).join('');
 if(inst.st===0)return `<div class="status"><span><i class="dot" style="background:var(--warn)"></i>没找到乐鑫编译工具。这个项目要 ${IDF()}，码表帮你装 ${INSTV()}</span></div>
  <div class="acts" style="justify-content:flex-start;margin:10px 0"><button class="btn pri" data-inst="go">帮我安装 ${INSTV()}</button><button class="pill" data-idf="pick">手动指定安装位置</button></div>
  <p class="note">调用乐鑫官方的安装方式，装到 <span class="code">${instPath()}</span>。要下载约 3 GB，一般 10～30 分钟（估算，看网速）。装的时候可以先去做别的，码表开着就行。</p>`;
 const head=inst.st===1?`正在装：第 ${inst.step+1} 步 / 共 ${n} 步`:inst.st===2?`装好了：${INSTV()}，在 <span class="code">${instPath()}</span>`:(inst.msg||`第 ${inst.step+1} 步"${S[inst.step][0]}"没装成`);
 const color=inst.st===1?'var(--speak)':inst.st===2?'var(--ok)':'var(--bad)';
 return `<div class="status"><span><i class="dot" style="background:${color}"></i>${head}</span></div>
  <ol class="isteps">${list}</ol>
  ${inst.st!==2?`<div class="ilog"><span class="note">最新一行</span><code>${htmlEsc(inst.log)||'…'}</code></div>`:''}
  ${inst.st===3?`<p class="note">已下载的部分留着，重试会从断的地方接着装。多次失败可以换网络，或照手动步骤自己装，装好这里也会自动变绿。</p><div class="acts" style="justify-content:flex-start;margin:6px 0 10px"><button class="btn pri" data-inst="retry">重试</button><button class="btn" data-inst="manual">看手动安装步骤</button></div>`:''}
  ${inst.st===1?`<p class="note">关掉这页没关系，安装在后台继续；关掉码表会停，下次打开从断的地方接着装。</p>`:''}`}
function settingsPage(){
  const first=setFirst;
  const guide=setOS==='mac'?`<ol class="guide"><li>打开"终端"，复制这一行粘贴回车：<span class="code">mkdir -p ~/esp &amp;&amp; cd ~/esp &amp;&amp; git clone -b v6.1 --recursive https://github.com/espressif/esp-idf.git esp-idf-v6.1</span></li><li>再复制这一行：<span class="code">cd esp-idf-v6.1 &amp;&amp; ./install.sh esp32s3</span>，等它装完（十几分钟）</li><li>装完这里会自动变绿</li></ol>`:`<ol class="guide"><li>下载乐鑫官方的 Windows 安装器（ESP-IDF Installer），选 v6.1</li><li>安装路径用纯英文，比如 <span class="code">C:\\esp</span>（不要放在中文用户名的文件夹里）</li><li>装完这里会自动变绿</li></ol>`;
  const opt=(id,t,sub,extra='')=>`<label><input type="radio" name="llm" value="${id}" ${setLLM===id?'checked':''}><span><b>${t}</b>${extra}<small>${sub}</small></span></label>`;
  const where=setOS==='mac'?'<span class="code">~/esp</span>、<span class="code">~/.espressif</span>':'<span class="code">C:\\Espressif</span>、<span class="code">C:\\esp</span>';
  const done=[keyOK(), compOK(), PORTS.length>0];
  const cnt=done.filter(Boolean).length;
  const head=first?`<section class="panel" style="border-color:var(--speak)"><h1 style="font-size:22px">第一次用码表，先准备三样东西</h1><p class="note" style="margin:0">已准备 <b class="num">${cnt}</b> / 3。都准备好后，点最下面的「保存」。</p></section>`:'';
  const idfE=idfErr?`<p class="note" style="color:var(--bad);margin:6px 0 0">✕ ${htmlEsc(idfErr)}</p>`:'';
  const compiler=!IDFS.length||(hSrc&&!realIdf())?`<div id="instbox">${installBox()}</div>${idfE}`:`<div class="status">${IDFS.map((i,k)=>`<span><i class="dot" style="background:var(--${k?'muted':'ok'})"></i>找到 v${shortV(i.version)}：<span class="code">${htmlEsc(tilde(i.path))}</span></span>`).join('')}<button class="pill" data-idf="find">重新查找</button><button class="pill" data-idf="pick">手动指定位置</button></div>${idfE}<p class="note">每个项目要哪个版本，码表读项目文件自动挑；项目要的版本没装时，这里会出现"帮我安装"。</p>`;
  return `<div class="stack">${head}
  <section class="panel ${page==='settings'&&envMark&&!keyOK()?'miss':''}" id="set-llm"><h2>${first?(done[0]?'✓ ':'① '):''}大模型（找关键步骤、写报告建议时用）</h2>
   <div class="radio">${opt('claude','Claude','识别准确率最高（验证期 9 个对 8 个）。需要你自己的 Claude API 密钥，按用量付费。',' <span class="chip c-ok">推荐</span>')}
   ${opt('deepseek','DeepSeek','便宜，国内访问快。验证期识别 9 个只对 4 个，选它时找关键步骤那页会提醒你仔细核对。')}
   ${opt('cc','本机已登录的 Claude Code','不用填密钥，用你自己 Claude 会员的额度。属于可选用法，以官方条款为准。')}</div>
   ${setLLM==='cc'?(!CCST?`<p class="note" style="margin:10px 0 0">正在本机找 Claude Code……</p>`:ccOK()?`<p class="note" style="margin:10px 0 0">${first?'正在本机找 Claude Code……找到了，已登录。':'已找到本机的 Claude Code，已登录。'}</p>`:`<p class="note" style="color:var(--bad);margin:10px 0 0">✕ ${CCST.found?'找到了 Claude Code，但还没登录：在终端里运行 claude，按提示登录一次，再点「重新检查」。':'这台电脑上没找到 Claude Code。装好并登录后点「重新检查」，或者改用填密钥的方式。'} <button class="pill" data-cc="1">重新检查</button></p>`):`<div class="field" style="margin-top:12px"><label for="set-key">${setLLM==='claude'?'Claude':'DeepSeek'} API 密钥</label><div class="inrow"><input type="text" id="set-key" value="${keyDraft?keyDraft.replace(/"/g,'&quot;'):hasKey(setLLM)?'••••••••••••••••':''}" placeholder="粘贴你的密钥" autocomplete="off"><button class="btn" data-test="1">测试密钥</button></div>${keyErr?`<p class="note" style="color:var(--bad);margin:6px 0 0">✕ ${keyErr}</p>`:''}
   ${keyTested?'':`<details class="manual" style="margin-top:10px" ${hasKey(setLLM)?'':'open'}><summary>密钥是什么、去哪拿</summary>
   <p style="margin:6px 0;font-size:14px">密钥是大模型公司给你的一串字符，码表用它替你调用大模型读代码，按用量从你的账户扣钱。一次找关键步骤约几毛钱到一两块钱（估算）。</p>
   ${setLLM==='claude'?`<ol class="notes" style="font-size:14px;color:var(--fg);margin:0"><li>打开 Claude 开发者平台 console.anthropic.com，用邮箱注册登录</li><li>先在"账单"里充值（最少 5 美元）</li><li>左边点"API Keys"→"Create Key"，起个名字比如"码表"，复制出现的那一串（只显示一次）</li><li>回到这里粘贴进上面的框</li></ol>`:`<ol class="notes" style="font-size:14px;color:var(--fg);margin:0"><li>打开 DeepSeek 开放平台 platform.deepseek.com，用手机号注册登录</li><li>先在"充值"里充一点（10 元就够试用）</li><li>左边点"API keys"→"创建 API key"，复制出现的那一串（只显示一次）</li><li>回到这里粘贴进上面的框</li></ol>`}
   <p class="note" style="margin:6px 0 0">已经有 Claude 会员、电脑上装了 Claude Code？选上面第三项"本机已登录的 Claude Code"，不用密钥。</p></details>`}
   <span class="note">${keyTested?'<span style="color:var(--ok)">✓ 能用</span>　':''}只存在这台电脑的 <span class="code">~/.config/firmware-stopwatch/</span>，不上传、不进代码。</span></div>`}
   <label style="display:flex;gap:8px;align-items:center;margin-top:12px;font-size:14px"><input type="checkbox" id="set-reuse" ${reuseLast?'checked':''}>源码没变时，直接用上次的识别结果，不再调用大模型（省钱、省时间）</label>
  </section>
  <section class="panel ${page==='settings'&&envMark&&hSrc&&!compOK()&&inst.st!==1?'miss':''}" id="set-idf"><h2>${first?(done[1]?'✓ ':'② '):''}乐鑫编译工具（ESP-IDF）</h2>
   <p class="note" style="margin-top:0">码表打开时自动在电脑上找：常见安装位置、系统里记下的安装路径、乐鑫安装器留下的记录。只是按位置找文件，不用大模型。</p>
   ${compiler}
   <details class="manual" id="manual" ${inst.st===3&&inst.manual?'open':''}><summary>手动安装步骤</summary><p class="note" style="margin:4px 0">自己装、或帮装失败时用。</p>
   ${first?'':`<div class="tabs seg" role="group" aria-label="电脑系统"><button data-os="mac" aria-pressed="${setOS==='mac'}">Mac</button><button data-os="win" aria-pressed="${setOS==='win'}">Windows</button></div>`}
   ${guide}<p class="note">码表每隔几秒自己查一次，自己装好的也会自动变绿。</p></details>
  </section>
  <section class="panel" id="set-dev"><h2>${first?'③ ':''}设备连接</h2>
   <div class="status">${PORTS.length?PORTS.map((p,k)=>`<span><i class="dot" style="background:var(--${k?'muted':'ok'})"></i>${k?'也插着':'已连上'}：<span class="code">${htmlEsc(p)}</span></span>`).join(''):`<span><i class="dot" style="background:var(--warn)"></i>现在没看到设备。用 USB 线插上后这里会自动出现，比如 <span class="code">${setOS==='mac'?'/dev/cu.usbmodem1101':'COM3'}</span></span>`}</div>
   <p class="note">读设备输出的速度默认 115200，一般不用改。</p>
  </section>
  <section class="panel"><h2>测量结果放在哪</h2>
   <div class="inrow"><input type="text" id="set-data" value="${htmlEsc(tilde(dataDraft||SAVED.data_dir||'~/firmware-stopwatch-data'))}" readonly><button class="btn" data-datadir="1">更换位置</button></div>
   <p class="note">每个项目一个文件夹：识别结果、合理范围、每次的原始日志和报告。都在你电脑上。</p>
  </section>
  <section class="panel"><h2>提示音</h2><p class="note" style="margin:0 0 8px">多轮实测时，收好一轮"叮"一声，没收全低一声。点"试听"听听这台电脑上能不能听到。</p><div class="acts" style="margin:0;align-items:center"><button class="btn" data-beeptest="1">试听</button>${beepMsg?`<span class="note">${beepMsg==='running'?'浏览器的声音是开着的：要是没听到，看看电脑音量、有没有静音、有没有接了别的耳机。':`浏览器没让网页出声（${htmlEsc(beepMsg)}）：再点一次「试听」；还不行的话看看浏览器对这个网页是不是设了"不允许自动播放"。`}</span>`:''}</div></section>
  <div class="acts" style="justify-content:center;margin:4px 0 8px"><button class="btn pri" data-save="1" style="min-width:10em">保存</button></div>
</div>`;
}

// ===== 为什么、怎么改（2026-10-13 补：需求 9）=====
// 真产品里：问题从数据算出（反而晚、偶发慢、大头），"为什么、怎么改"由 AI 读代码 + 这次数据写，效果都是估算
const REPVAR={a:{label:'A 问题卡片',idea:'A 问题卡片：每个问题一张卡，先说为什么、对用户有什么影响，再列可选方案（效果、代价、工作量、找谁），最后一句建议。最接近验证 06 样例报告，从上往下读完就能拍板。'},
 b:{label:'B 勾方案',idea:'B 勾方案：把所有问题的方案摊在一张表里，负责人直接勾要做的，下面自动汇总预计效果、要找谁、哪些有代价要权衡，还能生成一份待办发给相关的人。重在"拍板"这个动作。'},
 c:{label:'C 点哪段看哪段',idea:'C 点哪段看哪段：先选一段，旁边说清这段在做什么、用了多久、正不正常；有问题的段再给为什么和方案。适合想弄懂整条链路的人，也能看"这段为什么不用改"。'}};
let repVar='a',fixPick=null,fixTodo=false,segSel=null,stale=1;
// 真测的报告：合理范围（标准文件）在测完以后改过，才提示（决策 54）；刚测完的不显示
function staleBar(){if(JREP&&!JREP._none&&JREP.scenario&&JREP.scenario.standard!==undefined&&repKey().startsWith('real')){
  if(JREP.stale)return `<section class="panel stale"><p style="margin:0 0 4px"><b>⚠ 合理范围在 ${htmlEsc(JREP.stale_since)} 改过，这份报告还是按改之前的范围判断的。</b></p><p class="note" style="margin:0 0 8px">不用再测。测出来的用时不变，会重新给出：哪段正常、哪段偏慢，以及要不要改。</p><div class="acts" style="margin:0"><button class="btn pri" data-stale="1">用新范围更新这份报告</button></div></section>`;
  return JREP._refreshed?`<p class="note" style="margin:0">✓ 已按 ${htmlEsc(JREP._refreshed)} 改过的合理范围更新。测出来的用时没变。</p>`:''}
 return flow?'':stale===1?`<section class="panel stale"><p style="margin:0 0 4px"><b>⚠ 合理范围在 10-13 14:20 改过，这份报告还是按改之前的范围判断的。</b></p><p class="note" style="margin:0 0 8px">不用再测。测出来的用时不变，会重新给出：哪段正常、哪段偏慢，以及要不要改。</p><div class="acts" style="margin:0"><button class="btn pri" data-stale="1">用新范围更新这份报告</button></div></section>`:stale===2?`<p class="note" style="margin:0">✓ 已按 10-13 14:20 改过的合理范围更新。测出来的用时没变。</p>`:''}
// 负责人看的文字不出现代码：括号里带文件名、行号、函数名的整段去掉（码表格式检查也会拦，这里兜底早先存下的建议）
const noCode=t=>String(t||'').replace(/\s*[（(][^（）()]*?(?:[\w/.-]+\.(?:c|cc|cpp|cxx|h|hpp)\b|第\s*\d+\s*行|[A-Za-z_]\w*\(\))[^（）()]*?[）)]/g,'');
function activeFixes(D){const out=[];decisions(D).forEach(d=>{const F=(D.S.fixes||[])[cardOf(D.S.fixes||[],d)];if(F&&!out.some(o=>o.when===F.when))out.push({...F,title:d.t,lv:d.lv,why:noCode(plain(F.why)),impact:noCode(plain(F.impact)),rec:noCode(plain(F.rec)),opts:F.opts.map(o=>({...o,name:noCode(plain(o.name)),effect:noCode(plain(o.effect)),cost:noCode(plain(o.cost)),wn:noCode(plain(o.wn))}))})});return out}
const ESTNOTE='<ul class="notes"><li><b>工作量</b>只分大、中、小，看的是要改多少、要不要别人配合，不折算成天数（码表不知道你们团队的人手和熟练度）。</li><li><span class="wk wk-s">小</span> 改一处　<span class="wk wk-m">中</span> 改几处，或要另一方配合　<span class="wk wk-l">大</span> 新做一块，或要长期维护</li><li><b>效果</b>都是估算：依据是这次的实测数据、代码和公开资料，做完要用码表再测一次才算数。</li><li>码表只给建议，不会自动改代码。</li></ul>';
function optTable(F,fi,pick){
  return `<div class="scroll"><table><thead><tr>${pick?'<th></th>':''}<th>方案</th><th>效果（估算）</th><th>代价</th><th>工作量</th><th>找谁</th></tr></thead><tbody>${F.opts.map((o,oi)=>{const id=fi+'-'+oi;return `<tr>${pick?`<td><input type="checkbox" data-pick="${id}" ${fixPick.has(id)?'checked':''} aria-label="选这个方案"></td>`:''}<td><b>${o.name}</b>${o.rec?' <span class="chip c-ok">建议</span>':''}${o.risk?' <span class="chip c-warn">要权衡</span>':''}</td><td>${o.effect}</td><td>${o.cost}</td><td><span class="wk wk-${{'小':'s','中':'m','大':'l'}[o.work]||'u'}">${o.work}</span><br><span class="note">${o.wn}</span></td><td>${o.who}</td></tr>`}).join('')}</tbody></table></div>`;
}
function initPick(FX){if(!fixPick){fixPick=new Set();FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(o.rec)fixPick.add(fi+'-'+oi)}))}}
let copied=null;
// 结论建议、待办里的文字：把"P2-P5""P4→P5""P7"这类编号换成中文名（技术细节里照原样）
function plain(t){if(!t)return t;const D=build(page==='report'&&JREP&&JREP._key===repKey()&&!JREP._none?repScene():SCENES[scene]);
 const seg={};Object.entries(D.S.segs).forEach(([k,g])=>{seg[k]=g.name;const [a,b]=String(g.code||'').split('→').map(x=>x.trim());if(a&&b){seg[a+'-'+b]=g.name;seg[a+'→'+b]=g.name}});
 const pt={};(repKey().startsWith('real')&&IDENT&&IDENT.page?IDENT.page.steps:FF[scene].steps||[]).forEach(x=>pt[x.p]=x.n);
 // 括号里的编号前面已有中文名，直接去掉；没括号的换成中文名
 return String(t).replace(/\s*[（(]\s*P\d+\s*(?:[-→～~]\s*P\d+\s*)?[）)]/g,'')
  .replace(/\b(P\d+)\s*[-→～~]\s*(P\d+)\b/g,(m,a,b)=>seg[a+'-'+b]?`“${seg[a+'-'+b]}”`:pt[a]&&pt[b]?`“${pt[a]}”到“${pt[b]}”`:m)
  .replace(/\b(P\d+)\b/g,(m,a)=>pt[a]?`“${pt[a]}”`:m)}
// 导出：把这份报告存成一个 HTML 文件（样式和"结论建议 / 技术细节"切换都在文件里，断网能开，不引用外面的文件）。
// 按钮去掉、勾选换成 ☑ / ☐（导出那一刻勾的）、待办展开；"合理范围改过"提示条和"改完以后 / 还原"不放进去。
function expStrip(h,pre){const d=document.createElement('div');d.innerHTML=h;
  d.querySelectorAll('.stale,.expdone').forEach(x=>x.remove());
  d.querySelectorAll('section').forEach(x=>{const t=(x.querySelector('h2')||{}).textContent||'';if(t.startsWith('改完以后')||t.startsWith('设备上现在是'))x.remove()});
  d.querySelectorAll('input[type=checkbox],input[type=radio]').forEach(i=>{const s=document.createElement('span');s.textContent=i.checked?'☑':'☐';s.className='ck';i.replaceWith(s)});
  d.querySelectorAll('button').forEach(b=>{if(b.dataset.jump){const a=document.createElement('a');a.href='#'+pre+b.dataset.jump;a.className='lnk';a.textContent=b.textContent;b.replaceWith(a)}else b.remove()});
  d.querySelectorAll('[id]').forEach(x=>x.id=pre+x.id);d.querySelectorAll('.flash').forEach(x=>x.classList.remove('flash'));
  d.querySelectorAll('details').forEach(x=>x.removeAttribute('open'));return d.innerHTML}  // 导出也默认收起，点开能看（决策 98）
// 导出存到哪、存完的提示（一直在，直到你关掉）
let EXP={dir:'',saved:null};
const expWhat=()=>realFlash()?(IDENT.page.mapWhat||hSay||''):'';  // 文件名用你自己写的场景说法
function expDone(){return EXP.saved?`<section class="panel expdone" role="status"><div class="acts" style="justify-content:space-between;align-items:center;margin:0;flex-wrap:wrap"><span>✓ 已导出：<span class="code">${htmlEsc(EXP.saved)}</span></span><span style="display:flex;gap:8px"><button class="pill" data-expshow="1">在访达里显示</button><button class="pill" data-expclose="1">关掉</button></span></div></section>`:''}
async function exportStart(){EXP.state='';EXP.err='';try{EXP.dir=(await (await fetch('/api/report/export-dir'+(repKey().startsWith('real')?'':'?demo='+encodeURIComponent(scene)))).json()).dir}catch(e){}pop='export';render()}
async function exportReport(folder){
  const keep={mode,fixTodo},m=document.getElementById('main');
  mode='boss';fixTodo=false;render();const boss=m.innerHTML;mode='dev';render();const dev=m.innerHTML;mode=keep.mode;fixTodo=keep.fixTodo;render();
  const css=await (await fetch('/static/style.css')).text();
  const crumb=document.getElementById('crumb').cloneNode(true);crumb.querySelectorAll('button,.mock').forEach(x=>x.remove());
  const info=crumb.textContent.replace(/^\s*›\s*/,'').trim(),parts=info.split('·').map(x=>x.trim()),what=expWhat()||parts[1]||parts[0]||'报告';
  const real=repKey().startsWith('real')&&JREP&&!JREP._none,mock=!!document.querySelector('#crumb .mock');
  const at=real&&JREP.measured_at?'测于 '+JREP.measured_at:'导出于 '+new Date().toLocaleString('zh-CN',{hour12:false}).replace(/\//g,'-').slice(0,-3);
  const log=real&&JREP.raw_log?`原始日志在：${htmlEsc(JREP.raw_log)}`:'这份是模拟数据，没有原始日志文件。';
  const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>码表报告 · ${htmlEsc(info)}</title><style>${css}
.exphead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin:16px 0 8px}.exphead h1{font-size:15px;margin:0;color:var(--muted);font-weight:500}.ck{font-size:16px;margin-right:4px}
.expnote{font-size:13px;color:var(--muted);border-top:1px solid var(--line);margin-top:24px;padding-top:10px}
@media print{.tabs{display:none}#v-dev-body{display:block!important;page-break-before:always}}</style></head>
<body><div class="wrap" style="max-width:960px;margin:0 auto;padding:0 16px">
<div class="exphead"><div><h1>⏱ 固件码表 · 测量报告（导出）</h1><div class="note">${htmlEsc(info)} · ${at}${mock?'<span class="mock">模拟数据，不是实测</span>':''}</div></div>
<div class="tabs seg" role="group" aria-label="报告版本"><button id="tb-b" aria-pressed="true" onclick="sw('b')">结论建议</button><button id="tb-d" aria-pressed="false" onclick="sw('d')">技术细节</button></div></div>
<div id="v-boss-body">${expStrip(boss,'')}</div><div id="v-dev-body" hidden>${expStrip(dev,'d-')}</div>
<p class="expnote">这是码表导出的报告：一个文件，断网也能打开，可以直接发给别人。数字由码表按这次的数据直接算出，不经过 AI；"为什么、怎么改"的文字是 AI 读代码写的。勾选的方案和待办是导出那一刻的样子；要改勾选，请在码表里打开这份报告。${log}</p></div>
<script>function sw(v){document.getElementById('v-boss-body').hidden=v!=='b';document.getElementById('v-dev-body').hidden=v!=='d';document.getElementById('tb-b').setAttribute('aria-pressed',v==='b');document.getElementById('tb-d').setAttribute('aria-pressed',v==='d');scrollTo(0,0)}<\/script></body></html>`;
  let r,j;try{r=await fetch('/api/report/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({html,what,demo:real?null:scene,folder:folder||null})});j=await r.json()}catch(e){r={ok:false};j={detail:'码表后台没有回应，看看运行码表的窗口是不是关了。'}}
  if(!r.ok){EXP.state='fail';EXP.err=j.detail||'导出没成功';toast('✕ '+EXP.err);render();return}
  EXP.saved=j.path;EXP.state='ok';EXP.partial=!!(ADV&&ADV.status!=='done'&&repKey().startsWith('real'));histLoad();render();return j.path}
function todoText(w,chosen,FX){const mine=chosen.filter(o=>o.who===w);
  return `你好，这次用码表测了设备的反应速度，有 ${mine.length} 件事要请你处理：\n\n`+mine.map((o,k)=>{const F=FX[o.fi];return `${k+1}. ${plain(o.name)}\n   为什么：${F.title}。${plain(F.why)}\n   怎么做：${plain(o.how)}\n   做完的标准：${plain(F.dev.verify)}`}).join('\n\n')+`\n\n做完告诉我，我用码表按同样的条件再测一次。`}
function myPick(D,FX){initPick(FX);
  const chosen=[];FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(fixPick.has(fi+'-'+oi))chosen.push({...o,fi})}));
  const who={};chosen.forEach(o=>(who[o.who]=who[o.who]||[]).push(o.name));
  const none=FX.map((F,fi)=>chosen.some(o=>o.fi===fi)?'':`问题 ${fi+1}`).filter(Boolean);
  const body=chosen.length?`<details class="lvwhy"><summary>勾了哪几个、每个预计的效果</summary><ul style="margin:6px 0;padding-left:1.2em">${chosen.map(o=>`<li>问题 ${o.fi+1}：${o.name}。预计${o.effect}。</li>`).join('')}</ul></details>
   <p style="margin:6px 0"><b>要找：</b>${Object.entries(who).map(([w,a])=>`${w}（${a.length} 件）`).join('、')}</p>
   ${chosen.some(o=>o.risk)?`<p style="margin:6px 0;color:var(--warn)"><b>要你权衡：</b>${chosen.filter(o=>o.risk).map(o=>`${o.name}，${o.cost}`).join('；')}。</p>`:''}
   ${none.length?`<p style="margin:6px 0;color:var(--bad)">${none.join('、')}一个方案都没选，等于先不处理。</p>`:''}
   <details class="lvwhy" ${fixTodo?'open':''} data-todod="1"><summary>待办：按人分好的消息（${Object.keys(who).length} 个人）</summary><p class="note" style="margin:10px 0 6px">按人分好，每人一块，各自复制后贴到钉钉、微信或邮件里发给对方。</p>${Object.keys(who).map((w,wi)=>`<div class="todo1"><div class="todoh"><b>发给：${w}</b><button class="pill" data-copy="todo-${wi}">${copied===w?'已复制':'复制给'+w}</button></div><pre id="todo-${wi}" data-who="${w}">${todoText(w,chosen,FX)}</pre></div>`).join('')}</details>`
   :'<p class="note">还没勾任何方案。</p>';
  return `<div class="panel" id="my-pick" style="border-left:3px solid var(--speak)"><h3 style="margin:0 0 4px">你的决定：勾了 ${chosen.length} 个方案</h3>${body}</div>`;
}
function fixA(D){const FX=activeFixes(D);initPick(FX);
  return FX.map((F,fi)=>`<div class="panel" id="fix-${fi}" style="border-left:3px solid ${F.lv===0?'var(--bad)':'var(--warn)'};padding-top:10px;padding-bottom:10px">
   <details class="fixmore"><summary style="display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap">${lvTag(F.lv)}问题 ${fi+1}：为什么、对用户的影响、可选方案<span hidden>${fi}</span></summary>
   <h3 style="margin:10px 0 8px">${F.title}</h3>
   <p style="margin:0 0 6px"><b>为什么：</b>${F.why}</p><p style="margin:0 0 10px"><b>对用户的影响：</b>${F.impact}</p>
   ${optTable(F,fi,true)}<p style="margin:10px 0 0"><b>建议：</b>${F.rec}</p></details></div>`).join('')+myPick(D,FX)+
   `<details class="lvwhy"><summary>不用改的、工作量和效果怎么估的</summary><p class="note" style="margin:6px 0 0"><b>不用改的：</b>${D.S.fine}</p>${ESTNOTE}</details>`;
}
function fixB(D){const FX=activeFixes(D);
  if(!fixPick){fixPick=new Set();FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(o.rec)fixPick.add(fi+'-'+oi)}))}
  const chosen=[];FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(fixPick.has(fi+'-'+oi))chosen.push({...o,fi})}));
  const who={};chosen.forEach(o=>(who[o.who]=who[o.who]||[]).push(o.name));
  const rows=FX.map((F,fi)=>`<h3 id="fix-${fi}" style="margin:14px 0 4px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">${lvTag(F.lv)}问题 ${fi+1}：${F.title}</h3><p class="note" style="margin:0 0 6px">${F.why}</p>${optTable(F,fi,true)}`).join('');
  const sum=chosen.length?`<ul style="margin:6px 0">${chosen.map(o=>`<li>问题 ${o.fi+1}：${o.name}。预计${o.effect}。</li>`).join('')}</ul>
   <p style="margin:6px 0"><b>要找：</b>${Object.entries(who).map(([w,a])=>`${w}（${a.length} 件）`).join('、')}</p>
   ${chosen.some(o=>o.risk)?`<p style="margin:6px 0;color:var(--warn)"><b>要你权衡：</b>${chosen.filter(o=>o.risk).map(o=>`${o.name}，${o.cost}`).join('；')}。</p>`:''}
   ${FX.some((F,fi)=>!chosen.some(o=>o.fi===fi))?`<p style="margin:6px 0;color:var(--bad)">还有问题一个方案都没选：${FX.map((F,fi)=>chosen.some(o=>o.fi===fi)?'':`问题 ${fi+1}`).filter(Boolean).join('、')}。不选也可以，等于先不处理。</p>`:''}
   <div class="acts" style="margin-top:8px"><button class="btn pri" data-todo="1">${fixTodo?'收起待办':'生成待办，发给相关的人'}</button></div>
   ${fixTodo?`<pre style="white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;font-size:13.5px;margin:10px 0 0">${Object.entries(who).map(([w,a])=>`【${w}】\n${chosen.filter(o=>o.who===w).map(o=>`· ${o.name}（问题 ${o.fi+1}：${FX[o.fi].title}）\n  做完的标准：${FX[o.fi].dev.verify}`).join('\n')}`).join('\n\n')}\n\n做完后用码表按同样的条件再测一次，看前后对比。</pre><p class="note">样子里只演示文字；真产品可以复制，或存成文件。</p>`:''}`
   :'<p class="note">还没勾任何方案。</p>';
  return `<div class="panel">${rows}</div><div class="panel" style="border-left:3px solid var(--speak)"><h3 style="margin:0 0 4px">你勾了 ${chosen.length} 个方案</h3>${sum}</div><p class="note" style="margin:0"><b>不用改的：</b>${D.S.fine}</p>${ESTNOTE}`;
}
function segJudge(D,s){if(D.J){const j=D.J.segments.find(x=>x.k===s.k);if(j){if(j.spiky)return ['偶发特别慢','c-warn'];return [j.verdict==='没有范围'?'还没有合理范围':j.verdict,VCLS[j.verdict]||'c-gray']}}const [lo,hi]=s.ok.split('～').map(Number);const spk=s.max>s.med*2.5&&s.max-s.med>.3;return spk?['偶发特别慢','c-warn']:s.med>hi?['偏慢','c-warn']:['正常','c-ok']}
function fixC(D){const FX=activeFixes(D);
  if(!segSel||!D.S.segs[segSel])segSel=FX.length?FX[0].seg:D.allSegs[0].k;
  const s=D.allSegs.find(x=>x.k===segSel),[jv,jc]=segJudge(D,s),fi=FX.findIndex(F=>F.seg===segSel),F=FX[fi];
  const btns=D.allSegs.map(x=>{const [v,c]=segJudge(D,x),hit=FX.some(F=>F.seg===x.k);return `<button class="btn" data-seg="${x.k}" aria-pressed="${x.k===segSel}" style="${x.k===segSel?'border-color:var(--fg);font-weight:600':''}">${x.name} <span class="chip ${hit&&c==='c-ok'?'c-warn':c}">${hit&&c==='c-ok'?'有问题':v}</span></button>`}).join('');
  return `<div class="panel"><p class="note" style="margin:0 0 8px">按发生顺序，点一段看详情：</p><div class="acts">${btns}</div></div>
   <div class="panel" id="fix-${fi}" style="border-left:3px solid ${F?(F.lv===0?'var(--bad)':'var(--warn)'):'var(--ok)'}"><h3 style="margin:0 0 6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">${F?lvTag(F.lv):''}${s.name} <span class="chip ${jc}">${jv}</span> <span class="note">谁负责：${s.owner}</span></h3>
   <p style="margin:0 0 6px"><b>这段在做什么：</b>${s.what}。</p>
   <p class="num" style="margin:0 0 10px">一般 ${f(s.med)} 秒，最慢 ${f(s.max)} 秒，合理 ${s.ok} 秒</p>
   ${F?`<p style="margin:0 0 6px"><b>为什么是问题：</b>${F.why}</p><p style="margin:0 0 10px"><b>对用户的影响：</b>${F.impact}</p>${optTable(F,fi,false)}<p style="margin:10px 0 0"><b>建议：</b>${F.rec}</p>`:'<p style="margin:0"><b>不用改。</b>这段在合理范围里，也没有偶发特别慢的轮。</p>'}</div>${ESTNOTE}`;
}
function fixDev(D){const FX=activeFixes(D);initPick(FX);
  return `<section class="panel"><h2>怎么改</h2><p class="note" style="margin:-4px 0 10px">每个方案的改法都列出来；"已选"跟着结论建议里的勾选走，改了勾选这里会跟着变。</p>${FX.map((F,fi)=>`<div style="margin-bottom:16px"><h3 style="margin:0 0 6px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">${lvTag(F.lv)}问题 ${fi+1}：${F.title}</h3>
   <table><tbody><tr><th style="width:7em">数据</th><td>${F.dev.data}</td></tr><tr><th>原因</th><td>${F.dev.cause}</td></tr><tr><th>怎么复测</th><td>${F.dev.verify}</td></tr></tbody></table>
   ${F.opts.map((o,oi)=>{const on=fixPick.has(fi+'-'+oi);return `<div class="optdev ${on?'on':''}"><b>${o.name}</b> <span class="chip ${on?'c-ok':'c-gray'}">${on?'已选':'没选'}</span><ul class="notes"><li>改法：${o.how}</li><li>改动大小：${o.size}</li><li>风险：${o.rk}</li></ul></div>`}).join('')}</div>`).join('')}
   <p class="note" style="margin:0">改法是 AI 读代码后给的思路，没有实现、没有测过；代码位置按这次识别结果。码表不会自动改代码。</p></section>`;
}
function reportPage(D){
  if(mode==='dev')return dev(D);
  return `<div class="stack">${expDone()}${staleBar()}<div><h1>${headline(D)}</h1><p class="sub">${subline(D)}</p>${D.S.real&&JREP?qualityLine(JREP.quality):''}${D.n<10?`<p class="note" style="margin:4px 0 0;color:var(--bad)">只测了 ${D.n} 轮，适合跑流程，数字不作数；要看一般多快、偶尔出的问题，建议测到 20 轮。</p>`:D.n<20?`<p class="note" style="margin:4px 0 0;color:var(--warn)">这次只测了 ${D.n} 轮（少于 20 轮），次数少，结论只供参考；再测几轮会更准。</p>`:''}</div>
  <section class="panel"><h2>要你拍板</h2>${decideList(D)}</section>
  <section class="panel" id="sec-tl"><h2>一次的计时图</h2><details class="lvwhy" style="margin:-4px 0 8px"><summary>画的是第 ${D.rep.n} 轮</summary><p class="note" style="margin:4px 0 0">画的是真实的一轮：第 ${D.rep.n} 轮。${D.n} 轮里它最接近一般情况，所以拿它当代表。<br>每轮的情况见下方「${D.n} 轮逐轮」图，一般、${D.n<10?'':'10 次里最慢、'}最差见「三档用时」。</p></details>${timeline(D)}</section>
  <section class="stack"><h2 style="margin:8px 0 -6px">为什么、怎么改</h2>${D.S.real&&(!ADV||ADV.status!=='done')?advBox():fixA(D)}</section>
  <h2 style="margin:12px 0 -6px">依据</h2><p class="note" style="margin:0 0 -6px">上面结论里的数，都能在下面找到。</p>
  <section class="grid3" id="sec-lines">${lineCards(D)}</section>
  <section class="panel" id="sec-tiers"><h2 style="display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline">三档用时<span class="tiernote">${D.n<10?`只测了 ${D.n} 轮，不算"10 次里最慢"`:`<i class="rdot"></i>"10 次里最慢"：每 10 次大约有 1 次会这么慢或更慢${D.n<20?'（这次只收了 '+D.n+' 轮，这一档只供参考）':''}`}</span></h2>${tiers(D)}</section>
  <section class="panel" id="sec-rounds"><h2>${D.n} 轮逐轮</h2>${roundsChart(D)}</section>${retestEntry()}${restoreBox(scene)}</div>`;
}

let page='home',mode='boss',scene='',homeVar='m',hStage=0,hSay='';
try{const h=location.hash.slice(1);if(PAGES.some(p=>p.id===h))page=h;const s=localStorage.getItem('fsw-home');}catch(e){}
const save=(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}};
// ===== 找关键步骤 =====

let edits={},added=[],editP=null,addSt=0,addManual=false,addLine='talk',addName='',mapOpen=false,stepsVar='a',stepsRead=0,readT=null,readSec=0,readFound=0,alsoFace=true,dropped=new Set(),mapSel='';
const useFace=()=>alsoFace!==false;
const allRows=()=>[...STEPS,...added].filter(x=>useFace()||x.line!=='face');
const live=()=>allRows().filter(x=>!dropped.has(x.p));
let bgSec=0,bgDur=70,bgT=null,bgSrc='';
const bgDone=()=>bgSec>=bgDur,bgPct=()=>Math.min(100,Math.round(bgSec/bgDur*100));
// ===== 后台编原始代码（真的）：选了真源码时，后台真编那份一个字没改的副本，这里每秒问一次进度 =====
let BG=null,bgPoll=null;
const bgFailed=()=>!!(BG&&BG.state==='failed'),bgWaitTool=()=>!!(BG&&BG.state==='waiting_tool');
const htmlEsc=t=>String(t||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const BGTIME=()=>{if(BG&&BG.seconds!=null){const t=Math.round(BG.seconds);return t>=60?`${Math.floor(t/60)} 分 ${t%60} 秒`:`${t} 秒`}return FF[scene].bg};
function bgStartReal(){clearInterval(bgT);bgT=null;clearInterval(bgPoll);bgSrc=hSrc;bgSec=0;bgDur=100;BG={state:'running',pct:0};
 const body={work:REAL.work,chip:REAL.chip,board:REAL.board,idf_min:REAL.idf_min,project_dir:REAL.project_dir};  // 带上项目：源码没变就用上次编好的原版（决策 134）
 const show=s=>{BG=s;bgSec=s.state==='done'?100:(s.pct||0);bgPill();if(pop==='bg')morphHTML(document.getElementById('popdlg'),popHTML());if(page==='home'&&hStage===3&&stepsRead===2)document.getElementById('dlg').innerHTML=modalHTML()};
 const tick=async()=>{let s;try{s=await (await fetch('/api/bgbuild')).json();if(s.state==='waiting_tool'||s.state==='idle'||s.work!==body.work)s=await api('/api/bgbuild/start',body)}catch(e){return}
  show(s);
  if(s.state==='done'||s.state==='failed'){clearInterval(bgPoll);bgPoll=null;if(s.state==='done'){bgShownDone=Date.now();setTimeout(bgPill,5100)}
   if(page==='flash'||page==='steps'||(page==='home'&&hStage===3))render()}};
 api('/api/bgbuild/start',body).then(show).catch(()=>{});bgPoll=setInterval(tick,1000)}
function bgStart(){if(REAL&&REAL.work)return bgStartReal();clearInterval(bgT);bgSrc=hSrc;bgSec=0;bgDur=demoErr==='slowbg'?260:70;
 bgT=setInterval(()=>{if(!envComp())return;bgSec+=7;const done=bgDone();
  bgPill();if(pop==='bg')morphHTML(document.getElementById('popdlg'),popHTML());
  if(done){clearInterval(bgT);bgT=null;bgShownDone=Date.now();setTimeout(bgPill,5100);if(page==='flash'||page==='steps'||(page==='home'&&hStage===3))render()}},400)}
// 真找：后台调用大模型只读源码，这里每秒问一次进度（已等多少秒、读了几处代码、已找到哪几个）
let IDENT=null,reuseLast=true;
const LLMN={claude:'Claude',deepseek:'DeepSeek',cc:'本机 Claude Code','claude-api':'Claude','claude-code':'本机 Claude Code'};
const usedLLM=()=>LLMN[(IDENT&&IDENT.provider)||SAVED.llm_provider||setLLM];
const mmss=x=>{x=Math.round(x||0);return x<60?`${x} 秒`:`${Math.floor(x/60)} 分 ${x%60} 秒`};
function identApply(j){IDENT=j;readSec=j.state==='running'&&j.started?Math.max(0,Math.round(Date.now()/1000-j.started)):Math.round(j.seconds||0);readFound=(j.found||[]).length;
 if(j.state==='done'){clearInterval(readT);readT=null;FF[scene]={...FF[scene],...j.page};mapSel=j.page.mapSel;dropped=new Set();edits={};added=[];alsoFace=true;applyScene();readFound=STEPS.length;stepsRead=2;render();return}
 if(j.state==='failed'){clearInterval(readT);readT=null;stepsRead=3;render();return}
 const b=document.getElementById('readbox');if(b)morphHTML(b,readBox())}
async function identStart(retry){clearInterval(readT);stepsRead=1;readSec=0;readFound=0;IDENT=null;if(bgSrc!==hSrc)bgStart();
 const r=await fetch(retry?'/api/identify/retry':'/api/identify/start',{method:'POST',headers:{'Content-Type':'application/json'},body:retry?'{}':JSON.stringify({work:REAL.work,what:hSay,project_dir:REAL.project_dir,fingerprint:REAL.fingerprint,chip:CHIP(),reuse:reuseLast})});
 const j=await r.json();if(!r.ok){IDENT={state:'failed',kind:'start',message:j.detail,found:[]};stepsRead=3;render();return}
 identApply(j);if(stepsRead===1)readT=setInterval(async()=>{try{identApply(await (await fetch('/api/identify')).json())}catch(e){}},1000)}
function readStart(){if(REAL&&REAL.work)return identStart();clearInterval(readT);stepsRead=1;readSec=0;readFound=0;if(bgSrc!==hSrc)bgStart();
 readT=setInterval(()=>{readSec+=7;if(readSec>=42)readFound=Math.min(STEPS.length,readFound+2);
  if(readFound>=STEPS.length&&readSec>=70){clearInterval(readT);readT=null;stepsRead=2;if(page==='home'&&hStage===3){render()}else if(page==='steps')render();return}
  const b=document.getElementById('readbox');if(b)morphHTML(b,readBox())},400)}
function readBox(){const llm=usedLLM();
 if(REAL&&IDENT)return readBoxReal(llm);
 const st=stepsRead===2?3:readSec<6?0:readFound===0?1:2;
 const row=(i,t,d)=>`<li class="istep ${i<st?'ok':i===st?'cur':''}"><b>${i<st?'✓':i+1}</b><span>${t}<small>${d}</small></span></li>`;
 return `<ol class="isteps">
  ${row(0,'挑出要读的代码','码表自己做，不用大模型：'+FF[scene].scope)}
  ${row(1,`交给 ${llm}，等它读完`,`已等 ${readSec} 秒。大模型要整段读完才开始回答，这一步没有百分比，一般 1～3 分钟（估算；验证期 DeepSeek 实测 53 秒）`)}
  ${row(2,'AI 读完后，在这里列出找到的步骤',`${stepsRead===2?'找完了，共':'已找到'} ${readFound} 个`)}</ol>
  ${stepsRead===2?foundList():`${readFound?`<div class="list">${STEPS.slice(0,readFound).map(x=>`<div class="srow"><b>${x.n}</b><span class="note">${x.f} 第 ${x.l} 行</span></div>`).join('')}</div>`:''}
  <p class="note">只发了代码，没发任何"标准答案"；结果先存在本机，源码没变下次直接用，不再花钱调用。关掉这页不影响，读完会留着。</p>`}`}
// 没法知道真实进度的等待（大模型读代码）：按一般用时估一个进度，最多到 95%，等完了才满（决策 105）
const estPct=(sec,typ)=>Math.min(95,Math.round(100*(1-Math.exp(-sec/typ))));
function readBoxReal(llm){const J=IDENT,fail=stepsRead===3,done=stepsRead===2,found=J.found||[];
 const st=done?3:fail?(found.length?2:1):!found.length&&readSec<6?0:found.length?2:1;
 const row=(i,t,d)=>`<li class="istep ${i<st?'ok':i===st?(fail?'bad':'cur'):''}"><b>${i<st?'✓':fail&&i===st?'!':i+1}</b><span>${t}<small>${d}</small></span></li>`;
 if(J.reused)return `<ol class="isteps">${row(0,'挑出要读的代码','码表自己做，不用大模型')}${row(1,'源码和要测的都没变，直接用上次的结果','没有调用大模型，不花钱（设置页可以关掉这个）')}${row(2,'列出找到的步骤',`共 ${STEPS.length} 个`)}</ol>${foundList()}`;
 return `<ol class="isteps">
  ${row(0,'挑出要读的代码',`码表自己做，不用大模型：${htmlEsc(REAL.name)} 复制好的那份源码，大模型只能读、改不了任何文件`)}
  ${row(1,`交给 ${llm}，等它读完`,`${st===1?`已等 ${mmss(readSec)}，`:''}一般 1～3 分钟。${st===1?`<span class="pbar"><i style="width:${estPct(readSec,150)}%"></i></span>进度条是按一般用时估的，不是真实进度`:''}`)}
  ${row(2,'AI 读完后，在这里列出找到的步骤',`${done?'找完了，共':'已找到'} ${done?STEPS.length:found.length} 个`)}</ol>
  ${done?foundList():`${found.length?`<div class="list">${found.map((n,k)=>{const at=((J.at||[])[k]||'').match(/([^/\s]+):(\d+)$/);return `<div class="srow"><b>${htmlEsc(n)}</b>${at?`<span class="note">${htmlEsc(at[1])} 第 ${at[2]} 行</span>`:''}</div>`}).join('')}</div>`:''}`}
  ${fail?`<p class="note" style="color:var(--bad)">✕ ${J.message||'没找完'}</p><div class="acts" style="justify-content:flex-start;margin:6px 0"><button class="btn pri" data-idretry="1">重试</button><button class="btn" data-go="settings">到设置页换大模型</button>${/no_start|no_end/.test(J.kind||'')?'<button class="btn" data-mclose="1">换个说法写</button>':''}</div>`:''}
  <p class="note">只给它读代码，没给任何"标准答案"；结果存在本机，源码和要测的都没变，下次直接用，不再花钱调用。关掉这页不影响，读完会留着。</p>`}
function foundList(){const F=FF[scene],rows=STEPS,mid=rows.filter(x=>x.sure!=='高').length;
 return `${REAL&&IDENT&&IDENT.page&&IDENT.page.warn?`<div class="why" style="border-left-color:var(--bad);margin-top:12px">${htmlEsc(IDENT.page.warn)}</div>`:''}<p style="margin:12px 0 4px">找到 <b>${rows.length}</b> 个关键步骤：${mid} 个需要你核对，${rows.length-mid} 个可信度高。</p>
   <ol class="found">${rows.map(x=>`<li>${x.n} <span class="note">${x.f} 第 ${x.l} 行${x.line==='face'?' · '+F.ln.face:''}${x.sure!=='高'?' · <span style="color:var(--warn)">需核对</span>':''}</span></li>`).join('')}</ol>
   ${F.sideAsk?`<p class="note">另外发现：${F.sideAsk.found}。标「${F.ln.face}」的 ${STEPS.filter(x=>x.line==='face').length} 个是顺带发现的，默认也测，下一页可以去掉。</p>`:''}`}
let bgShownDone=0;
const BGTIP='原始代码：你选的那份源码，码表复制一份，一个字没改、没插计时点。<br>点「开始找关键步骤」时开始编，这时源码和要测的内容都定了。插完计时点后的「编译」要用它，只重编改过的几个文件（验证期实测：从头编 119 秒，只重编 80 秒）。<br>没编完，只有「编译」那一步会先等它，其他步骤照常做。你原来的文件夹不动。';
function bgPill(){const el=document.getElementById('bgpill');if(!el)return;let h='';
 if(bgSrc&&hSrc){if(bgFailed())h=`<button class="bgp warn" data-bgpop="1">原始代码编译没通过</button>`;else if(!envComp()||bgWaitTool())h=`<button class="bgp warn" data-bgpop="1">后台编原始代码：等编译工具装好</button>`;
  else if(!bgDone())h=`<button class="bgp" data-bgpop="1" title="点开看详情"><span class="spin"></span>后台在编原始代码 ${bgPct()}%</button>`;
  else if(Date.now()-bgShownDone<5000)h=`<span class="bgp ok">✓ 原始代码已编好</span>`}
 morphHTML(el,h)}
function bgRow(){const done=bgDone(),noTool=!envComp();
 return `<div class="bgrow">${done?'✓':noTool?'!':'<span class="spin"></span>'} <b>后台在编原始代码（选好源码就开始了）</b>：${done?'编好了，用了 '+BGTIME():noTool?(inst.st===1?'等编译工具装好就开始':'等编译工具装好就开始，烧录前装好就行'):bgPct()+'%（验证期实测约 2 分钟）'}<span class="note">　不用管；插完计时点后编译要用它，没编完不挡「下一步」</span></div>`}
function bgLight(){return bgDone()?`<span><i class="dot" style="background:var(--ok)"></i>后台：原版编译通过（${BGTIME()}）</span>`:`<span><i class="dot" style="background:var(--warn)"></i>后台：原版还在编（${bgPct()}%），不挡核对和插计时点，编译那步会等它</span>`}
function bgBox(){const pct=bgPct(),done=bgDone(),noTool=!envComp();
 const row=(i,t,d,st)=>`<li class="istep ${st}"><b>${st==='ok'?'✓':i}</b><span>${t}<small>${d}</small>${st==='cur'&&i===2?`<span class="pbar"><i style="width:${pct}%"></i></span>`:''}</span></li>`;
 return `<div class="bgjob"><h3 class="h3" style="margin-top:0">${done?'✓ 后台已把原版编好':stepsRead===2?`后台还在编原版：${pct}%（不挡「下一步」）`:'等它读的时候，码表在后台先编一遍原版（不用你管）'}</h3>
  ${stepsRead===2&&!done?`<p style="margin:0 0 8px;font-size:14px">可以先点「下一步」去核对步骤、看插计时点的改动；<b>只有"编译"那一步要等它编完</b>，到时会自动接着编，不用你点。</p>`:''}
  <ul class="notes" style="margin:0 0 8px"><li><b>为什么编：</b>先确认这个项目在你电脑上编得过。原版就编不过，是环境问题（编译工具、依赖、板子），和计时点无关，码表会当场说缺什么。</li><li><b>编好有什么用：</b>插完计时点只要重编改过的几个文件，快很多；这份原版也留着，"还原成原版"和比较计时点开销时直接用。</li><li><b>动不动你的文件：</b>编的是码表复制出来的那份，你原来的文件夹不动。</li></ul>
  <ol class="isteps">${row(1,'复制一份源码','复制到纯英文的工作目录（乐鑫编译器读不了中文路径）','ok')}
  ${row(2,'编译原版',done?'编译通过，用了 '+BGTIME():noTool?(inst.st===1?'编译工具正在后台装，装好自动开始编':'编译工具还没装，装好后自动开始编'):`已编 ${pct}%。第一次要从头编，一般 1～3 分钟（按项目大小估算）；电脑会忙一点`,done?'ok':noTool?'':'cur')}
  ${REAL?row(3,'查设备',devNow().on?`已连上 ${htmlEsc(devNow().port)}`:'没插上。不急，烧录前插上就行（插上会自动变成"已连上"）',devNow().on?'ok':''):row(3,'查设备','没插上。不急，烧录前插上就行',done?'ok':'')}</ol></div>`}
function faceAsk(){const A=FF[scene].sideAsk;
 if(alsoFace!==null)return `<div class="status"><span><i class="dot" style="background:var(--ok)"></i>${alsoFace?`也测「${A.side}」这条线（多 ${A.k} 个计时点）`:A.no}</span><button class="pill" data-face="reset">重新选择</button></div>`;
 return `<section class="panel" id="sideask"><h2>要不要多测一条线？</h2>
  <p style="margin:6px 0 4px">码表读代码发现：${A.found}。</p>
  <p style="margin:0 0 10px">现在只测「${A.main}」。${A.why}要不要把「${A.side}」也测上？</p>
  <div class="acts" style="justify-content:flex-start"><button class="btn pri" data-face="yes">也测这条（推荐）</button><button class="btn" data-face="no">${A.no}</button></div>
  <p class="note">也测：多插 ${A.k} 个计时点，报告里多一条线；不影响「${A.main}」那条的结果。</p></section>`}
const sureChip=x=>x.sure==='高'?'<span class="chip c-ok">可信度高</span>':x.sure==='你加'?'<span class="chip c-gray">你加的</span>':'<span class="chip c-warn">需核对</span>';
function editBox(x){return `<div class="edit"><div class="inrow"><label for="ed-f">文件</label><input type="text" id="ed-f" value="${x.f}"><label for="ed-l">行号</label><input type="text" id="ed-l" value="${x.l}" style="max-width:6em"></div>
 <p class="note" style="margin:4px 0">在你的代码编辑器里找到要计时的那一行，把文件名和行号填这里。改动会记下来：谁、什么时候、从哪改到哪。</p>
 <div class="acts" style="justify-content:flex-start;margin:4px 0 0"><button class="btn" data-edsave="${x.p}">保存位置</button><button class="btn" data-edcancel="1">取消</button></div></div>`}
let addGuess=null,addErr='';
function addBox(){const F=FF[scene],A=F.sideAsk||{addEx:'收到服务器回复',addGuess:null};
 if(addSt===0)return `<button class="btn" data-add="open" style="margin-top:12px">＋ 添加步骤</button>`;
 const lines=[['talk',F.ln.talk]].concat(useFace()?[['face',F.ln.face]]:[]);
 const head=`<b>添加一个漏掉的步骤</b>`;
 if(addSt===2)return `<div class="edit" style="margin-left:0">${head}<p style="margin:8px 0 0"><span class="spin"></span> AI 正在读代码，找"${addName}"在哪一行……（调用一次大模型，一般几秒到十几秒）</p></div>`;
 if(addSt===3){const g=REAL&&addGuess?addGuess:A.addGuess;return `<div class="edit" style="margin-left:0">${head}<p style="margin:8px 0 4px">AI 找到的位置：<span class="code">${g.f}</span> 第 ${g.l} 行</p><p class="note" style="margin:0 0 6px">${g.why}。AI 推测的，请核对。</p>
  <div class="acts" style="justify-content:flex-start;margin:4px 0 0"><button class="btn" data-add="ok">加上这一步</button><button class="btn" data-add="open">不对，重新描述</button><button class="btn" data-add="cancel">取消</button></div></div>`}
 return `<div class="edit" style="margin-left:0">${head}
  <div class="inrow"><input type="text" id="add-n" value="${addName}" placeholder="用一句话说这一步是什么，比如：${A.addEx||'收到服务器回复'}"></div>${addErr?`<p class="note" style="color:var(--bad);margin:4px 0">✕ ${addErr}</p>`:''}
  <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0"><span class="note">属于哪条线：</span>${lines.map(([k,t])=>`<button class="pill" data-addline="${k}" aria-pressed="${addLine===k}">${t}</button>`).join('')}</div>
  ${addManual?`<div class="inrow"><label for="add-f">文件</label><input type="text" id="add-f" placeholder="比如 main/xxx.c"><label for="add-l">行号</label><input type="text" id="add-l" style="max-width:6em"></div>`:''}
  <div class="acts" style="justify-content:flex-start;margin:6px 0 0">${addManual?'<button class="btn" data-add="manualok">加上这一步</button>':'<button class="btn" data-add="ai">让 AI 找位置</button><button class="btn" data-add="manual">我知道位置，自己填</button>'}<button class="btn" data-add="cancel">取消</button></div></div>`}
function stepsA(){const rows=allRows(),all=[...STEPS.filter(x=>x.line==='face'&&!useFace()),...rows].sort((a,b)=>STEPS.indexOf(a)-STEPS.indexOf(b));const idx=new Map(STEPS.map((x,i)=>[x.p,i]));rows.forEach((x,i)=>{if(!idx.has(x.p))idx.set(x.p,STEPS.length+i)});
 const A=FF[scene].sideAsk,mine=rows.filter(x=>x.line!=='face'),fc=STEPS.filter(x=>x.line==='face');
 const mid=mine.filter(x=>x.sure!=='高'),hi=mine.filter(x=>x.sure==='高');
 const one=x=>{const i=idx.get(x.p),off=dropped.has(x.p);
  if(x.line==='face'&&!useFace())return `<div class="srow2 off"><span class="num">${i+1}</span><div><s>${x.n}</s> <span class="note">这条不测，不插计时点</span></div></div>`;
  if(off)return `<div class="srow2 off"><span class="num">${i+1}</span><div><s>${x.n}</s> <span class="note">已删除，这里不插计时点</span></div><button class="pill" data-undel="${x.p}">恢复</button></div>`;
  return `<div class="srow2"><span class="num">${i+1}</span>
  <div><b>${x.n}</b> <span class="chip ${x.line==='face'?'c-face':x.line==='start'?'c-gray':'c-speak'}">${lineName[x.line]}</span> ${sureChip(x)}${edits[x.p]?' <span class="chip c-gray">你改过位置</span>':''}
   <div class="note"><span class="code">${x.f}</span> 第 ${x.l} 行 · ${x.why}${x.note?`<br><span style="color:var(--warn)">要留意：${x.note}</span>`:''}</div></div>
  <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end"><button class="pill" data-editp="${x.p}">修改位置</button><button class="pill" data-del="${x.p}">删除</button></span></div>${editP===x.p?editBox(x):''}`};
 return `<section class="panel"><h2>找到 ${STEPS.length} 个关键步骤${fc.length?`<span class="note" style="font-weight:400">　你要测的这条 ${STEPS.length-fc.length} 个，顺带发现的 ${fc.length} 个</span>`:''}</h2><p class="note" style="margin-top:0">每个关键步骤插一个计时点，量出每一段用了多久。序号是发生的先后顺序。</p>
 <ul class="notes" style="margin:0 0 8px"><li>位置不对：点「修改位置」，改文件和行号。</li><li>不需要：点「删除」，这里就不插计时点，删了能恢复。</li><li>漏了：点最下面的「添加步骤」。</li></ul>
 <h3 class="h3">需要你核对（${mid.length} 个）</h3><p class="note" style="margin-top:0">AI 不太确定的，看一下说明和"要留意"。</p><div class="stable">${mid.map(one).join('')}</div>
 <details class="hiwrap"><summary>可信度高的 ${hi.length} 个：展开查看</summary><div class="stable">${hi.map(one).join('')}</div></details>
 ${fc.length?`<div id="sideask" style="margin-top:16px;padding-top:12px;border-top:1px solid var(--line)"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap"><h3 class="h3" style="margin:0">「${A.side}」这条：码表顺带发现的 ${fc.length} 个${useFace()?'（推荐也测）':'（不测）'}</h3><button class="pill" data-face="${useFace()?'no':'yes'}">${useFace()?'这条不测':'恢复测这条'}</button></div>
 <p class="note" style="margin:4px 0 6px">你要测的是「${A.main}」。码表读代码时发现：${A.found}。${A.why}也测只多插 ${fc.length} 个计时点，不影响「${A.main}」那条的结果。</p><div class="stable">${fc.map(one).join('')}</div></div>`:''}
 ${addBox()}</section>`}
function stepsB(){const F=FF[scene],W=760,H=250,main=F.main,n=main.length;
 // 最后一个名字长时（真识别出来的常比演示数据长），右边留出地方，免得被切掉
 const lastLen=Math.min(9,((S_N[main[n-1]]||{}).n||'').length),span=lastLen>5?W-60-Math.round(lastLen*6.5+4):675;
 const pos={};main.forEach((p,i)=>pos[p]=[Math.round(60+i*span/(n-1)),70]);F.branch.nodes.forEach((p,i)=>pos[p]=[pos[F.branch.from][0]+115*(i+1),190]);
 const pal=['var(--est)','var(--speak)','var(--face)','var(--ok)','var(--warn)'],fileCol={};STEPS.forEach(x=>{if(!(x.f in fileCol))fileCol[x.f]=pal[Object.keys(fileCol).length%pal.length]});
 const nodes=STEPS.filter(x=>useFace()||x.line!=='face');
 const edge=(a,b,dash)=>{const[x1,y1]=pos[a],[x2,y2]=pos[b];return `<path d="M${x1} ${y1} ${y1===y2?`L${x2} ${y2}`:`C${x1} ${y2} ${x1} ${y2} ${x2} ${y2}`}" fill="none" stroke="var(--line)" stroke-width="2" ${dash?'stroke-dasharray="5 4"':''}/>`};
 const sel=S_N[mapSel]||STEPS[0];
 const sx=F.dash.length?(pos[main[F.dash[0]]][0]+pos[main[F.dash[F.dash.length-1]+1]][0])/2:0;
 const br=[F.branch.from,...F.branch.nodes];
 return `<details class="panel mapbox" ${mapOpen?'open':''}><summary data-mapt="1"><b>关键流程地图</b>　<span class="note">点开看${F.mapWhat}在代码里怎么走、经过哪些文件</span></summary><p class="note">圆点是要插计时点的地方，颜色表示在哪个文件；虚线是设备看不见的服务器那段。点圆点看详情。</p>
 <div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:620px" role="img" aria-label="关键流程地图">
  ${F.dash.length&&F.server?`<rect x="${sx-80}" y="20" width="160" height="22" rx="4" fill="var(--soft)"/><text x="${sx}" y="35" text-anchor="middle" font-size="12" fill="var(--muted)">${F.server.length>13?F.server.slice(0,12)+'…':F.server}<title>${F.server}</title></text>`:''}
  ${main.slice(0,-1).map((p,i)=>edge(p,main[i+1],F.dash.includes(i))).join('')}
  ${useFace()?br.slice(0,-1).map((p,i)=>edge(p,br[i+1],i===0&&F.branch.dash)).join(''):''}
  ${nodes.map(x=>{const[cx,cy]=pos[x.p],on=x.p===mapSel,off=dropped.has(x.p);return `<g data-map="${x.p}" style="cursor:pointer;opacity:${off?.35:1}"><circle cx="${cx}" cy="${cy}" r="${on?13:10}" fill="${fileCol[x.f]}" stroke="${on?'var(--fg)':'none'}" stroke-width="2"/>
   <text x="${cx}" y="${cy+30}" text-anchor="middle" font-size="12.5" fill="var(--fg)">${x.n.length>9?x.n.slice(0,8)+'…':x.n}<title>${x.n}</title></text>${x.sure!=='高'?`<text x="${cx}" y="${cy+46}" text-anchor="middle" font-size="11" fill="var(--warn)">需核对</text>`:''}</g>`}).join('')}
 ${useFace()&&F.branch.nodes.length?`<text x="${pos[F.branch.from][0]-14}" y="195" text-anchor="end" font-size="12" fill="var(--face)">${F.sideLbl}</text>`:''}
 </svg></div>
 <div class="pills" style="margin:4px 0 10px">${Object.entries(fileCol).map(([f,c])=>`<span class="note"><i class="dot" style="background:${c}"></i>${f}</span>`).join('')}</div>
 <div class="detail"><b>${sel.n}</b> ${sureChip(sel)}<div class="note"><span class="code">${sel.f}</span> 第 ${sel.l} 行 · ${sel.why}${sel.note?`<br><span style="color:var(--warn)">要留意：${sel.note}</span>`:''}</div>
  <div class="pills" style="margin-top:6px">${dropped.has(sel.p)?`<span class="note">已删除</span><button class="pill" data-undel="${sel.p}">恢复</button>`:`<button class="pill" data-editp="${sel.p}">修改位置</button><button class="pill" data-del="${sel.p}">删除</button>`}</div></div></details>`}
function stepsC(){const segRow=(sg,c)=>{const off=dropped.has(sg.a)||dropped.has(sg.b);return `<div class="cseg ${off?'off':''}"><span class="cbar" style="background:${sg.out?'var(--est)':c}"></span><div><b>${sg.t}</b> <span class="note">${sg.who}${sg.out?' · 设备外':''}</span><div class="note">从"${S_N[sg.a].n}"到"${S_N[sg.b].n}"</div></div></div>`};
 return `<section class="panel"><h2>按这些步骤，用时会切成这几段</h2><p class="note" style="margin-top:0">报告里就按这几段算用时、判快慢。段切得不对，说明步骤找得不对。灰色是设备以外的段，码表只能量、改不了。</p>
  <h3 class="h3"><i class="dot" style="background:var(--speak)"></i>${FF[scene].mainTitle}</h3>${SEGS.map(x=>segRow(x,'var(--speak)')).join('')}
  ${useFace()?`<h3 class="h3"><i class="dot" style="background:var(--face)"></i>${FF[scene].sideTitle}</h3>${FSEGS.map(x=>segRow(x,'var(--face)')).join('')}`:''}
  <details style="margin-top:10px"><summary class="note" >代码位置（${live().length} 个）</summary>${STEPS.filter(x=>useFace()||x.line!=='face').map(x=>`<div class="srow"><b>${x.n}</b><span class="note">${x.f} 第 ${x.l} 行 ${x.sure!=='高'?'· <span style="color:var(--warn)">需核对</span>':''}</span></div>`).join('')}</details></section>`}
const STEPVAR={a:{label:'A 清单',idea:'A 清单：按发生顺序一条条列，每条有代码位置、为什么在这、可信度高低，研发逐条打勾。最直接，适合研发核对。'},
 b:{label:'B 流程地图',idea:'B 流程地图：画出一次对话在代码里怎么走、经过哪些文件，点圆点看详情。顺带满足需求 17（关键流程地图），也最适合录视频展示。'},
 c:{label:'C 切成几段',idea:'C 切成几段：不看点，看点之间切出来的段（报告里就按这些段算用时）。不写代码的人也能判断"这样切对不对"。'}};
// 插计时点提前（决策 96）：步骤一找完，后台就按 AI 找到的步骤准备插法；人没改步骤，点确认直接用
let PREP=null,PREPI=null,PREPK='',prepT=null;
async function prepKick(){if(!realFlash()||PREPI===IDENT)return;PREPI=IDENT;PREP=null;PREPK=JSON.stringify(fswSteps());clearInterval(prepT);
 try{const r=await fetch('/api/fsw/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({work:REAL.work,project_dir:REAL.project_dir,steps:fswSteps(),chip:REAL.chip,board:REAL.board})});if(!r.ok)return;PREP=await r.json()}catch(e){return}
 if(PREP.status==='running')prepT=setInterval(async()=>{try{PREP=await (await fetch('/api/fsw/prepare')).json()}catch(e){}if(!PREP||PREP.status!=='running'){clearInterval(prepT);prepT=null}if(page==='steps')render()},1000);if(page==='steps')render()}
const prepNote=()=>{if(!PREP||!['running','ready'].includes(PREP.status))return '';if(JSON.stringify(fswSteps())!==PREPK)return '<br>你改了步骤：AI 提前按原来的步骤准备的插法会作废，点确认后按新步骤重新生成（多调用一次大模型）。';
 return PREP.status==='running'?`<br><span class="spin"></span> 你核对的时候，AI 已经在后台按这些步骤准备插法（${Math.max(0,Math.round(Date.now()/1000-PREP.started))} 秒）；没改步骤的话，点确认就直接用。`:'<br>✓ 插法已经在后台准备好了：没改步骤的话，点确认马上插，不用等。'};
function stepsPage(){
 if(stepsRead===0)readStart();
 if(stepsRead===2)prepKick();
 if(stepsRead!==2)return `<div class="stack"><section class="panel"><h1 style="font-size:22px">${stepsRead===3?'没找完':'正在读代码找关键步骤'}</h1><p class="note" style="margin-top:0">测的是：${hSay?hSay.replace(/^从/,'').replace('到',' → '):FF[scene].measure}　固件：${hSrc||FF[scene].pick.dir}</p><div id="readbox">${readBox()}</div></section></div>`;
 const warn=(REAL&&IDENT&&IDENT.page&&IDENT.page.warn?`<div class="why" style="border-left-color:var(--bad)">${htmlEsc(IDENT.page.warn)}</div>`:'')+((REAL&&IDENT?IDENT.provider:setLLM)==='deepseek'?`<div class="why" style="border-left-color:var(--warn)">这次用的是 DeepSeek，验证期它 9 个只找对 4 个，请每条都仔细核对。</div>`:'');
 const body=stepsB()+stepsA();
 const n=live().length,ch=dropped.size;
 return `<div class="stack">${warn}<div class="status"><span><i class="dot" style="background:var(--ok)"></i>${REAL&&IDENT?(IDENT.reused?'源码和要测的都没变，直接用了上次的结果，没调用大模型':`读完了，用了 ${mmss(IDENT.seconds)}，调用 1 次 ${usedLLM()}`):`读完了，用了 1 分 10 秒，调用 1 次 ${usedLLM()}`}</span><span><i class="dot" style="background:var(--ok)"></i>读代码认出来：${REAL?`${htmlEsc(REAL.name)}，${CHIP()}`:FF[scene].kind}</span><button class="pill" data-reread="1">重看读取过程</button></div>
 ${body}
 ${flow==='first'&&flowMax>=2?`<section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0"><span class="note">步骤没改过，上次插好的计时点还在。改了步骤才需要重新插。</span><button class="btn pri" data-go="flash" data-keepflow="1">回到插计时点和烧录</button></div></section></div>`:`<section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0"><span class="note">确认后，码表马上把 ${n} 个计时点插进它复制的那份代码${ch||Object.keys(edits).length||added.length?'；你的删除、修改、添加都会记下来':''}。${prepNote()}</span><button class="btn pri" data-goins="1" >确认，插入计时点</button></div></section></div>`}`}

// ===== 插计时点和烧录 =====（数字来自验证 02：4 个文件 +154 −0；编译 119 秒/增量 80 秒；程序多 784 字节）
const FST=()=>{const F=FF[scene];return [
 ['复制一份源码',`开始找关键步骤时已经复制好${bgDone()?'、原始代码也编好了':''}；在这份上改，你原来的文件夹一个字都不动`,`已复制 ${F.files} 个文件到 ~/firmware-stopwatch-data/${F.slug}/work`],
 ['让 AI 插计时点',FSWJ&&FSWJ.reused?'源码和步骤都没变，用上次的插入方案，没调用大模型（设置页可以关掉）；码表自己检查"只加不删"':FSWJ&&FSWJ.prepared?'用你核对步骤时 AI 在后台提前准备好的插法（调用 1 次大模型），不用再等；码表自己检查"只加不删"':FSWJ&&FSWJ.redo?`${htmlEsc(FSWJ.redo)}（这一步一共调用 ${FSWJ.calls} 次大模型）；码表自己检查"只加不删"`:`按你确认的步骤插入计时语句，再调用一次大模型；码表自己检查"只加不删"`,FSWJ&&FSWJ.view?`改了 ${FSWJ.view.files} 个文件：新增 ${FSWJ.view.added} 行，删除 ${FSWJ.view.deleted} 行 ✓`:F.ins],
 ['编译',`${bgDone()?'原始代码已经编过':'等后台把原始代码编完后'}，只重编改过的文件，一般 1 分钟左右（按改动的文件数估算）`,F.build],
 ['烧录到设备','通过 USB 线写进设备','Writing at 0x0012c000... ({p} %)'],
 ['试一次：确认计时点有输出',realFlash()?`烧好后设备会重启，码表自己看它开机没有；开机后按提示做一次要测的事，码表看设备有没有把计时点打出来。只是试一下，不算进数据。`:`烧好后设备会重启。请${F.trig}，码表看设备有没有把计时点打出来。只是试一下，不算进数据。`,F.fswLog]]};
let demoErr=FAIL==='nodev'?'':FAIL,justFlashed=false,flashVar='b',fl={st:0,step:0,pct:0,log:''},flT=null,flNoDev=false,diffOk=false,diffFile=0,swOn=true,onDev='orig';
function flStop(){clearInterval(flT);flT=null}
// 真插计时点（选了真源码、真找完步骤时）：后台做"插 → 等你确认 → 编译 → 烧录 → 试一次"，这里每 0.7 秒问一次
let FSWJ=null,fswT=null;
const realFlash=()=>!!(REAL&&REAL.work&&IDENT&&IDENT.page);
// 试一次要人做的事：真流程按你写的"测什么"说（对话类：先喊唤醒词，再说一句）；演示数据用它自己的说法
// 试一次没收到的计时点叫什么（页面上写出来，说清为什么少）
const tryMissing=()=>{if(!FSWJ||!FSWJ.view)return [];const nm=Object.fromEntries(live().map(x=>[fswId(x.p),x.n]));return (FSWJ.view.steps||[]).filter(k=>!(FSWJ.got||[]).includes(+k.slice(1))).map(k=>nm[k]||k)};
const trySay=()=>{if(!realFlash())return FF[scene].big;const w=IDENT.page.mapWhat||hSay||'';const st=(IDENT.page.measure||'').split('→')[0].trim();return /说|话|聊/.test(w)?'先喊唤醒词，再说一句':`先做一次：${htmlEsc(st||w)}`};
// 计时点编号和后台一样：P3 → P3；你自己加的 U1、U2 → P21、P22
const fswId=p=>{if(/^P\d+$/.test(p))return p;const u=live().filter(x=>!/^P\d+$/.test(x.p)).map(x=>x.p);return 'P'+(20+u.indexOf(p)+1)};
const fswSteps=()=>live().map(x=>({p:x.p,n:x.n,path:x.path||x.f,l:x.l,line:x.line,why:x.why||'',where:x.where||'',once:x.once||''}));
function fswApply(j){FSWJ=j;const PH={insert:1,build:2,flash:3,try:4};
 fl.step=PH[j.phase]||1;fl.pct=j.pct||0;fl.log=j.log||'';fl.wait=false;fl.to=4;
 if(j.status==='running')fl.st=1;else if(j.status==='waiting_device'){fl.st=4;fl.log='没看到设备'}
 else if(j.status==='inserted'){fl.st=3;fl.step=2}
 else if(j.status==='ready_try'){fl.st=6;fl.step=4}
 else if(j.status==='failed'){fl.st=5;fl.err=j.err.kind}
 else if(j.status==='done'&&fl.st!==2){fl.st=2;fl.step=5;onDev='fsw';clearInterval(fswT);fswT=null;loadRCT();
  }  // 试通了不自己跳走（用户：等我主动点「开始多轮实测」）
 if(j.status!=='running'&&j.status!=='waiting_device'&&j.status!=='ready_try'){clearInterval(fswT);fswT=null}
 if(page==='flash')render()}
function fswPoll(){clearInterval(fswT);fswT=setInterval(async()=>{try{fswApply(await (await fetch('/api/fsw')).json())}catch(e){}},700)}
async function fswPost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});const j=await r.json();if(!r.ok){toast('✕ '+(j.detail||'没做成'));return null}fswApply(j);fswPoll();return j}
function fswInsert(){diffOk=false;diffOpen=new Set();fl={st:1,step:1,pct:0,log:'AI 在读代码，确定每个计时点插在哪一行'};FSWJ=null;
 fswPost('/api/fsw/insert',{work:REAL.work,project_dir:REAL.project_dir,steps:fswSteps(),chip:REAL.chip,board:REAL.board})}
function flRun(from,to){flStop();fl={st:1,step:from,pct:0,log:'',to};
 flT=setInterval(()=>{const S=FST()[fl.step];
  const fe=demoErr==='build'&&fl.step===2&&fl.pct>=60?'build':demoErr==='burn'&&fl.step===3&&fl.pct>=48?'burn':demoErr==='try'&&fl.step===4&&fl.pct>=70?'try':'';
  if(fe){fl.st=5;fl.err=fe;flStop();if(page==='flash')render();return}
  if(fl.step===3&&(REAL?!PORTS.length:flNoDev)){fl.st=4;fl.log='没看到设备';flStop();if(REAL)portsWatch(true);if(page==='flash')render();return}
  if(fl.step===2&&bgFailed()){fl.st=5;fl.err='orig';flStop();if(page==='flash')render();return}
  if(fl.step===2&&!bgDone()){fl.wait=true;fl.log=`等后台把原始代码编完（${bgPct()}%）`;if(page==='flash')render();return}fl.wait=false;
  fl.pct+=fl.step===2||fl.step===3?8:fl.step===4?6:25;
  if(fl.pct>=100){fl.step++;fl.pct=0;if(fl.step>to){flStop();fl.st=fl.step>4?2:3;if(fl.st===2){onDev='fsw'}if(page==='flash')render();return}}
  fl.log=FST()[fl.step][2].replace('{n}',Math.round(fl.pct*16.3)).replace('{p}',Math.min(fl.pct,99));
  if(page==='flash')render()},350)}
function flErrReal(){const J=FSWJ,E=J.err,C=CC(),F=FF[scene];
 const box=(t,d,code,acts)=>`<div class="errbox"><b>${t}</b><p style="margin:6px 0">${d}</p>${code?`<div class="ilog"><span class="note">报错</span><code>${htmlEsc(code)}</code>${fullLogBtn()}</div>${fullLog()}`:''}<div class="acts" style="margin:8px 0 0">${acts}</div></div>`;
 if(E.kind==='insert')return box('计时点没插上',E.message,'',`<button class="btn pri" data-fl="redo">重新插入</button>`);
 if(E.kind==='orig')return box('原版编译没通过，不是码表改的',`你选的源码本身在这台电脑上编不过（编译工具、依赖或板子配置的问题），和插的计时点无关，烧录前要先解决。码表不替你改代码。`,E.error,`<button class="btn" data-bgpop="1">看详情</button>`);
 if(E.kind==='build')return E.step?box('编译没通过：是码表插的计时点引起的',`原版在找关键步骤时已经编译通过，所以问题出在插进去的计时点：「${htmlEsc(E.step_name||E.step)}」那一处（${htmlEsc(E.where)}）。<br>码表自己的问题，不用你改代码。`,E.error,`<button class="btn pri" data-flx="drop">撤掉这处计时点，重新编译</button><button class="btn" data-flx="move">回去换个位置</button>`)
  :box('编译没通过',`${htmlEsc(E.message)}${E.error?'。报错里没对上是哪一处计时点，看下面的报错，或者重新插入。':''}`,E.error,`<button class="btn pri" data-flx="rebuild">重新编译</button><button class="btn" data-fl="redo">重新插入</button>`);
 if(E.kind==='burn')return box('烧录中断：设备断开了','写到一半连接断了，多半是 USB 线松了或换了接口。<br>设备上现在是写了一半的固件，开不了机是正常的，<b>不会坏</b>：插好线再烧一次就行。',E.error,`<button class="btn pri" data-flx="reburn">重新烧录</button>`);
 return box('试一次没收到计时点',`${htmlEsc(E.message)}${E.why?`<details class="lvwhy" style="margin-top:6px"><summary>可能的原因</summary><p class="note" style="margin:6px 0 0">${htmlEsc(E.why)}</p></details>`:''}${E.offer_restore?'<br>试了几次都不行：可以先还原成原版，设备回到原来的样子，不会坏。':''}`,E.error,`<button class="btn pri" data-flx="retry">再试一次</button>${E.offer_restore?'<button class="btn" data-hrestore="'+htmlEsc(REAL.project_dir)+'">还原成原版</button>':''}`)}
function flErr(){if(realFlash()&&FSWJ&&FSWJ.err)return flErrReal();const F=FF[scene],C=CC(),L=live(),p=L[Math.min(2,L.length-1)];
 const E={orig:['原版编译没通过，不是码表改的',`你选的源码本身在这台电脑上编不过，和插的计时点无关。${htmlEsc(BG&&BG.message)}`,htmlEsc(BG&&BG.error),`<button class="btn" data-bgpop="1">看详情</button>`],build:['编译没通过：是码表插的计时点引起的',`原版在找关键步骤时已经编译通过，所以问题出在插进去的计时点。<br>AI 看了报错：「${p.n}」那个计时点（${p.f} 第 ${p.l} 行）插进了一个宏定义的中间，编译器不认。<br>码表自己的问题，不用你改代码。`,`${p.f}:${p.l}: error: expected ';' before 'FSW_MARK'`,`<button class="btn pri" data-flx="drop">撤掉这个计时点，重新编译</button><button class="btn" data-flx="move">回去换个位置</button>`],
  burn:['烧录中断：设备断开了','写到一半连接断了，多半是 USB 线松了或换了接口。<br>设备上现在是写了一半的固件，开不了机是正常的，<b>不会坏</b>：插好线再烧一次就行。','A fatal error occurred: The chip stopped responding.',`<button class="btn pri" data-flx="reburn">重新烧录</button>`],
  try:['试一次没收到计时点',`烧录成功，设备也重启了，但${F.trig}后 15 秒内没收到计时点。常见原因：<br>1. 没触发到：再${C.act}一次试试。<br>2. 串口被别的软件占着（比如别的串口监视器）：关掉它。<br>3. 设备在反复重启：点「完整日志」看。`,'（15 秒内没有新输出）',`<button class="btn pri" data-flx="retry">再试一次</button>`]}[fl.err];
 return `<div class="errbox"><b>${E[0]}</b><p style="margin:6px 0">${E[1]}</p><div class="ilog"><span class="note">报错</span><code>${E[2]}</code>${fullLogBtn()}</div>${fullLog()}<div class="acts" style="margin:8px 0 0">${E[3]}</div></div>`}
function flList(from=0,to=4){const n=to-from+1;
 const li=FST().slice(from,to+1).map(([t,d],j)=>{const i=from+j,done=fl.st===2||(fl.st>=1&&i<fl.step)||(fl.st===3&&i<=fl.step-1),cur=(fl.st===1||fl.st===4||fl.st===5||fl.st===6)&&i===fl.step,bad=(fl.st===4||fl.st===5)&&cur;
  return `<li class="istep ${done?'ok':cur?(bad?'bad':'cur'):''}"><b>${done?'✓':bad?'!':j+1}</b><span>${t}<small>${d}</small>${cur&&fl.st===1&&i===2&&fl.wait?`<small style="color:var(--warn)">先等后台把原始代码编完：已编 ${bgPct()}%。编完自动接着编，不用点。</small><span class="pbar"><i style="width:${bgPct()}%;background:var(--warn)"></i></span>`:cur&&fl.st===1&&(i===2||i===3)?`<span class="pbar"><i style="width:${fl.pct}%"></i></span>`:''}${cur&&fl.st===1&&i!==4?stepLog():''}${bad&&fl.st===5?flErr():''}${bad&&fl.st===4?`<small style="color:var(--warn)">没看到设备。用 USB 线插上设备，插上会自动继续，不用点。</small>${REAL?'':'<button class="pill" data-fl="plug">演示：设备已插上</button>'}`:''}</span></li>`}).join('');
 const say=FF[scene].big;
 const N=realFlash()&&FSWJ?FSWJ.total||live().length:live().length,got=realFlash()&&FSWJ?(FSWJ.got||[]).length:Math.min(N,Math.round(fl.pct/100*N));
 const big=fl.st===6&&to>=4?`<div class="bigask"><b>试一次：${trySay()}</b><span class="note">设备烧完会重启。等它准备好（有屏幕的，等回到待命画面），再点「准备好了」；点了以后最多等 ${FSWJ&&FSWJ.try_secs||45} 秒。只是确认计时点能收到，不算进数据。</span><button class="btn pri" data-flx="trygo">准备好了</button></div>`
  :fl.st===1&&fl.step===4&&to>=4?(realFlash()?tryBox(got,N):`<div class="bigask"><b>试一次：${say}</b><span class="note">${FF[scene].bigNote}只是确认计时点能收到，不算进数据。</span><span class="note">已收到 <b class="num">${got}</b> / ${N} 个计时点</span></div>`):fl.st===2&&to>=4?`<div class="bigask ok"><b>✓ 试通了</b><span class="note">${got<N&&realFlash()?`收到 ${got} / ${N} 个计时点。没收到：${tryMissing().join("、")||"—"}。这一次没走到这一步（有的计时点只在某些情况下才走到，比如喊醒那一下设备不会判断你说完了），不影响多轮实测`:`${N} 个计时点都收到了`}，设备准备好了。</span></div>`:'';
 return `<ol class="isteps">${li}</ol>${big}`}
const stepLog=()=>`<div class="ilog" style="margin:6px 0 0"><code>${htmlEsc(fl.log)||'…'}</code>${fullLogBtn()}</div>${fullLog()}`;
// 试一次（决策 107、108）：烧完直接开始听，码表自己看设备开机没有，不用人判断"准备好了"；告诉人现在做什么，一句话
const tryAct=()=>{const w=(IDENT&&IDENT.page&&IDENT.page.mapWhat)||hSay||'';return /说|话|聊|问|答/.test(w)?'喊唤醒词，再说一句话（比如"一加一等于几"）':trySay()};
function tryBox(got,N){const J=FSWJ||{},now=Date.now()/1000,up=J.dev_seen,since=up?Math.round(now-up):0,idle=up&&!got&&since>30;
 if(!up)return `<div class="bigask"><b><span class="spin"></span> 设备刚烧完，正在重启</b><span class="note">等它开机（有屏幕的会回到待命画面），码表会自己看到，不用点什么。开机后这里会告诉你做什么。</span></div>`;
 return `<div class="bigask"><b>现在对设备：${tryAct()}</b>
  <span class="note">设备已经开机了。只是确认计时点能收到，不算进数据。</span>
  ${idle?`<span class="note" style="color:var(--warn)">已经 ${since} 秒没收到计时点：设备可能还在联网，或者还没被唤醒。请现在再做一次。</span>`:''}
  <span class="note">已收到 <b class="num">${got}</b> / ${N} 个计时点　·　设备最新一行：<code>${htmlEsc(J.log||'…')}</code></span></div>`}
// 完整日志（决策 106）：编译、烧录、试一次时设备和工具的输出，最近 200 行，去掉了控制字符
let showFull=false;
const fullLogBtn=()=>realFlash()&&FSWJ&&(FSWJ.tail||[]).length?`<button class="pill" data-fulllog="1">${showFull?'收起日志':'完整日志'}</button>`:'';
const fullLog=()=>showFull&&realFlash()&&FSWJ&&(FSWJ.tail||[]).length?`<pre class="logtail">${htmlEsc(FSWJ.tail.join('\n'))}</pre>`:'';

let diffOpen=new Set();
function diffViewReal(){const V=FSWJ.view;
 const one=(k,i,t,sub,lines)=>`<div class="drow"><span class="num">${i}</span><div><b>${htmlEsc(t)}</b> <span class="note">${sub}</span></div><button class="pill" data-dopen="${k}">${diffOpen.has(k)?'收起代码':'看代码'}</button>${diffOpen.has(k)?`<pre class="diff">${lines.map(([k2,t2])=>`<span class="${k2==='+'?'add':k2==='…'?'more':''}">${k2==='…'?'':k2} ${htmlEsc(t2)}</span>`).join('\n')}</pre>`:''}</div>`;
 const order=live().map(x=>fswId(x.p)),mine=V.places.filter(x=>/^P\d+$/.test(x.step)).sort((a,b)=>order.indexOf(a.step)-order.indexOf(b.step)),help=V.places.filter(x=>!/^P\d+$/.test(x.step));
 return `<div class="status" style="margin-bottom:8px"><span><i class="dot" style="background:var(--ok)"></i>只加不删：${V.files} 个文件，新增 ${V.added} 行，删除 ${V.deleted} 行（码表逐行核对）</span><span><i class="dot" style="background:var(--ok)"></i>${V.steps.length} 个计时点都插上了，和你确认的一一对应</span></div>
 <p class="note" style="margin:0 0 4px">每处只加两行：一行说明、一行记时间。"+"开头的是新加的。</p>
 <div class="dlist">${one('hdr','＋',`新文件 ${V.header.split('/').pop()}`,`计时点工具，+${V.header_lines} 行；总开关也在这里`,[['+',`// 总开关：FW_STOPWATCH_ENABLE 改成 0，所有计时点变成空语句`],['+','#define FW_STOPWATCH_ENABLE 1'],['…',`（共 ${V.header_lines} 行）`]])}
 ${mine.map((x,i)=>one('m'+i,i+1,x.n,`${x.file.split('/').pop()} 第 ${x.at} 行 · +${x.added} 行`,x.lines)).join('')}
 ${help.length?one('help','＋',`辅助的 ${help.length} 处`,'加上计时点工具、每轮开始清零等',help.flatMap(x=>[['…',`${x.file.split('/').pop()} 第 ${x.at} 行后：${x.n}`],...x.lines.filter(l=>l[0]==='+')])):''}</div>
 ${FSWJ.notes&&FSWJ.notes.length?`<details style="margin-top:8px"><summary class="note">AI 插的时候提醒的 ${FSWJ.notes.length} 件事</summary><ol class="notes" style="font-size:14px">${FSWJ.notes.map(n=>`<li>${htmlEsc(n)}</li>`).join('')}</ol></details>`:''}
 <p class="note">总开关在 <span class="code">${V.header.split('/').pop()}</span>：关掉后固件和原版大小相同、逻辑相同。要完全回到原版，用"还原"，会逐字节比对。</p>`}
function diffView(){if(realFlash()&&FSWJ&&FSWJ.view)return diffViewReal();const rows=live(),F=FF[scene];const num=p=>p.replace(/\D/g,'')||'0';
 const one=(k,i,t,sub,lines)=>`<div class="drow"><span class="num">${i}</span><div><b>${t}</b> <span class="note">${sub}</span></div><button class="pill" data-dopen="${k}">${diffOpen.has(k)?'收起代码':'看代码'}</button>${diffOpen.has(k)?`<pre class="diff">${lines.map(([k2,t2])=>`<span class="${k2==='+'?'add':k2==='…'?'more':''}">${k2==='…'?'':k2} ${t2.replace(/</g,'&lt;')}</span>`).join('\n')}</pre>`:''}</div>`;
 return `<div class="status" style="margin-bottom:8px"><span><i class="dot" style="background:var(--ok)"></i>只加不删：${F.diffSum}，删除 0 行（码表逐行核对）</span><span><i class="dot" style="background:var(--ok)"></i>${rows.length} 个计时点都插上了，和你确认的一一对应</span></div>
 <p class="note" style="margin:0 0 4px">每处只加两行：一行说明、一行记时间。"+"开头的是新加的。</p>
 <div class="dlist">${one('hdr','＋','新文件 fw_stopwatch.h','计时点工具，+120 行；总开关也在这里',DIFF[0].lines)}
 ${rows.map((x,i)=>one(x.p,i+1,x.n,`${x.f} 第 ${x.l} 行 · +2 行`,[[' ',`    …（第 ${x.l} 行原有代码，不动）`],['+',`    // 计时点 ${x.p}：${x.n}`],['+',`    FSW_MARK(${num(x.p)}, 0);`]])).join('')}</div>
 <p class="note">总开关在 <span class="code">fw_stopwatch.h</span>：关掉后固件和原版大小相同、逻辑相同。要完全回到原版，用"还原"，会逐字节比对。</p>`}
function flashA(){return `<section class="panel"><h2>一键插计时点、编译、烧录</h2><p class="note" style="margin-top:0">点一下从头走到尾，中间不停。改动内容装好后可以在"看改动"里查。</p>
 <div id="flbox">${flList()}</div>
 ${fl.st===0?`<div class="acts"><button class="btn pri" data-fl="all">开始</button></div>`:''}
 ${fl.st>=2||fl.step>1?`<details><summary class="note" >改动明细</summary>${diffView()}</details>`:''}</section>`}
function flashB(){
 const s1=fl.st===0&&!diffOk?`<div class="acts"><button class="btn pri" data-fl="ins">开始插计时点</button></div>`:'';
 const insDone=diffOk||(fl.step>=2)||fl.st===2;
 return `<section class="panel"><h2>第一段：插计时点，插完先给你看改动</h2>
  <div id="flbox">${flList(0,1)}</div>${s1}
  ${insDone||fl.st===3?`${diffView()}${diffOk?'':`<div class="acts"><button class="btn pri" data-fl="ok">确认改动，编译并烧录</button><button class="btn" data-fl="redo">重新插入</button></div>`}`:''}</section>
  <section class="panel" ${diffOk?'':'style="opacity:.45"'}><h2>第二段：编译、烧录、确认</h2>
  ${diffOk?`<div id="flbox2">${flList(2,4)}</div>`:'<p class="note">改动确认后才开始。烧录时才检查设备插没插。</p>'}</section>`}
function flashC(){const card=(k,t,sub,size)=>`<div class="vcard ${onDev===k?'on':''}"><h3>${t}${onDev===k?' <span class="chip c-ok">设备上正在跑</span>':''}</h3><p class="note">${sub}</p><p class="num">${size}</p><button class="btn ${onDev===k?'':'pri'}" data-ondev="${k}" ${onDev===k?'disabled':''}>${onDev===k?'正在用':'烧这个'}</button></div>`;
 return `<section class="panel"><h2>原版和计时版，随时切换</h2><p class="note" style="margin-top:0">码表同时留着两份编译好的固件。测量用计时版；想确认"计时点本身有没有拖慢"，切回原版对比体感。</p>
 <div class="vcards">${card('orig','原版','你的源码，没改过',FF[scene].sizes[0])}${card('fsw','计时版',live().length+' 个计时点，总开关开着',FF[scene].sizes[1])}</div>
 <label style="display:flex;gap:8px;align-items:center;margin:12px 0 4px;font-size:14px"><input type="checkbox" data-sw="1" ${swOn?'checked':''}>计时版的总开关（关掉后重新编译，等于原版但保留插好的位置）</label>
 <div id="flbox">${fl.st?flList(2,3):''}</div>
 <details style="margin-top:8px"><summary class="note" >计时版改动明细</summary>${diffView()}</details></section>`}
const FLASHVAR={a:{label:'A 一键到底',idea:'A 一键到底：点一下从复制、插计时点、编译、烧录走到确认，中间不停，改动事后再看。最省事，但研发烧之前没看过改动。'},
 b:{label:'B 先看改动',idea:'B 先看改动再烧：AI 插完先停下，给研发看改了哪几个文件、每处加了什么、确认只加不删，点"没问题"才编译烧录。'},
 c:{label:'C 两版切换',idea:'C 两版切换：原版和计时版两张卡，随时烧哪个；带总开关。适合反复对比"计时点有没有拖慢"，但第一次用不直观。'}};
function flashPage(){
 const body={a:flashA,b:flashB,c:flashC}[flashVar]();
 const done=fl.st===2||(flashVar==='c'&&onDev==='fsw'&&fl.st===3);
 return `<div class="stack"><div class="status"><span><i class="dot" style="background:var(--ok)"></i>要插 ${typeof live==='function'?live().length:9} 个计时点（找关键步骤那页确认的）</span>${envComp()?`<span><i class="dot" style="background:var(--ok)"></i>编译工具 v${realIdf()?shortV(realIdf().version):'6.1'}</span>`:(inst.st===1?`<span><i class="dot" style="background:var(--warn)"></i>编译工具：正在后台装 ${Math.round(((inst.step||0)+(inst.pct||0)/100)/5*100)}%，装好自动变绿</span>`:'<span><i class="dot" style="background:var(--bad)"></i>编译工具：还没装，编译前要装好</span>')}${(()=>{const on=REAL?devNow().on:done||(fl.step>=3&&!flNoDev&&fl.st);return `<span><i class="dot" style="background:${on?'var(--ok)':'var(--warn)'}"></i>设备：${on?'设备已连上 '+htmlEsc(devNow().port):'没插上，烧录前插上就行（插上会自动变成"已连上"）'}</span>`})()}</div>
 ${body}
 <section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0"><span class="note">${done?'设备上已经是计时版，计时点输出正常。测完以后可以在报告底部或首页还原成原版。':''}</span><span style="display:flex;gap:8px"><button class="btn pri" data-go="collect" ${done?'':(()=>{const g=guide();return blk(g&&g[1]||'#flbox','烧录和试一次都完成后才能测')})()}>开始多轮实测</button></span></div></section></div>`}


let STEPS,SEGS,FSEGS,S_N,DIFF,lineName;
const FFs=()=>FF[scene];
function applyScene(){const F=FF[scene];STEPS=F.steps;SEGS=F.segs;FSEGS=F.fsegs;DIFF=F.diff;lineName=F.ln;S_N=Object.fromEntries(STEPS.map(x=>[x.p,x]));if(!S_N[mapSel])mapSel=F.mapSel;if(diffFile>=DIFF.length)diffFile=0}
function sceneSwitch(k){if(k===scene)return;colStop();col={st:0,att:0,got:[],phase:0};scene=k;rngAsk=0;fixPick=null;segSel=null;fixTodo=false;
 clearInterval(readT);readT=null;stepsRead=0;alsoFace=true;dropped=new Set();edits={};added=[];editP=null;addSt=0;mapSel=FF[k].mapSel;flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';onDev='orig';diffFile=0;
 if(page==='home'){hStage=0;hSrc='';hScan=0;hSay='';hErr=''}pName=FF[k].pname;render()}

// ===== 多轮实测 =====（通用：动作、提示语、阶段名、问题怎么判，都来自场景；数据和报告页同一份）
let beep=true,actx=null;
// 声音要在点按钮的那一下打开（Safari 等浏览器不认点击之后才开的声音），顺手放一段无声的，把声音通道打通（决策 115）
// Safari 只在点击那一下真的放出声音时才给网页开声音（只放一个空的不算，决策 128）。放一段听不见的（音量 0.001），
// 不让人以为"收好一轮了"（决策 131）；页面上任何一次点击，声音还没开就顺手开一下，等到多轮实测时早就开好了
function unlockAudio(){if(!beep)return;try{actx=actx||new (window.AudioContext||window.webkitAudioContext)();if(actx.state==='running')return;actx.resume();
 const o=actx.createOscillator(),g=actx.createGain(),t=actx.currentTime;o.frequency.value=20;g.gain.setValueAtTime(0,t);g.gain.linearRampToValueAtTime(.00001,t+.04);g.gain.linearRampToValueAtTime(0,t+.08);o.connect(g);g.connect(actx.destination);o.start(t);o.stop(t+.1);beepLog('点击时打开声音')}catch(e){}}  // 20 赫兹、慢慢起慢慢收：人耳听不到，也没有"咔"的一下
document.addEventListener('click',()=>unlockAudio(),true);
let dings=0,beepMsg='',beepBlocked=false;  // 决策 132：该响的时候浏览器的声音没开：页面上显示一个按钮，点一下就开
function ding(bad){if(!beep)return;try{actx=actx||new (window.AudioContext||window.webkitAudioContext)();if(actx.state==='running')beepBlocked=false;else{actx.resume();setTimeout(()=>{if(actx.state!=='running'&&!beepBlocked){beepBlocked=true;beepLog('声音被浏览器关着');if(page==='collect')render()}},400)}dings++;if(dings<=3||dings%10===0)beepLog('响');const o=actx.createOscillator(),g=actx.createGain();o.frequency.value=bad?330:880;g.gain.value=.25;o.connect(g);g.connect(actx.destination);o.start();o.stop(actx.currentTime+(bad?.35:.12))}catch(e){}}
function beepBtn(){return `${beepBlocked&&beep&&!(actx&&actx.state==='running')?'<button class="btn" data-beeptest="1" style="border-color:var(--warn);color:var(--warn)">🔇 提示音被浏览器关着，点这里打开</button>':''}<span style="display:inline-flex;gap:10px;align-items:center"><button class="tgl" role="switch" aria-checked="${beep}" data-beep="1"><span class="trk"></span>提示音${beep?'开':'关'}</button>${beep?'<button class="pill" data-beeptest="1">试听</button>':''}</span>`}
function beepLog(what){try{fetch('/api/client-log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({what,state:actx?actx.state:'none',dings,ua:navigator.userAgent,hidden:document.hidden})})}catch(e){}}
function beepNote(){return CX().beepNote}
function needMore(){const g=okRounds(),C=CX(),L=DX().lines.length-1;if(g.length<10)return false;const all=g.map(x=>x.e[L]),half=Math.floor(g.length/2),run=all.slice(0,half);const stable=Math.abs(med(run)-med(all))<.05;const few=C.probs.some(p=>{const n=g.filter(x=>x.probs.some(q=>q.k===p.k)).length;return n>0&&n<=3})&&g.length<30;return !stable||few}
let moreWhy='';
function enough(){const g=okRounds(),C=CX(),D=DX(),L=D.lines.length-1,ln=D.lines[L];if(g.length<10)return '';
 const all=g.map(x=>x.e[L]),fin=med(all),run=all.map((_,i)=>med(all.slice(0,i+1))),half=Math.floor(g.length/2);
 const cap=g.length>=30,stable=cap||Math.abs(run[half-1]-fin)<.05;  // 到 30 轮就不再催：再测下去一般用时变化也不大
 const rare=C.probs.map(p=>({p,ns:g.filter(x=>x.probs.some(q=>q.k===p.k)).map(x=>x.n)})).filter(o=>o.ns.length>0);
 const few=rare.filter(o=>o.ns.length<=3&&!cap),more=!stable||few.length>0;
 moreWhy=more?(!stable?`一般用时还在变：测到一半时 ${f(run[half-1])} 秒，现在 ${f(fin)} 秒，差 ${f(Math.abs(run[half-1]-fin))} 秒（差 0.05 秒以内算稳）`:`「${few[0].p.t}」只出现了 ${few[0].ns.length} 次，还分不清是偶尔还是常态`)+'。最多建议测到 30 轮。':'';
 const W=940,H=150,l=46,r=10,t=14,b=24,lo=Math.min(...run,fin-.1),hi=Math.max(...run,fin+.1);
 const X=i=>l+(W-l-r)*i/Math.max(1,g.length-1),Y=v=>t+(H-t-b)*(1-(v-lo)/(hi-lo));
 const band=`<rect x="${l}" y="${Y(fin+.05)}" width="${W-l-r}" height="${Y(fin-.05)-Y(fin+.05)}" fill="var(--ok)" opacity=".15"/>`;
 const path=run.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join(' ');
 const dots=run.map((v,i)=>`<g class="hit" data-tip="${`前 ${i+1} 轮：一般 &lt;b&gt;${f(v)} 秒&lt;/b&gt;`}"><rect x="${X(i)-8}" y="${t}" width="16" height="${H-t-b}" fill="transparent"/><circle cx="${X(i)}" cy="${Y(v)}" r="${i===g.length-1?4.5:3}" fill="var(--speak)" stroke="var(--panel)" stroke-width="2"/></g>`).join('');
 const chart1=`<svg viewBox="0 0 ${W} ${H}" style="width:100%" role="img" aria-label="一般用时随轮数的变化">${band}
  <text x="${l-6}" y="${Y(fin)+4}" text-anchor="end" font-size="11" fill="var(--muted)">${f(fin)}</text>
  <line x1="${X(half-1)}" x2="${X(half-1)}" y1="${t}" y2="${H-b}" stroke="var(--line)" stroke-dasharray="3 3"/><text x="${X(half-1)+4}" y="${t+10}" font-size="11" fill="var(--muted)">测到一半</text>
  <path d="${path}" fill="none" stroke="var(--speak)" stroke-width="2"/>${dots}
  <text x="${l}" y="${H-6}" font-size="11" fill="var(--muted)">第 1 轮</text><text x="${W-r}" y="${H-6}" text-anchor="end" font-size="11" fill="var(--muted)">第 ${g.length} 轮</text></svg>`;
 const strip=o=>{const cw=Math.min(14,Math.floor(440/g.length));return `<div class="estrip"><span class="elab">${o.p.t}</span><span class="ecells">${g.map(x=>{const on=o.ns.includes(x.n);return `<i class="ecell" style="width:${cw}px;${on?`background:var(--${o.p.c})`:''}" ${on?`data-tip="第 ${x.n} 轮出现"`:''}></i>`}).join('')}</span><span class="ev ${o.ns.length<=3&&!cap?'warn':'ok'}">${o.ns.length} 次${o.ns.length<=3&&!cap?'，太少':''}</span></div>`};
 const v=(ok,a,b)=>`<span class="ev ${ok?'ok':'warn'}">${ok?'✓ '+a:'… '+b}</span>`;
 return `<section class="panel" id="enough" style="border-color:${more?'var(--warn)':'var(--ok)'}">
 <h2 style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">测的够不够<span class="ev ${more?'warn':'ok'}" style="font-size:15px">${more?'建议再测 10 轮':'✓ 够了，可以看结果'}</span></h2>
 <div class="eblk"><div class="ehead"><b>① 一般用时稳了吗</b>${v(stable,'稳了','还在变')}</div><p class="note" style="margin:2px 0 0">线是"测到第几轮时，${ln.end}的一般用时"。后半段落在绿色带里就是稳了。</p>${chart1}</div>
 ${rare.length?`<div class="eblk"><div class="ehead"><b>② 偶尔出的问题看清了吗</b>${v(!few.length,'看清了','次数太少')}</div><p class="note" style="margin:2px 0 6px">一格一轮，亮的是出了这个问题的那轮。只出现 1～3 次，还分不清是偶尔还是常态。</p>${rare.map(strip).join('')}</div>`:''}
 <p class="note" style="margin:6px 0 0">${more?`为什么建议再测：${moreWhy}<br>不再测也能出报告，报告里会注明"次数少，只供参考"。`:''}按收到的数算的，不用大模型。</p></section>`}
const MIN_ROUNDS=3;  // 至少几轮能出报告（3～9 轮只适合跑流程，报告注明）
let colVar='b',colN=20,col={st:0,att:0,got:[],phase:0},colT=null;
const CC=()=>COLLECT[scene];
// 真流程：每轮做什么（/api/rounds 按你写的"测什么"挑模板）、线和问题（后台多轮实测状态里这次真的场景），不借演示数据
let RCT=null,COLJ=null;
const realCol=()=>realFlash()&&FSWJ&&FSWJ.status==='done';
const CX=()=>realCol()&&RCT?RCT:COLLECT[scene];
async function loadRCT(){const P=IDENT.page,st=(P.measure||'').split('→')[0].trim(),names=Object.fromEntries((P.steps||[]).map(x=>[x.p,x.n]));
 try{const t=await (await fetch('/api/rounds?'+new URLSearchParams({what:hSay||P.mapWhat||'',start:st}))).json();
  RCT={...t,ph:[...(P.main||[]).map(k=>names[k]).filter(Boolean),'这一轮记好了'],probs:(COLJ&&COLJ.problems||[]).map(x=>({k:x.key,t:x.name,c:x.level==='bad'?'bad':'warn'}))};if(page==='collect')render()}catch(e){}}
function realD(){const P=IDENT.page,[sShort,mEnd]=(P.measure||'起点 → 终点').split('→').map(x=>x.trim()),sEnd=P.sideAsk?P.sideAsk.q:'';
 const L=COLJ&&COLJ.lines&&COLJ.lines.length?COLJ.lines:(sEnd?[{name:sEnd},{name:mEnd}]:[{name:mEnd}]);
 const lines=L.map((l,i)=>({name:l.name,end:i<L.length-1?(sEnd||l.name):mEnd,ok:l.range?[l.range[0],l.range[1]??Infinity]:null,slow:l.slow??Infinity,col:LCOL[i]}));
 return {lines,S:{startShort:sShort,exp:COLJ&&COLJ.experience?{fair:COLJ.experience.fair}:null},rounds:okRounds().map(r=>({ends:r.e}))}}
const DX=()=>realCol()?realD():build(SCENES[scene]);
const PH=()=>CX().ph;
// 多轮实测：后台一行行读设备输出（没真源码时回放演示数据的设备输出），按轮切分、算用时、查问题；这里每 0.3 秒问一次
let colES=null;
function colStop(){if(colT){clearInterval(colT);colT=null}if(colES){colES.close();colES=null}}
// 收进度：先用服务器推送（网页在后台也能马上收到、马上"叮"）；推送断了就退回每 0.3 秒查一次（决策 102）
function colWatch(){colStop();const poll=()=>{if(!colT)colT=setInterval(async()=>{try{colApply(await (await fetch('/api/collect')).json())}catch(e){}},300)};
 if(!window.EventSource){poll();return}
 try{colES=new EventSource('/api/collect/events');colES.onmessage=e=>{try{colApply(JSON.parse(e.data))}catch(x){}};
  colES.onerror=()=>{if(colES){colES.close();colES=null}if(col.st===1)poll()}}catch(e){poll()}}
function colApply(j){COLJ=j;if(realCol()&&RCT)RCT.probs=(j.problems||[]).map(x=>({k:x.key,t:x.name,c:x.level==='bad'?'bad':'warn'}));const C=CX(),before=col.got.length;if(j.status==='done'||j.status==='stopped'){fetchReport();if(realFlash())histLoad()}
 col.got=(j.rounds||[]).map(r=>r.ok?{n:r.n,e:r.ends,probs:C.probs.filter(p=>(r.probs||[]).includes(p.k)),att:r.att,segs:r.segs}:{bad:true,att:r.att,missing:r.missing||[]});
 col.att=col.got.length?col.got[col.got.length-1].att:0;col.last=j.last||'';col.msg=j.status==='paused'||j.status==='waiting_device'?(j.message||'没看到设备：用 USB 线插上，插上会自动开始'):(j.half_hint||j.hint||'');  // 调试口开着却没有计时点：说是哪种情况，不停下
 const [got,need]=j.progress||[0,1],n=PH().length-1;col.phase=got>=need?n:Math.min(n-1,Math.floor(got*n/need));
 for(const x of col.got.slice(before))ding(!!x.bad);
 if(j.status==='done'){colStop();col.st=2}else if(j.status==='stopped'&&col.st!==3){colStop();col.st=3}
 if(page==='collect')render()}
async function colStart(keep){colStop();if(keep){col.st=1;col.phase=0}else col={st:1,att:0,got:[],phase:0,sc:scene};ADV=null;clearInterval(advT);advT=null;fixPick=null;
 const real=realFlash()&&FSWJ&&FSWJ.status==='done';
 const body=real?{page:IDENT.page,source:'device',project_dir:REAL.project_dir,rounds:colN,resume:!!keep,what:hSay||IDENT.page.mapWhat||''}
  :{demo:scene,rounds:colN,resume:!!keep,interval:.12,drop_at:keep?0:9};
 try{const r=await fetch('/api/collect/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok){toast('✕ '+(j.detail||'没开始'));col.st=0;render();return}colApply(j)}catch(e){toast('✕ 码表后台没有回应');return}
 if(beep){try{actx=actx||new (window.AudioContext||window.webkitAudioContext)();actx.resume&&actx.resume()}catch(e){}}  // 点开始时就把声音准备好，网页在后台也能响
 colWatch()}
const okRounds=()=>col.got.filter(x=>!x.bad);
const missTxt=x=>x.missing&&x.missing.length?`没收全：缺「${x.missing.join('」「')}」`:CX().miss;
function issues(x){if(x.bad)return `<span class="chip c-gray">${missTxt(x)}，不计入，自动补收一轮</span>`;
 return x.probs.map(p=>`<span class="chip c-${p.c}">${p.t}</span>`).join(' ')||'<span class="note">正常</span>'}
function colSetup(){const C=CX(),D=DX();return `${justFlashed?`<div class="okbar">✓ 计时版已烧进设备，试的那一次 ${realFlash()&&FSWJ&&(FSWJ.got||[]).length<FSWJ.total?`收到了 ${(FSWJ.got||[]).length} / ${FSWJ.total} 个计时点（没收到：${tryMissing().join('、')}，这一次没走到，不影响多轮实测）`:`${realFlash()&&FSWJ?FSWJ.total:live().length} 个计时点都收到了`}。下面开始多轮实测。</div>`:''}<section class="panel"><h1 style="font-size:22px">多轮实测：${C.cnt} ${colN} ${C.unit}</h1>
 <p style="margin:4px 0 0">同样的事做很多轮，码表从设备输出里记下每一轮各段用了多久。刚才试的那一次不算。</p>
 <div class="status" style="margin:6px 0 4px">${REAL&&!devNow().on?'<span><i class="dot" style="background:var(--warn)"></i>没看到设备：用 USB 线插上，插上会自动继续</span>':`<span><i class="dot" style="background:var(--ok)"></i>${C.dev}已连上${REAL?' '+htmlEsc(devNow().port):''}，计时点输出正常</span>`}<span><i class="dot" style="background:var(--ok)"></i>${C.mode}</span><span><i class="dot" style="background:var(--ok)"></i>量 ${D.lines.length} 条线：${D.lines.map(l=>D.S.startShort+' → '+l.end).join('；')}</span></div>
 <dl class="cform">
  <dt>谁来${C.act}</dt><dd><p style="margin:0">${C.who}</p><p class="note" style="margin:4px 0 0">${C.why}</p></dd>
  <dt>测几轮</dt><dd><div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><div class="seg" role="group" aria-label="测几轮">${[10,20,30].map(n=>`<button data-coln="${n}" aria-pressed="${n===colN}">${n} 轮${n===20?'（推荐）':''}</button>`).join('')}</div><label class="note" style="display:inline-flex;align-items:center;gap:6px">自己填<input type="number" id="col-n" min="3" max="100" step="1" value="${[10,20,30].includes(colN)?'':colN}" placeholder="3～100" style="width:5.5em"> 轮</label></div>
   <p class="note" style="margin:4px 0 0">20 轮够看出一般多快、最慢的几次、偶尔出的错。轮数越多越准，也越费时间（${C.per}，估算）。${colN<10?`<br><span style="color:var(--warn)">${colN} 轮只适合跑流程：报告会注明"数字不作数"，不显示"10 次里最慢"。</span>`:''}</p></dd>
  <dt>${C.listName} ${colN} ${C.unit}</dt><dd><details><summary class="pill" style="display:inline-block;list-style:none;cursor:pointer">展开清单</summary><ol class="note" style="columns:2;margin:8px 0 0">${[...Array(colN)].map((_,i)=>`<li>${C.items[i%C.items.length]}</li>`).join('')}</ol></details>
   <p class="note" style="margin:4px 0 0">${C.itemsFrom||'固定清单'}。不用背，每轮页面上会大字提示。</p></dd>
  <dt>提示音</dt><dd>${beepBtn()}<p class="note" style="margin:4px 0 0">${beepNote()}</p></dd>
 </dl>
 <div class="acts"><button class="btn pri" data-col="go" onclick="justFlashed=false">开始实测</button></div>
 <p class="note">${C.wait}原始日志同时存一份，以后能重新算。</p></section>`}
function colA(open=true){const g=col.got,L=DX().lines,C=CX();
 const tab=`<table class="rtab"><thead><tr><th>轮</th>${L.map(l=>`<th>${l.end}</th>`).join('')}<th>情况</th></tr></thead><tbody>${[...g].reverse().map(x=>`<tr ${x.bad?'class="off"':''}><td class="num">${x.bad?'—':x.n}</td>${L.map((_,i)=>`<td class="num">${x.bad?'—':f(x.e[i])+' 秒'}</td>`).join('')}<td>${issues(x)}</td></tr>`).join('')}</tbody></table>`;
 return `<section class="panel"><h2>已测 <span class="num">${okRounds().length}</span> / ${colN} 轮</h2>
 ${col.st===1?`<p class="note" style="margin-top:0">现在：第 ${col.att+1} 次，${PH()[col.phase]}……　下一${C.unit}："${C.items[okRounds().length%C.items.length]}"</p>`:''}${tab}</section>`}
function tipOf(x){const C=CX(),L=DX().lines,at=t=>t.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
 if(x.bad)return at(`<b>第 ${x.att} 次：${missTxt(x)}</b><br>不计入，已自动补测一轮`);
 return at(`<b>第 ${x.n} 轮</b>　${C.said}“${C.items[(x.n-1)%C.items.length]}”<br>`+L.map((l,i)=>`${l.end}：<b>${f(x.e[i])} 秒</b>${l.ok&&x.e[i]>l.ok[1]?`（超出合理范围 ${l.ok[0]}～${l.ok[1]} 秒）`:''}`).join('<br>')+'<br>'+(x.probs.length?x.probs.map(p=>'⚠ '+p.t).join('　'):'✓ 没出问题'))}
function colB(){const k=okRounds().length,cur=Math.min(k,colN-1),C=CX();
 const sq=x=>{const p=x.probs&&x.probs[0];return `<i class="sq" tabindex="0" data-tip="${tipOf(x)}" style="background:${x.bad?'var(--soft)':p?`var(--${p.c})`:'var(--ok)'}"></i>`};
 return `<section class="panel" style="text-align:center">
 ${col.st===1?`<p class="note" style="margin:0">第 ${cur+1} 轮 / 共 ${colN} 轮</p><p style="font-size:15px;margin:10px 0 4px">${col.phase===0?C.say:C.said}</p><p style="font-size:26px;font-weight:600;margin:0 0 16px">"${C.items[cur%C.items.length]}"</p>
 <div class="phs">${PH().map((p,i)=>`<span class="${i<col.phase?'ok':i===col.phase?'cur':''}">${p}</span>`).join('<b>→</b>')}</div>`:col.st===3?`<p style="font-size:20px;font-weight:600">已停止：测了 ${k} / ${colN} 轮</p>`:`<p style="font-size:20px;font-weight:600">测完了</p>`}
 <div class="sqs">${col.got.map(sq).join('')}${[...Array(Math.max(0,colN-k))].map(()=>'<i class="sq"></i>').join('')}</div>
 <p class="note" style="margin-bottom:2px">一格是一轮，颜色表示这轮有没有出问题（不是快慢）。鼠标移到格子上看这轮各段用时。</p><p class="note"><i class="dot" style="background:var(--ok)"></i>正常　${C.probs.map(p=>`<i class="dot" style="background:var(--${p.c})"></i>${p.t}　`).join('')}<i class="dot" style="background:var(--soft)"></i>没收全，自动补一轮</p>
 ${col.got.filter(x=>x.bad||x.probs.length).slice(-3).reverse().map(x=>`<p class="note" style="margin:2px 0">${x.bad?'第 '+x.att+' 次':'第 '+x.n+' 轮'}：${issues(x)}</p>`).join('')}
 <details style="text-align:left;margin-top:10px"><summary class="note" >逐轮明细（${okRounds().length} 轮）</summary>${colA().replace(/^<section class="panel">/,'<div>').replace(/<\/section>$/,'</div>')}</details></section>`}
function colC(){const g=okRounds(),D=DX(),L=D.lines,main=L[L.length-1],W=720,H=230,Lp=46,R=12,T=14,B=30;
 const ymax=Math.ceil(Math.max(...[main.slow,...D.rounds.flatMap(r=>r.ends)].filter(v=>isFinite(v)),.5)*1.1*2)/2;
 const x=i=>Lp+(W-Lp-R)*(i+.5)/colN,y=v=>T+(H-T-B)*(1-v/ymax);
 const hl=(v,t,c)=>`<line x1="${Lp}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="${c}" stroke-dasharray="4 4"/><text x="${W-R}" y="${y(v)-4}" text-anchor="end" font-size="11" fill="${c}">${t}</text>`;
 const mains=g.map(r=>r.e[L.length-1]);const st=ymax<=2.5?.5:1,ticks=[...Array(Math.floor(ymax/st)+1)].map((_,i)=>+(i*st).toFixed(1));
 return `<section class="panel"><h2>已测 <span class="num">${g.length}</span> / ${colN} 轮　<span class="note" style="font-weight:400">${col.st===1?`现在：${PH()[col.phase]}`:col.st===3?'已停止':'测完了'}</span></h2>
 <div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:560px" role="img" aria-label="每轮用时">
  ${main.ok&&isFinite(main.ok[1])?`<rect x="${Lp}" y="${y(main.ok[1])}" width="${W-Lp-R}" height="${y(main.ok[0])-y(main.ok[1])}" fill="var(--speak-soft)" opacity=".5"/>`:''}
  ${ticks.map(v=>`<text x="${Lp-8}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="var(--muted)">${v} 秒</text>`).join('')}
  ${D.S.exp?hl(D.S.exp.fair,D.S.exp.fair+' 秒：超过算体验差','var(--warn)'):''}
  ${mains.length?hl(med(mains),main.end+'一般 '+f(med(mains))+' 秒','var(--speak)'):''}
  ${g.map((r,i)=>L.map((l,j)=>`<circle cx="${x(i)}" cy="${y(r.e[j])}" r="${j===L.length-1?5:4}" fill="${j===L.length-1&&r.probs.length?'var(--bad)':l.col}"/>`).join('')).join('')}
  <text x="${Lp}" y="${H-8}" font-size="11" fill="var(--muted)">第 1 轮</text><text x="${W-R}" y="${H-8}" text-anchor="end" font-size="11" fill="var(--muted)">第 ${colN} 轮</text>
 </svg></div>
 <p class="note">${L.map(l=>`<i class="dot" style="background:${l.col}"></i>${l.end}　`).join('')}<i class="dot" style="background:var(--bad)"></i>这轮有问题${main.ok?`　浅色带：${main.end}合理范围 ${main.ok[0]}～${isFinite(main.ok[1])?main.ok[1]:'不限'} 秒`:''}</p>
 <div class="ilog"><span class="note">设备输出</span><code>${col.st===1&&col.last?col.last:'—'}</code></div></section>`}
const COLVAR={a:{label:'A 逐轮表',idea:'A 逐轮表：每收一轮加一行（换表情、开口、有没有问题），最新的在上面。信息最全，适合研发盯着看。'},
 b:{label:'B 跟着做',idea:'B 跟着做（推荐）：大字提示下一步做什么、现在到哪一步，一轮一格小方块、出问题变色；下面能展开 A 的逐轮明细。人眼要看着设备，页面只需扫一眼。'},
 c:{label:'C 边测边画',idea:'C 边测边画：每收一轮在图上多两个点，合理范围和"一般用时"线跟着变，收到一半就能看出稳不稳。'}};
function collectPage(){
 if(col.st===0)return `<div class="stack">${colSetup()}</div>`;
 const body={a:colA,b:colB,c:colC}[colVar]();
 const g=okRounds(),bad=col.got.length-g.length;
 return `<div class="stack">${col.st===1&&col.msg?`<div class="why" style="border-left-color:var(--warn)">${htmlEsc(col.msg)}</div>`:''}${col.st===1?`<div class="status" style="justify-content:flex-end">${beepBtn()}</div>`:''}${body}${col.st===2||(col.st===3&&okRounds().length>=10)?enough():''}
 <section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0">${col.st===3?(()=>{const k=g.length,can=k>=MIN_ROUNDS,to=flow==='retest'&&rtC().steps[3][1]?['compare','查看前后对比']:['report','查看报告'];return `<span class="note">已停止。测了的 ${k} 轮都留着，可以接着测。${can?'也可以先看结果，会注明只测了 '+k+' 轮。':''}</span><span style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end"><button class="btn" data-go="${to[0]}" ${can?'':blk('[data-col="resume"]',`至少测 ${MIN_ROUNDS} 轮才能看结果`)}>${to[1]}</button><button class="btn" data-col="restart">从头重测</button><button class="btn pri" data-col="resume">接着测（从第 ${k+1} 轮）</button></span>`})():`<span class="note">${col.st===2?`测完 ${g.length} 轮${bad?`（另有 ${bad} 轮没收全，没算）`:''}；原始日志已存。`:'可以随时停，停了能接着测，已测的都留着。'}</span><span style="display:flex;gap:8px">${col.st===1?'<button class="btn" data-col="stop">停止实测</button>':`<button class="btn ${needMore()?'pri':''}" data-col="more">再测 10 轮${needMore()?'（建议）':''}</button>`}${flow==='retest'&&rtC().steps[3][1]?`<button class="btn ${col.st===2&&needMore()?'':'pri'}" data-go="compare" ${col.st===2?'':blk('[data-col="stop"]','还在测')}>查看前后对比</button>`:`<button class="btn ${col.st===2&&needMore()?'':'pri'}" data-go="report" ${col.st===2?'':blk('[data-col="stop"]','还在测')}>查看报告</button>`}</span>`}</div></section></div>`}

// ===== 前后对比 =====（通用：两次结果都用报告页同一套计算；改后数据是模拟的）

function afterScene(k){const S=JSON.parse(JSON.stringify(SCENES[k]));for(const s in RUNS[k].afterSegs)S.segs[s].v=RUNS[k].afterSegs[s];return S}
let cmpVar='c';
const flat=(a,b)=>Math.abs(b-a)<Math.max(.03,a*.05);
function dchip(a,b){if(flat(a,b))return '<span class="note">→ 持平</span>';const d=b-a;return d<0?`<span class="down">↓ 快了 ${f(-d)} 秒</span>`:`<span class="up">↑ 慢了 ${f(d)} 秒</span>`}
const TIERS=[['一般','med'],['10 次最慢','p90'],['最差一次','max']];
function cmpData(){const B=build(SCENES[scene]),A=build(afterScene(scene));return {B,A,R:RUNS[scene]}}
function cmpHead(){const {B,A}=cmpData();
 return A.lines.map((l,i)=>{const b=B.lines[i],m=flat(b.med,l.med),p=flat(b.p90,l.p90);
  const s1=m?`一般持平（${f(b.med)} → ${f(l.med)} 秒）`:`一般${l.med<b.med?'快':'慢'}了 ${f(Math.abs(l.med-b.med))} 秒（${f(b.med)} → ${f(l.med)}）`;
  const s2=p?'最慢那几次也差不多':`最慢那几次${l.p90<b.p90?'好了很多':'更慢了'}（${f(b.p90)} → ${f(l.p90)} 秒）`;
  return `<b>${l.end}</b>：${s1}，${s2}。`}).join('<br>')}
function fair(){const {R}=cmpData(),C=COLLECT[scene];
 return `<div class="status"><span><i class="dot" style="background:var(--ok)"></i>两次都按同样的 20 ${C.unit}${C.act}</span><span><i class="dot" style="background:var(--ok)"></i>计时点相同</span><span><i class="dot" style="background:var(--ok)"></i>用同一份合理范围</span>${flow==='retest'&&rtCase==='tool'?'<span><i class="dot" style="background:var(--warn)"></i>编译工具不同（v6.1 → v6.2），变化不一定都来自你改的那一处</span>':''}<span><i class="dot" style="background:var(--warn)"></i>网络、服务器负载码表看不到，请自己保证差不多</span></div>`}
function cmpPick(){const R=RUNS[scene];return `<section class="panel"><h1 style="font-size:22px">前后对比</h1>
 <div class="pickrow"><label>改前<select><option>${flow==='retest'?RT[scene].last+'　上一次（自动选的）':R.before}</option></select></label><span class="note">→</span><label>改后<select><option>${flow==='retest'?'今天　这次改完再测':R.after}</option></select></label></div>
 <p class="note" style="margin:6px 0">这次改了什么（${rtWhat?'改完再测时码表先写、你确认过的':'你填'}，报告里会带上）：<b>${rtWhat||R.what}</b></p>${fair()}</section>`}
function cmpA(){const {B,A}=cmpData();
 const tier=`<table class="rtab"><thead><tr><th></th>${A.lines.map(l=>`<th colspan="3">${l.end}</th>`).join('')}</tr><tr><th></th>${A.lines.map(()=>'<th>改前</th><th>改后</th><th>变化</th>').join('')}</tr></thead><tbody>${TIERS.map(([t,k])=>`<tr><td>${t}</td>${A.lines.map((l,i)=>`<td class="num">${f(B.lines[i][k])}</td><td class="num">${f(l[k])}</td><td>${dchip(B.lines[i][k],l[k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
 const segs=`<table class="rtab" style="margin-top:14px"><thead><tr><th>分段（中位数）</th><th>谁负责</th><th>改前</th><th>改后</th><th>变化</th></tr></thead><tbody>${A.allSegs.map((s,i)=>`<tr><td>${s.name}</td><td class="note">${s.owner}</td><td class="num">${f(B.allSegs[i].med,3)}</td><td class="num">${f(s.med,3)}</td><td>${dchip(B.allSegs[i].med,s.med)}</td></tr>`).join('')}</tbody></table>`;
 return `<section class="panel"><h2>对照表</h2>${tier}${segs}<p class="note">变化小于 0.03 秒或 5% 算持平：同一台设备连测两次，本来就会有这么大的抖动。</p></section>`}
function cmpB(){const {B,A}=cmpData();const T=niceT(Math.max(...B.lines.map(l=>l.med),...A.lines.map(l=>l.med)));
 const W=900,X0=150,X1=820,sx=t=>X0+(X1-X0)*t/T,h=18,top=30;let g='',y=top;const st=step(T);
 const rows=[];A.lines.forEach((l,i)=>{rows.push([B.lines[i],'改前',.45,B]);rows.push([l,'改后',1,A])});
 const H=top+rows.length*30+40;
 for(let t=0;t<=T+1e-9;t+=st)g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="${top-10}" y2="${H-26}" stroke="var(--soft)"/><text x="${sx(t)}" y="${H-8}" text-anchor="middle" font-size="12" fill="var(--muted)">${+t.toFixed(2)} 秒</text>`;
 rows.forEach(([l,lab,op,D],r)=>{y=top+r*30+(r%2?0:6);let t0=0;
  g+=`<text x="${X0-12}" y="${y+13}" text-anchor="end" font-size="13" fill="${l.col}" ${op<1?'opacity=".7"':'font-weight="700"'}>${l.end}·${lab}</text>`;
  l.segs.forEach((k,j)=>{const d=l.segMed[j];g+=`<rect x="${sx(t0)}" y="${y}" width="${Math.max(2,sx(t0+d)-sx(t0))}" height="${h}" fill="${j===l.segs.length-1?l.col:l.soft}" opacity="${op}" rx="2"><title>${D.S.segs[k].name} ${f(d)} 秒</title></rect>`;
   if(op===1&&sx(t0+d)-sx(t0)>80){const bd=B.lines[l.i].segMed[j];g+=`<text x="${(sx(t0)+sx(t0+d))/2}" y="${y+13}" text-anchor="middle" font-size="11.5" fill="var(--fg)">${D.S.segs[k].name}${flat(bd,d)?'':d<bd?' ↓':' ↑'}</text>`}t0+=d});
  g+=`<text x="${sx(t0)+8}" y="${y+13}" font-size="12.5" class="num" fill="${l.col}">${f(l.med)} 秒</text>`});
 return `<section class="panel"><h2>改前改后叠在一起看</h2><div class="scroll"><svg viewBox="0 0 ${W} ${H}" style="min-width:640px;width:100%" role="img" aria-label="前后对比时间线">${g}</svg></div>
 <p class="note">浅色是改前，深色是改后；段名旁的 ↓ ↑ 表示这一段快了还是慢了。数字取 20 轮中位数。</p></section>`}
function cmpC(){const {B,A}=cmpData();const L=A.lines.length-1,lb=B.lines[L],la=A.lines[L];
 const T=niceT(Math.max(...B.rounds.flatMap(r=>r.ends),...A.rounds.flatMap(r=>r.ends))/1.1),W=900,X0=90,X1=860,sx=t=>X0+(X1-X0)*t/T,st=step(T);
 let g='';for(let t=0;t<=T+1e-9;t+=st)g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="10" y2="104" stroke="var(--soft)"/><text x="${sx(t)}" y="122" text-anchor="middle" font-size="12" fill="var(--muted)">${+t.toFixed(2)} 秒</text>`;
 [[B,'改前',30,.45],[A,'改后',80,1]].forEach(([D,lab,y,op])=>{g+=`<text x="${X0-12}" y="${y+4}" text-anchor="end" font-size="13" fill="var(--fg)">${lab}</text>`;
  D.rounds.forEach(r=>g+=`<circle cx="${sx(r.ends[L])}" cy="${y}" r="6" fill="${D.lines[L].col}" opacity="${op}"/>`);
  g+=`<line x1="${sx(D.lines[L].med)}" x2="${sx(D.lines[L].med)}" y1="${y-14}" y2="${y+14}" stroke="var(--fg)" stroke-width="2"/>`});
 const card=([t,k])=>`<div class="tcard"><span class="note">${t}</span><b class="num">${f(la[k])} 秒</b><span>${dchip(lb[k],la[k])}</span></div>`;
 return `<section class="panel"><h2>结论</h2><p style="font-size:16px;line-height:1.7;margin:4px 0 12px">${cmpHead()}</p>
 <div class="tcards">${TIERS.map(card).join('')}</div><p class="note">上面三格是"${la.end}"那条线。</p>
 <h3 class="h3">20 轮"${la.end}"的用时，一个点一轮</h3><div class="scroll"><svg viewBox="0 0 ${W} 130" style="min-width:640px;width:100%" role="img" aria-label="改前改后每轮分布">${g}</svg></div>
 <p class="note">竖线是一般用时。点挤得越紧，说明越稳定。</p></section>`}
const CMPVAR={a:{label:'A 对照表',idea:'A 对照表：三档用时和每一段，改前、改后、变化三列，箭头标快慢。最全，适合研发查是哪一段变了。'},
 b:{label:'B 叠图',idea:'B 叠图：报告里那张计时图，改前（浅）改后（深）叠在一起，段名旁带 ↓ ↑。一眼看出变化落在哪一段。'},
 c:{label:'C 结论先行',idea:'C 结论先行：先一句话说改完变快没有，再给三档大数字和箭头，最后是每轮分布（点挤得紧说明稳）。负责人只看上面就能拍板。'}};
function comparePage(){return `<div class="stack">${flow==='retest'?`<section class="panel" style="border-color:var(--ok)"><b>改完再测完成。</b><span class="note">　下面是和上一次的对比；这次的完整报告也已生成。</span><div class="acts" style="margin:8px 0 0"><button class="btn" data-go="report">看这次的完整报告</button><button class="btn" data-go="home">回首页</button></div></section>`:''}${cmpPick()}${cmpC()}<details class="panel"><summary style="cursor:pointer"><b>对照表</b>　<span class="note">三档用时和每一段的改前、改后、变化</span></summary>${cmpA().replace(/^<section class="panel"><h2>对照表<\/h2>/,'').replace(/<\/section>$/,'')}</details></div>`}

// ===== 合理范围 =====（通用：部件、分段标准、依据都按场景）
let rngVar='a',rngAsk=0;
const sureC=s=>s==='高'?'c-ok':s==='中'?'c-warn':'c-bad';
function rngParts(){const R=RANGE[scene];return `<section class="panel"><h2>先认出关键部件</h2><p class="note" style="margin-top:0">合理范围要看是什么硬件、连的什么服务器。下面是码表读代码认出来的，可信度不高的请你确认。</p>
 <div class="stable">${R.parts.map(([k,v,s,why])=>`<div class="srow2" style="grid-template-columns:8em 1fr auto"><b>${k}</b><div>${v} <span class="chip ${sureC(s)}">可信度${s}</span><div class="note">${why}</div></div>${s==='高'?'<span class="note">✓</span>':'<button class="pill">确认或修改</button>'}</div>`).join('')}</div></section>`}
function rngTable(){const S=SCENES[scene],R=RANGE[scene],D=build(S);
 return `<table class="rtab"><thead><tr><th>分段</th><th>谁负责</th><th>合理</th><th>超过算慢</th><th>依据</th><th>可信度</th><th>实测基线</th></tr></thead><tbody>${Object.entries(S.segs).map(([k,s])=>{const r=R.segs[k];return `<tr><td>${s.name}</td><td class="note">${s.owner}</td><td class="num">${r[0]}</td><td class="num">${r[1]}</td><td class="note">${r[2]}</td><td><span class="chip ${sureC(r[3])}">${r[3]}</span></td><td class="num">${f(med(s.v),s.v[0]<.05?3:2)} 秒</td></tr>`}).join('')}</tbody></table>
 <p class="note">${R.exp}。实测基线：${D.n} 轮中位数，有实测后自动加上，以后每次测量都会和它比。</p>`}
function rngHist(){const R=RANGE[scene];const rows=[...R.hist];if(rngAsk>=2)rows.push(['今天','你：'+R.chat[0]+'（示例）',R.chat[2]]);
 return `<details><summary class="note" >改动历史（${rows.length} 条）</summary>${rows.map(r=>`<div class="srow"><span><b>${r[0]}</b>　${r[1]}</span><span class="note">${r[2]}</span></div>`).join('')}</details>`}
function rngA(){return `${rngParts()}<section class="panel"><h2>每段多久算正常</h2>${rngTable()}${rngHist()}</section>`}
function rngChat(){const R=RANGE[scene],steps=[['you',R.chat[0]],['ai',R.chat[1]]];
 return `<h3 class="h3" style="margin-top:18px">想改哪里，直接说</h3><p class="note" style="margin-top:0">码表先把改动摆出来（旧 → 新），你点确认才生效，并记进改动历史。</p>
 <div class="chat">${steps.slice(0,rngAsk?2:0).map(([w,t])=>`<div class="msg ${w}">${t}</div>`).join('')}${rngAsk===1?'<div class="acts"><button class="btn pri" data-rng="ok">确认修改</button><button class="btn" data-rng="no">取消修改</button></div>':''}${rngAsk===2?'<p class="note">已改，记进了改动历史。<br>之前按旧范围判断的报告，打开时顶上会提示用新范围更新。</p>':''}</div>
 <div class="inrow" style="margin:10px 0"><input type="text" placeholder="${R.chat[3]}" ${rngAsk?'':`value="${R.chat[0]}"`}><button class="btn pri" data-rng="ask" ${rngAsk?'disabled':''}>发给码表</button></div>`}
function rngB(){const R=RANGE[scene],steps=[['you',R.chat[0]],['ai',R.chat[1]]];
 return `${rngParts()}<section class="panel"><h2>用说话的方式改标准</h2><p class="note" style="margin-top:0">不用找是哪一格，直接说你想怎么改；码表先把改动摆出来，你点确认才生效。</p>
 <div class="chat">${steps.slice(0,rngAsk?2:0).map(([w,t])=>`<div class="msg ${w}">${t}</div>`).join('')}${rngAsk===1?'<div class="acts"><button class="btn pri" data-rng="ok">确认修改</button><button class="btn" data-rng="no">取消修改</button></div>':''}${rngAsk===2?'<p class="note">已改，记进了改动历史。<br>之前按旧范围判断的报告，打开时顶上会提示用新范围更新。</p>':''}</div>
 <div class="inrow" style="margin-top:10px"><input type="text" placeholder="${R.chat[3]}" ${rngAsk?'':`value="${R.chat[0]}"`}><button class="btn pri" data-rng="ask" ${rngAsk?'disabled':''}>发给码表</button></div>
 <details style="margin-top:12px"><summary class="note" >完整标准</summary>${rngTable()}</details>${rngHist()}</section>`}
function rngC(){const S=SCENES[scene],R=RANGE[scene],D=build(S);
 const parse=t=>{const m=t.replace(' 秒','').replace(' 毫秒','').split('～').map(Number);return t.includes('毫秒')?m.map(x=>x/1000):m};
 const rows=Object.entries(S.segs).map(([k,s])=>{const r=R.segs[k],ok=parse(r[0]),slow=parse(r[1])[0],me=med(s.v),T=Math.max(slow*1.25,me*1.1,ok[1]*1.3);
  const W=520,sx=v=>10+(W-20)*v/T;
  return `<div class="ruler"><div><b>${s.name}</b> <span class="chip ${sureC(r[3])}">可信度${r[3]}</span><div class="note">${s.owner} · ${r[2]}</div></div>
  <svg viewBox="0 0 ${W} 40" style="width:100%;max-width:${W}px" role="img" aria-label="${s.name}的合理范围">
   <line x1="10" x2="${W-10}" y1="20" y2="20" stroke="var(--line)" stroke-width="2"/>
   <rect x="${sx(ok[0])}" y="12" width="${sx(ok[1])-sx(ok[0])}" height="16" rx="3" fill="var(--ok)" opacity=".25"/>
   <line x1="${sx(slow)}" x2="${sx(slow)}" y1="6" y2="34" stroke="var(--bad)" stroke-width="2"/><text x="${sx(slow)+4}" y="10" font-size="11" fill="var(--bad)">超过算慢</text>
   <circle cx="${sx(me)}" cy="20" r="6" fill="${me>slow?'var(--bad)':me>ok[1]?'var(--warn)':'var(--speak)'}"/><text x="${sx(me)}" y="39" text-anchor="middle" font-size="11" fill="var(--muted)">实测 ${me<.05?Math.round(me*1000)+' 毫秒':f(me)+' 秒'}</text></svg></div>`}).join('');
 return `${rngParts()}<section class="panel"><h2>每段的尺子</h2><p class="note" style="margin-top:0">绿色是合理范围，红线是超过算慢，圆点是实测一般用时。一眼看出哪段落在范围外、哪段的依据可信度低。</p>${rows}
 <p class="note">${R.exp}</p>${rngHist()}</section>`}
const RNGVAR={a:{label:'A 标准表',idea:'A 标准表：先确认部件，再一张表列出每段合理范围、超过算慢、依据、可信度、实测基线，下面是改动历史。和存下来的 markdown 文件一一对应。'},
 b:{label:'B 说话改',idea:'B 说话改：直接打字说想怎么改，码表把改动摆出来（旧 → 新），你点确认才生效并记进历史。点"发给码表"试试。'},
 c:{label:'C 尺子',idea:'C 尺子：每段一把尺子，绿色是合理范围、红线是超过算慢、圆点是实测，适合负责人看"标准定得合不合理"。'}};
// 真流程的"合理范围依据"：这次按代码位置对上的标准（码表自带）和实测基线，只读；改范围以后做
function rangeReal(){const J=JREP,sc=J.scenario||{},segs=sc.segments||{},med=Object.fromEntries((J.segments||[]).map(g=>[g.k,g.med])),rg=r=>r?`${r[0]}～${r[1]??'不限'} 秒`:'—';
 const rows=Object.entries(segs).map(([k,g])=>`<tr><td>${htmlEsc(g.name)}</td><td>${htmlEsc(g.owner||'')}</td><td class="num">${rg(g.range)}</td><td class="num">${g.slow!=null?g.slow+' 秒':'—'}</td><td>${g.range?(g.basis==='estimated'?'推算':'实测'):'没有范围'}</td><td class="num">${med[k]!=null?f(med[k])+' 秒':'—'}</td></tr>`).join('');
 return `<div class="stack"><div class="status"><span><i class="dot" style="background:${sc.standard?'var(--ok)':'var(--warn)'}"></i>${sc.standard?`码表自带的标准：${htmlEsc(sc.standard)}（按代码位置对上 ${Object.keys(sc.matched||{}).length} 个计时点）`:'还没有这个设备的合理范围：只记用时，先不判快慢'}</span></div>
 <section class="panel scroll"><h2>每段多久算正常</h2><table class="num"><thead><tr><th>分段</th><th>谁负责</th><th class="r">合理</th><th class="r">超过算慢</th><th>依据</th><th class="r">实测基线</th></tr></thead><tbody>${rows}</tbody></table>
 <p class="note">实测基线是这次 ${J.n} 轮的一般用时（中位数）。合理范围来自码表自带的标准文件；对不上标准的段没有范围，只记用时。</p></section>
 <section class="panel"><p class="note" style="margin:0">这一版只读：照着标准判快慢。改合理范围、补没有范围的段，以后做。</p><div class="acts"><button class="btn" data-go="report" data-keepflow="1">回到报告</button></div></section></div>`}
function rangePage(){if(realFlash()&&JREP&&!JREP._none)return rangeReal();const R=RANGE[scene];return `<div class="stack"><div class="status"><span><i class="dot" style="background:var(--ok)"></i>标准文件：<span class="code">${R.file}</span>，存在你电脑上，每个被测项目一份</span><span><i class="dot" style="background:var(--ok)"></i>报告、多轮实测、前后对比都按它判快慢</span></div>${rngParts()}<section class="panel"><h2>每段多久算正常</h2>${rngTable()}${rngChat()}${rngHist()}</section></div>`}


let rtCase='same',rtWhat=null,flow=null,showWhy=false;
const FLOW1=[['home','选固件'],['steps','找关键步骤'],['flash','插计时点和烧录'],['collect','多轮实测'],['report','报告']];
// ===== 引导 =====（被挡住的按钮点了会告诉你缺什么并闪两下；长任务做完弹窗；顶部一行"现在要做"）
let pop=null;
const blk=(sel,msg,go)=>` aria-disabled="true" data-need='${sel}' data-msg='${msg}'${go?` data-needgo="${go}"`:''}`;
// 被挡住的按钮：原因直接写在按钮左边；点它只把原因加粗、把要补的地方框成橙色，不跳不闪
function nudge(sel){setTimeout(()=>{const el=sel&&document.querySelector(sel);if(el){el.scrollIntoView({behavior:'smooth',block:'center'});el.classList.add('need')}},40)}
function guide(){const F=FF[scene];
 if(page==='home'){if(hStage>=2)return null;if(hReading)return ['正在读项目，稍等一下',null];
  if(hStage===0)return ['把固件文件夹拖到下面的框里，或点「选文件夹」','#dropzone'];
  return ['用一句话写要测从什么到什么（不确定就点「AI 推荐」里的一个），再点「下一步」','#h-what']}
 if(page==='settings'){if(hStage<1)return null;if(setFirst&&!keyTested&&setLLM!=='cc')return ['填大模型密钥：粘贴进去，点「测试密钥」','#set-key'];return ['准备好了，点「回去继续」','[data-resume]']}
 if(page==='steps'){if(stepsRead<2)return ['不用操作，等它读完（一般 1～3 分钟）。读完会弹窗告诉你',null];
  if(alsoFace===null)return ['先回答「读代码时发现一件事」','#sideask'];return ['看一眼「要你看一眼」里的步骤，没问题就点「确认，去插计时点」','[data-go="flash"]']}
 if(page==='flash'){if(fl.st===4)return ['没看到设备：用 USB 线插上设备，插上会自动继续','[data-fl="plug"]'];
  if(fl.st===2)return ['装好了，马上进入多轮实测',null];
  if(fl.st===1&&fl.step===4)return [F.big,'.bigask'];
  if(fl.st===1)return [`正在${FST()[fl.step][0]}，不用操作，做完会提示你`,null];
  if(flashVar==='b'&&(fl.st===3||fl.step>=2)&&!diffOk)return ['看一下改了什么，没问题就点「确认改动，编译并烧录」','[data-fl="ok"]'];
  if(fl.st===0)return [flashVar==='a'?'点「开始」':'点「开始插计时点」',flashVar==='a'?'[data-fl="all"]':'[data-fl="ins"]'];return null}
 if(page==='collect'){const C=CC();if(col.st===0)return ['看一眼要'+C.act+'的内容，点「开始实测」','[data-col="go"]'];
  if(col.st===1)return [`照大字提示${C.act}，每收好一轮会提示一次`,null];
  return [flow==='retest'?'测完了，点「查看前后对比」':'测完了，点「查看报告」','.acts [data-go="report"],.acts [data-go="compare"]']}
 if(page==='retest')return rtSrc<2?['告诉码表改好的代码在哪','.rtsrc']:['看码表比对出的变化，再点下面的按钮继续','[data-rtgo],[data-rtchoice]'];
 return null}
function stepInfo(){const C=CX(),F=FF[scene];const pos=(list,id)=>{const k=list.indexOf(id);return k<0?'':`第 ${k+1} 步 / 共 ${list.length} 步`};
 const L1=['home','steps','flash','collect','report'],L2=['retest','steps','flash','collect','compare'];
 const tag=flow==='retest'?'改完再测':'第一次测',P=flow==='retest'?pos(L2,page):pos(L1,page);
 const nxt=flow==='retest'?{retest:'按查出的情况，跳过不用做的步骤',steps:'插计时点、编译、烧到设备上',flash:'照提示一轮一轮触发设备，码表记下每轮用时',collect:'看前后对比：改动有没有用',compare:'这次测量结束'}:{home:'码表读代码，找出中间要插计时点的关键步骤',steps:'把计时点插进代码副本，编译、烧到设备上',flash:'照提示一轮一轮触发设备，码表记下每轮用时',collect:'看报告：哪一段慢、为什么、怎么改',report:'照建议改完后，点最下面的「改完了，再测一次」'};
 const T={home:['选固件，说清要测什么','告诉码表测哪个固件、从什么时候到什么时候。码表只读代码，不改你的文件。'],
  steps:['找关键步骤','大模型读代码，找出起点到终点中间经过的关键步骤，计时点就插在这些地方。找错一个，后面量的就不对，所以要你看一眼。'],
  flash:['插计时点和烧录','把计时点插进码表复制的那份代码，编译后烧到设备上，再确认设备真的在输出计时点。你原来的文件夹不动。'],
  collect:['多轮实测',`照提示一轮一轮${C.act}，码表记下每轮每一段用了多久。同样的内容测多轮，才看得出一般多快、偶尔多慢。`],
  report:['看报告','一般多快、偶尔多慢、慢在哪一段、为什么、怎么改。'],
  retest:['查改了什么','改完再测一次，和上次比。先告诉码表代码在哪，码表比对改了什么，能跳过的步骤自动跳过。'],
  compare:['前后对比','两次放在一起比，看改动有没有用。'],
  settings:['先把工具准备好','找关键步骤要用大模型，编译要用乐鑫编译工具。准备好再回去接着测。']};
 const t=T[page];if(!t)return null;
 if(page==='settings'&&hStage<1)return null;if((page==='report'||page==='compare')&&!flow)return null;
 const g=guide();
 const todo=g?g[0]:page==='report'?'从上往下看结论；要改的在方案表里勾选':page==='compare'?'先看最上面的结论':'';
 return {pos:page==='settings'?'中途准备工具':`${tag} · ${P}`,title:t[0],what:t[1],todo,next:page==='settings'?'回到刚才那一步接着走':nxt[page]}}
function guideBar(){const S=stepInfo();if(!S)return '';
 return `<section class="stepcard" aria-label="这一步"><div class="sc-hd"><span class="note">${S.pos}</span><b>${S.title}</b></div><dl><dt>这一步</dt><dd>${S.what}</dd><dt>你要做</dt><dd class="sc-todo">${S.todo}</dd><dt>做完以后</dt><dd>${S.next}</dd></dl></section>`}
let cd=0,cdT=null;
function startCd(){clearInterval(cdT);cd=3;cdT=setInterval(()=>{cd--;if(page!=='home'||hStage!==3){clearInterval(cdT);cdT=null;cd=0;return}if(cd<=0){clearInterval(cdT);cdT=null;hStage=4;render()}else render()},1000)}
// "找完了"里那一行：后台编原版的结果（找完窗口里写用时、后台编译结果和步骤清单）
function bgFoundSpan(){return bgFailed()?`<span><i class="dot" style="background:var(--bad)"></i>原版编译没通过，不是码表改的：你选的源码本身在这台电脑上编不过，烧录前要先解决（点右上角看报错）</span>`
 :!envComp()||bgWaitTool()?`<span><i class="dot" style="background:var(--warn)"></i>${inst.st===1?'编译工具正在后台装，装好就编原版':'编译工具还没装，原版没编：烧录前装好就行'}</span>`
 :!bgDone()?`<span><i class="dot" style="background:var(--warn)"></i>后台还在编原版：${bgPct()}%（不挡下一步，编译那步会等它）</span>`
 :`<span><i class="dot" style="background:var(--ok)"></i>后台已把原版编译通过（${BGTIME()}）</span>`}
const tookTxt=llm=>REAL&&IDENT?(IDENT.reused?'源码和要测的都没变，直接用了上次的结果，没调用大模型':`用时 ${mmss(IDENT.seconds)}，调用 1 次 ${llm}`)+(REAL.aiCalled?`；选源码时扫「AI 推荐」另调用了 1 次`:''):`用时 1 分 10 秒，调用 1 次 ${llm}`;
function modalHTML(){const F=FF[scene],llm=usedLLM();
 const meant=hSay===F.ai[0]?F.measure:hSay.replace(/^从/,'').replace('到',' → ');
 if(hStage===2)return `<h2 id="dlg-t">确认要测的内容</h2>
   <dl class="kv"><dt>项目名称</dt><dd><input type="text" id="h-pname" value="${pName}" style="width:100%;max-width:22em"></dd><dt>固件源码位置</dt><dd class="num">${hSrc}</dd><dt>芯片和板子</dt><dd>${CHIP()}</dd><dt>要测的</dt><dd>${hSay}</dd><dt>码表理解为</dt><dd><b>${meant}</b></dd></dl>
   <p class="note" style="margin:0">接下来码表把代码交给大模型（${llm}），找出中间经过的关键步骤，一般 1～3 分钟。</p>
   <div class="acts"><button class="btn" data-mclose="1" id="dlg-back">关闭</button><button class="btn pri" data-mfind="1">开始找关键步骤</button></div>`;
 if(hStage===3)return `<h2 id="dlg-t">${stepsRead===2?'找完了':stepsRead===3?'没找完':'正在找关键步骤'}</h2><p class="note" style="margin-top:0">${pName} · 要测：${meant}</p>${stepsRead===2?`<div class="status"><span><i class="dot" style="background:var(--ok)"></i>${tookTxt(llm)}</span>${bgFoundSpan()}</div>`:''}<div id="readbox">${readBox()}</div>
   <div class="acts" style="justify-content:flex-end"><button class="btn pri" ${stepsRead===2?'data-mnext="1"':blk('',stepsRead===3?'没找完，点「重试」找完后才能点':'AI 还在读代码，读完才能点下一步（一般 1～3 分钟）')}>下一步</button></div>`;
 const rows=STEPS,mid=rows.filter(x=>x.sure!=='高').length;
 return `<h2 id="dlg-t">找完了</h2><div class="status"><span><i class="dot" style="background:var(--ok)"></i>${tookTxt(llm)}</span>${bgFoundSpan()}</div>
   <p style="margin:10px 0 4px">找到 <b>${rows.length}</b> 个关键步骤：${mid} 个需要你核对，${rows.length-mid} 个可信度高。</p>
   <ol class="found">${rows.map(x=>`<li>${x.n} <span class="note">${x.f} 第 ${x.l} 行${x.line==='face'?' · '+F.ln.face:''}${x.sure!=='高'?' · <span style="color:var(--warn)">需核对</span>':''}</span></li>`).join('')}</ol>
   ${F.sideAsk?`<p class="note">另外发现：${F.sideAsk.found}。标「${F.ln.face}」的 ${STEPS.filter(x=>x.line==='face').length} 个是顺带发现的，默认也测，下一页可以去掉。</p>`:''}
   <div class="acts" style="justify-content:flex-end"><button class="btn pri" data-mnext="1">下一步</button></div>`}
function popHTML(){const F=FF[scene],C=CC();
 const box=(t,body,acts)=>`<h2>${t}</h2>${body}<div class="acts">${acts}</div>`;
 if(pop==='export'){const busy=ADV&&ADV.status==='running'&&repKey().startsWith('real');
  if(EXP.state==='saving')return box('导出报告','<p><span class="spin"></span> 正在存……</p>','');
  if(EXP.state==='ok')return box('导出报告',`<p>✓ 已导出：</p><p class="code" style="overflow-wrap:anywhere">${htmlEsc(EXP.saved)}</p>${EXP.partial?'<p class="note" style="color:var(--warn)">导出时"为什么、怎么改"还没写完，这份里没有建议。写完后再导出一份。</p>':''}`,`<button class="btn pri" data-hopen="${htmlEsc(EXP.saved)}">打开看看</button><button class="btn" data-pop="close">关闭</button>`);
  if(EXP.state==='fail')return box('导出报告',`<p style="color:var(--bad)">✕ 没导出成功：${htmlEsc(EXP.err)}</p>`,`<button class="btn pri" data-expgo="1">再试一次</button><button class="btn" data-pop="close">取消</button>`);
  if(EXP.state==='wait')return box('导出报告',`<p><span class="spin"></span> 等"为什么、怎么改"写完，写完马上自动导出。</p>`,`<button class="btn" data-expgo="1">不等了，现在导出</button><button class="btn" data-pop="close">取消</button>`);
  const advFail=ADV&&ADV.status==='failed'&&repKey().startsWith('real');
  return box('导出报告',`${busy?'<div class="why" style="border-left-color:var(--warn);margin:0 0 10px">AI 还在写"为什么、怎么改"。现在导出，只少这一部分，别的都一样。</div>':advFail?'<div class="why" style="border-left-color:var(--bad);margin:0 0 10px">"为什么、怎么改"没写成，导出的报告里会少这一部分。想要的话先在报告里点「重试」。</div>':''}<p class="note" style="margin:0 0 8px">存成一个网页文件：断网也能打开，可以直接发给别人。</p><dl class="kv" style="grid-template-columns:4em 1fr"><dt>存到</dt><dd><div class="inrow"><input type="text" id="exp-dir" value="${htmlEsc(tilde(EXP.dir))}" readonly><button class="btn" data-expdir="1">换个文件夹</button></div><p class="note" style="margin:4px 0 0">默认放在被测源码的同级目录。</p></dd><dt>文件名</dt><dd><span class="code">${htmlEsc((expWhat()||'报告').replace(/[\\/:*?"<>|\s]+/g,'-'))}_测完时间.html</span></dd></dl>`,busy?`<button class="btn pri" data-expwait="1">等写完自动导出</button><button class="btn" data-expgo="1">现在就导出</button><button class="btn" data-pop="close">取消</button>`:`<button class="btn pri" data-expgo="1">导出</button><button class="btn" data-pop="close">取消</button>`)}
 if(pop==='readlog')return box('读取过程',`<div class="status"><span><i class="dot" style="background:var(--ok)"></i>${tookTxt(usedLLM())}</span></div>${readBox()}`,`<button class="btn pri" data-pop="close">知道了</button>`);
 if(pop==='steps'){const n=live().length,mid=STEPS.filter(x=>x.sure!=='高').length;
  return box('找完了',`<p>找到 ${STEPS.length} 个关键步骤。接下来按顺序做三件事：</p><ol class="notes" style="font-size:15px;color:var(--fg)">${F.sideAsk?`<li>回答一个问题："${F.sideAsk.q}"要不要一起测</li>`:''}<li>看一眼 ${mid} 个需核对的步骤，对不对</li><li>点「确认，去插计时点」</li></ol>`,
   `<button class="btn pri" data-pop="close" data-nudge="#sideask" data-msg="先回答这个问题">开始核对</button>`)}
 if(pop==='flash')return box('设备准备好了',`<p>插计时点、编译、烧录都完成了。刚才试的那一次，${live().length} 个计时点都收到了。</p><p>下一步<b>多轮实测</b>：照提示${C.act} ${colN} ${C.unit}，码表记下每一${C.unit}里每一段用了多久，最后出报告。</p>`,
   `<button class="btn" data-pop="close">留在这页</button><button class="btn pri" data-go="collect">开始多轮实测</button>`);
 if(pop==='col'){const g=okRounds(),bad=col.got.length-g.length;const to=flow==='retest'&&rtC().steps[3][1]?['compare','查看前后对比']:['report','查看报告'];
  return box(`测完了 ${g.length} 轮`,`<p>${bad?`有 ${bad} 轮没收全，没算，已自动补收。`:'每轮都收全了。'}</p><p class="note">页面下方「测的够不够」写了要不要再测；不再收就直接看结果。</p>`,
   `<button class="btn" data-pop="close" data-nudge="#enough">先看够不够</button><button class="btn pri" data-go="${to[0]}">${to[1]}</button>`)}
 if(pop==='bg'&&bgFailed())return box('原版编译没通过，不是码表改的',`<p>${htmlEsc(String(BG.message||'').replace(/^原版编译没通过，不是码表改的：/,''))}</p><div class="ilog"><span class="note">报错</span><code>${htmlEsc(BG.error)}</code></div><p class="note">找关键步骤照常做；烧录前要先让原版编得过。</p>`,`<button class="btn pri" data-pop="close">知道了</button>`);
 if(pop==='bg'){const d=bgDone(),no=!envComp()||bgWaitTool();return box(d?'✓ 原始代码已编好':no?'后台编原始代码：等编译工具装好':`后台在编原始代码：${bgPct()}%`,`${d||no?'':`<span class="pbar" style="display:block;margin:0 0 12px"><i style="width:${bgPct()}%"></i></span>`}<ul class="notes" style="font-size:14px;color:var(--fg)">${BGTIP.split('<br>').map(x=>`<li>${x}</li>`).join('')}</ul>${no?'<p class="note">编译工具装好后自动开始，不用点。</p>':''}`,`<button class="btn pri" data-pop="close">知道了</button>`)}
 if(pop==='colre')return box('从头重测？',`<p>会清掉已测的 ${okRounds().length} 轮，从第 1 轮重新开始。</p><p class="note">只是想补几轮，用「接着测」就行。</p>`,`<button class="btn" data-pop="close">取消</button><button class="btn pri" data-col="restart2">清掉，从头测</button>`);
 if(pop==='inst')return box('编译工具装好了',`<p>乐鑫编译工具 v6.1 装好了。</p><p class="note">都准备好后，点设置页最下面的「保存」。</p>`,`<button class="btn pri" data-pop="close">知道了</button>`);
 if(pop==='env'){const m=missing(envBack==='flash'?'comp':null);return box(envBack==='flash'?'编译前还缺编译工具':'还缺这些环境',`<p>${envBack==='flash'?'插好计时点了，编译这个固件要用下面这样，你电脑上还没准备好：':`这个固件要用到下面 ${m.length} 样，你电脑上还没准备好：`}</p><ul class="notes" style="font-size:15px;color:var(--fg)">${m.map(([,t,w])=>`<li><b>${t}</b><br><span class="note">${w}</span></li>`).join('')}</ul>`,`<button class="btn" data-pop="close">关闭</button>${m.some(x=>!(x[0]==='set-idf'&&inst.st===1))?'<button class="btn pri" data-envgo="1">配置环境</button>':''}`)}
 return ''}
let flowMax=0;
function flowReset(){flowMax=0}
function stepsChanged(){if(flow==='first'&&flowMax>1){flowMax=1;flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;colStop();col={st:0,att:0,got:[],phase:0};toast('步骤改了：插计时点和烧录、多轮实测要重新做')}}
function flowBar(){if(!flow&&page!=='home')return '';let st;
 if(flow==='first'||!flow){if(!FLOW1.some(x=>x[0]===page))return '';st=FLOW1.map(([id,n])=>[id,n,0])}
 else{const C0=rtC(),pre=page==='retest'&&rtSrc<2,C={steps:C0.steps.map(x=>pre?[x[0],1]:x)};const ids=['retest','steps','flash','collect','compare'];if(!ids.includes(page))return '';st=[['retest','查改了什么',0],['steps','找关键步骤',!C.steps[0][1]],['flash','插计时点和烧录',!C.steps[1][1]],['collect','多轮实测',0],['compare','前后对比',!C.steps[3][1]]]}
 const ci=st.findIndex(x=>x[0]===page);if(flow&&ci>flowMax)flowMax=ci;const mx=flow?flowMax:ci;
 return `<ol class="flow" aria-label="测量步骤"><span class="note" style="margin-right:4px">${flow==='retest'?'改完再测':'第一次测'}：</span>${st.map(([id,n,sk],i)=>`<li>${sk?`<span class="fs skip">${n}（跳过）</span>`:i===ci?`<span class="fs cur ${page==='flash'&&(fl.st===5||fl.st===4)?'bad':''}">${page==='flash'&&(fl.st===5||fl.st===4)?'! ':i<mx?'✓ ':''}${n}</span>`:i<mx?`<button class="fs done" data-go="${id}" data-keepflow="1" title="回去看看，已做的不会清掉">✓ ${n}</button>`:i===mx?`<button class="fs goon" data-go="${id}" data-keepflow="1" title="回到做到一半的这一步">${n}（做到这里）</button>`:`<span class="fs">${n}</span>`}</li>`).join('')}</ol>${flow&&ci<mx?`<p class="note" style="margin:-4px 0 10px">在回看已做完的「${st[ci][1]}」，后面已做的都留着。只有在这里改了东西，后面的步骤才要重做。</p>`:''}`}
let RESTORE=null;
// 拔了线还原：插上后后台自动烧回原版，这里跟着看结果
let restoreT=null;
function restoreWatch(){clearInterval(restoreT);restoreT=setInterval(async()=>{try{const j=await (await fetch('/api/fsw')).json();const R=j.restore;if(R&&!R.waiting){clearInterval(restoreT);restoreT=null;RESTORE=R;onDev=R.flashed?'orig':onDev;toast((R.flashed?'✓ ':'✕ ')+(R.say||''));render()}}catch(e){}},1500)}
function restoreBox(sc){const r=restored[sc];if(r&&RESTORE&&realFlash()){const R=RESTORE;return `<section class="panel"><h2>设备上现在是：${R.flashed?'原版':'计时版（源码已还原）'}</h2><p class="note" style="margin:0">${R.same?`✓ 源码已还原，和你原来的源码逐字节比对一致（${R.files} 个文件）。`:R.same===false?`✕ 源码还原后和你原来的源码有 ${R.diff.length} 个文件不一样：${R.diff.map(htmlEsc).join('、')}。`:'源码已还原。'}${R.flashed?`<br>✓ 已把原版烧回设备（${htmlEsc(R.port)}）。`:R.waiting?'<br><span class="spin"></span> 设备没插着，原版还没烧回去：插上设备就自动烧，不用再点。':R.error?`<br><span style="color:var(--bad)">✕ ${htmlEsc((R.say||'原版没烧上').replace(/^源码已还原，/,''))}</span>${R.detail?`<details class="lvwhy"><summary>详情</summary><code>${htmlEsc(R.detail)}</code></details>`:''}`:'<br>原版还没烧回去。'}</p>${R.flashed||R.waiting?'':`<div class="acts" style="margin:8px 0 0"><button class="btn" data-restore="${sc}">还原成原版</button></div>`}</section>`}
 return `<section class="panel"><h2>设备上现在是：${r?'原版':'计时版'}</h2>${r?'<p class="note" style="margin:0">✓ 已还原成原版，和你的原版逐字节比对一致。</p>':`<p class="note" style="margin:0 0 8px">计时版每过一个计时点会多打印一行，交货、演示前请还原。<br>还原是直接烧读代码时编好的那份原版，烧完逐字节比对。还要接着测就先别还原。</p><div class="acts" style="margin:0"><button class="btn" data-restore="${sc}">还原成原版</button></div>`}</section>`}
function retestEntry(){return `<section class="panel"><h2>改完以后</h2><p class="note" style="margin:0 0 8px">照上面的建议改完代码、换了模型或升级了版本，点这里再测一次。<br>码表先查改了什么，能跳过的步骤自动跳过，测完直接和这次比。</p><div class="acts" style="margin:0"><button class="btn pri" data-retest="${scene}">改完了，再测一次</button></div></section>`}

function rtExtra(sc,k){const P=PROJ.find(p=>p.scene===sc),R=RT[sc],u=COLLECT[sc];const same=R.same;
 if(k==='gone')return {label:'文件夹没了',checks:[['bad','源码',`找不到 ${P.path}（可能挪走或删了）`],['est','计时点','没有源码，查不了'],['ok','设备','插着，上面还是上次烧的计时版']],
  route:'上次的固件文件夹找不到了。两条路，选一条：',choice:[['重新选文件夹','代码挪了地方就选新位置。码表会比对是不是同一个项目，不是会先问你。','home'],['直接用设备上的版本测','你这次只改了服务器或网络、没改固件时用。设备上已经是计时版，不用代码也能测。','collect']],
  steps:[['找关键步骤',0,'沿用上次确认的步骤（存在码表的项目文件夹里，不跟代码走）'],['插计时点和烧录',0,'直接用设备上现有的计时版'],['多轮实测',1,`同样 20 ${u.unit}${u.act}，句子清单也存在码表里，没丢`],['前后对比',1,`上一次（${R.last}）自动当"改前"`]],what:'固件没改（源码找不到，按你说的）。服务器那边：',whatHint:same.whatHint};
 if(k==='tool')return {label:'编译工具变了',checks:[['warn','源码','改了 1 个文件'],['ok','计时点','都在原位'],['warn','编译工具',`上次用 ESP-IDF v6.1，这次电脑上是 v6.2`],['warn','设备','代码改了，要重新烧']],
  route:'照样能测。编译工具换了版本，底层代码也跟着变了，对比时会单独标出来，变快变慢不一定都是你改的那一处带来的。',
  steps:R.code.steps,what:R.code.what+'另：编译工具 v6.1 → v6.2。',whatHint:R.code.whatHint};
 if(k==='nodata')return {label:'上次数据没了',checks:[['ok','源码','找到了'],['ok','计时点','都在原位'],['bad','上次数据',`上次（${R.last}）的测量数据找不到了（被删或挪走）`]],
  route:'没有上次的数据，就没法对比。改成按"第一次测"走：步骤、句子清单、合理范围还在，都沿用，测完出一份新报告。',
  steps:[['找关键步骤',0,'沿用上次确认的步骤'],['插计时点和烧录',1,'在现在的代码上插计时点、编译、烧录'],['多轮实测',1,`20 ${u.unit}，沿用句子清单`],['前后对比',0,'没有"改前"，测完看报告']],what:'',whatHint:'这次没法对比，这一栏可以不填。'};}
const RTVAR={same:{label:'固件没改',idea:'固件没改（比如只换了服务器的大模型）：跳过找步骤和烧录，直接多轮实测。'},code:{label:'改了固件',idea:'改了固件、计时点位置没动（比如照报告建议改了一处）：沿用步骤，AI 只看改过的地方，重新插点、烧录。'},up:{label:'升级了版本',idea:'升级了版本、计时点所在代码也变了：AI 把计时点接到新代码上，接不上的列出来让你确认。'},gone:{label:'文件夹没了',idea:'环境变了：上次的固件文件夹找不到。给两条路：重新选文件夹，或者直接用设备上的计时版测。'},tool:{label:'编译工具变了',idea:'环境变了：编译工具版本和上次不同。照样测，对比里单独标出来。'},nodata:{label:'上次数据没了',idea:'环境变了：上次的测量数据没了，改按"第一次测"走，出新报告。'}};
function rtC(){return RT[scene][rtCase]||rtExtra(scene,rtCase)}
let rtSrc=0,rtPath='',rtReading=false,rtOther=false;
function rtSrcPanel(P){const F=FF[scene],gone=rtCase==='gone';
 if(rtReading)return `<section class="panel rtsrc"><h2>改好的代码在哪？</h2><p><span class="spin"></span> 正在看 ${rtPath}：是不是同一个项目、和上次比改了什么……</p></section>`;
 if(rtOther)return `<section class="panel rtsrc" style="border-color:var(--warn)"><h2>这不像同一个项目</h2>
  <dl class="kv"><dt>你选的</dt><dd class="num">${rtPath}</dd><dt>仓库地址</dt><dd>上次是 ${F.repo}，这次是 github.com/someone/other-fw</dd><dt>项目配置</dt><dd>项目名字、板子都和上次不一样</dd></dl>
  <p class="note">码表认同一个项目看仓库地址和项目配置，不看文件夹名和路径。</p>
  <div class="acts" style="justify-content:flex-start"><button class="btn" data-rtsrc="reset">重新选</button><button class="btn" data-rtsrc="force">就是同一个项目，继续比对</button><button class="btn pri" data-rtchoice="home">当成新项目，从头测</button></div></section>`;
 return `<section class="panel rtsrc"><h2>改好的代码在哪？</h2><p class="note" style="margin-top:0">放在哪都行：原来的文件夹、新拉的分支目录、别人发来的压缩包都可以。<br>上次测的时候码表已经留了一份原版，比对"改了什么"用的是那一份。</p>
  ${gone?`<p style="color:var(--warn);margin:0 0 10px">上次的文件夹 ${P.path} 找不到了（挪走或删了）。</p>`:`<div class="inrow"><input type="text" value="${P.path}" readonly><button class="btn pri" data-rtsrc="last">就用上次这个文件夹</button></div><p class="note" style="margin:10px 0 6px">不在这里？</p>`}
  <div class="drop"><b>把改好的固件文件夹或压缩包拖到这里</b><div class="acts" style="justify-content:center;margin:8px 0 0"><button class="btn ${gone?'pri':''}" data-rtsrc="dir">选文件夹</button><button class="btn" data-rtsrc="zip">选压缩包</button></div></div>
  <div class="inrow" style="margin-top:8px"><input type="text" id="rt-paste" placeholder="或者粘贴路径"><button class="btn" data-rtsrc="paste">用这个路径</button></div>
  ${gone?`<p class="note" style="margin:12px 0 6px">这次没改固件、只改了服务器或网络？不用代码也能测：</p><button class="btn" data-rtchoice="collect">直接用设备上的计时版测</button>`:''}
  <div class="pills" style="margin-top:10px"><span class="note">演示：</span><button class="pill" data-rtsrc="other">拖进来的是别的项目</button></div></section>`}
function retestPage(){const P=PROJ.find(p=>p.scene===scene),R=RT[scene];
 if(rtSrc<2)return `<div class="stack"><section class="panel"><h1 style="font-size:22px">改完再测：${P.name}</h1><p class="note" style="margin:0">和上一次（${R.last}）比。测的还是"${P.what}"。</p></section>${rtSrcPanel(P)}</div>`;
 const C0=rtC(),C={...C0,checks:[['ok','代码',`${rtPath}（认出是同一个项目：仓库地址、项目配置都对得上）　<button class="pill" data-rtsrc="reset">换一个</button>`],...C0.checks]};
 const dot=c=>`<i class="dot" style="background:var(--${c})"></i>`;
 const conf=C.confirm?`<section class="panel"><h2>接到新版本时，要你看一眼的计时点</h2><table><thead><tr><th>情况</th><th>计时点</th><th>变化</th><th>可信度</th><th></th></tr></thead><tbody>${C.confirm.map(([k,n,d,b])=>`<tr><td><span class="chip ${k==='要你确认'?'c-warn':'c-ok'}">${k}</span></td><td>${n}</td><td>${d}</td><td>${b}</td><td><button class="pill" data-go="steps">${k==='要你确认'?'去确认':'查看'}</button></td></tr>`).join('')}</tbody></table><p class="note" style="margin:8px 0 0">其余计时点原样接上，不用看。<br>确认后如果这个点量的意思变了，前后对比里这一段标"量法不同，不比"，其他段照比。</p></section>`:'';
 return `<div class="stack"><section class="panel"><h1 style="font-size:22px">改完再测：${P.name}</h1><p class="note" style="margin:0">和上一次（${R.last}）比。测的还是"${P.what}"。</p></section>
 <section class="panel"><h2>码表先查了一遍</h2><p class="note" style="margin:-4px 0 8px">只比对文件，不用大模型，几秒钟。</p><dl class="kv" style="grid-template-columns:4em 1fr">${C.checks.map(([c,k,v])=>`<dt>${k}</dt><dd>${dot(c)}${v}</dd>`).join('')}</dl></section>
 <section class="panel"><h2>所以这次这样走</h2><p style="margin:0 0 8px">${C.route}</p>${C.choice?C.choice.map(([n,d,to])=>`<div class="rtstep" style="grid-template-columns:12em 1fr"><button class="btn ${to==='collect'?'pri':''}" data-rtchoice="${to}">${n}</button><span class="note">${d}</span></div>`).join('')+'<p class="note" style="margin:12px 0 4px"><b>选"直接用设备上的版本测"时这样走：</b></p>':''}${C.steps.map(([n,d,r])=>`<div class="rtstep ${d?'':'skip'}"><b>${n}</b><span class="chip ${d?'c-ok':'c-est'}">${d?'要做':'跳过'}</span><span class="note">${r}</span></div>`).join('')}</section>
 ${conf}
 <section class="panel"><h2>这次改了什么</h2><p class="note" style="margin:-4px 0 8px">${C.whatHint}<br>会写进前后对比和报告。</p><textarea class="rtta" id="rt-what">${C.what}${C.what.endsWith('：')?RT[scene].whatFill:''}</textarea>
  <div class="acts">${C.choice?'':`<button class="btn pri" data-rtgo="1">${C.steps[1][1]?(C.steps[0][1]?'先确认计时点，再插点烧录':'开始插点、烧录'):'开始多轮实测'}</button>`}<button class="btn" data-go="report">不测了，回报告</button></div></section></div>`}
// 只改变了的地方（决策 103）：页面每隔零点几秒就要更新一次（读秒、进度、收到的轮），以前整页重画，
// 再加上换页的淡入动画，整页一直闪；输入框也会丢掉光标。现在新旧对比，只改动不一样的节点。
function morphAttrs(a,b){for(const {name} of [...a.attributes])if(!b.hasAttribute(name)&&!(a.tagName==='DETAILS'&&name==='open'))a.removeAttribute(name);
 for(const {name,value} of [...b.attributes])if(a.getAttribute(name)!==value)a.setAttribute(name,value)}
function morphNode(a,b){
 if(a.nodeType!==b.nodeType||a.nodeName!==b.nodeName){a.replaceWith(b);return}
 if(a.nodeType!==1){if(a.nodeValue!==b.nodeValue)a.nodeValue=b.nodeValue;return}
 if(a.isEqualNode(b))return;
 morphAttrs(a,b);
 if(a.tagName==='INPUT'||a.tagName==='TEXTAREA'){if(a!==document.activeElement){if(a.type==='checkbox'||a.type==='radio')a.checked=b.hasAttribute('checked');else if(b.hasAttribute('value'))a.value=b.getAttribute('value')}return}
 morphKids(a,b);
 if(a.tagName==='SELECT'&&a!==document.activeElement){const o=[...b.options].findIndex(x=>x.hasAttribute('selected'));if(o>=0)a.selectedIndex=o}}
function morphKids(a,b){const x=[...a.childNodes],y=[...b.childNodes];
 y.forEach((n,i)=>{if(x[i])morphNode(x[i],n);else a.appendChild(n)});
 for(let i=y.length;i<x.length;i++)x[i].remove()}
function morphHTML(el,html){const t=document.createElement(el.tagName);t.innerHTML=html;morphKids(el,t)}
function render(){if(!scene||!SCENES[scene])return;applyScene();
  if(page==='report'&&(!JREP||JREP._key!==repKey()))fetchReport();
  if(page==='report'&&JREP&&JREP._key===repKey()&&repKey().startsWith('real')&&!ADV&&D0ok())advStart();
  const D=build(page==='report'&&JREP&&JREP._key===repKey()&&!JREP._none?repScene():SCENES[scene]);
  document.getElementById('nav-home').toggleAttribute('aria-current',page==='home');document.getElementById('nav-set').toggleAttribute('aria-current',page==='settings');
  morphHTML(document.getElementById('flowbar'),flowBar());bgPill();
  const isReport=page==='report',isHome=page==='home';
  document.getElementById('verseg').hidden=!isReport;document.getElementById('v-exp').hidden=!isReport;
  const PN=realFlash()?htmlEsc(pName):(PROJ.find(p=>p.scene===scene)||{}).name;morphHTML(document.getElementById('crumb'),isReport?`${flow?'':'<button class="lnk2" data-go="home">首页</button> › '}${String(D.S.crumb).replace(/\d+ 轮\s*$/,D.n+' 轮')}${D.S.real?'':'<span class="mock">模拟数据，不是实测</span>'}`:page==='range'?`<button class="lnk2" data-go="report" data-keepflow="1">${PN} 的报告</button> › 合理范围`:page==='compare'&&!flow?`<button class="lnk2" data-go="home">首页</button> › ${PN} › 前后对比`:'');
  let body;
  if(isHome)body=HOME[homeVar].html();
  else if(isReport)body=reportPage(D);
  else if(page==='settings')body=settingsPage();
  else if(page==='steps')body=stepsPage();
  else if(page==='flash')body=flashPage();
  else if(page==='collect')body=collectPage();
  else if(page==='compare')body=comparePage();
  else if(page==='retest')body=retestPage();
  else if(page==='range')body=rangePage();
  else body=`<div class="panel todo">这一页还没做样子，按顺序下一步做。</div>`;
  const mEl=document.getElementById('main'),same=mEl.dataset.pg===page+'|'+mode&&mEl.firstElementChild;
  if(same)morphHTML(mEl.firstElementChild,body);else mEl.innerHTML=`<div class="view">${body}</div>`;  // 换页才整页换（带淡入），同一页只改变了的地方
  if(mEl.dataset.pg!==page+'|'+mode)mEl.dataset.pg=page+'|'+mode;
  const md=document.getElementById('modal');md.hidden=!(isHome&&hStage>=2);
  if(!md.hidden)morphHTML(document.getElementById('dlg'),modalHTML());
  const pp=document.getElementById('pop');pp.hidden=!pop;if(pop)morphHTML(document.getElementById('popdlg'),popHTML());
  document.querySelectorAll('[data-need]').forEach(b=>{if(b.previousElementSibling&&b.previousElementSibling.classList.contains('gate'))return;const g=document.createElement('span');g.className='gate';g.textContent='还差：'+b.dataset.msg;b.before(g)});
  {const m=document.getElementById('main');const need=page==='steps'&&stepsRead===2&&alsoFace===null?'#sideask':null;const el=need&&m.querySelector(need);if(el)el.classList.add('need')}
  document.getElementById('v-boss').setAttribute('aria-pressed',String(mode==='boss'));
  document.getElementById('v-dev').setAttribute('aria-pressed',String(mode==='dev'));
  syncFlow();
}
// 步骤条同步给后台：后台记下走到哪一步、哪步出错、哪些跳过（刷新网页、以后接真功能都靠它）
let lastKind='',lastSync='';
function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
function syncFlow(){if(!flow)return;
 const ids=flow==='retest'?['retest','steps','flash','collect','compare']:FLOW1.map(x=>x[0]);
 let skipped=[];if(flow==='retest'&&!(page==='retest'&&rtSrc<2)){const C=rtC();skipped=[['steps',!C.steps[0][1]],['flash',!C.steps[1][1]],['compare',!C.steps[3][1]]].filter(x=>x[1]).map(x=>x[0])}
 const err=page==='flash'&&(fl.st===4||fl.st===5)?(fl.st===4?'没看到设备':{build:'编译没通过',burn:'烧录中断',try:'试一次没收到计时点'}[fl.err]):null;
 const b={reached:flowMax,viewing:ids.includes(page)?page:null,error:err,skipped},k=JSON.stringify(b),kind=flow+'/'+scene;
 if(k===lastSync&&kind===lastKind)return;lastSync=k;
 (kind!==lastKind?post('/api/flow/start',{kind:flow,demo:scene}):Promise.resolve()).then(()=>post('/api/flow/sync',b)).catch(()=>{});lastKind=kind}
function tipShow(e){const el=e.target.closest&&e.target.closest('[data-tip]'),T=document.getElementById('tip');if(!el){T.hidden=true;return}T.innerHTML=el.dataset.tip;T.hidden=false;const r=el.getBoundingClientRect(),w=T.offsetWidth,h=T.offsetHeight;let x=r.left+r.width/2-w/2,y=r.top-h-8;if(y<4)y=r.bottom+8;x=Math.max(4,Math.min(x,innerWidth-w-4));T.style.left=x+'px';T.style.top=y+'px'}
document.addEventListener('mouseover',tipShow);document.addEventListener('focusin',tipShow);document.addEventListener('scroll',()=>{document.getElementById('tip').hidden=true},true);
document.addEventListener('click',e=>{
  const nd=e.target.closest('[data-need]');if(nd){if(nd.dataset.needgo&&nd.dataset.needgo!==page){if(hStage>=2)hStage=1;page=nd.dataset.needgo;render();window.scrollTo(0,0);return}const g=nd.previousElementSibling;if(g&&g.classList.contains('gate'))g.classList.add('hit');nudge(nd.dataset.need);return}
  if(e.target.closest('#pop button')){pop=null;document.getElementById('pop').hidden=true}
  if(e.target.closest('[data-envgo]')){envBack=page==='flash'?'flash':'home';envMark=true;hStage=Math.min(hStage,1);page='settings';render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-save]')){const sb=e.target.closest('[data-save]');const ki=document.getElementById('set-key'),kv=ki?ki.value.trim():'';sb.disabled=true;
   fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({llm_provider:setLLM,key:setLLM!=='cc'&&kv&&!kv.startsWith('•')?kv:null,data_dir:dataDraft,reuse:reuseLast})}).then(async r=>{const j=await r.json();if(!r.ok){sb.disabled=false;toast('✕ '+(j.detail||'没保存上'));return}
   SAVED=j.settings;keyDraft='';dataDraft=null;compPending=inst.st===1;envMark=false;toast(compPending?'✓ 已保存。编译工具还在后台装，装好自动变绿':'✓ 保存成功');const back=hSrc?envBack:'home';envBack='home';setTimeout(()=>{page=back;if(hStage>=2)hStage=1;render();window.scrollTo(0,0)},compPending?1800:1000)}).catch(()=>{sb.disabled=false;toast('✕ 码表后台没有回应，看看运行码表的窗口是不是关了')});return}
  if(e.target.closest('[data-cc]')){CCST=null;render();fetch('/api/env').then(r=>r.json()).then(j=>{CCST=j.claude_code;render()});return}
  const ib=e.target.closest('[data-idf]');if(ib){idfErr='';if(ib.dataset.idf==='find'){ib.textContent='正在查找……';fetch('/api/env').then(r=>r.json()).then(j=>{IDFS=(j.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);toast(IDFS.length?`✓ 找到 ${IDFS.length} 个`:'没找到乐鑫编译工具');render()});return}
   api('/api/source/pick',{kind:'dir'}).then(async p=>{if(!p.path)return;const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({idf_path:p.path})});const j=await r.json();if(!r.ok){idfErr=j.detail;render();return}
    SAVED=j.settings;const e2=await (await fetch('/api/env')).json();IDFS=(e2.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);toast('✓ 已记下这个位置');render()});return}
  if(e.target.closest('[data-datadir]')){api('/api/source/pick',{kind:'dir'}).then(p=>{if(p.path){dataDraft=p.path;render()}});return}
  if(e.target.closest('[data-mclose]')){hStage=1;render();return}
  if(e.target.closest('[data-mfind]')){flowReset();const pn=document.getElementById('h-pname');if(pn&&pn.value.trim())pName=pn.value.trim().replace(/[<>&"]/g,'');flow='first';clearInterval(readT);readT=null;stepsRead=0;alsoFace=true;dropped=new Set();edits={};added=[];flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';onDev='orig';hStage=3;readStart();render();return}
  if(e.target.closest('[data-cdskip]')){clearInterval(cdT);cdT=null;cd=0;hStage=4;render();return}
  if(e.target.closest('[data-mnext]')){clearInterval(cdT);cdT=null;cd=0;hStage=1;page='steps';mode='boss';render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-goins]')&&realFlash()){page='flash';flashVar='b';flStop();flNoDev=false;fswInsert();render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-goins]')){page='flash';flashVar='b';flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';diffOpen=new Set();flRun(0,1);render();window.scrollTo(0,0);return}
  const dop=e.target.closest('[data-dopen]');if(dop){const k=dop.dataset.dopen;diffOpen.has(k)?diffOpen.delete(k):diffOpen.add(k);render();return}
  const del=e.target.closest('[data-del]');if(del){stepsChanged();dropped.add(del.dataset.del);if(editP===del.dataset.del)editP=null;render();return}
  const und=e.target.closest('[data-undel]');if(und){dropped.delete(und.dataset.undel);render();return}
  const ep=e.target.closest('[data-editp]');if(ep){editP=editP===ep.dataset.editp?null:ep.dataset.editp;render();const el=document.getElementById('ed-f');if(el){el.scrollIntoView({block:'center'});el.focus()}return}
  const es=e.target.closest('[data-edsave]');if(es){const x=allRows().find(r=>r.p===es.dataset.edsave),fv=(document.getElementById('ed-f').value||'').trim().replace(/[<>&"]/g,''),lv=parseInt(document.getElementById('ed-l').value,10);if(x&&fv&&lv>0){stepsChanged();x.f=fv;x.l=lv;edits[x.p]=true}editP=null;render();return}
  if(e.target.closest('[data-edcancel]')){editP=null;render();return}
  const al=e.target.closest('[data-addline]');if(al){const i=document.getElementById('add-n');if(i)addName=i.value;addLine=al.dataset.addline;render();return}
  const ad=e.target.closest('[data-add]');if(ad){const a=ad.dataset.add,i=document.getElementById('add-n');if(i)addName=i.value.trim().replace(/[<>&"]/g,'');
   if(a==='open'){addSt=1;addManual=false}else if(a==='cancel'){addSt=0;addName=''}else if(a==='manual'){addManual=true}
   else if(a==='ai'){if(!addName){i&&i.focus();i&&i.classList.add('need');return}addSt=2;
    if(REAL&&REAL.work){addErr='';api('/api/identify/add',{work:REAL.work,sentence:addName,what:hSay}).then(j=>{if(j.ok){addGuess={f:j.f,l:j.l,why:j.why,path:j.path};addSt=3}else{addErr=j.message||j.detail||'没找到';addSt=1}if(page==='steps')render()}).catch(()=>{addErr='码表后台没有回应，看看运行码表的窗口是不是关了。';addSt=1;render()})}
    else setTimeout(()=>{addSt=3;if(page==='steps')render()},1400)}
   else if(a==='ok'||a==='manualok'){const g=REAL&&addGuess?addGuess:FF[scene].sideAsk.addGuess;let f=g.f,l=g.l;if(a==='manualok'){f=(document.getElementById('add-f').value||'').trim().replace(/[<>&"]/g,'');l=parseInt(document.getElementById('add-l').value,10);if(!addName||!f||!(l>0))return}
    stepsChanged();added.push({p:'U'+(added.length+1),n:addName||'你加的步骤',f,l,line:addLine,why:'你加的步骤',sure:'你加'});addSt=0;addName='';addManual=false}
   render();return}
  if(e.target.closest('[data-bgpop]')){pop='bg';render();return}
  const pc=e.target.closest('[data-pop]');if(pc){render();if(pc.dataset.nudge)nudge(pc.dataset.nudge);return}
  const g=e.target.closest('[data-go]');if(g){const to=g.dataset.go;const pn=document.getElementById('h-pname');if(pn&&pn.value.trim())pName=pn.value.trim().replace(/[<>&"]/g,'');if(hStage>=2&&to==='steps'){flow='first';clearInterval(readT);readT=null;stepsRead=0;alsoFace=true;dropped=new Set();flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';onDev='orig'}if(hStage>=2)hStage=1;if((to==='home'||to==='settings')&&!g.dataset.keepflow)flow=flow&&to==='settings'?flow:null;page=to;if(!g.dataset.keepflow)mode='boss';render();window.scrollTo(0,0);return}
  const rt=e.target.closest('[data-retest]');if(rt){flowReset();scene=rt.dataset.retest;rtSrc=0;rtOther=false;rtReading=false;rtPath='';page='retest';rtCase=RTCASE;flow='retest';render();window.scrollTo(0,0);return}
  const rv2=e.target.closest('[data-rtvar]');if(rv2){rtCase=rv2.dataset.rtvar;rtSrc=0;rtOther=false;render();return}
  const rs2=e.target.closest('[data-rtsrc]');if(rs2){const a=rs2.dataset.rtsrc,P=PROJ.find(p=>p.scene===scene),F=FF[scene];
   if(a==='reset'){rtSrc=0;rtOther=false;render();return}
   if(a==='force'){rtOther=false;rtSrc=2;render();return}
   if(a==='paste'){const v=(document.getElementById('rt-paste').value||'').trim().replace(/[<>&"]/g,'');if(!v){nudge('#rt-paste');return}rtPath=v}
   else rtPath=a==='last'?P.path:a==='zip'?`~/Downloads/${F.slug}-fix.zip`:a==='other'?'~/Downloads/other-fw':`~/work/${F.slug}-fix`;
   rtReading=true;render();setTimeout(()=>{rtReading=false;if(a==='other')rtOther=true;else{rtSrc=2;if(rtCase==='gone')rtCase='code'}if(page==='retest')render()},900);return}
  const rch=e.target.closest('[data-rtchoice]');if(rch&&rch.dataset.rtchoice==='home'){flow=null;page='home';hStage=0;hSrc='';render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-rtgo]')||rch){flow='retest';const t=document.getElementById('rt-what');rtWhat=t?t.value:null;const C=rtC();page=C.steps[0][1]?'steps':C.steps[1][1]?'flash':'collect';colStop();col={st:0,att:0,got:[],phase:0};if(page==='flash'){diffOk=false;fl={st:0,step:0,pct:0,log:''}}render();window.scrollTo(0,0);return}
  const o=e.target.closest('[data-open]');if(o){scene=o.dataset.open;flow=null;page='report';mode='boss';render();window.scrollTo(0,0);return}
  const v=e.target.closest('[data-var]');if(v){homeVar=v.dataset.var;save('fsw-home',homeVar);render();return}
  const pk=e.target.closest('button[data-pick]');if(pk){const a=pk.dataset.pick;  // 只认首页选源码的按钮（报告里方案的勾选框也叫 data-pick）
   if(a==='reset'){clearInterval(bgT);bgT=null;bgSrc='';hStage=0;hSrc='';hScan=0;REAL=null;hBoardQ='';render();return}
   if(a==='paste'){const v=(document.getElementById('h-paste').value||'').trim();if(!v){hErr='先粘贴一个路径。';render();return}realPick({path:v});return}
   api('/api/source/pick',{kind:a==='zip'?'zip':'dir'}).then(r=>{if(r.path)realPick({path:r.path})}).catch(()=>{hErr='打不开选择窗口，可以把路径粘贴到下面的框里。';render()});return}
  const pr=e.target.closest('[data-pickroot]');if(pr){realPick({path:pr.dataset.pickroot});return}
  const rs=e.target.closest('[data-restore]');if(rs&&realFlash()&&rs.closest('#main')&&page==='report'){rs.disabled=true;rs.textContent='正在还原……';  // 决策 109：一定问后台，结果说清楚
   api('/api/fsw/restore',{project_dir:REAL.project_dir}).then(j=>{if(j.detail){toast('✕ '+j.detail);rs.disabled=false;rs.textContent='还原成原版';return}
    RESTORE=j;restored[rs.dataset.restore]=true;onDev=j.flashed?'orig':onDev;toast((j.flashed?'✓ ':j.waiting?'':'✕ ')+(j.say||'已还原'));render();if(j.waiting)restoreWatch()}).catch(()=>{toast('✕ 码表后台没有回应，看看运行码表的窗口是不是关了');rs.disabled=false;rs.textContent='还原成原版'});return}
  if(rs){restored[rs.dataset.restore]=true;render();return}
  const sgo=e.target.closest('[data-setgo]');if(sgo){page='settings';render();setTimeout(()=>{const el=document.getElementById(sgo.dataset.setgo);if(el){el.scrollIntoView({block:'start'});el.classList.add('flash')}},30);return}
  const sg=e.target.closest('[data-stage]');if(sg){const i=document.getElementById('h-what');const n=+sg.dataset.stage;if(n===2){if(REAL&&!REAL.chip){hBoardNeed=true;render();const b=document.getElementById('h-boardq');b&&b.focus();return}const v=(i&&i.value.trim())||'';if(!v){hNeed=true;render();return}hNeed=false;hSay=v.replace(/[<>&"]/g,'');if(!envKey()){pop='env';render();return}}hStage=n;render();return}
  const os=e.target.closest('[data-os]');if(os){setOS=os.dataset.os;if(inst.st!==2){instStop();inst={st:0,step:0,pct:0,log:'',timer:null}}render();return}
  const ins=e.target.closest('[data-inst]');if(ins){const a=ins.dataset.inst;if(a==='manual'){inst.manual=true;render();const m=document.getElementById('manual');m&&m.scrollIntoView({block:'center'});return}instStart(a==='retry');render();return}
  const svr=e.target.closest('[data-stepvar]');if(svr){stepsVar=svr.dataset.stepvar;render();return}
  const fc=e.target.closest('[data-face]');if(fc){const a=fc.dataset.face;if(a!=='reset')stepsChanged();alsoFace=a==='reset'?null:a==='yes';render();return}
  const mp=e.target.closest('[data-map]');if(mp){mapSel=mp.dataset.map;mapOpen=true;render();return}
  const mt=e.target.closest('[data-mapt]');if(mt){mapOpen=!mapOpen;return}
  if(e.target.closest('[data-advretry]')){advStart();return}
  const rr=e.target.closest('[data-reread]');if(rr){if(REAL&&IDENT){pop='readlog';render();return}stepsRead=0;render();return}
  if(e.target.closest('[data-idretry]')){identStart(true);render();return}
  const fv=e.target.closest('[data-flashvar]');if(fv){flashVar=fv.dataset.flashvar;flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';render();return}
  const fa=e.target.closest('[data-fl]');if(fa){const a=fa.dataset.fl;
   if(realFlash()){if(a==='ok'){if(!envComp()){envBack='flash';pop='env';render();return}diffOk=true;fswPost('/api/fsw/run',{from:'build'})}else if(a==='redo'||a==='ins'){if(flow==='first'&&flowMax>2){flowMax=2;colStop();col={st:0,att:0,got:[],phase:0}}fswInsert()}render();return}
   let a2=a;if(a==='nodev'){flNoDev=true;a2=flashVar==='b'?'ins':'all'}
   if(a2==='all'&&!envComp()){envBack='flash';pop='env';render();return}if(a2==='all')flRun(0,4);else if(a2==='ins')flRun(0,1);else if(a2==='ok'){if(!envComp()){envBack='flash';pop='env';render();return}diffOk=true;flRun(2,4)}else if(a2==='redo'){if(flow==='first'&&flowMax>2){flowMax=2;colStop();col={st:0,att:0,got:[],phase:0}}diffOk=false;diffOpen=new Set();flRun(0,1)}else if(a2==='plug'){flNoDev=false;flRun(3,flashVar==='c'?3:4)}
   render();return}
  const de=e.target.closest('[data-demoerr]');if(de){const k=de.dataset.demoerr;flNoDev=k==='nodev';demoErr=k==='nodev'?'':k;if(page==='home'&&k){hErr=''}render();return}
  const fx=e.target.closest('[data-flx]');if(fx&&realFlash()&&FSWJ){const a=fx.dataset.flx;
   if(a==='drop'){const sid=FSWJ.err.step,x=live().find(r=>fswId(r.p)===sid);stepsChanged();diffOk=true;if(x)dropped.add(x.p);flowMax=Math.max(flowMax,2);toast('已撤掉这处计时点，重新编译');fswPost('/api/fsw/drop',{step:sid})}
   else if(a==='move'){const sid=FSWJ.err.step,x=live().find(r=>fswId(r.p)===sid);editP=x?x.p:null;fl={st:0,step:0,pct:0,log:''};diffOk=false;page='steps';render();window.scrollTo(0,0);setTimeout(()=>{const el=document.getElementById('ed-f');if(el){el.scrollIntoView({block:'center'});el.focus()}},50);return}
   else if(a==='rebuild')fswPost('/api/fsw/run',{from:'build'});else if(a==='reburn')fswPost('/api/fsw/run',{from:'flash'});else if(a==='retry'||a==='trygo')fswPost('/api/fsw/run',{from:'try'});
   render();return}
  if(fx){const a=fx.dataset.flx;demoErr='';
   if(a==='drop'){const L=live();stepsChanged();diffOk=true;dropped.add(L[Math.min(2,L.length-1)].p);flowMax=Math.max(flowMax,2);toast('已撤掉这个计时点，重新编译');flRun(2,4)}
   else if(a==='move'){const L=live();editP=L[Math.min(2,L.length-1)].p;fl={st:0,step:0,pct:0,log:''};diffOk=false;page='steps';render();window.scrollTo(0,0);setTimeout(()=>{const el=document.getElementById('ed-f');if(el){el.scrollIntoView({block:'center'});el.focus()}},50);return}
   else if(a==='reburn')flRun(3,4);else if(a==='retry')flRun(4,4);
   render();return}
  const df=e.target.closest('[data-df]');if(df){diffFile=+df.dataset.df;render();return}
  const od=e.target.closest('[data-ondev]');if(od){onDev=od.dataset.ondev;flRun(2,3);render();return}
  const rv=e.target.closest('[data-rngvar]');if(rv){rngVar=rv.dataset.rngvar;rngAsk=0;render();return}
  const ra=e.target.closest('[data-rng]');if(ra){const a=ra.dataset.rng;rngAsk=a==='ask'?1:a==='ok'?2:0;if(a==='ok')stale=1;render();return}
  if(e.target.closest('[data-stale]')){if(repKey().startsWith('real')&&JREP&&JREP.stale){const since=JREP.stale_since,key=JREP._key;api('/api/report/refresh',{}).then(j=>{JREP={...j,_key:key,_refreshed:since};ADV=null;fixPick=null;render()});return}stale=2;render();return}
  if(e.target.closest('[data-openlog]')){if(repKey().startsWith('real')&&JREP&&JREP.raw_log)api('/api/open-folder',{path:JREP.raw_log}).then(j=>toast(j.ok?'✓ 已在访达里打开':'✕ '+(j.detail||'打不开')));else toast('演示数据没有原始日志文件：真测完才有');return}
  const cp=e.target.closest('[data-copy]');if(cp){const el=document.getElementById(cp.dataset.copy);if(el){try{navigator.clipboard.writeText(el.textContent)}catch(_){};copied=el.dataset.who;render();setTimeout(()=>{copied=null;if(page==='report')render()},1500)}return}
  if(e.target.closest('#v-exp')){exportStart();return}
  if(e.target.closest('[data-expdir]')){api('/api/source/pick',{kind:'dir'}).then(p=>{if(p&&p.path){EXP.dir=p.path;render()}});return}
  if(e.target.closest('[data-expgo]')){pop='export';EXP.state='saving';render();exportReport(EXP.dir);return}  // 对话框留着，存好、没存成都在框里说
  if(e.target.closest('[data-expwait]')){pop='export';EXP.state='wait';render();return}
  if(e.target.closest('[data-expshow]')){api('/api/open-folder',{path:EXP.saved}).then(j=>toast(j.ok?'✓ 已在访达里打开':'✕ '+(j.detail||'打不开')));return}
  if(e.target.closest('[data-expclose]')){EXP.saved=null;render();return}
  const hr=e.target.closest('[data-hrestore]');if(hr){hr.disabled=true;hr.textContent='正在还原……';  // 码表重开过也认得：按项目文件夹里存的插入记录还原
   api('/api/fsw/restore',{project_dir:hr.dataset.hrestore}).then(j=>{toast(j.detail?'✕ '+j.detail:(j.flashed?'✓ ':j.waiting?'':'✕ ')+(j.say||'已还原'));if(j.waiting)restoreWatch();histLoad().then(render)});return}
  const ho=e.target.closest('[data-hopen]');if(ho){api('/api/history/open',{path:ho.dataset.hopen}).then(j=>toast(j.ok?'✓ 已用浏览器打开这次的报告':'✕ '+(j.detail||'打不开')));return}
  const jp=e.target.closest('[data-jump]');if(jp){const id=jp.dataset.jump;if(id.startsWith('fix-')&&repVar==='c'){const F=activeFixes(build(SCENES[scene]))[+id.slice(4)];if(F){segSel=F.seg;render()}}const el=document.getElementById(id);if(el){el.scrollIntoView({behavior:'smooth',block:'center'});el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash')}return}
  const rpv=e.target.closest('[data-repvar]');if(rpv){repVar=rpv.dataset.repvar;render();return}
  if(e.target.closest('[data-fulllog]')){showFull=!showFull;render();return}
  const td=e.target.closest('[data-todo]');if(td){fixTodo=!fixTodo;render();return}
  const sgb=e.target.closest('[data-seg]');if(sgb){segSel=sgb.dataset.seg;render();return}
  const cmv=e.target.closest('[data-cmpvar]');if(cmv){cmpVar=cmv.dataset.cmpvar;render();return}
  const cv=e.target.closest('[data-colvar]');if(cv){colVar=cv.dataset.colvar;render();return}
  if(e.target.closest('[data-beeptest]')){unlockAudio();beepBlocked=false;ding(false);setTimeout(()=>{beepMsg=actx?actx.state:'none';beepLog('试听');render()},400);return}
  if(e.target.closest('[data-beep]')){beep=!beep;render();return}
  const cn=e.target.closest('[data-coln]');if(cn){colN=+cn.dataset.coln;render();return}
  const ca=e.target.closest('[data-col]');if(ca){unlockAudio();const a=ca.dataset.col;if(a==='go')colStart();else if(a==='more'){colN+=10;colStart(true)}else if(a==='stop'){colStop();col.st=3;api('/api/collect/stop',{}).then(colApply)}else if(a==='resume'){colStart(true)}else if(a==='restart'){pop='colre'}else if(a==='restart2'){colStart(false)}render();return}
  if(e.target.closest('[data-resume]')){page='home';hStage=2;render();window.scrollTo(0,0);return}
  const tk=e.target.closest('[data-test]');if(tk){const i=document.getElementById('set-key'),v=i?i.value.trim():'';keyErr='';if(v&&!v.startsWith('•'))keyDraft=v;tk.disabled=true;tk.textContent='正在测试……';
   api('/api/llm/test',{provider:setLLM,key:v&&!v.startsWith('•')?v:null}).then(r=>{keyTested=!!r.ok;keyErr=r.ok?'':r.message;render()}).catch(()=>{keyErr='码表后台没有回应，看看运行码表的窗口是不是关了。';render()});return}
  const fil=e.target.closest('[data-fill]');if(fil){const i=document.getElementById('h-what');if(i)i.value=fil.dataset.fill;hSay=fil.dataset.fill;return}
});
document.addEventListener('input',e=>{if(e.target.id==='h-boardq'){hBoardQ=e.target.value;hBoardOpen=true;hBoardHi=0;render()}});
document.addEventListener('focusin',e=>{if(e.target.id==='h-boardq'&&!hBoardOpen){hBoardOpen=true;render()}});
document.addEventListener('focusout',e=>{if(e.target.id==='h-boardq')setTimeout(()=>{if(hBoardOpen&&document.activeElement&&document.activeElement.id!=='h-boardq'){hBoardOpen=false;render()}},150)});
document.addEventListener('mousedown',e=>{const o=e.target.closest&&e.target.closest('[data-pickboard]');if(o){e.preventDefault();boardPick(o.dataset.pickboard)}});
document.addEventListener('keydown',e=>{if(e.target.id!=='h-boardq'||!REAL)return;const list=boardMatch(REAL.boards||[],hBoardQ);
 if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();hBoardOpen=true;hBoardHi=Math.max(0,Math.min(list.length-1,hBoardHi+(e.key==='ArrowDown'?1:-1)));render();const h=document.querySelector('#h-blist .hi');h&&h.scrollIntoView({block:'nearest'})}
 else if(e.key==='Enter'&&hBoardOpen&&list.length){e.preventDefault();boardPick(list[Math.min(hBoardHi,list.length-1)].id)}
 else if(e.key==='Escape'){hBoardOpen=false;render()}});
document.addEventListener('dragover',e=>{const z=e.target.closest&&e.target.closest('#dropzone');if(z){e.preventDefault();z.style.borderColor='var(--speak)'}});
document.addEventListener('dragleave',e=>{const z=e.target.closest&&e.target.closest('#dropzone');if(z)z.style.borderColor=''});
document.addEventListener('drop',e=>{const z=e.target.closest&&e.target.closest('#dropzone');if(!z)return;e.preventDefault();uploadDrop(e.dataTransfer.items)});
document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal'&&hStage===2){hStage=1;render()}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&pop){pop=null;render();return}if(e.key==='Escape'&&hStage===2){hStage=1;render()}});
document.addEventListener('change',e=>{if(e.target.id==='col-n'){const n=Math.round(+e.target.value);if(n>=3&&n<=100){colN=n;render()}else{toast('测几轮要在 3～100 轮之间');e.target.value=[10,20,30].includes(colN)?'':colN}return}if(e.target.id==='h-board'){return}if(e.target.dataset&&e.target.dataset.pick){const id=e.target.dataset.pick;fixPick.has(id)?fixPick.delete(id):fixPick.add(id);render();return}if(e.target.dataset&&e.target.dataset.sw){swOn=e.target.checked;return}if(e.target.dataset&&e.target.dataset.drop){const p=e.target.dataset.drop;dropped.has(p)?dropped.delete(p):dropped.add(p);render();return}if(e.target.id==='set-reuse'){reuseLast=e.target.checked;return}if(e.target.name==='llm'){setLLM=e.target.value;keyTested=false;keyErr='';keyDraft='';render()}});
document.getElementById('v-boss').addEventListener('click',()=>{mode='boss';render()});
document.getElementById('v-dev').addEventListener('click',()=>{mode='dev';render()});
(async function init(){
 const list=await (await fetch('/api/demo')).json();
 for(const d of list){const x=await (await fetch('/api/demo/'+encodeURIComponent(d.id))).json();SCENES[d.id]=x.S;FF[d.id]=x.F;COLLECT[d.id]=x.C;RUNS[d.id]=x.R;RANGE[d.id]=x.RG;RT[d.id]=x.RT;if(!d.hidden)PROJ.push({...x.P,scene:d.id})}  // 只用来核对报告的演示数据（hidden）不进"以前测过的"
 scene=DEMO&&SCENES[DEMO]?DEMO:list[0].id;pName=FF[scene].pname;mapSel=FF[scene].mapSel;
 try{const e=await (await fetch('/api/env')).json();IDFS=(e.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);CCST=e.claude_code||null;PORTS=e.ports||[]}catch(e){}
 try{SAVED=(await (await fetch('/api/state')).json()).settings;if(P2UI[SAVED.llm_provider])setLLM=P2UI[SAVED.llm_provider];reuseLast=SAVED.reuse_last!==false;setFirst=!SAVED.llm_provider}catch(e){}
 await histLoad();
 portsWatch(true)
 render()})();
