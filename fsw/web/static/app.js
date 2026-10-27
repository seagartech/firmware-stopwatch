// 演示数据（一个起点 + 几条线 + 每条线的段等）从后台 /api/demo 读，代码里不写死任何场景
let SCENES={},FF={},COLLECT={},RUNS={},RT={},PROJ=[];
// 原型底部"演示栏"的开关，真软件里改用网址参数：?demo= 选演示数据（选源码后面的页面先用它），?fail= 演示出错（nodev/build/burn/try/slowbg），?retest= 改完再测的情况，?settings=first|done 设置页状态
const QS=new URLSearchParams(location.search);
const FAIL=QS.get('fail')||'',DEMO=QS.get('demo')||'',RTCASE=QS.get('retest')||'same';
// ===== 通用计算 =====
const sorted=a=>[...a].sort((x,y)=>x-y);
const num=a=>a.filter(x=>x!=null&&isFinite(x));  // 顺带那条线某轮没收到（比如这轮没换表情）：那一轮不算
const med=a=>{const s=sorted(num(a)),m=s.length/2;return s.length%2?s[m|0]:(s[m-1]+s[m])/2};
const p90=a=>{a=num(a);return sorted(a)[Math.ceil(a.length*.9)-1]};
const mx=a=>Math.max(...num(a));
const f=(x,d=2)=>x==null||!isFinite(x)?'—':x.toFixed(d);
const fms=x=>x>0&&x<.1?x.toFixed(3):f(x);  // 技术细节表：几毫秒的段多留一位，不显示成 0.00
const LCOL=['var(--face)','var(--speak)','var(--warn)','var(--muted)'];
const LSOFT=['var(--face-soft)','var(--speak-soft)','var(--soft)','var(--soft)'];
function build(S){
  const n=Object.values(S.segs)[0].v.length;
  const use={};S.lines.forEach(l=>l.segs.forEach(k=>use[k]=(use[k]||0)+1));
  const lines=S.lines.map((l,i)=>{const ends=[...Array(n)].map((_,r)=>l.ends0&&l.ends0[r]!=null?l.ends0[r]:l.segs.some(k=>S.segs[k].v[r]==null)?null:l.segs.reduce((t,k)=>t+S.segs[k].v[r],0));
    return {...l,i,col:LCOL[i],soft:LSOFT[i],ends,med:med(ends),p90:p90(ends),max:mx(ends),segMed:l.segs.map(k=>med(S.segs[k].v))}});
  const rounds=[...Array(n)].map((_,r)=>({n:r+1,ends:lines.map(l=>l.ends[r])}));
  let reversed=[];
  if(S.firstBeforeSecond&&lines.length>1)reversed=rounds.filter(r=>r.ends[0]>r.ends[1]);
  const fair=S.exp&&S.exp.fair,mi=lines.length-1;
  if(fair){const e=lines[mi].ends.filter(x=>x!=null),k=e.filter(x=>x<=fair).length;Object.assign(lines[mi],{pass_n:k,count:e.length,pass_rate:k/e.length})}  // 达标率：没超过体验标准的轮次占几成  // 让人等太久的轮：这一轮超过体验标准（体验标准挂在要测的这条线上）
  const slowRounds=fair==null?[]:rounds.filter(r=>r.ends[mi]!=null&&r.ends[mi]>fair);
  const allSegs=Object.entries(S.segs).map(([k,s])=>({k,...s,med:med(s.v),max:mx(s.v)}));
  const biggest=[...allSegs].sort((a,b)=>b.med-a.med)[0];
  const spiky=allSegs.filter(s=>s.max>s.med*2.5&&s.max-s.med>.3).sort((a,b)=>(b.max-b.med)-(a.max-a.med))[0];
  const main=lines[lines.length-1];
  const verdict=()=>['只记录用时','c-gray'];  // 没有后台判定时（演示原型）：不判快慢
  const exp=S.exp?(main.med>S.exp.fair?['差','c-bad']:['可以','c-ok']):null;
  const out={S,n,use,lines,rounds,reversed,slowRounds,allSegs,biggest,spiky,main,verdict,exp};
  return S.J?withJudge(out,S.J):out;
}
// 报告用这次多轮实测真收到的数（演示数据回放时和演示数据一样）；选了真源码时，段、线、范围都按真步骤和对上的标准
let JREP=null,ADV=null,advT=null;
// 为什么、怎么改（任务 17）：选了真源码、测完后，让 AI 读代码和这次的数据写（问题是码表按规则算的），这里每秒问一次
const D0ok=()=>okRounds().length>0;
async function advStart(){ADV={status:'running',read:''};fixPick=null;render();
 const r=await fetch('/api/advise/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({work:REAL.work,steps:IDENT.page.steps,what:hSay,project_dir:REAL.project_dir})});
 const j=await r.json();if(!r.ok){ADV={status:'failed',message:j.detail};render();return}ADV=j;advWatch()}
function advWatch(){clearInterval(advT);
 advT=setInterval(async()=>{try{const x=await (await fetch('/api/advise')).json();if(x.status!=='running'){clearInterval(advT);advT=null;fixPick=null}ADV=x;if(page==='report')render();if(EXP.state==='wait'&&pop!=='export')EXP.state='';  // 等的时候点了取消（或关掉了对话框）：不再自动导出
 if(x.status==='done'&&EXP.state==='wait'){EXP.state='saving';render();exportReport(EXP.dir)}else if(x.status==='failed'&&EXP.state==='wait'){EXP.state='';render()}}catch(e){}},1000)}
function advBox(){if(!ADV||ADV.status==='done')return '';
 if(ADV.status==='failed')return `<div class="errbox"><b>为什么、怎么改还没写出来</b><p style="margin:6px 0">${ADV.message||''}</p><div class="acts" style="margin:8px 0 0"><button class="btn pri" data-advretry="1">重试</button></div></div>`;
 return `<div class="panel"><p style="margin:0"><span class="spin"></span> AI 正在读代码和这次的数据，写"为什么、怎么改"……（调用一次大模型，一般 1～3 分钟）</p>${ADV.read?`<p class="note" style="margin:6px 0 0">最近在看：<span class="code">${htmlEsc(String(ADV.read).replace(/^(Read|Grep|Glob|LS|Bash)\s*:?\s*/,''))}</span></p>`:''}<p class="note" style="margin:6px 0 0">要改哪些问题是码表按规则从数据算出来的；AI 只写原因和方案，码表只给建议，不改代码。</p></div>`}
const repKey=()=>(realFlash()&&FSWJ&&FSWJ.status==='done'?'real':'demo:'+scene)+':'+okRounds().length;
async function fetchReport(){const key=repKey(),u=key.startsWith('real')?'/api/report':'/api/report?demo='+encodeURIComponent(scene);
 try{const r=await fetch(u);JREP=r.ok?{...(await r.json()),_key:key}:{_key:key,_none:true}}catch(e){JREP={_key:key,_none:true}}if(page==='report')render()}
// 长短取决于内容的线（比如到回答全部放完：回答多少字决定多长）不判快慢，也不进图和结论：拉开刻度、看着像不稳定（10-18 用户定）。
// 码表按数据判（每轮差很多才算），门"开到位"这类每轮差不多的照常保留；原始数据还在调试日志里
function lenByContent(J){const sc0=J.scenario,bad=new Set((J.lines||[]).filter(x=>x.bad).map(x=>x.name)),
  sc={...sc0,lines:sc0.lines.filter(l=>!bad.has(l.name)||sc0.lines.every(y=>bad.has(y.name)))},  // 数据有问题的线（有负数）不进图、主数、结论，只在数据检查里列（10-26）
  hide=new Set((J.lines||[]).filter(x=>x.verdict==='看回答长短').map(x=>x.name)),keep0=sc.lines.filter(l=>!hide.has(l.name)),keep=keep0.length?keep0:sc.lines;
 // 从中间分出去、同时在走的段（不在哪条线从起点到终点的那一串上）：不进图、不进合计，单列在"只记录"里，照样有数（10-26）
 const onLine=new Set(sc.lines.flatMap(l=>l.segments)),par=(sc0.parallel||[]).filter(k=>sc.segments[k]&&!onLine.has(k));
 const used=new Set(keep.flatMap(l=>l.segments)),gone=new Set(sc.lines.filter(l=>hide.has(l.name)).flatMap(l=>l.segments).filter(k=>!used.has(k)));
 const main=keep[keep.length-1];
 if(!gone.size&&!par.length&&keep===sc.lines)return {...sc,cutEnd:main.end||main.name};
 // 不进图和结论，但不丢（10-26 用户要看全程）：报告里单列"只记录、不判快慢"，写一般、最快、最慢
 return {...sc,lines:keep,segments:Object.fromEntries(Object.entries(sc.segments).filter(([k])=>!gone.has(k)&&!par.includes(k))),cutEnd:main.end||main.name,
  recOnly:{lines:sc.lines.filter(l=>hide.has(l.name)).map(l=>l.name),segments:[...gone],...(par.length?{parallel:par}:{})}}}
// 报告顶上一行带上芯片（10-21：测量条件那块删了，芯片挪到这里）：项目名（芯片，板子）
const withChip=n=>{const c=REAL&&REAL.chip;if(!c||String(n).includes(c))return n;return /（[^）]*）$/.test(n)?n.replace(/（([^）]*)）$/,`（${c}，$1）`):`${n}（${c}）`};
function repScene(){const J=JREP,S0=SCENES[scene],real=realFlash()&&FSWJ&&FSWJ.status==='done';
 const val=k=>J.rounds.map(r=>r.segs[k]);
 if(!real){const S={...S0,segs:Object.fromEntries(Object.entries(S0.segs).map(([k,g])=>[k,{...g,v:J.rounds.map(r=>r.segs[k]??g.v[0])}])),J};return S}
 const sc=lenByContent(J),st={...Object.fromEntries((IDENT.page.steps||[]).map(x=>[x.p,x])),...(sc.points||{})};
 const segs=Object.fromEntries(Object.entries(sc.segments).map(([k,g])=>[k,{name:g.name,what:g.what,owner:g.owner,code:`${g.from}→${g.to}`,v:val(k)}]));
 const P=IDENT.page,mEnd=sc.cutEnd||(P.measure||'起点 → 终点').split('→').map(x=>x.trim())[1],sShort=startLabel(P,sc),sEnd=P.sideAsk?P.sideAsk.q:'';
 const lines=sc.lines.map((l,i)=>{const side=i<sc.lines.length-1,end=l.first||l.also_first||l.sl||l.actual?l.end:side?(sEnd||l.end):mEnd;const li=J.scenario.lines.findIndex(x=>x.name===l.name);
  // 线的用时用后台每轮按首尾算的数（中间某段空着的轮也有，比如被打断那几轮）；按段相加会把这些轮丢掉（10-19：报告写 1.22、实际中位 1.34）
  return {name:l.name,end,phrase:(side&&!l.first?'先':'后')+end,segs:l.segments,ends0:li<0?null:J.rounds.map(r=>(r.ends||[])[li]??null)}});
 const exp=sc.experience?{fair:sc.experience.fair,source:sc.experience.source||'',label:`${sc.experience.fair} 秒：超过算体验差（${sc.experience.short||'参考值'}）`}:null;  // 图上只写短的，卡片里写全出处（决策 137）
 const start=st[sc.start.point];
 return {crumb:`${htmlEsc(withChip(pName))} · ${htmlEsc(sShort)}到${htmlEsc(mEnd)} · ${J.n} 轮`,real:true,startShort:sShort,start:sShort,est:{label:'推算段',note:'起点之前设备看不见的那段，不计入'},exp,segs,lines,
  firstBeforeSecond:lines.length>1,recOnly:sc.recOnly||null,
  // 结论版说人话用（10-26）：设备类别、设备外那段在做什么（识别时的 outside_note）、各步怎么感觉到、AI 是否说过用户感觉不到
  plain:{cat:String(P.category||'').trim(),words:P.plain&&typeof P.plain==='object'?P.plain:{},server:String(P.server||'').trim(),kind:Object.fromEntries((P.steps||[]).filter(x=>x.felt).map(x=>[x.n,x.kind||''])),
   unseen:Object.fromEntries((P.steps||[]).filter(x=>x.felt).map(x=>[x.n,(typeof UNSEEN_RX!=='undefined'&&UNSEEN_RX.test(`${x.note||''} ${x.why||''}`))]))},fixes:ADV&&ADV.status==='done'?ADV.fixes:[],
  tech:{
   loc:Object.fromEntries(Object.entries(sc.segments).map(([k,g])=>[k,st[g.to]?`${st[g.to].f} ${lnTxt(st[g.to].l)}`:''])),log:J.raw_log||S0.tech.log},J}}
// 判快慢、两把尺子、结论标签：后台按固定规则算（任务 16，fsw/judge.py），这里只照着显示
// 段、线的判定（10-19 去掉合理范围清单）：第一次只记录用时，第二次起和上次比
const VCLS={'数据有问题':'c-bad','慢了':'c-warn','快了':'c-ok','持平':'c-ok','已记录':'c-gray','推算':'c-gray','看回答长短':'c-gray','数据不足':'c-warn'};
const VTXT={'慢了':'比上次慢了','快了':'比上次快了','持平':'和上次持平','已记录':'只记录用时'};
function withJudge(D,J){const L=n=>J.lines.find(x=>x.name===n)||{};
  // 一般、最差、达标率都用后台算好的，网页不再自己算一遍（两边规则不一样就会对不上：10-19 报告写 1.22、实际 1.34）
  D.lines.forEach(l=>{const b=L(l.name);if(b.med!=null)Object.assign(l,{med:b.med,p90:b.p90,max:b.max,count:b.count,pass_n:b.pass_n,pass_rate:b.pass_rate})});
  D.allSegs.forEach(g=>{const b=(J.segments||[]).find(x=>x.k===g.k);if(b&&b.med!=null)Object.assign(g,{med:b.med,max:b.max});
   if(b&&b.thin)Object.assign(g,{thin:true,count:b.count,of:b.of,thinWhy:b.why||''})});  // 不到八成轮次有数：不当完整的段显示（见 judge.ENOUGH）
  D.verdict=l=>{const v=L(l.name).verdict||'已记录';return [VTXT[v]||v,VCLS[v]||'c-gray']};
  const e=L(D.main.name).exp;D.exp=e?[e,e==='差'?'c-bad':'c-ok']:null;
  const sp=J.labels.find(x=>x.kind==='spiky'&&!x.with);  // 和对错问题同一根因的，算进那条
  D.probs=J.labels.filter(x=>x.kind==='problem'&&!x.order);D.spiky=sp?D.allSegs.find(s=>s.k===sp.segment)||null:null;
  const ord=J.labels.find(x=>x.kind==='problem'&&x.order);D.reversed=ord?D.rounds.filter(r=>ord.rounds.includes(r.n)):[];
  D.slowRounds=D.rounds.filter(r=>D.lines.some((l,i)=>{const fa=(L(l.name).exp_std||{}).fair;return fa!=null&&r.ends[i]!=null&&r.ends[i]>fa}));  // 超过这条线体验标准的轮
  if(J.biggest&&D.allSegs.find(s=>s.k===J.biggest))D.biggest=D.allSegs.find(s=>s.k===J.biggest);
  D.J=J;return D}
const roundList=a=>'第 '+a.map(r=>r.n).join('、')+' 轮';
// 分段用时的范围：某轮后一个点比前一个点先到（如回答开头被吞），算出来是负数，不写负数，写这一轮先后反了
function rangeTxt(v,ns){const pos=v.filter(x=>x!=null&&x>=0),  // 某轮这条线没数（null>=0 在 JS 里是真，会被当成 0）
 neg=ns.filter((n,i)=>v[i]<0);
  return (pos.length?`${fms(Math.min(...pos))}～${fms(Math.max(...pos))}`:'—')+(neg.length?`<br><span class="note">第 ${neg.join('、')} 轮先后反了</span>`:'')}  // 备注换一行写：不把这一列撑宽
// 线的中位数在标题、要你拍板、计时图终点都写两位小数：同一个数写成一个样（10-19 用户：标题 1.4、图上 1.45）
function headline(D){
  const L=D.lines;
  if(L.length===1)return `${D.S.startShort} ${f(L[0].med)} 秒${L[0].phrase}`;
  // 按实际先后写（10-26：顺带那条比结果还晚，原来照样写"先"）：最早的写"先"，其余写"后"
  const o=[...L].filter(l=>l.med!=null&&isFinite(l.med)).sort((a,b)=>a.med-b.med);
  const end=l=>l.end!=null?l.end:String(l.phrase||'').replace(/^[先后]/,'');
  return `${D.S.startShort} `+(o.length?o:L).map((l,i)=>`${f(l.med)} 秒${i?'后':'先'}${end(l)}`).join('，');
}
// 量的是哪一段（结果页的 measure）：全程那条线（只记录、不判快慢）当标题，主数照旧写判快慢的那条（10-26 用户：报告标题要是要测的那件事）
function measureLine(D){const J=D.J;return J&&(J.lines||[]).find(x=>x.measure&&!x.bad)||null}
// 主指标（设备往外给出结果的第一下，比如开始出声，见 standards.primary_first）当标题、首屏的大数；其余的线跟在后面（10-26 用户：最关心说完到首段出声）
function mainFirst(D){const ls=(D.J&&D.J.scenario&&D.J.scenario.lines)||[],p=ls.find(x=>x.first);return p?D.lines.find(l=>l.name===p.name)||null:null}
// ===== 结论版说人话（10-26 用户：最终用户看不懂代码、也没法追问，报告要自己说明白）=====
// 只在真测、有主指标、拿得到后台判定时用；老存档、演示数据、缺信息时退回原来的写法。词按设备类别和主指标那一下怎么感觉到取，类别对不上用通用词
const UNSEEN_RX=/看不到|看不见|不显示|不会显示|显示不出|藏起来|被隐藏|隐藏了|听不到|听不见|不可见|用户感觉不到|感觉不到/;  // 和 standards.UNSEEN 一样
// 类别对应的说法由后台给（identify.PLAIN_WORDS → page.plain.words）；对不上的类别用通用说法
const PLAIN_GEN={start:'操作完',done:'处理完你的输入',len:'这次要做的事有多少'};
const PLAIN_FEEL={'声音':'听到设备的第一声','画面':'看到屏幕有变化','灯光':'看到灯亮','动作':'看到它开始动'};
function plainOn(D){const M=D&&D.S&&D.S.real&&D.S.plain&&D.J&&D.J.scenario?mainFirst(D):null;return !!(M&&M.med!=null&&isFinite(M.med)&&(M.segs||[]).length)}
function plainWords(D){const p=D.S.plain||{},w={...PLAIN_GEN,...(p.words||{})},M=mainFirst(D),k=M&&p.kind?p.kind[M.end]:'';
 return {...w,feel:(k==='声音'&&w.sound)||PLAIN_FEEL[k]||'感觉到设备的第一下反应'}}
// 谁负责：服务器 → 云端（含网络）；固件、硬件 → 设备；网络 + 固件 → 设备（含网络）
const plainWho=o=>{o=String(o||'');const sv=/服务器|云/.test(o),dv=/固件|硬件|设备/.test(o),nt=/网络/.test(o);
 return sv&&dv?'云端和设备都有':sv?'云端（含网络）':dv?(nt?'设备（含网络）':'设备'):nt?'网络':o}
// 这一段算在哪边：在设备外（服务器）→ 云端；设备上的（含网络）→ 设备；只有网络、或服务器和设备都有 → 其他 / 混合
function plainWhere(D,k){const g=((D.J.scenario||{}).segments||{})[k]||{},o=String(g.owner||(D.S.segs[k]||{}).owner||''),sv=/服务器|云/.test(o),dv=/固件|硬件|设备/.test(o);
 if(sv&&dv)return 'mix';if(sv||(g.side==='outside'&&!dv))return 'cloud';return dv?'dev':'mix'}
const plainServer=D=>String((D.S.plain||{}).server||'').replace(/^(服务器|云端|服务端)[：:]\s*/,'');
function plainSplit(D,M){const t={cloud:0,dev:0,mix:0};(M.segs||[]).forEach(k=>{const v=segMed(D,k);if(v!=null&&isFinite(v)&&v>0)t[plainWhere(D,k)]+=v});return t}
// 体验标准按平均判（T/CESA 1045 6.3.3 写的是平均）；逐次超过的只当补充
function plainStd(D,M){const j=(D.J.lines||[]).find(x=>x.name===M.name)||{},es=j.exp_std;if(!es||es.fair==null)return null;
 const e=num(M.ends||[]);if(!e.length)return null;const avg=e.reduce((t,x)=>t+x,0)/e.length;
 return {fair:es.fair,good:es.good,avg,n:e.length,over:e.filter(x=>x>es.fair).length,ok:avg<=es.fair}}
function plainHead(D){const M=mainFirst(D),w=plainWords(D),st=plainStd(D,M);
 return `你${w.start}后，一般约 ${f(M.med)} 秒${w.feel}${st?`（${st.n} 次里 ${st.n-st.over} 次在 ${st.fair} 秒内）`:''}。`}
function plainWhy(D){const M=mainFirst(D),w=plainWords(D),st=plainStd(D,M),t=plainSplit(D,M),tot=t.cloud+t.dev+t.mix,sv=plainServer(D);
 const s2=t.cloud>0.005?`其中约 ${f(t.cloud)} 秒在等云端${sv?`（${htmlEsc(sv)}）`:''}，设备自己约 ${f(t.dev)} 秒${t.mix>0.005?`，另有约 ${f(t.mix)} 秒是网络或两边都有的部分`:''}。`
  :`这段时间基本都花在设备上（约 ${f(t.dev)} 秒）${t.mix>0.005?`，另有约 ${f(t.mix)} 秒是网络或两边都有的部分`:''}。`;
 const fast=st?(st.good!=null?M.med<=st.good:st.ok&&!st.over):false,b=D.J.best_fix;
 const s3=fast?'已经很快，不用改。'
  :tot>0&&t.cloud/tot>=.7?'慢主要在云端，改设备代码帮不上；要更快需要换更快的服务、离你更近的服务器或更好的网络。'
  :b&&b.name?`最值得改的是设备上的「${htmlEsc(b.name)}」，一般约 ${fms(b.med)} 秒${b.share!=null?`，占 ${Math.round(+b.share)}%`:''}。`
  :st&&st.ok?'已经很快，不用改。':'';
 const s4=st?`按行业标准（从你${w.start}到设备开始回应，平均不超过 ${st.fair} 秒），这次平均 ${f(st.avg)} 秒，${st.ok?'达标':'没达标'}${st.ok&&st.good!=null&&st.avg>st.good?`，离理想水平（${st.good} 秒）还差约 ${f(st.avg-st.good,1)} 秒`:''}${st.over?`；${st.ok?'偶尔超过':'超过的次数'}：${st.n} 次里 ${st.over} 次超过 ${st.fair} 秒`:''}。`:'';
 return [s2,s3,s4].filter(Boolean).join('')}
// 其他参考：次要的"第一下"（比如识别文字到达）、全程，每条一句说清它意味着什么
function plainRefs(D){const M=mainFirst(D),w=plainWords(D),p=D.S.plain||{},sl=(D.J.scenario.lines||[]),out=[];
 sl.filter(x=>x.also_first).forEach(x=>{const l=D.lines.find(y=>y.name===x.name);if(!l||l.med==null||!isFinite(l.med))return;
  const last=(l.segs||[])[l.segs.length-1],cloud=last&&plainWhere(D,last)==='cloud',ld=D.J.lead&&D.J.lead.first===l.name&&D.J.lead.second===M.name?D.J.lead.med:null;
  out.push(`<li><b>${htmlEsc(l.end)}</b>：一般约 ${f(l.med)} 秒${ld!=null&&ld>0?`，比${htmlEsc(M.end)}早约 ${f(ld)} 秒`:''}。${cloud?`这只说明云端很快就${w.done}`:`这只说明「${htmlEsc(l.end)}」这一步来得早`}，不是你等到结果的时间${p.unseen&&p.unseen[l.end]?'；这台设备不显示这一步，你感觉不到':''}。你真正在等的是${htmlEsc(M.end)}那 ${f(M.med)} 秒。</li>`)});
 const m=measureLine(D),me=m&&(sl.find(x=>x.name===m.name)||{}).end;if(m&&m.med!=null)out.push(`<li><b>全程（到${htmlEsc(me||m.end||m.name)}）</b>：一般约 ${f(m.med)} 秒${m.min!=null&&m.max!=null?`，最快 ${f(m.min)}、最慢 ${f(m.max)} 秒`:''}。长短主要看${w.len}，不判快慢。</li>`);
 return out.length?`<section class="panel" id="sec-refs"><h2>其他参考</h2><ul style="margin:0;padding-left:20px">${out.join('')}</ul></section>`:''}
const PLAIN_LV=['需要处理','可以改进','仅供参考','没问题','出错了'];
function titleHtml(D){if((typeof plainOn==='function'&&plainOn(D)))return `<h1>${plainHead(D)}</h1>`;const m=measureLine(D),M=D.S.real?mainFirst(D):null;
 if(M&&M.med!=null&&isFinite(M.med)){const rest=D.lines.filter(l=>l!==M&&l.med!=null&&isFinite(l.med)&&!(m&&l.name===m.name)).map(l=>`${htmlEsc(l.end)} ${f(l.med)} 秒`);
  return `<h1>${htmlEsc(D.S.startShort)}到${htmlEsc(M.end)} ${f(M.med)} 秒</h1></div><p class="headnum" style="font-size:16px;font-weight:600;margin:4px 0 0">主指标：从「${htmlEsc(D.S.startShort)}」到「${htmlEsc(M.end)}」（设备给出结果的第一下）${rest.length?`；另外：${rest.join('，')}`:''}${m?`；全程 ${f(m.med)} 秒${htmlEsc(m.end)}<span class="note" style="font-weight:400">（只记录、不判快慢）</span>`:''}</p><div>`}
 if(!m)return `<h1>${headline(D)}</h1>`;
 return `<h1>${htmlEsc(m.name)}</h1></div><p class="headnum" style="font-size:18px;font-weight:700;margin:4px 0 0">${headline(D)}；全程 ${f(m.med)} 秒${htmlEsc(m.end)}<span class="note" style="font-weight:400">（只记录、不判快慢）</span></p><div>`}

const LV=[['必须改','lv-must'],['建议改','lv-should'],['未判','lv-none'],['达标','lv-ok'],['出错','lv-must']];
const lvTag=lv=>`<span class="lvtag ${LV[lv][1]}">${LV[lv][0]}</span>`;
const jump=(id,t)=>`<button class="lnk" data-jump="${id}">${t}</button>`;
// 要你拍板的一条 → 讲它的那张卡片：按问题编号对；早先的卡片把几段"偶尔特别慢"都叫 spiky，再按段对
const cardOf=(FX,d)=>FX.findIndex(F=>F.when.includes(d.k)||(d.seg&&F.when.includes('spiky')&&F.seg===d.seg));
function decisionsJ(D){const J=D.J,out=[];
  const src={line:['tiers','用时和达标率'],spiky:['rounds',`${D.n} 轮逐轮`],order:['rounds',`${D.n} 轮逐轮`],problem:['rounds',`${D.n} 轮逐轮`]};  // 「依据」链接：点一下跳到下面对应的数据
  for(const x of J.labels){const lv=LV.findIndex(v=>v[0]===x.level);if(x.level==='达标')continue;  // 达标的不列（10-20 用户定）：要你拍板只写要处理的
   if(x.kind==='line'){const l=D.lines.find(y=>y.name===x.line);if(!l)continue;const isMain=l===D.main,b=isMain?D.biggest:([...D.allSegs].filter(s=>l.segs.includes(s.k)&&!s.thin).sort((p,q)=>q.med-p.med)[0]||D.biggest),share=Math.round(b.med/l.med*100),[v,c]=D.verdict(l),ex=isMain?D.exp:null;
    out.push({k:isMain?'main':'line:'+l.name,lv,src:src.line,tag:ex?[`体验${ex[0]}`,ex[1]]:[v,c],t:`${l.end} ${f(l.med)} 秒`,
     d:x.why==='取决于回答长短，不判快慢'?`长短主要看这次回答说了多少字，不判快慢。`:x.level==='未判'?`${x.why}。`
       :`${x.why}。`,
     who:x.why==='取决于回答长短，不判快慢'?'不判快慢':x.level==='未判'?'下次再测就和这次比':x.level==='达标'?'不用改':`找${b.owner}`})}
   else if(x.kind==='spiky'){const s=D.allSegs.find(y=>y.k===x.segment);if(!s||x.with)continue;  // 和对错问题同一根因的，合并到那条里
    out.push({k:x.k,seg:x.segment,lv,src:src.spiky,tag:['偶发','c-warn'],t:`"${s.name}"偶尔特别慢`,d:`平时 ${f(s.med)} 秒，最慢 ${f(s.max)} 秒（${roundList(x.rounds.map(n=>({n})))}）。`,who:`找${s.owner}`})}
   else if(x.order&&D.lines.length>1)out.push({k:'reversed',lv,src:src.order,tag:['要查','c-bad'],t:`${x.rounds.length} 轮${D.lines[0].end}反而比${D.lines[1].end}晚`,d:`${roundList(x.rounds.map(n=>({n})))}。"先给反应"这个设计在这几轮白做了。`,who:'先查是哪一段变慢'});
   else{const also=J.labels.filter(y=>y.kind==='spiky'&&y.with===x.k).map(y=>D.allSegs.find(s=>s.k===y.segment)).filter(s=>s&&!x.name.includes(s.name)).map(s=>`同一轮"${s.name}"也特别慢（最慢 ${f(s.max)} 秒，平时 ${f(s.med)} 秒），是同一个问题。`).join('');
    out.push({k:x.k,lv,src:src.problem,tag:['要查','c-bad'],t:x.name,d:`${D.n} 轮里 ${x.rounds.length} 轮（${roundList(x.rounds.map(n=>({n})))}）。${also}`,who:'先查是哪里出的问题'})}}
  const rank=[1,2,3,4,0];return out.map((x,i)=>[x,i]).sort((p,q)=>rank[p[0].lv]-rank[q[0].lv]||p[1]-q[1]).map(p=>p[0])}  // 越重要越靠前：出错、必须改、建议改、未判、达标；"问题 N"的编号也跟着这个顺序
function decisions(D){if(D.J)return decisionsJ(D);
  const out=[];
  if(D.reversed.length)out.push({k:'reversed',tag:['要查','c-bad'],t:`${D.reversed.length} 轮${D.lines[0].end}反而比${D.lines[1].end}晚`,d:`${roundList(D.reversed)}。"先给反应"这个设计在这几轮白做了。`,who:'先查是哪一段变慢'});
  if(D.spiky)out.push({k:'spiky',tag:['偶发','c-warn'],t:`"${D.spiky.name}"偶尔特别慢`,d:`平时 ${f(D.spiky.med)} 秒，最慢 ${f(D.spiky.max)} 秒（${roundList(D.allSegs.find(s=>s.k===D.spiky.k).v.map((x,i)=>({x,n:i+1})).filter(o=>o.x>D.spiky.med*2.5))}）。`,who:`找${D.spiky.owner}`});
  const b=D.biggest,share=Math.round(b.med/D.main.med*100);
  const bad=D.exp&&D.exp[0]==='差';
  out.push({k:'main',tag:D.exp?[`体验${D.exp[0]}`,D.exp[1]]:D.verdict(D.main),t:`${D.main.end}要 ${f(D.main.med)} 秒，大头是"${b.name}"`,d:`这一段 ${f(b.med)} 秒，占 ${share}%。${D.exp?'':noExp(D)+'，只记录用时。'}`,who:bad?`找${b.owner}`:D.exp?'在体验标准内，不用改':'只记录用时'});
  // 等级按固定规则算，不由 AI 判断（2026-10-13 用户提出加标签）
  const L=out.map(x=>{let lv;
    if(x.k==='main')lv=bad?0:D.exp?3:2;  // 3 = 可以不改，2 = 未判（没有体验标准）
    else if(x.k==='spiky')lv=D.S.exp&&D.lines.filter(l=>l.segs.includes(D.spiky.k)).some(l=>l===D.main&&l.p90>D.S.exp.fair)?0:1;
    else lv=D.reversed.length/D.n>.1?0:1;
    const src=x.k==='main'?['tiers','「用时和达标率」里“一般”那一列；各段占多少见「一次的计时图」']:x.k==='spiky'?['rounds',`「${D.n} 轮逐轮」图里红底的几轮`]:['rounds',`「${D.n} 轮逐轮」图里红底、标"${D.lines[0].end}比${D.lines[1].end}晚"的几轮`];
    return {...x,lv,src}});
  return L.sort((a,b)=>a.lv-b.lv);
}

// ===== 计时图：同一起点、多条横线（按数据画）=====
function niceT(x){return Math.max(.5,Math.ceil(x*1.2/.5)*.5)}
function step(T){return T<=1?.1:T<=2?.25:T<=4?.5:1}
// 一次的计时图按中位数画（10-19 用户定"一套标准"）：每条线总长 = 这条线的中位数（和标题、「用时和达标率」里的"一般"同一个数）；
// 线里各段按各段中位数的比例分。不再挑某一轮画：挑出来的那轮和标题对不上（标题 1.45、图上 1.51）
function segMed(D,k){return (D.allSegs.find(x=>x.k===k)||{med:med(D.S.segs[k].v)}).med}
function lineParts(D,l){const bad=new Set(((D.J&&D.J.segments)||[]).filter(x=>x.bad).map(x=>x.k)),ks=l.segs.filter(k=>!bad.has(k)&&!((segMed(D,k)??0)<0));  // 数据有问题、负数的段不画（也不当"极短段"写出来），只在数据检查里列
  const m=ks.map(k=>Math.max(0,segMed(D,k)||0)),sum=m.reduce((t,x)=>t+x,0);
  return ks.map((k,j)=>({k,real:segMed(D,k),share:sum>0?m[j]/sum:1/ks.length,d:l.med==null?0:(sum>0?m[j]/sum:1/ks.length)*l.med}))}
function timeline(D){
// 共用那段走到哪一步（图下"从哪里分开"用）
const sameEnd=(D,k)=>{const sc=(D.J&&D.J.scenario)||{},g=(sc.segments||{})[k]||{},to=g.to||String(k).split('-')[1];return (sc.point_names||{})[to]||''}
// 字宽估算（中文按一个字号宽，其余按 0.62 个）：线名、终点名按它定左右留多宽，不再写出框（10-27 用户：左侧标题看不全）
const cw=(t,fs)=>[...String(t||'')].reduce((x,c)=>x+(c.charCodeAt(0)>255?fs:fs*.62),0);
// 线名末尾括号里的说明（从哪一步分出去、跟什么走）拆出来，写成第二行小字
const splitNm=s=>{const m=String(s||'').match(/^(.+?)（(.+)）$/);return m?[m[1].trim(),m[2].trim()]:[String(s||''),'']};
const wrapT=(t,fs,mw)=>{const out=[];let a='';for(const c of [...String(t||'')]){if(a&&cw(a+c,fs)>mw){out.push(a);a=''}a+=c}if(a)out.push(a);return out};
// 图下：这条线开头和别的线共用的那几段，写成"到「某一步」之前和「某条线」走的是同一段，从那里分开"（10-27 用户："同上"看不懂）
const sameTo=(D,l,rows,i)=>{const mine=rows.filter(r=>r.li===i),own=new Set(mine.flatMap(r=>[r.k,...(r.tiny||[]).map(t=>t.k)])),ks=(l.segs||[]),pre=[];
 const other=k=>rows.find(r=>r.li!==i&&(r.k===k||(r.tiny||[]).some(t=>t.k===k)));
 for(const k of ks){if(own.has(k))break;if(other(k))pre.push(k)}
 if(!pre.length)return '';const k=pre[pre.length-1],n=sameEnd(D,k),src=other(k),sn=src?`「${splitNm(D.lines[src.li].name)[0]}」`:'上面那条';
 return n?`到「${n}」之前和${sn}走的是同一段，从那里分开`:`开头和${sn}走的是同一段`}
  // 结论版说人话（10-26）：只画主指标那条（和顺带的线），次要的"第一下"放到"其他参考"；不用圈号，色块直接写短名字和秒数
  const PL=(typeof plainOn==='function'&&plainOn(D));if(PL){const also=new Set(((D.J.scenario||{}).lines||[]).filter(x=>x.also_first).map(x=>x.name));D={...D,lines:D.lines.filter(l=>!also.has(l.name))}}
  // 色块里写短名字（后台按段名算好的 short，见 standards.short_name；老存档没有就按段名、太长写"到「终点」"），全名写在图下面
  const segOf=k=>((((D.J&&D.J.scenario)||{}).segments)||{})[k]||{};
  const shortOf=k=>{const s=String(segOf(k).short||'').trim();if(s)return s;const nm=(D.S.segs[k]||{}).name||k,to=sameEnd(D,k);return [...nm].length<=14?nm:to?`到${to}`:[...nm].slice(0,14).join('')+'…'};
  const pname=k=>PL&&plainWhere(D,k)==='cloud'?'等云端':shortOf(k);
  // 编号先给主线（判快慢的那条），顺带的线和主线共用的那几段不再另编号、不另列（10-26：共用段在图下列了两遍，顺带线的段排成了主流程）
  const ord=[D.lines.indexOf(D.main),...D.lines.map((_,i)=>i).filter(i=>D.lines[i]!==D.main)].filter(i=>i>=0);
  // 每条线开头和先画的线共用的几段（主线先画）：这几段画在先画那条线同样的位置、同样长（同一段时间必然等长）；分开的地方标竖线
  // （10-27 用户：三条"同上"灰柱长短不一样——原来每条线按自己的中位数摊、而且各自分开的那一步不同）
  const partsOf={},preOf={},seenK=new Set();
  ord.forEach(i=>{const l=D.lines[i];if(l.med==null||!isFinite(l.med)){partsOf[i]=[];preOf[i]=[];return}
    const ps=lineParts(D,l),pre=[];while(pre.length<ps.length-1&&seenK.has(ps[pre.length].k))pre.push(ps[pre.length]);
    partsOf[i]=ps;preOf[i]=pre;ps.forEach(p=>seenK.add(p.k))});
  const forkName=i=>preOf[i].length?sameEnd(D,preOf[i][preOf[i].length-1].k):'';
  const LMAX=210,lab={};
  ord.forEach(i=>{const [nm,note]=splitNm(D.lines[i].name),fk=forkName(i),sub=note||(fk?`从「${fk}」分出去`:'');lab[i]={a:wrapT(nm,14,LMAX),b:sub?wrapT(sub,11.5,LMAX):[]}});
  const LW=Math.max(80,...Object.values(lab).flatMap(x=>[...x.a.map(t=>cw(t,14)*1.08),...x.b.map(t=>cw(t,11.5))]));
  const endTxt=l=>`${splitNm(l.end)[0]} ${f(l.med)} 秒`,EW=Math.max(60,...D.lines.map(l=>cw(endTxt(l),13)*1.08+16));
  const maxL=Math.max(1,...Object.values(lab).map(x=>x.a.length+x.b.length));
  const T=niceT(Math.max(...D.lines.map(l=>l.med).filter(x=>x!=null&&isFinite(x))));const X0=Math.ceil(LW+26),W=Math.ceil(Math.max(900,X0+430+EW+10)),X1=W-EW-10,sx=t=>X0+(X1-X0)*t/T,h=22,gap=Math.max(64,maxL*16+30),top=60;
  const H=top+D.lines.length*gap+30;
  let g='';const st=step(T);
  for(let t=0;t<=T+1e-9;t+=st){g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="30" y2="${H-30}" stroke="var(--soft)"/><text x="${sx(t)}" y="${H-10}" text-anchor="middle" font-size="12" fill="var(--muted)" class="num">${+t.toFixed(2)} 秒</text>`}
  // 体验标准每条线一条：画在那条线自己那一行上，写明是哪条线的；演示数据只有一条（主线）的，照旧画一条贯穿的
  const jls=D.J&&D.J.lines?D.lines.map(l=>(D.J.lines.find(x=>x.name===l.name)||{}).exp_std):null;
  if(jls&&jls.some(Boolean))D.lines.forEach((l,i)=>{const es=jls[i];if(!es||es.fair==null||es.fair>T)return;const y=top+i*gap,x=sx(es.fair),below=i>0;  // 下面几条写在横条下面，不和"早多少秒"叠在一起
   g+=`<line x1="${x}" x2="${x}" y1="${below?y-4:y-16}" y2="${below?y+h+18:y+h+10}" stroke="var(--warn)" stroke-dasharray="3 4" stroke-width="1.5"/><text x="${x>W-320?x-4:x+4}" text-anchor="${x>W-320?'end':'start'}" y="${below?y+h+16:y-6}" font-size="11.5" fill="var(--warn)">${PL?`行业标准：平均不超过 ${es.fair} 秒`:`${l.name}：超过 ${es.fair} 秒算体验差（${htmlEsc(es.short||'参考值')}）`}</text>`});
  else if(D.S.exp&&D.S.exp.fair<=T)g+=`<line x1="${sx(D.S.exp.fair)}" x2="${sx(D.S.exp.fair)}" y1="30" y2="${H-30}" stroke="var(--warn)" stroke-dasharray="3 4"/><text x="${sx(D.S.exp.fair)+4}" y="40" font-size="11.5" fill="var(--warn)">${D.S.exp.label}</text>`;
  g+=`<text x="${sx(0)}" y="22" text-anchor="middle" font-size="12" fill="var(--muted)">${D.S.start}</text>`;
  // 归成阶段（10-20 用户定）：占这条线不到 5% 的段算极短，不单独画；连着的极短段并进后面那个大段，排在最后的并进前一个大段。
  // 色块里只写名字（或编号）和用时，段名全称、谁负责、并进了几段写在图下面；不标哪块最长、不要浮窗
  const rows=[],NO='①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳',numOf={};
  const drawn=[],pos={},blocks=[];
  const blk=(b,y)=>{const w=sx(b.b)-sx(b.a);return `<g><rect x="${sx(b.a)}" y="${y}" width="${Math.max(2,w)}" height="${h}" fill="${b.fill}" stroke="var(--panel)" stroke-width="1" rx="2"/>${b.lab?`<text x="${(sx(b.a)+sx(b.b))/2}" y="${y+h/2+5}" text-anchor="middle" font-size="12" fill="${b.fg}"${PL?'':' style="font-variant-numeric:tabular-nums"'}>${htmlEsc(b.lab)}</text>`:''}</g>`};
  ord.forEach(i=>{const l=D.lines[i],y=top+i*gap,L=lab[i];let t0=0;
    const y0=y+h/2-(L.a.length*16+L.b.length*14)/2+12;
    L.a.forEach((t,j)=>{g+=`<text x="${X0-14}" y="${y0+j*16}" text-anchor="end" font-size="14" font-weight="700" fill="${l.col}">${htmlEsc(t)}</text>`});
    L.b.forEach((t,j)=>{g+=`<text x="${X0-14}" y="${y0+L.a.length*16+j*14}" text-anchor="end" font-size="11.5" fill="var(--muted)">${htmlEsc(t)}</text>`});
    if(l.med==null||!isFinite(l.med)){g+=`<text x="${sx(0)+8}" y="${y+h/2+5}" font-size="12" fill="var(--muted)">没收到</text>`;return}
    const pre=preOf[i],parts=partsOf[i].slice(pre.length).map(p=>({...p}));
    if(pre.length){const pk=new Set(pre.map(p=>p.k)),cov=new Set();t0=pos[pre[pre.length-1].k].b;
      // 共用的几段：照先画那条线的样子再画一遍（同样的位置、长短、颜色和字），不写"同上"
      blocks.filter(b=>b.keys.every(k=>pk.has(k))).forEach(b=>{b.keys.forEach(k=>cov.add(k));g+=blk(b,y)});
      pre.filter(p=>!cov.has(p.k)).forEach(p=>{g+=blk({a:pos[p.k].a,b:pos[p.k].b,fill:'var(--soft)',fg:'var(--fg)',lab:''},y)});
      const fx=sx(t0),txt='从这里分开走',tw=cw(txt,11),left=fx-tw-6>=X0;
      g+=`<line x1="${fx}" x2="${fx}" y1="${y-12}" y2="${y+h+4}" stroke="${l.col}" stroke-width="2"/><text x="${left?fx-4:fx+4}" y="${y-4}" text-anchor="${left?'end':'start'}" font-size="11" fill="${l.col}">${txt}</text>`}
    // 剩下的段按各自中位数的比例摊到"分开的地方 → 这条线的中位数"
    const rest=Math.max(0,l.med-t0),rs=parts.reduce((t,p)=>t+Math.max(0,p.real||0),0);
    parts.forEach(p=>{p.d=rest>0?(rs>0?Math.max(0,p.real||0)/rs:1/parts.length)*rest:Math.max(0,p.real||0)});
    {let t=t0;parts.forEach(p=>{if(!pos[p.k])pos[p.k]={a:t,b:t+p.d};t+=p.d})}
    drawn.push(i);
    const st=stages(parts);
    st.forEach((x,j)=>{const a=t0,b=t0+x.d;t0=b;const last=j===st.length-1,k=x.parts[0].k,shared=x.parts.some(p=>D.use[p.k]>1),seen=numOf[k],no=seen||NO[rows.length]||String(rows.length+1),w=sx(b)-sx(a);
      x.parts.forEach(p=>{numOf[p.k]=numOf[p.k]||no});
      const c=last?l.col:shared?'var(--soft)':l.soft,fg=last?'#fff':'var(--fg)';
      const th=x.parts.find(p=>(D.allSegs.find(s=>s.k===p.k)||{}).thin),ths=th&&D.allSegs.find(s=>s.k===th.k);
      // 块里、图下写的秒数是这一段自己的中位数，和「分段用时」同一个数（10-26 核对）；并进来的极短段单独写名字和秒数，不加进这个数里
      const own=x.parts[0].real,tiny=x.parts.slice(1).map(p=>({k:p.k,name:D.S.segs[p.k].name,v:p.real}));
      if(!seen)rows.push({k,no,li:i,name:D.S.segs[k].name+(ths?`（数据不足：${ths.count}/${ths.of} 轮）`:''),d:own,share:l.med>0&&own!=null?own/l.med:x.share,owner:D.S.segs[k].owner||'',inc:x.parts.length-1,tiny,shared});
      // 短名字放不下就写这段走到哪一步（比如 收到识别结果 0.36）
      let lb='';if(PL){const nm=pname(k),to=plainWhere(D,k)==='cloud'?'':sameEnd(D,k);lb=w>cw(nm,12)+44?`${nm} ${f(own)}`:to&&w>cw(to,12)+44?`${to} ${f(own)}`:w>36?f(own):''}else lb=w>48?`${no} ${f(own)}`:w>16?no:'';
      const bk={keys:x.parts.map(p=>p.k),a,b,fill:c,fg,lab:lb};blocks.push(bk);g+=blk(bk,y)});
    g+=`<circle cx="${sx(t0)}" cy="${y+h/2}" r="6" fill="${l.col}"/><text x="${sx(t0)+10}" y="${y+h/2+5}" font-size="13" font-weight="700" fill="${l.col}" class="num halo">${htmlEsc(endTxt(l))}</text>`;});
  const L0=D.lines[0],L1=D.lines[1];
  // "早多少秒"用后台按每轮算的差的中位数（judge lead），和逐轮一致；没有才用两条线中位数相减（10-26 核对：图上 0.38、逐轮中位 0.36）
  const LD=D.J&&D.J.lead&&D.J.lead.first===L0.name&&L1&&D.J.lead.second===L1.name?D.J.lead.med:null;
  if(!PL&&L1&&L0.med!=null&&L1.med!=null&&isFinite(L0.med)&&isFinite(L1.med)&&(LD!=null?LD>0:L1.med>L0.med)){const a=L0.med,b=L1.med,y=top+gap-12,early=LD!=null?LD:b-a;  // 上面那条真的更早才写「早多少」
    g+=`<line x1="${sx(a)}" x2="${sx(a)}" y1="${top+h}" y2="${top+gap+h/2}" stroke="var(--muted)" stroke-dasharray="2 3"/><line x1="${sx(a)}" x2="${sx(b)}" y1="${y}" y2="${y}" stroke="var(--muted)"/><text x="${(sx(a)+sx(b))/2}" y="${y-6}" text-anchor="middle" font-size="12" fill="var(--muted)" class="halo">早 ${f(early,2)} 秒</text>`;}
  if(PL){const sv=plainServer(D),tiny=rows.flatMap(r=>r.tiny.map(t=>({...t,where:plainWhere(D,t.k)}))),ts=tiny.reduce((t,x)=>t+Math.max(0,x.v||0),0);
   const say=r=>{const wh=plainWhere(D,r.k);return wh==='cloud'?'这段在设备外，设备改不了。':`属于${plainWho(r.owner)||'网络'}。`};
   // 设备外那段写它自己在等什么（后台按段名、"服务器负责什么"算好的 plain，见 standards.plain_cloud）；老存档没有就照旧写"服务器负责什么"
   const cloudLbl=k=>{const p=String((((D.J.scenario||{}).segments||{})[k]||{}).plain||'').trim()||sv;return `等云端${p?`（${p}）`:''}`};
   const tnOf=tiny=>{const ts=tiny.reduce((t,x)=>t+Math.max(0,x.v||0),0);return !tiny.length?'':ts<0.05&&tiny.every(x=>x.where==='dev')?'设备内部还有几步，加起来不到 0.05 秒，可忽略。':`另有几步很短，加起来约 ${f(ts)} 秒（${tiny.map(x=>htmlEsc(x.name)).join('、')}）。`};
   const one=(r,of)=>`<li><span class="nm"><b>${htmlEsc(plainWhere(D,r.k)==='cloud'?cloudLbl(r.k):r.name)}</b>：一般 ${f(r.d)} 秒，占${of} ${Math.round(r.share*100)}%。${say(r)}${r.also&&r.also.length?`<span class="note">（${r.also.map(n=>`「${htmlEsc(n)}」`).join('、')}也走这一段）</span>`:''}</span></li>`;
   const UL='class="plainlst" style="margin:8px 0 0;padding-left:20px;line-height:1.7;font-size:14px"';
   const tail=`图里的数是 ${D.n} 次里的一般情况（中位数）；各段加起来和总数可能差几十毫秒，是正常的。`;
   if(drawn.length<2){const li=rows.map(r=>one(r,'')).join(''),tn=tnOf(tiny);
    return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" style="min-width:640px;width:100%" role="img" aria-label="时间花在哪">${g}</svg></div>
  <ul ${UL}>${li}</ul><p class="note" style="margin:6px 0 0">${tn}${tail}</p>`}
   // 几条线（10-26 用户：0.04 秒那行看不出是哪条线的、占的是哪条线）：按线分组，每行写占这条线的百分之几；
   // 几条线共用的段只写一次（写在第一次画到它的那条线下面），注明还有哪几条也走；和上面画过的线一样的开头只写一句
   const M=D.main,grp=drawn.map(i=>{const l=D.lines[i],mine=rows.filter(r=>r.li===i).map(r=>({...r,also:D.lines.filter((o,j)=>j!==i&&drawn.includes(j)&&(o.segs||[]).includes(r.k)).map(o=>o.name)}));
     const tn=tnOf(mine.flatMap(r=>r.tiny.map(t=>({...t,where:plainWhere(D,t.k)})))),pre=sameTo(D,l,rows,i),side=!M||l.name!==M.name;
     const [nm,note]=splitNm(l.name),why=[note,side?'顺带的线，不是上面的主数':''].filter(Boolean).join('；');
     return `<li style="margin-top:6px"><b>${htmlEsc(nm)}</b>${why?`<span class="note">（${htmlEsc(why)}）</span>`:''}：一般 ${f(l.med)} 秒到「${htmlEsc(splitNm(l.end)[0])}」${pre?`；${htmlEsc(pre)}`:''}
      ${mine.length?`<ul class="plainlst" style="margin:2px 0 0;padding-left:18px;line-height:1.7;font-size:14px">${mine.map(r=>one(r,'这条线')).join('')}</ul>`:''}${tn?`<div class="note">${tn}</div>`:''}</li>`});
   return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" style="min-width:640px;width:100%" role="img" aria-label="时间花在哪">${g}</svg></div>
  <ul ${UL}>${grp.join('')}</ul><p class="note" style="margin:6px 0 0">${tail}</p>`}
  const list=rows.map(r=>`<li><span class="no">${r.no}</span><span class="nm">${htmlEsc(r.name)}${r.shared?'（共用）':''}</span><span class="r num">${f(r.d)} 秒</span><span class="r num">${Math.round(r.share*100)}%</span><span class="ow">${htmlEsc(r.owner)}</span><span class="ow">${r.inc?`另含极短段：${r.tiny.map(t=>`${htmlEsc(t.name)} ${f(t.v)} 秒`).join('、')}`:''}</span></li>`).join('');
  return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" style="min-width:640px;width:100%" role="img" aria-label="计时图">${g}</svg></div>
  <ol class="tlst">${list}</ol><p class="note" style="margin:6px 0 0">按 ${D.n} 轮中位数画：每条线总长是这条线的中位数；块里和上面列的秒数是这一段自己的中位数，和「分段用时」一样（各段中位数加起来和整条线的中位数不一定相等，色块按比例画）。不到 5% 的段并进相邻的块，名字和秒数单独写出。</p>`;
}
// 把一条线的各段归成阶段：share 不到 5% 的段并进后面那个大段，排在最后的并进前一个大段；全都极短时合成一块
function stages(parts){const out=[];let pend=[];
  parts.forEach(p=>{if(p.share<0.05){pend.push(p);return}out.push({parts:[p,...pend],d:p.d,share:p.share});pend.forEach(q=>{out[out.length-1].d+=q.d;out[out.length-1].share+=q.share});pend=[]});
  if(pend.length){if(!out.length)out.push({parts:[],d:0,share:0});const o=out[out.length-1];pend.forEach(q=>{o.parts.push(q);o.d+=q.d;o.share+=q.share})}
  return out}
// "偶发特别慢"点名的那几轮（和拍板区说的"逐轮图里红底的几轮"对上，真测第 36 次）：轮号 → 哪一段
function spikyRounds(D){const m=new Map(),J=D.J;
 if(J)J.labels.filter(x=>x.kind==='spiky'&&!x.with).forEach(x=>{const s=D.allSegs.find(y=>y.k===x.segment);(x.rounds||[]).forEach(n=>m.set(n,s?s.name:x.segment))});
 else if(D.spiky){const s=D.allSegs.find(y=>y.k===D.spiky.k);s&&s.v.forEach((x,i)=>{if(x>s.med*2.5)m.set(i+1,s.name)})}
 return m}
function roundsChart(D){
  const T=niceT(Math.max(...D.rounds.flatMap(r=>r.ends))/1.2);const W=900,X0=70,X1=820,sx=t=>X0+(X1-X0)*t/T,rh=20,top=34,st=step(T);
  const spk=spikyRounds(D),prob=new Map(),add=(n,t)=>prob.set(n,prob.has(n)?prob.get(n)+'；'+t:t),J=D.J?D.J.labels:[];
  J.filter(x=>x.kind==='problem'&&!x.order).forEach(x=>(x.rounds||[]).forEach(n=>add(n,x.name)));
  J.filter(x=>x.kind==='line'&&x.rounds).forEach(x=>{const l=D.lines.find(y=>y.name===x.line);x.rounds.forEach(n=>add(n,`${l?l.end:x.line}超过标准 ${x.tail} 倍`))});  // 必须改点名的"超过标准 1.5 倍"那几轮
  const bad=new Set([...D.reversed.map(r=>r.n),...spk.keys(),...prob.keys()]);  // 红底只给「要你拍板」点名的轮；超标看红虚线
  let g='';
  for(let t=0;t<=T+1e-9;t+=st){g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="${top-8}" y2="${top+D.n*rh}" stroke="var(--soft)"/><text x="${sx(t)}" y="${top-14}" text-anchor="middle" font-size="12" fill="var(--muted)" class="num">${+t.toFixed(2)}</text>`}
  D.rounds.forEach((r,i)=>{const y=top+i*rh;const isBad=bad.has(r.n);
    if(isBad)g+=`<rect x="0" y="${y}" width="${W}" height="${rh}" fill="var(--bad-soft)"/>`;
    g+=`<text x="${X0-12}" y="${y+14}" text-anchor="end" font-size="12" fill="${isBad?'var(--bad)':'var(--muted)'}" class="num">第 ${r.n} 轮</text>`;
    g+=`<line x1="${sx(0)}" x2="${sx(Math.max(...r.ends))}" y1="${y+10}" y2="${y+10}" stroke="var(--line)" stroke-width="2"/>`;
    r.ends.forEach((e,j)=>{if(e==null)return;g+=j===0?`<circle cx="${sx(e)}" cy="${y+10}" r="5" fill="${D.lines[j].col}"/>`:`<rect x="${sx(e)-5}" y="${y+5}" width="10" height="10" fill="${D.lines[j].col}"/>`});
    const why=[prob.get(r.n),D.reversed.some(x=>x.n===r.n)?`${D.lines[0].end}比${D.lines[1].end}晚`:'',spk.has(r.n)?`${spk.get(r.n)}特别慢`:''].filter(Boolean).join('；');
    const xe=sx(Math.max(...r.ends)),left=xe+10+why.length*12>W;  // 放不下就写在点的左边
    if(why)g+=`<text x="${left?xe-10:xe+10}" y="${y+14}" text-anchor="${left?'end':'start'}" font-size="12" font-weight="700" fill="var(--bad)" class="halo">${why}</text>`;});
  D.lines.forEach(l=>{g+=`<line x1="${sx(l.med)}" x2="${sx(l.med)}" y1="${top-8}" y2="${top+D.n*rh}" stroke="${l.col}" stroke-dasharray="3 3"/>`});
  const fairs=[...new Set((D.J?D.J.lines:[]).map(j=>j.exp_std&&j.exp_std.fair).filter(x=>x!=null&&x<=T))];
  fairs.forEach(fa=>{g+=`<line x1="${sx(fa)}" x2="${sx(fa)}" y1="${top-8}" y2="${top+D.n*rh}" stroke="var(--bad)" stroke-width="1.5" stroke-dasharray="6 3"/>`});
  return `<div class="scroll"><svg viewBox="0 0 ${W} ${top+D.n*rh+6}" style="min-width:640px;width:100%" role="img" aria-label="逐轮计时">${g}</svg></div>
  <div class="legend">${D.lines.map((l,j)=>`<span><i class="sw" style="background:${l.col};width:10px;height:10px;${j===0?'border-radius:50%':''}"></i>${l.end}</span>`).join('')}<span>细虚线：中位数</span>${fairs.length?`<span style="color:var(--bad)">红粗虚线：体验标准 ${fairs.join('、')} 秒</span>`:''}${bad.size?'<span style="color:var(--bad)">红底：「要你拍板」点名的轮</span>':''}</div>`;
}
// 达标率（10-20 用户定，换掉自造的"10 次里最慢"）：没超过体验标准的轮次占几成；没有体验标准的线写「—」
const passPct=l=>l.pass_rate==null?null:`${Math.round(l.pass_rate*100)}%`;
const passTxt=l=>l.pass_rate==null?'':`达标率 ${passPct(l)}（${l.pass_n}/${l.count}）`;
function tiers(D){  // 依据合成一张表（10-21 样子）：一般 ｜ 最差 ｜ 达标率 ｜ 体验标准 ｜ 和上次比；名字和「要你拍板」一样用线的终点
  const jl=l=>D.J&&D.J.lines.find(x=>x.name===l.name);
  const std=l=>{const j=jl(l),es=j&&j.exp_std;return es&&es.fair!=null?`超过 ${es.fair} 秒算差${es.good!=null?`，${es.good} 秒内算好`:''}`:'—'};
  const vs=l=>{const j=jl(l),v=j&&j.vs;return v?`${f(v.prev)} → ${f(l.med)} 秒，<b class="${v.change==='慢了'?'up':'down'}">${v.change}</b>`:'第一次测'};
  return `<div class="tiers num"><span></span><span class="h">一般（中位数）</span><span class="h">最差</span><span class="h">${(typeof plainOn==='function'&&plainOn(D))?'在标准内的次数':'达标率'}</span><span class="h">体验标准</span><span class="h">和上次比</span>
  ${D.lines.map(l=>`<b style="color:${l.col};font-family:var(--sans)">${l.end}</b><span>${f(l.med)} 秒</span><span>${f(l.max)} 秒</span><span${l.pass_rate==null?' style="font-family:var(--sans)"':''}>${l.pass_rate==null?'—':`${passPct(l)}（${l.pass_n}/${l.count}）`}</span><span style="font-family:var(--sans)">${std(l)}</span><span style="font-family:var(--sans)">${vs(l)}</span>`).join('')}</div>`;
}
function stdNote(D){  // 表下一行：达标率怎么算 + 标准出处（每条标准只写一次）
  const seen=new Set(),src=[];(D.J?D.J.lines:[]).forEach(j=>{const es=j.exp_std;if(es&&es.source&&!seen.has(es.source)){seen.add(es.source);src.push(srcHtml(es.source).replace(/。$/,'')+(es.good==null?'（只有及格线）':''))}});
  return `<p class="note" style="margin:8px 0 0">${(typeof plainOn==='function'&&plainOn(D))?'在标准内的次数：这几次里没超过体验标准的有几次（行业标准看的是平均，这一列只作补充）':'达标率：没超过体验标准的轮次占几成'}${D.n<10?`；只测了 ${D.n} 轮，只供参考`:''}。${src.length?`体验标准出处：${src.join('；')}。`:'这个场景没有对得上的体验标准。'}</p>`;
}
// 负责人版写短（决策 98）：越重要越靠前：出错、必须改、建议改、未判，都展开；达标的收起来（10-20 用户定）。内容不删，只是默认收起
function bossSplit(ds){const by=lv=>ds.filter(x=>x.lv===lv);return {open:[...by(4),...by(0),...by(1),...by(2)],fold:by(3)}}  // 未判的不收进"不用改"：没判不等于没问题
// 要你拍板每条加一行：建议做哪个、预计省多少（取卡片里标了"建议"的方案）
function recLine(F){const o=F&&F.opts?(F.opts.find(o=>o.rec)||F.opts[0]):null;if(!o)return '';
  // 一句话里只留最关键的效果：优先摘出"约快 X 秒"，摘不出来、全文又不长才整句放，否则只写做什么，效果去"详情"里看
  const e=String(o.effect||''),m=e.match(/(?:约快|快约|快)\s*[\d.]+(?:～[\d.]+)?\s*秒/),eff=m?`，预计${m[0]}`:e&&e.length<=30?`，预计${e}`:'';
  return `建议：${o.name}${eff}。`}  // 出错 / 建议改 / 必须改每条一句的"建议"
// 设备自己的段都正常时，拍板列表末尾说一句（10-21 用户定）：负责人知道不用找固件研发；明细在分段用时表里
function devOk(D){const g=D.J?D.J.segments.filter(x=>!x.outside):[];if(!g.length||g.some(x=>x.spiky||x.bad||x.verdict==='慢了'))return '';
  return `<p class="note" style="margin:6px 0 0;color:var(--fg)">设备自己的几段都正常${D.J.prev&&D.J.prev.segs?'，也没比上次慢':''}，不用改。</p>`}
// 结论版的"结论和建议"（10-26）：主指标按平均已达标就不再列"建议改"（顶上几句已经说清，免得和"设备都正常"打架）；次要的线、全程、未判的不列；
// 标签换成人话，"标签规则"换成"这些说法怎么定的"
function plainDecide(D){const FX=activeFixes(D),M=mainFirst(D),st=plainStd(D,M),sl=D.J.scenario.lines||[],t=plainSplit(D,M),tot=t.cloud+t.dev+t.mix;
  const side=n=>{const x=sl.find(y=>y.name===n);return !x||x.also_first||x.measure};
  const ds=decisions(D).filter(x=>x.lv!==2&&x.lv!==3).filter(x=>{if(x.k==='main')return !(st&&st.ok);if(String(x.k).startsWith('line:'))return !side(String(x.k).slice(5));return true})
   .map(x=>x.k==='main'&&st?{...x,t:`${htmlEsc(M.end)} ${f(M.med)} 秒`,d:`平均 ${f(st.avg)} 秒，超过行业标准（平均不超过 ${st.fair} 秒）；${st.n} 次里 ${st.over} 次超过。${tot>0&&t.cloud/tot>=.7?'慢主要在云端，改设备代码帮不上。':''}`}:x);
  const tag=lv=>`<span class="lvtag ${LV[lv][1]}">${PLAIN_LV[lv]}</span>`;
  const li=x=>{const n=cardOf(FX,x),r=n>=0?recLine(FX[n]):'';return `<li><span class="tagcol">${tag(x.lv)}</span><span><b>${x.t}</b>：${x.d}${r?` <b>${r}</b>`:''}　${n>=0?jump('fix-'+n,'详情 → 问题 '+(n+1)):''}${jump('sec-'+x.src[0],'依据 → '+String(x.src[1]).replace('用时和达标率','用时和标准'))}</span></li>`};
  return `${ds.length?`<ul class="decide">${ds.map(li).join('')}</ul>`:'<p class="note" style="margin:0 0 8px">没有需要你处理的。</p>'}${devOk(D)}
  <details class="lvwhy"><summary>这些说法怎么定的</summary><p class="note" style="margin:8px 0">数字由码表按这几次的数据直接算出，不经过 AI；"为什么、怎么改"是 AI 读代码写的。</p><ul class="decide rules">
  <li><span class="tagcol">${tag(4)}</span><span>功能出错了，先不看快慢。</span></li>
  <li><span class="tagcol">${tag(0)}</span><span>明显比标准慢，或者很多次都超过标准很多。</span></li>
  <li><span class="tagcol">${tag(1)}</span><span>平均还行，但不够稳定，或者比上次慢了。</span></li></ul>
  <p class="note" style="margin:4px 0 0">行业标准看的是平均用时；偶尔超过一两次，只作为补充写出来。</p></details>`}
function decideList(D){if((typeof plainOn==='function'&&plainOn(D)))return plainDecide(D);const FX=activeFixes(D),{open,fold}=bossSplit(decisions(D));
  // 每条一句（10-20 用户定）：标签（右对齐一列）、线名 用时：问题。建议：做什么，预计效果。　详情 → 问题 N　依据 → 数据
  // 标签规则（10-20 用户定）：一句来源 + 每个标签一行，和上面列表同一套标签、同样右对齐
  // 还有一个选择（10-21 用户定）：同一个问题里有找别人做的方案，点一句，免得藏在收起的方案表里；同一个方案只点一次
  const said=new Set(),alt=F=>{const r=F.opts.find(o=>o.rec)||F.opts[0],a=F.opts.find(o=>!o.rec&&o.who!==r.who&&!said.has(o.name));if(!a)return '';said.add(a.name);return `<br><span class="note" style="color:var(--fg)">还有一个选择：${a.name}。${a.effect}；${a.cost}。</span>`};
  const li=x=>{const n=cardOf(FX,x),r=n>=0?recLine(FX[n]):'';return `<li><span class="tagcol">${lvTag(x.lv)}</span><span><b>${x.t}</b>：${x.d}${r?` <b>${r}</b>`:''}　${n>=0?jump('fix-'+n,'详情 → 问题 '+(n+1)):''}${jump('sec-'+x.src[0],'依据 → '+x.src[1])}${n>=0?alt(FX[n]):''}</span></li>`};
  return `${open.length?'':'<p class="note" style="margin:0 0 8px">这次没有需要处理的。</p>'}<ul class="decide">${open.map(li).join('')}</ul>${devOk(D)}${fold.length?`<details class="lvwhy"><summary>另有 ${fold.length} 条达标 / 不判快慢</summary><ul class="decide">${fold.map(li).join('')}</ul></details>`:''}
  <details class="lvwhy"><summary>标签规则</summary><p class="note" style="margin:8px 0">数字和标签由码表按规则算，"为什么、怎么改"由 AI 读代码写。</p><ul class="decide rules">
  <li><span class="tagcol">${lvTag(4)}</span><span>功能出错，不看快慢。</span></li>
  <li><span class="tagcol">${lvTag(0)}</span><span>达标率 &lt; 50%，或超过标准 1.5 倍的轮次 &gt; 10%。</span></li>
  <li><span class="tagcol">${lvTag(1)}</span><span>达标率 &lt; 90%，或比上次慢 0.1 秒且 10% 以上。</span></li>
  <li><span class="tagcol">${lvTag(2)}</span><span>没有体验标准，也没有上次可比。</span></li></ul><p class="note" style="margin:4px 0 0">达标率：这几轮里没超过体验标准的占几成。测不满 10 轮时只按达标率 &lt; 50% 判。</p></details>`}
// 数据检查（决策 122）：这份报告用了几轮、丢了几轮、为什么丢，让人自己判断准不准
// 起点按计时点自己的名字叫（比如"设备听出你停了"），不叫"你说完"：它是设备自己察觉的时刻，比人真正做完晚（决策 135）
function startLabel(P,sc){const p=sc&&sc.start&&sc.start.point,s=(P.steps||[]).find(x=>x.p===p);return s?s.n:(P.measure||'起点').split('→')[0].trim()}
// 最值得优化的一段（10-19 用户定，固定点名）：设备上能改的段里占用时最多的，只写占多少，不分等级（后台 judge 算的 best_fix）
function bestFix(D){const b=D.J&&D.J.best_fix;if(!b)return '';const l=D.lines.find(x=>x.name===b.line);
 return `<p style="margin:0 0 10px">最值得优化的一段：<b>「${htmlEsc(b.name)}」</b>，一般 ${fms(b.med)} 秒${b.share!=null?`，占「${htmlEsc(l?l.end:b.line)}」的 ${(+b.share).toFixed(1)}%`:''}<span class="note">（设备上能改的段里最大的；服务器、网络那几段不算）</span>${b.device_share!=null?`。设备上能改的段合计占 ${(+b.device_share).toFixed(1)}%，其余是服务器、网络、回答长短`:''}</p>`}
// 起点比人真正做完晚多少：没核对过时，用标准库里这一类设备写的估计（写明是估计，不是标准）
const startGap=J=>{const x=(J.lines||[]).find(l=>l.exp_std&&l.exp_std.start_gap);return x?{txt:x.exp_std.start_gap,cat:x.exp_std.category}:null};
function startLagLine(name,L,gap){return `<p class="note" style="margin:4px 0 0">起点是「${htmlEsc(name)}」，是设备自己察觉的时刻。${!L&&gap?`人实际做完的那一刻还要早，${htmlEsc(gap.cat?`按「${gap.cat}」类设备`:'')}${htmlEsc(gap.txt)}，还没核对过；实际等的时间会比报告里长一点。`:L?`人实际停嘴还要再早约 ${(Math.round(L.secs*10)/10).toFixed(1)} 秒（${htmlEsc(L.when||'')} 用 ${htmlEsc(L.how||'录音')}核对过${L.rounds?`，${L.rounds} 轮`:''}），按人的感受，各条线的用时都要再加上这么多。`:'人实际做完的那一刻还要早一点，码表看不到，还没核对过。'}</p>`}
// 「记最后一次」的中间点越过了下一步：前后两段合成了一段（见 judge.merge_overruns），写明合了哪两段、为什么
const mergedLine=M=>(M||[]).map(m=>`<p class="note" style="margin:4px 0 0">「${htmlEsc(m.names[0])}」和「${htmlEsc(m.names[1])}」合成一段算：${htmlEsc(m.why)}。</p>`).join('');
function qualityLine(Q,J){if(!Q)return '';const nb=((J&&J.checks)||[]).filter(x=>x.kind==='bad'||x.kind==='bad_line').length;const drop=[Q.missing?`${Q.missing} 轮没收全`:'',Q.stale?`${Q.stale} 轮记到的是上一轮剩下的`:'',Q.half?`${Q.half} 次开头没收到`:'',Q.nostart?`${Q.nostart} 次没记到起点「${Q.startName||'起点'}」`:''].filter(Boolean);
 return `<p class="note" style="margin:4px 0 0">数据检查：用了 ${Q.ok} 轮，${nb?`都完整收齐，但有 ${nb} 段 / 条线的计时点先后不对（出了负数，见上面的数据检查），那几处没算进结论`:'都是完整收齐、计时点先后正常的'}${drop.length?`；另有 ${drop.join('、')}，这些不算进来，已经自动补测`:'，没有丢掉的'}${gapLine(Q.gap)}${(Q.late||[]).map(x=>`；「${htmlEsc(x.name)}」有 ${x.n} 次在每轮清零 1 秒以后才到，没算进那一轮`).join('')}${(Q.choices||[]).map(c=>`；测量中停下来问过，选了${c.choice==='continue'?'接着测':'去改计时点'}`).join('')}。数字都是码表按这些轮算的，不经过 AI。</p>`}
// 报告最上面的数据检查（judge 的数据不足 + 测量过程里要提醒的）：不收在折叠里，一眼看得到
function checksBox(J){const C=(J&&J.checks)||[];if(!C.length)return '';const thin=C.filter(x=>x.kind==='thin'||x.kind==='thin_line'),bad=C.filter(x=>x.kind==='bad'||x.kind==='bad_line');
 const why=[bad.length?`${bad.length} 段 / 条线数据有问题，没算进结论、图和主数`:'',thin.length?`${thin.length} 段 / 条线数据不足，那几处的数不当结论`:''].filter(Boolean).join('；');
 return `<div class="errbox" style="border-color:${bad.length?'var(--bad)':'var(--warn)'};background:color-mix(in srgb,var(--warn) 7%,var(--panel))"><b>数据检查：有 ${C.length} 条要注意${why?`（${why}）`:''}</b>${C.map(x=>`<p style="margin:4px 0">${htmlEsc(x.why)}</p>`).join('')}</div>`}
// 中间的点不是这一轮的（打断回答后服务器补发的那次）：这一轮照样算，用到它的两段空着，整条线按首尾算
function gapLine(G){if(!G||!G.length)return '';const by={};G.forEach(g=>g.names.forEach(x=>(by[x]=by[x]||[]).push(g.n)));
 return '；'+Object.entries(by).map(([x,ns])=>`第 ${ns.join('、')} 轮没记到起点之后的「${x}」（只有起点之前的一次，那是上一轮的），这几轮用到它的两段空着，整条线照样按首尾算`).join('；')}
function subline(D){
  if((typeof plainOn==='function'&&plainOn(D)))return plainWhy(D);
  // 只留一句判断（10-20 用户定）：哪条线按哪类设备的标准算什么；"可以"等标签悬停看离"好"差多少。
  // 别的（谁比谁早、出错的轮次、偶发慢）各有自己的位置，不挤在这里
  const je=D.J&&D.J.lines?D.J.lines.filter(x=>x.exp):[];  // 体验标准挂在哪条线上就说哪条（比如只挂在"到喇叭出第一声"）
  const chip=(x,l)=>`<span class="chip tip ${x.exp==='差'?'c-bad':'c-ok'}" tabindex="0">${x.exp}<i class="tipbox">${htmlEsc(expTip(x,l))}</i></span>`;
  if(je.length)return je.map(x=>{const l=D.lines.find(l=>l.name===x.name)||{end:x.name,med:x.med};return `${l.end}，按${x.exp_std&&x.exp_std.category?`「${htmlEsc(x.exp_std.category)}」类设备的`:''}体验标准算${chip(x,l)}`}).join('；')+'。';
  if(D.exp)return `按体验标准算<span class="chip ${D.exp[1]}">${D.exp[0]}</span>。`;
  return noExp(D)+'。';
}
// 体验标准标签的悬停说明：及格线、理想水平（标准里给了"好"才有）、离它们差多少
function expTip(x,l){const e=x.exp_std||{},m=x.med!=null?x.med:l.med;if(e.fair==null)return '';
 const fair=`及格线 ${e.fair} 秒`,good=e.good!=null?`；理想水平 ${e.good} 秒`:'';
 if(x.exp==='差')return `${fair}，这条线一般要 ${f(m)} 秒，超过了${good}`;
 if(e.good!=null&&x.exp==='可以')return `${fair}，已达到${good}，还差 ${f(m-e.good)} 秒`;
 if(x.exp==='好')return `${fair}，已达到${good}，也达到了`;
 return `${fair}，已达到（这个标准没给"好"的档）`}

// 技术细节（10-21 用户定）：不写计时点编号（P1→P2 谁也看不懂），按线分组；代码位置写"路径:行号 + 函数名"，点"展开代码"在这段下面整行显示前后各 3 行；
// 判断只标有问题的；不再有测量条件、计时点开销推算、码表自己用了多久（决策 99 改了）
const SANS='style="font-family:var(--sans)"';
function codeRows(C,cols){if(!C)return '';const pad=Math.min(...C.ctx.filter(([,t])=>t.trim()).map(([,t])=>t.length-t.trimStart().length));
 return `<tr class="coderow" hidden><td colspan="${cols}"><pre class="codectx">${C.ctx.map(([n,t])=>`<span class="${n===C.l?'hit':''}"><i>${n}</i>${htmlEsc(t.slice(pad))}</span>`).join('\n')}</pre></td></tr>`}
function codeTog(e){const b=e.target.closest&&e.target.closest('[data-code]');if(!b)return false;const r=b.closest('tr').nextElementSibling;if(r&&r.classList.contains('coderow')){r.hidden=!r.hidden;b.textContent=r.hidden?'展开代码':'收起代码'}return true}
function prevCell(PV,now,p,nz){if(!PV)return '';return `<td class="r wrap">${p==null?'—<br><span class="note">上次没测到</span>':p<0?'—<br><span class="note">上次先后反了</span>':`${fms(p)}<br>${cmpTxt(now,p,nz)}`}</td>`}
// 路径、函数名太长时只在 / 和 :: 后面换行，文件名:行号、函数名本身不拆开（10-21：拆成 application.cc:67 / 1 没法看）
const brk=t=>htmlEsc(t).replace(/(\/|::)/g,'$1<wbr>');
const OKNOTE='<span class="note">正常</span>';
function segVerdict(D,s){const j=D.J&&D.J.segments.find(x=>x.k===s.k);
 if(j&&j.bad)return `<span class="chip c-bad">数据有问题</span>`;
 if(j&&j.thin)return `<span class="chip c-warn">数据不足（${j.count}/${j.of} 轮）</span>`;
 if(j?j.spiky:s.max>s.med*2.5&&s.max-s.med>.3)return '<span class="chip c-warn">偶发特别慢</span>';
 if(j&&j.verdict==='慢了')return '<span class="chip c-warn">比上次慢</span>';return OKNOTE}
function dev(D){const T=D.S.tech,PV=D.J&&D.J.prev&&D.J.prev.segs?D.J.prev:null,CODE=(JREP&&JREP.code)||{},cols=PV?7:6;
  const segRow=s=>{const to=String(s.code||'').split('→')[1],C=D.S.real?CODE[to]:null;
   const loc=C?`<span class="code">${brk(C.path)}:${C.l}</span>${C.func?`<br><span class="note code">${brk(C.func)}</span>`:''}<br><button class="pill codebtn" data-code="1">展开代码</button>`:`<span class="code">${T.loc[s.k]||''}</span>`;
   return `<tr><td ${SANS}>${s.name}</td><td>${loc}</td><td class="own" ${SANS}>${s.owner}</td>${s.thin?`<td class="r wrap" colspan="${PV?3:2}" ${SANS}>数据不足（${s.count}/${s.of} 轮）<br><span class="note">${htmlEsc(s.thinWhy)}</span></td>`:`<td class="r">${fms(s.med)}</td><td class="r">${fms(s.max)}</td>${prevCell(PV,s.med,PV&&PV.segs[s.k],PV&&(PV.noise||{})[s.k])}`}<td ${SANS}>${segVerdict(D,s)}</td></tr>${codeRows(C,cols)}`};
  const groups=D.lines.map(l=>{const [v]=D.verdict(l),jl=D.J&&D.J.lines.find(x=>x.name===l.name),ex=jl?(jl.exp?[jl.exp,jl.exp==='差'?'c-bad':'c-ok']:null):l===D.main?D.exp:null;
   const jd=(v==='比上次慢了'?'<span class="chip c-warn">比上次慢</span> ':'')+(ex?`<span class="chip ${ex[1]}">体验${ex[0]}</span>`:'')||OKNOTE;
   const segs=l.segs.map(k=>D.allSegs.find(s=>s.k===k)).filter(Boolean),worst=l.max!=null?l.max:Math.max(...l.ends.filter(x=>x!=null));
   return `<tr class="grp"><td colspan="${cols}" ${SANS}><b>${l.end}</b> <span class="note">从「${htmlEsc(D.S.startShort)}」到「${l.end}」，共 ${segs.length} 段</span></td></tr>${segs.map(segRow).join('')}
   <tr><td ${SANS}><b>${l.end} 合计</b>${(()=>{const ms=segs.map(x=>x.med).filter(x=>x!=null&&isFinite(x)),sm=ms.reduce((t,x)=>t+x,0);
     // 合计是每轮从起点到终点的用时再取中位；和各段中位数相加不一定相等，差得出来就写明，免得以为算错（10-26）
     return l.med!=null&&ms.length===segs.length&&Math.abs(sm-l.med)>=0.005?`<br><span class="note">各段中位数相加 ${f(sm)}；合计按每轮首尾算再取中位</span>`:''})()}</td><td></td><td></td><td class="r"><b>${f(l.med)}</b></td><td class="r"><b>${f(worst)}</b></td>${prevCell(PV,l.med,PV&&PV.lines[l.name],PV&&(PV.noise_lines||{})[l.name])}<td ${SANS}>${jd}</td></tr>`}).join('');
  const shortest=Math.min(...D.allSegs.map(s=>s.med).filter(x=>x>0));
  const ovNote=D.S.real&&isFinite(shortest)?`计时点本身的开销不超过 ${fms(shortest)} 秒（这次最短的一段只测出这么多）${shortest<.01?'，可以忽略':''}。<br>`:'';
  const bad=[...new Set([...D.reversed,...D.slowRounds])].sort((a,b)=>a.n-b.n);
  // 慢在哪段按超标的那条线算（没超标的按主线）：后台 judge 每条线一份 round_cause
  const why=r=>{const JL=(D.J&&D.J.lines)||[],ln=D.lines.find((l,i)=>{const fa=((JL.find(x=>x.name===l.name)||{}).exp_std||{}).fair;return fa!=null&&r.ends[i]!=null&&r.ends[i]>fa})||D.main,
   c=((JL.find(x=>x.name===ln.name)||{}).round_cause||[]).find(x=>x.n===r.n);return c&&c.more>=.1?c:null};
  const ks=[...new Set(bad.map(r=>(why(r)||{}).k))],one=bad.length>1&&ks.length===1&&ks[0]?D.allSegs.find(s=>s.k===ks[0]):null;
  const outside=s=>{const j=D.J&&D.J.segments.find(x=>x.k===s.k);return j&&j.outside};
  return `<div class="stack">${expDone()}<div><div class="titlerow"><h1>技术细节</h1>${vsTag()}</div><p class="sub">给研发：每段在代码哪里、慢在哪、怎么改。单位秒。${realFlash()?'':'这里只演示样子。'}</p></div>
  <section class="panel scroll"><h2>分段用时</h2><table class="num segtab"><thead><tr><th class="segn">段</th><th class="loc">代码位置</th><th class="own">谁负责</th><th class="r">一般<br>（中位数）</th><th class="r">最差</th>${PV?`<th class="r">上次<br><span class="note">${htmlEsc(String(PV.measured_at||'').replace(/^\d{4}-/,''))}</span></th>`:''}<th>判断</th></tr></thead><tbody>${groups}</tbody></table>
  <p class="note" style="margin:8px 0 0">${ovNote}比上次慢：差同时大于 0.1 秒和上次的 10%。偶发特别慢：最差超过一般的 2.5 倍，且多出 0.3 秒以上。</p></section>${recOnlyBox(D)}
  ${bad.length?`<section class="panel scroll"><h2>有问题的轮</h2><table class="num"><thead><tr><th>轮</th>${D.lines.map(l=>`<th class="r">${l.end}</th>`).join('')}<th>慢在哪段</th></tr></thead><tbody>${bad.map(r=>{const c=why(r);return `<tr class="late"><td>第 ${r.n} 轮</td>${r.ends.map(e=>`<td class="r">${e==null?'—':f(e)}</td>`).join('')}<td ${SANS}>${c?`${htmlEsc(c.name)}：${f(c.v)} 秒（一般 ${f(c.med)}）`:'<span class="note">没有哪一段特别慢，各段都慢一点</span>'}</td></tr>`}).join('')}</tbody></table>${one?`<p class="note" style="margin:8px 0 0">这几轮都慢在「${htmlEsc(one.name)}」${outside(one)?'，这段在设备外':''}。</p>`:''}</section>`:''}${fixDev(D)}
  <section class="panel"><h2>原始日志</h2><p style="margin:0 0 6px"><span class="code">${T.log}</span></p><p class="note" style="margin:0 0 8px">设备调试口原样输出，没有删改；码表插的计时点行以 FSW 开头。报告里的数都从它算。</p><div class="acts"><button class="pill" data-openlog="1">打开所在文件夹</button></div></section>${retestEntry()}${restoreBox(scene)}</div>`;
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
 {id:'compare',name:'前后对比',done:'已定'}];

const arrow=(now,prev)=>{const d=now-prev;if(Math.abs(d)<.03||Math.abs(d)<prev*.05)return '<span class="note">持平</span>';return d<0?`<span class="down">↓ 快了 ${f(-d)} 秒</span>`:`<span class="up">↑ 慢了 ${f(d)} 秒</span>`};  // 变化不到 0.03 秒或 5% 算持平，不报假进步（决策 156）
// 已保存的设置（/api/state 读设置文件夹；密钥只知道有没有，不回传）、本机 Claude Code 登没登录、插着的设备（/api/env、/api/ports 真查）
let SAVED={llm_provider:null,has_key:{},data_dir:'',idf_path:''},CCST=null,PORTS=[],dataDraft=null,idfErr='',portT=null;
const P2UI={'claude-api':'claude','claude-code':'cc',deepseek:'deepseek'},UI2P={claude:'claude-api',cc:'claude-code',deepseek:'deepseek'};
const ccOK=()=>!!(CCST&&CCST.logged_in),hasKey=ui=>!!SAVED.has_key[UI2P[ui]];
const keyOK=()=>setLLM==='cc'?ccOK():keyTested||hasKey(setLLM);
const tilde=p=>String(p||'').replace(/^\/Users\/[^/]+|^\/home\/[^/]+/,'~');
// 每 2 秒查一次设备和编译工具（不跑 claude 命令）：插上设备、自己装好编译工具，灯和设置页都自动变
async function portsPoll(){try{const e=await (await fetch('/api/env?cc=0')).json(),n=e.ports||[],idf=(e.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1),tm=JSON.stringify(e.tools||null)!==JSON.stringify(TOOLS);TOOLS=e.tools||null;if(tm&&(page==='home'||page==='settings'))render();const ch=n.join()!==PORTS.join()||idf.map(i=>i.path).join()!==IDFS.map(i=>i.path).join();PORTS=n;IDFS=idf;if(!ch)return;if(page==='flash'&&fl.st===4&&PORTS.length&&!realFlash()){fl.st=1;flRun(3,fl.to);return}  // 真插计时点时后台自己接着烧
 if(page==='home'){const lt=document.querySelector('.lights');if(lt)lt.outerHTML=envStatus();return}  // 首页只换灯，不打断正在输入的
 if(page==='steps'){const bj=document.querySelector('.bgjob');if(bj)bj.outerHTML=bgBox();return}  // 找关键步骤页只换"查设备"那块
 if(page==='settings'){const k=document.getElementById('set-key');if(k&&k.value.trim()&&!k.value.startsWith('•'))keyDraft=k.value.trim();render()}else if(page==='flash')render()}catch(e){}}
// 设备插没插：各页都看这一个（真流程看后台每 2 秒查到的端口；演示数据照原型模拟）
const devNow=()=>REAL?{on:PORTS.length>0,port:PORTS[0]||''}:{on:!flNoDev,port:PORTS[0]||(setOS==='mac'?'/dev/cu.usbmodem1101':'COM3')};
function portsWatch(on){if(on&&!portT)portT=setInterval(portsPoll,2000);if(!on&&portT){clearInterval(portT);portT=null}}
// 电脑上真装了的乐鑫编译工具（后台 /api/env 查的）；够项目要求的版本就算装好了
let IDFS=[],TOOLS=null;  // TOOLS：编译前自检（/api/env 的 tools）：cmake、ninja、idf.py、esptool 找没找到
const toolMiss=()=>TOOLS&&TOOLS.missing?TOOLS.missing:[],toolBlock=()=>toolMiss().filter(m=>m.name!=='esptool');
const vt=v=>String(v||'0').split('.').map(Number).concat([0,0,0]).slice(0,3),vge=(a,b)=>{const x=vt(a),y=vt(b);for(let i=0;i<3;i++){if(x[i]!==y[i])return x[i]>y[i]}return true};
const realIdf=()=>{const min=REAL?REAL.idf_min:null;const ok=IDFS.filter(i=>!min||vge(i.version,min));return ok.sort((a,b)=>vge(a.version,b.version)?-1:1)[0]||null};
const shortV=v=>String(v).replace(/\.0$/,'');
// 首页灯和"用到时再检查"都按已保存的设置和真查的结果：没保存就回首页，缺的灯仍是红的
const envKey=()=>{const p=SAVED.llm_provider;return p==='claude-code'?ccOK():(p==='claude-api'||p==='deepseek')&&!!SAVED.has_key[p]},envComp=()=>!!realIdf();
function missing(only){const m=[];if(!envKey()&&only!=='comp')m.push(['set-llm',SAVED.llm_provider==='claude-code'?'大模型：本机 Claude Code 登录':'大模型密钥','找关键步骤要用大模型读代码，<b>不填就没法往下走</b>。']);if(hSrc&&!envComp()&&only!=='key')m.push(['set-idf',`乐鑫编译工具 ESP-IDF ${IDF()}`,inst.st===1?'正在后台安装，装好就能编译。':'编译这个固件要用（从源码里读到的要求）。<b>不挡找关键步骤</b>，烧录前装好就行。']);
 if(hSrc&&envComp()&&toolMiss().length&&only!=='key')m.push(['set-idf',`编译要用的工具：缺 ${toolMiss().map(x=>htmlEsc(x.name)).join('、')}`,`${toolMiss().map(x=>htmlEsc(x.why)).join('；')}。<b>不挡找关键步骤</b>，烧录前装好就行。`]);return m}
let envBack='home',compPending=false;
function envStatus(){const L=(c,t)=>`<span class="light"><i class="dot" style="background:var(--${c})"></i>${t}</span>`;
 const sp=P2UI[SAVED.llm_provider],llmName={claude:'Claude',deepseek:'DeepSeek',cc:'本机 Claude Code'}[sp];
 const a=[envKey()?L('ok',`大模型：${llmName}，${sp==='cc'?'已登录':'密钥已填'}`):L('bad',sp==='cc'?(CCST&&!CCST.found?'大模型：没找到本机 Claude Code':'大模型：本机 Claude Code 还没登录'):'大模型：还没填密钥'),PORTS.length?L('ok',`设备：已插上 ${PORTS[0]}`):L('muted','设备：没插上（烧录前插上就行）')];
 if(hSrc&&!hReading)a.push(!envComp()&&inst.st===1?L('warn',`乐鑫 ESP-IDF ${IDF()}：正在后台装 ${Math.round(((inst.step||0)+(inst.pct||0)/100)/5*100)}%`):envComp()?L('ok',`乐鑫 ESP-IDF ${IDF()}：已装 v${realIdf()?shortV(realIdf().version):'6.1'}`):L('bad',`乐鑫 ESP-IDF ${IDF()}：没装`));
 if(hSrc&&!hReading&&envComp()&&toolMiss().length)a.push(L(toolBlock().length?'bad':'warn',`编译工具缺 ${toolMiss().map(x=>htmlEsc(x.name)).join('、')}（设置页看详情）`));
 return `<div class="lights">${a.join('<span class="lsep" aria-hidden="true">|</span>')}</div>`}
// 提示（决策 129）：成功（✓）绿色、自己消失；出错（✕）红色、一直在，点 × 才关；其他黄色，按字数多留一会儿
let toastT=null;function toast(m){const t=document.getElementById('toast');m=String(m||'');const kind=m.startsWith('✓')?'ok':m.startsWith('✕')?'bad':'warn';
 t.className='toast t-'+kind;t.innerHTML=`<span>${htmlEsc(kind==='bad'?m.replace(/^✕\s*/,''):m)}</span>${kind==='bad'?'<button class="tclose" aria-label="关掉这个提示">×</button>':''}`;t.hidden=false;clearTimeout(toastT);
 if(kind!=='bad')toastT=setTimeout(()=>{t.hidden=true},Math.min(8000,1800+m.length*80))}
document.addEventListener('click',e=>{if(e.target.closest('#toast .tclose'))document.getElementById('toast').hidden=true});
document.addEventListener('click',e=>{codeTog(e)});  // 技术细节"展开代码"
// 首页"以前测过的"：真测过的记录（后台按结果文件夹里的原始日志重算）；演示数据只在演示入口（地址里写 ?demo=）出现
let HIST=[];async function histLoad(){try{HIST=await (await fetch('/api/history')).json()}catch(e){}}
const histRow=h=>`<div class="lrow"><button data-hopen="${htmlEsc(h.project_dir)}"><span><b>${htmlEsc(h.name)}</b>　<span class="note">${htmlEsc(String(h.what||'').startsWith('全程')?'全程':h.what)} · ${htmlEsc(h.last)}</span></span><span class="num">${htmlEsc(h.metric)} ${f(h.now)} 秒　${h.few?`<span class="note">只测了 ${h.rounds} 轮，跑流程，不比较</span>`:h.prev!=null?arrow(h.now,h.prev):'<span class="note">第一次测</span>'}</span></button>${h.path?`<button class="pill" data-hretest="${htmlEsc(h.project_dir)}">改完了，再测一次</button>`:''}</div>${h.fsw_on?`<div class="devrow"><span class="note"><i class="dot" style="background:var(--warn)"></i>设备上现在是计时版，交货、演示前请还原</span><button class="pill" data-hrestore="${htmlEsc(h.project_dir)}">还原成原版</button></div>`:''}`;
// 改完再测（任务 22）：带上次的源码位置、板子、要测什么，直接到"确认要测的内容"；不另开页面
let remeasure=false;  // 这一轮是从「改完了，再测一次」进来的：步骤条写"改完再测"，不写"第一次测"
async function retestFrom(h){flow=null;flowReset();page='home';hStage=0;await realPick({path:h.path},h.path);if(!REAL)return;remeasure=true;
 hSay=String(h.what||'').replace(/[<>&"]/g,'');if(!REAL.chip){hBoardNeed=true;render();return}if(!envKey()){pop='env';render();return}
 autoStart();window.scrollTo(0,0)}  // 改完再测也走测量页一页到底（决策 158），不回老的一页页流程（10-18 真机）
const recentList=()=>DEMO?PROJ_LIST():HIST.length?`<div class="list">${HIST.map(histRow).join('')}</div>`:'<p class="note" style="margin:0">还没有测过。选一份固件源码开始，测完会出现在这里。</p>';
const PROJ_LIST=()=>`<div class="list">${PROJ.map(p=>`<div class="lrow"><button data-open="${p.scene}"><span><b>${p.name}</b>　<span class="note">${p.what} · ${p.last}</span></span><span class="num">${p.metric} ${f(p.now)} 秒　${arrow(p.now,p.prev)}</span></button><button class="pill" data-retest="${p.scene}">改完了，再测一次</button></div>${p.fswOnDevice&&!restored[p.scene]?`<div class="devrow"><span class="note"><i class="dot" style="background:var(--warn)"></i>设备上现在是计时版，交货、演示前请还原</span><button class="pill" data-restore="${p.scene}">还原成原版</button></div>`:''}`).join('')}</div>`;
let pName='',restored={},hNeed=false,hSrc='',hReading=false,hErr='',hScan=0,hScanT=null;
// ===== 选固件源码（真读）=====：后台只看项目文件、不用大模型，认出是不是固件、芯片、要的编译工具；复制到纯英文路径，原文件夹不动
let REAL=null,hBoardQ='',hBoardNeed=false;
const IDF=()=>REAL?(REAL.idf_min?REAL.idf_min+' 或更新':'（项目没写要哪个版本）'):FF[scene].idf+' 或更新';
const CHIP=()=>REAL?(REAL.chip?`芯片 ${REAL.chip}${REAL.board?`，板子 ${REAL.board}`:''}`:'还没选板子'):FF[scene].chip;
async function api(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
async function realPick(body,label,board){if(!board)remeasure=false;const quiet=!!board&&!!REAL;hErr='';envBack='home';hBoardNeed=false;hSrc=label||body.path||'';if(!quiet){hReading=true;hStage=1;if(!board)hSay='';render()}  // 只是选板子：页面不跳回"正在读源码"，不闪（决策 113）
 let r;try{r=await api('/api/source/inspect',{...body,board:board||null})}catch(e){r={ok:false,message:'码表后台没有回应，看看运行码表的窗口是不是关了。'}}
 hReading=false;hBoardLoad='';
 if(!r.ok){REAL=null;hSrc='';hStage=0;hErr=r.kind==='multiple'?`<b>${r.message}</b><div class="acts" style="margin:8px 0 0">${r.projects.map(p=>`<button class="btn" data-pickroot="${p.root.replace(/"/g,'&quot;')}">${p.name}<span class="note">　${p.root}</span></button>`).join('')}</div>`:r.message;render();return}
 REAL={...r.info,body,label:hSrc,work:r.work,project_dir:r.project_dir,fingerprint:r.fingerprint,ai:null};if(REAL.board&&!hBoardOpen)hBoardQ=REAL.board;pName=REAL.name+(REAL.board?`（${REAL.board}）`:'');IDENT=null;  // 再测时板子按上次选的填好（决策 130）；项目名称按这次选的源码和板子
 hScan=1;newRun();if(r.status!=='needs_board'&&missing().length)pop='env';render();clearTimeout(hScanT);
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
// 设备上是哪种芯片（10-21 用户定）：插着设备、要选板子时读一次（设备会重启一下），记下来；芯片对不上的板子变灰、写原因，不让选
let DEVCHIP={port:'',chip:null,reading:false,err:''};
const chipN=s=>{const t=String(s||'').toUpperCase().replace(/[^A-Z0-9]/g,'');return t.startsWith('ESP32')&&t.length>5?t.slice(0,5)+'-'+t.slice(5):t};
const chipOff=b=>!!(DEVCHIP.chip&&b&&b.chip&&chipN(b.chip)!==DEVCHIP.chip);
async function devChipCheck(){if(!PORTS.length&&DEVCHIP.port&&!DEVCHIP.reading){DEVCHIP={port:'',chip:null,reading:false,err:''};render();return}  // 拔了：不再按它筛
 if(!REAL||!(REAL.boards||[]).length||!PORTS.length||DEVCHIP.port===PORTS[0])return;
 DEVCHIP={port:PORTS[0],chip:null,reading:false,err:''};
 try{let r=await (await fetch('/api/device/chip')).json();
  if(!r.chip&&r.port===DEVCHIP.port){DEVCHIP.reading=true;render();r=await (await post('/api/device/chip',{})).json()}
  if(r.port!==DEVCHIP.port)return;DEVCHIP={port:r.port,chip:r.chip||null,reading:false,err:r.chip?'':(r.busy?'':r.error||'')};if(r.busy)DEVCHIP.port=''}catch(e){DEVCHIP.reading=false}
 render()}
let hBoardOpen=false,hBoardHi=0,hBoardLoad='';  // hBoardLoad：刚点选、后台还在按这块板子重读项目
const boardOk=q=>boardMatch(REAL.boards||[],q).filter(b=>!chipOff(b));  // 能选的（芯片对得上），上下键和回车只在这里面走
function boardRow(){if(!REAL||!REAL.boards||!REAL.boards.length)return '';
 const all=boardMatch(REAL.boards,hBoardQ),list=all.filter(b=>!chipOff(b)),off=all.filter(chipOff),hi=Math.min(hBoardHi,Math.max(0,list.length-1));
 const rows=[...list.map((b,k)=>`<div class="bopt${k===hi?' hi':''}" role="option" aria-selected="${REAL.board===b.id}" data-pickboard="${b.id}">${b.id}<span class="note">　${b.chip}</span></div>`),
  ...off.map(b=>`<div class="bopt off" role="option" aria-disabled="true" aria-selected="false">${b.id}<span class="note">　${b.chip}：设备上是 ${DEVCHIP.chip}，不能选</span></div>`)];
 const drop=hBoardOpen?`<div class="bdrop" id="h-blist" role="listbox" aria-label="匹配的板子">${rows.length?(off.length?`<div class="note" style="padding:6px 10px;border-bottom:1px solid var(--line)">设备上的芯片是 ${DEVCHIP.chip}：另有 ${off.length} 块芯片不同的板子不能选，变灰排在最后</div>`:'')+rows.slice(0,50).join('')+(rows.length>50?`<div class="note" style="padding:6px 10px">还有 ${rows.length-50} 块${off.length?`（含 ${Math.min(off.length,rows.length-50)} 块不能选的）`:''}，多输几个字缩小范围</div>`:''):'<div class="note" style="padding:8px 10px">没有匹配的板子，换个说法试试（比如只输型号里的几个字）</div>'}</div>`:'';
 const mis=REAL.chip&&DEVCHIP.chip&&chipN(REAL.chip)!==DEVCHIP.chip;
 const chipNote=DEVCHIP.reading?'<p class="note" style="margin:6px 0 0"><span class="spin"></span>正在读设备上的芯片，设备会重启一下</p>':mis?`<p class="note" style="color:var(--bad);margin:6px 0 0">板子选错了：你选的 ${htmlEsc(REAL.board||'')} 用 ${htmlEsc(REAL.chip)}，设备上的芯片是 ${DEVCHIP.chip}。换一块芯片是 ${DEVCHIP.chip} 的板子。</p>`:DEVCHIP.chip&&off.length?`<p class="note" style="margin:6px 0 0">设备上的芯片是 ${DEVCHIP.chip}，芯片不同的板子列在后面、不能选。</p>`:'';
 return `<div class="field" style="margin-top:14px"><label for="h-boardq">这个项目支持 ${REAL.boards.length} 块板子，你用的是哪块？</label>
  <div class="bpick${(hBoardLoad||REAL.board)&&hBoardQ===(hBoardLoad||REAL.board)&&!hBoardOpen&&!mis?' picked':''}"><input type="text" id="h-boardq" role="combobox" aria-expanded="${hBoardOpen}" aria-controls="h-blist" autocomplete="off" placeholder="输入板子型号里的几个字，比如 core-s3" value="${htmlEsc(hBoardQ)}">${drop}</div>
  ${hBoardLoad?`<p class="note" style="margin:6px 0 0"><span class="spin"></span>已选 ${htmlEsc(hBoardLoad)}，正在读这块板子的配置</p>`:REAL.chip?(mis?'':`<p class="note" style="margin:6px 0 0"><i class="dot" style="background:var(--ok)"></i>已选 ${REAL.board||''}，芯片 ${REAL.chip}${REAL.board_from==='last'?'（按上次选的；想换就在框里重新输入）':''}</p>`):hBoardNeed?'<p class="note" style="color:var(--warn);margin:6px 0 0">先选你用的板子：芯片跟着板子走，编译和烧录要用。</p>':'<p class="note" style="margin:6px 0 0">源码里没写用哪块板子（没编译过），芯片跟着板子走。</p>'}${chipNote}</div>`}
function boardPick(id){const w=document.getElementById('h-what');hSay=w?w.value:hSay;hBoardQ=id;hBoardOpen=false;hBoardHi=0;hBoardLoad=id;hBoardNeed=false;const q=document.getElementById('h-boardq');if(q){q.value=id;q.blur()}render();realPick(REAL.body,REAL.label,id)}  // 点了马上收起列表、显示已选，不等后台读完（第 34 次走查：几秒不变，人以为没点上）
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

// 精简流程（决策 158）：一律测全程；点「开始测量」后自动执行，只在要人工操作或校验没过时停下
// 结构由码表定（起点、第一下和最后一下感觉到的结果，见找步骤的要求），这里只说测哪一段，不靠措辞去纠正某个固件（10-18 用户：通用代码不迁就某一个固件）
const WHOLE='全程：从设备最早察觉人做完那件事，到用户感觉到的最后一个结果';
let AUTO=false,autoHold='',autoHoldKind='',autoBusy='',autoT0=0;  // autoHoldKind：ident（找完要核对）/ fix（校验没过回来改）/ env（缺编译工具）
const TRY_SECS=180;  // 和后台 TRY_SECONDS 一致：试一次最多等多久
const AUTO_ROUNDS=10;  // 轮数固定，不让人选（10-21 用户定：10 轮和 20 轮中位数差 0.04 秒以内，多一个选项多一处要想）
function estBox(){const again=(DEMO?PROJ:HIST).some(p=>[hSrc,expandHome(hSrc),REAL&&REAL.root].includes(p.path));
 const auto=again?3.5:7.5,human=Math.round((AUTO_ROUNDS*15+45)/60);  // 按验证 14 的实测：大模型三次约 5 分钟，编译烧录约 2.5 分钟；每轮约 15 秒
 return `<section class="estbox"><h2 style="font-size:15px;margin:0 0 8px">预计总耗时</h2><div class="estgrid">
  <div><b class="num">约 ${Math.round(auto+human)} 分钟</b><span class="note">预计总耗时${again?'（再测，沿用上次结果）':'（首次测量）'}</span></div>
  <div><b class="num">约 ${auto} 分钟</b><span class="note">自动执行（无需值守）</span></div>
  <div class="hum"><b class="num">约 ${human} 分钟</b><span class="note">人工操作（需在场）</span></div></div>
  <ol class="note" style="margin:8px 0 0;padding-left:20px"><li>连接设备到电脑的 USB 口（烧录前提示）</li><li>按页面提示触发设备 ${AUTO_ROUNDS + 1} 次：第 1 次校验计时点，不计入数据；之后 ${AUTO_ROUNDS} 轮，每轮约 5～15 秒</li></ol>
  </section>`}
// 自动往下走：每次画完页面看一眼该不该进下一步（同一步只触发一次）
function autoTick(){if(!AUTO||pop||autoHold)return;
 const go=(key,fn)=>{if(autoBusy===key)return;autoBusy=key;setTimeout(fn,0)};
 if(page==='home'&&hStage===3&&stepsRead===2)return go('steps',()=>{hStage=1;mode='boss';stepsGo()});
 // 停在核对页但其实没有要人核对的（比如刷新前存着"这次用的是 DeepSeek"那种按模型名停下的）：码表自己核对，没问题就接着插点
 if(page==='steps'&&stepsRead===2&&flowMax<2&&!Object.keys(edits).length&&[...dropped].every(p=>autoDrop.some(x=>x.p===p))&&!added.length)return go('steps',()=>{if(page==='steps'&&!autoHold)stepsGo()});
 if(page==='flash'&&fl.st===3&&!diffOk)return go('build',()=>{if(!envComp()){autoHold='编译工具还没准备好：装好或在设置里选好后，点「继续」';autoHoldKind='env';envBack='flash';pop='env';render();return}diffOk=true;if(realFlash())fswPost('/api/fsw/run',{from:'build'});else flRun(2,4);render()});
 if(page==='flash'&&fl.st===2)return go('collect',()=>{justFlashed=true;page='collect';colStart();render();window.scrollTo(0,0)});
 if(page==='collect'&&col.st===2)return go('report',()=>setTimeout(()=>{if(!AUTO||page!=='collect'||col.st!==2)return;AUTO=false;page='report';mode='boss';render();window.scrollTo(0,0)},1500));
}
// 找完关键步骤要不要停下请人看：只看码表自己核对出的实际问题（起终点没找到、位置不对、落进条件分支）。
// 用的是哪个大模型不算理由（决策 181；10-26 又遇到：DeepSeek 找完就停、显示老的整页核对，用户看到的就是"老版本"）
// 码表核对出问题的点（10-26 用户：看不懂代码，停下来让人核对不行）：起点、终点有问题才停（没它量不了这条线）；
// 中间的点自动去掉、照常往下走（少一个点只是少分一段，结论不会错）。AI 自己说明在这块板子上什么都不做的点也去掉。
// 每个点的原因按它自己没通过的那一条说，不再一律说"落进条件分支或循环里"
const NOOP=/空操作|空实现|什么都不做|不做任何事|量不出任何/;
function identBad(){const P=REAL&&IDENT&&IDENT.page;if(!P)return [];const st=P.steps||[],endN=String(P.measure||'').split('→').pop().trim();
 return st.filter(x=>!dropped.has(x.p)&&!edits[x.p]).map(x=>{const key=x.line==='start'||x.line==='end'||x===st[0]||(!!endN&&x.n===endN),f=(x.chk||[]).find(c=>c.level==='fail');
  const why=x.loc_ok===false?'位置码表核对没通过（这个文件里找不到这一行，或行号对不上）':x.struct_ok===false?(f?f.text:'码表结构检查没通过'):!key&&NOOP.test(String(x.note||''))?'AI 自己说明这一步在这块板子上什么都不做，量出来没有意义':'';
  return why?{p:x.p,n:x.n,why,key}:null}).filter(Boolean)}
function identHold(){const P=REAL&&IDENT&&IDENT.page;if(!P)return '';
 if(P.warn)return '这次要测的起点或终点没在代码里找到，码表没法自己补上';
 const k=identBad().find(x=>x.key);return k?`「${k.n}」是这次要测的${k.n===String(P.measure||'').split('→').pop().trim()?'终点':'起点'}，${k.why.replace(/[。，]?请核对.*$/,'')}。没有它这条线量不了，码表没法自己绕开`:''}
let autoDrop=[];  // 码表自动去掉的点 [{p,n,why}]：测量页"找关键步骤"那一行写明
function stepsGo(){const off=identBad().filter(x=>!x.key);off.forEach(x=>dropped.add(x.p));autoDrop=autoDrop.filter(x=>dropped.has(x.p)).concat(off.map(({p,n,why})=>({p,n,why})));
 const bad=identHold();if(bad){autoHold=bad;autoHoldKind='ident';page='steps';render();window.scrollTo(0,0);return}
 page='flash';flashVar='b';flStop();flNoDev=false;if(realFlash()){fswInsert()}else{fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';diffOpen=new Set();flRun(0,1)}render();window.scrollTo(0,0)}
function autoStart(){if(REAL&&!REAL.chip){hBoardNeed=true;render();const b=document.getElementById('h-boardq');b&&b.focus();return}
 if(REAL&&!envKey()){pop='env';render();return}
 hSay=WHOLE;AUTO=true;autoHold='';autoDrop=[];autoBusy='';autoT0=Date.now();flowReset();flow='first';clearInterval(readT);readT=null;stepsRead=0;alsoFace=true;dropped=new Set();edits={};added=[];flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;flNoDev=FAIL==='nodev';onDev='orig';hStage=3;readStart();render()}
// 测量页（决策 158，原型 ②）：点了「开始测量」就在这一页走到底；要人操作时大字提示，出问题才回到原来的页面让人处理
// 10-26 用户：所有界面都显示新版本。一次测量（flow 'first'）从找关键步骤到多轮实测，每一页都画成测量页：
// 不再看网页内存里的"自动执行"开关（停下请人核对、刷新网页、点「查看并修改计时点位置」都会丢掉它，丢了就退回老的一页一页界面）；
// 停下要人处理的（核对步骤、编译出错、没装编译工具、实测停了）都在测量页里处理
function runView(){if(flow!=='first')return false;
 if(page==='home')return hStage===3;
 return page==='steps'||page==='flash'||page==='collect'}
function runAt(){if(page==='home'||page==='steps')return 0;if(page==='flash')return fl.step<=1&&fl.st!==3?1:fl.step<4?2:3;return col.st===2?5:4}
let runT=null,runPrev=-1,runS0=0,runLeft=()=>0;  // 剩余时间：后面几步的预计 + 当前这步预计减去已做的时间（至少留 30 秒）  // 测量页顶上的已用时、剩余时间每秒走
function runTick(){if(runT)return;runT=setInterval(()=>{if(!runView()){clearInterval(runT);runT=null;return}const e=document.getElementById('runused');if(e)e.textContent=mmss((Date.now()-autoT0)/1000);const l=document.getElementById('runleft');if(l)l.textContent=Math.max(1,Math.round(runLeft()/60))+' 分钟'},1000)}
function runPage(){runTick();const at=runAt(),used=autoT0?(Date.now()-autoT0)/1000:0;
 const L=[['找关键步骤','auto',150],['插入计时点','auto',90],['编译、烧录','auto',60],['试测：校验计时点','hum',60],[`多轮实测：${AUTO_ROUNDS} 轮`,'hum',AUTO_ROUNDS*15],['生成分析报告','auto',90]];
 if(at!==runPrev){runPrev=at;runS0=Date.now()}
 runLeft=()=>L.slice(at+1).reduce((a,x)=>a+x[2],0)+Math.max(30,L[at][2]-(Date.now()-runS0)/1000);const left=runLeft();
 const P=REAL&&IDENT&&IDENT.page,n=P?(P.steps||[]).length:0,AD=autoDrop.filter(x=>dropped.has(x.p));
 const done=i=>i===0&&P?`<div class="note">${htmlEsc(P.measure||'')}；${n-AD.length} 个关键步骤，位置已逐个核对${AD.length?`；码表自动去掉 ${AD.length} 个用不上的：${AD.map(x=>`<span title="${htmlEsc(x.why)}">${htmlEsc(x.n)}</span>`).join('、')}`:''}</div>`:i===3?`<div class="note">计时点收到，顺序校验通过</div>`:'';
 const tl=L.map((x,i)=>{const c=i<at?'done':i===at?((col.st===4&&i===4)||(i===0&&page==='steps'&&autoHold)?'err':'doing'):'wait';
  return `<li class="${c}"><span class="tdot"></span><div>${x[0]}<span class="who ${x[1]}">${x[1]==='hum'?'人工操作':'自动执行'}</span>${c==='done'?done(i):''}</div><span class="note">${c==='done'?'✓':'约 '+(x[2]>=60?Math.round(x[2]/60)+' 分钟':x[2]+' 秒')}</span></li>`}).join('');
 let now;const holdBox=autoHold?`<div class="why" style="border-left-color:var(--warn)"><b>自动执行在这里停下：</b>${htmlEsc(autoHold)}</div>`:'';
 if(page==='steps'&&stepsRead===2)now=runReview();  // 码表核对出问题、或校验没过回来改位置：只列要看的步骤和确认按钮，确认后接着自动走
 else if(page==='home'&&stepsRead===3)now=`<section class="panel">${modalHTML().replace(' id="dlg-t"','')}</section>`;  // 没找完：重试在这里
 else if(page==='flash'&&fl.st===5&&fl.err!=='try')now=flErr();  // 编译、烧录、插点出错：错误和重试按钮放在测量页里
 else if(page==='flash'&&fl.st===0)now=`<div class="bigask"><span class="lab">接着做</span><b>插入计时点、编译、烧录</b><div class="acts" style="margin:8px 0 0"><button class="btn pri" data-goins="1">插入计时点</button></div></div>`;
 else if(page==='flash'&&fl.st===3&&(!AUTO||autoHold))now=`${holdBox}<div class="bigask"><span class="lab">接着做</span><b>计时点已插好，下一步编译、烧录</b><div class="acts" style="margin:8px 0 0"><button class="btn pri" data-autoresume="1">继续</button></div></div>`;
 else if(page==='flash'&&fl.st===2)now=`<div class="bigask"><span class="lab">接着做</span><b>计时版已烧好，试测通过</b><div class="acts" style="margin:8px 0 0"><button class="btn pri" data-go="collect" data-keepflow="1">${col.st?'回到多轮实测':'开始多轮实测'}</button></div></div>`;
 else if(page==='collect'&&(col.st===0||col.st===3))now=collectPage();  // 还没开始 / 停下了：开始、接着测、看报告的按钮都在这里
 else if(at===0)now=`<div class="bigask auto"><span class="lab">自动执行 · 无需值守</span><b><span class="spin"></span> 正在找关键步骤</b><span class="note">AI 读取源码中，已找到 ${readFound} 个，已用时 ${mmss(readSec)}。需要人工操作时会在此提示并响一声。</span>${REAL&&IDENT&&IDENT.net?`<span class="note" style="color:var(--warn)">${htmlEsc(IDENT.net)}</span>`:''}</div>`;
 else if(page==='flash'&&fl.st===4)now=`<div class="bigask"><span class="lab">人工操作</span><b>未检测到设备：连接 USB，或按电源键开机</b><span class="note">设备闲置一段时间会自动关机。计时版已编译完成，检测到设备后自动烧录，无需点击。</span></div>`;
 else if(at<3)now=`<div class="bigask auto"><span class="lab">自动执行 · 无需值守</span><b><span class="spin"></span> ${at===1?'正在插入计时点':fl.step===3?'正在烧录':'正在编译'}${fl.step>=2?` ${fl.pct||0}%`:''}</b><span class="note">${htmlEsc(at===1?(FSWJ&&FSWJ.net)||'AI 读取源码，确定每个计时点的插入位置':fl.step===3?'正在写入设备，请勿拔线':'编译带计时点的固件')}</span></div>`;
 else if(at===3&&fl.st===5){const E=(FSWJ&&FSWJ.err)||{};now=`<div class="errbox"><b>试测没通过${htmlEsc(E.error||'')}</b><p style="margin:6px 0">${htmlEsc(E.message||'')}</p>${E.why?`<p class="note" style="margin:0 0 6px">${htmlEsc(E.why)}</p>`:''}<div class="acts" style="margin:8px 0 0"><button class="btn pri" data-flx="retry">再试一次</button></div></div>`}
 else if(at===3){const N=FSWJ?FSWJ.need||FSWJ.total||live().length:live().length,got=FSWJ?(FSWJ.got||[]).length:0;now=tryBox(got,N).replace('<div class="bigask">','<div class="bigask"><span class="lab">人工操作 · 试测（校验计时点，不计入数据）</span>')}
 else if(col.st===4)now=suspectBox();
 else if(col.st===2)now=`<div class="bigask auto"><span class="lab">自动执行</span><b><span class="spin"></span> 正在生成分析报告</b></div>`;
 else now=(col.msg?`<div class="why" style="border-left-color:var(--warn)">${htmlEsc(col.msg)}</div>`:'')+colB();
 return `<div class="stack"><div class="acts" style="justify-content:space-between;align-items:flex-start;margin:0"><div><h1 style="font-size:22px;margin:0">测量：全程</h1><p class="note" style="margin:2px 0 0">已用时 <span class="num" id="runused">${mmss(used)}</span>　剩余约 <span class="num" id="runleft">${Math.max(1,Math.round(left/60))} 分钟</span></p></div><button class="btn" data-autostop="1">停止测量</button></div>
 ${now}<section class="panel"><h2>执行进度</h2><ul class="runtl">${tl}</ul></section>
 <p class="note">计时点变更明细、关键步骤位置在报告「技术细节」中查看</p></div>`}
// 测量页里的核对（10-26）：不再搬老的整页"找关键步骤"（读取状态、关键流程地图、模型准确率提醒），只放停下的原因、码表检查结果、步骤清单和确认
function runReview(){if(stepsRead===2)prepKick();const rows=live(),all=allRows(),bad=new Map(identBad().map(x=>[x.p,x]));
 const one=(x,i)=>{const b=bad.get(x.p),off=dropped.has(x.p);
  return `<li class="${off?'wait':b?'err':'done'}"><span class="tdot"></span><div>${off?`<s>${htmlEsc(x.n)}</s>`:htmlEsc(x.n)}${edits[x.p]?' <span class="chip c-gray">你改过位置</span>':''}
   <div class="note"><span class="code">${htmlEsc(x.f||'')}</span> ${lnTxt(x.l)}${b&&!off?`<br><span style="color:var(--bad)">${htmlEsc(b.why)}</span>`:''}${off?' · 不插计时点':''}</div>${editP===x.p?editBox(x):''}</div>
   <span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">${off?`<button class="pill" data-undel="${x.p}">恢复</button>`:`<button class="pill" data-editp="${x.p}">修改位置</button><button class="pill" data-del="${x.p}">删除</button>`}</span></li>`};
 const why=autoHold||'步骤改过，确认后重新插计时点';
 return `<div class="bigask"><span class="lab">自动执行在这里停下 · 需要你决定</span><b>${htmlEsc(why)}</b>
  <span class="note">可以在下面改这个点的位置后点「确认，插入计时点」；或者点「重新分析」，让 ${usedLLM()} 重新读一遍代码。${prepNote()}</span>
  <div class="acts" style="margin:8px 0 0"><button class="btn pri" data-goins="1">确认，插入计时点</button><button class="btn" data-reanalyze="1">重新分析</button></div></div>
 <section class="panel"><h2>计时点（${rows.length} 个）</h2><ul class="runtl">${all.map(one).join('')}</ul></section>`}
function autoStop(){AUTO=false;autoHold='';clearInterval(runT);runT=null;flow=null;flowReset();
 if(page==='collect')post('/api/collect/stop',{}).catch(()=>{});
 if(page==='flash'){clearInterval(fswT);fswT=null;post('/api/fsw/stop',{}).catch(()=>{});fl.st=0}
 page='home';hStage=1;render();window.scrollTo(0,0)}
const suspectBox=()=>{const S=(COLJ&&COLJ.suspect)||[],run=S.some(x=>x.kind);  // kind：测到一半某个点连着几轮不对（gap / miss / stale）；没有 kind 是第 1 轮自动校验
 const head=run?`「${htmlEsc(S[0].name)}」连着几轮不对劲，已暂停`:'第 1 轮自动校验没通过，已暂停';
 return `<div class="errbox"><b>${head}</b>${S.map(x=>x.kind?`<p style="margin:6px 0">${htmlEsc(x.why)}</p><p class="note" style="margin:4px 0">接着测的话，用到它的段这次多半没有数（报告里会标"数据不足"）。</p>`:`<p style="margin:6px 0">「${htmlEsc(x.name)}」这一段 ${fms(Math.abs(x.seconds))} 秒${x.seconds<0?'（先后反了）':''}：${htmlEsc(x.why)}。计时点位置可能不对，接着测的数据不可信。</p>`).join('')}<div class="acts" style="margin:8px 0 0"><button class="btn pri" data-autofix="1">查看并修改计时点位置</button><button class="btn" data-col="resume">忽略，继续测量</button></div></div>`};

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
   <dl class="kv" style="margin-top:14px"><dt>测量范围</dt><dd>全程：从设备收到触发，到最后一个对外输出；起点和终点由 AI 读代码确定</dd></dl>
   ${estBox()}
   <div class="acts" style="justify-content:flex-start;align-items:center"><button class="btn pri" data-autostart="1" ${hBoardLoad?blk('#h-boardq','正在读这块板子的配置'):REAL&&!REAL.chip?blk('#h-boardq','先选板子'):REAL&&DEVCHIP.chip&&chipN(REAL.chip)!==DEVCHIP.chip?blk('#h-boardq',`换成 ${DEVCHIP.chip} 的板子`):''}>开始测量</button><span class="note">点了以后自动执行，需要人工操作时页面提示并响一声</span></div>`}
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
  fetch('/api/env').then(r=>r.json()).then(e=>{IDFS=(e.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);TOOLS=e.tools||null;render()});toast('✓ 编译工具装好了')}
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
// 编译前自检（设置页「乐鑫编译工具」一栏）：cmake、ninja、idf.py、esptool 各自找没找到；缺了点名、写清做什么用
function toolsBox(){if(!TOOLS)return '';const F=TOOLS.found||{},M=toolMiss();
 const ok=Object.keys(F).map(k=>`<span><i class="dot" style="background:var(--ok)"></i>${htmlEsc(k)}：<span class="code">${htmlEsc(tilde(F[k]))}</span></span>`).join('');
 const bad=M.map(x=>`<span><i class="dot" style="background:var(--bad)"></i>缺 ${htmlEsc(x.name)}：${htmlEsc(x.why)}</span>`).join('');
 return `<div class="status" id="tools-check" style="margin-top:8px"><span class="note" style="width:100%">编译前自检（码表编译、烧录时会自动把 Homebrew 等常见目录补进 PATH）：</span>${bad}${ok}</div>${M.length?`<p class="note" style="color:var(--bad);margin:4px 0 0">缺的装好后，点上面「重新查找」再看一次${setOS==='mac'?'；Mac 上 cmake、ninja 可以用 Homebrew 装：<span class="code">brew install cmake ninja</span>':'；也可以重新跑一遍乐鑫的安装脚本'}。</p>`:''}`}
function settingsPage(){
  const first=setFirst;
  const guide=setOS==='mac'?`<ol class="guide"><li>打开"终端"，复制这一行粘贴回车：<span class="code">mkdir -p ~/esp &amp;&amp; cd ~/esp &amp;&amp; git clone -b v6.1 --recursive https://github.com/espressif/esp-idf.git esp-idf-v6.1</span></li><li>再复制这一行：<span class="code">cd esp-idf-v6.1 &amp;&amp; ./install.sh esp32s3</span>，等它装完（十几分钟）</li><li>装完这里会自动变绿</li></ol>`:`<ol class="guide"><li>下载乐鑫官方的 Windows 安装器（ESP-IDF Installer），选 v6.1</li><li>安装路径用纯英文，比如 <span class="code">C:\\esp</span>（不要放在中文用户名的文件夹里）</li><li>装完这里会自动变绿</li></ol>`;
  const opt=(id,t,sub,extra='')=>`<label><input type="radio" name="llm" value="${id}" ${setLLM===id?'checked':''}><span><b>${t}</b>${extra}<small>${sub}</small></span></label>`;
  const where=setOS==='mac'?'<span class="code">~/esp</span>、<span class="code">~/.espressif</span>':'<span class="code">C:\\Espressif</span>、<span class="code">C:\\esp</span>';
  const done=[keyOK(), compOK(), PORTS.length>0];
  const cnt=done.filter(Boolean).length;
  const head=first?`<section class="panel" style="border-color:var(--speak)"><h1 style="font-size:22px">第一次用码表，先准备三样东西</h1><p class="note" style="margin:0">已准备 <b class="num">${cnt}</b> / 3。都准备好后，点最下面的「保存」。</p></section>`:'';
  const idfE=idfErr?`<p class="note" style="color:var(--bad);margin:6px 0 0">✕ ${htmlEsc(idfErr)}</p>`:'';
  const compiler=!IDFS.length||(hSrc&&!realIdf())?`<div id="instbox">${installBox()}</div>${idfE}`:`<div class="status">${IDFS.map((i,k)=>`<span><i class="dot" style="background:var(--${k?'muted':'ok'})"></i>找到 v${shortV(i.version)}：<span class="code">${htmlEsc(tilde(i.path))}</span></span>`).join('')}<button class="pill" data-idf="find">重新查找</button><button class="pill" data-idf="pick">手动指定位置</button></div>${idfE}${toolsBox()}<p class="note">每个项目要哪个版本，码表读项目文件自动挑；项目要的版本没装时，这里会出现"帮我安装"。</p>`;
  return `<div class="stack">${head}
  <section class="panel ${page==='settings'&&envMark&&!keyOK()?'miss':''}" id="set-llm"><h2>${first?(done[0]?'✓ ':'① '):''}大模型（找关键步骤、写报告建议时用）</h2>
   <div class="radio">${opt('claude','Claude','识别准确率最高（验证期 9 个对 8 个）。需要你自己的 Claude API 密钥，按用量付费。',' <span class="chip c-ok">推荐</span>')}
   ${opt('cc','本机已登录的 Claude Code','不用填密钥，用你自己 Claude 会员的额度。属于可选用法，以官方条款为准。')}
   ${opt('deepseek','DeepSeek','需要你自己的 DeepSeek API 密钥，按用量付费。验证期准确率较低（9 个对 4 个）。码表自己核对每个计时点的位置，有问题才停下请你看。')}</div>
   ${setLLM==='cc'?(!CCST?`<p class="note" style="margin:10px 0 0">正在本机找 Claude Code……</p>`:ccOK()?`<p class="note" style="margin:10px 0 0">${first?'正在本机找 Claude Code……找到了，已登录。':'已找到本机的 Claude Code，已登录。'}</p>`:`<p class="note" style="color:var(--bad);margin:10px 0 0">✕ ${CCST.found?'找到了 Claude Code，但还没登录：在终端里运行 claude，按提示登录一次，再点「重新检查」。':'这台电脑上没找到 Claude Code。装好并登录后点「重新检查」，或者改用填密钥的方式。'} <button class="pill" data-cc="1">重新检查</button></p>`):`<div class="field" style="margin-top:12px"><label for="set-key">${setLLM==='claude'?'Claude':'DeepSeek'} API 密钥</label><div class="inrow"><input type="text" id="set-key" value="${keyDraft?keyDraft.replace(/"/g,'&quot;'):hasKey(setLLM)?'••••••••••••••••':''}" placeholder="粘贴你的密钥" autocomplete="off"><button class="btn" data-test="1">测试密钥</button></div>${keyErr?`<p class="note" style="color:var(--bad);margin:6px 0 0">✕ ${keyErr}</p>`:''}
   ${keyTested?'':`<details class="manual" style="margin-top:10px" ${hasKey(setLLM)?'':'open'}><summary>密钥是什么、去哪拿</summary>
   <p style="margin:6px 0;font-size:14px">密钥是大模型公司给你的一串字符，码表用它替你调用大模型读代码，按用量从你的账户扣钱。一次找关键步骤约几毛钱到一两块钱（估算）。</p>
   ${setLLM==='claude'?`<ol class="notes" style="font-size:14px;color:var(--fg);margin:0"><li>打开 Claude 开发者平台 console.anthropic.com，用邮箱注册登录</li><li>先在"账单"里充值（最少 5 美元）</li><li>左边点"API Keys"→"Create Key"，起个名字比如"码表"，复制出现的那一串（只显示一次）</li><li>回到这里粘贴进上面的框</li></ol>`:`<ol class="notes" style="font-size:14px;color:var(--fg);margin:0"><li>打开 DeepSeek 开放平台 platform.deepseek.com，用手机号注册登录</li><li>先在"充值"里充一点（10 元就够试用）</li><li>左边点"API keys"→"创建 API key"，复制出现的那一串（只显示一次）</li><li>回到这里粘贴进上面的框</li></ol>`}
   <p class="note" style="margin:6px 0 0">已经有 Claude 会员、电脑上装了 Claude Code？选上面的"本机已登录的 Claude Code"，不用密钥。</p></details>`}
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
   <p class="note">每个项目一个文件夹：识别结果、每次的原始日志和报告。都在你电脑上。</p>
  </section>
  <section class="panel"><h2>提示音</h2><p class="note" style="margin:0 0 8px">多轮实测时，某一轮没收全会低响一声，收好了不响。点"试听"听听这台电脑上能不能听到。<br>听不到：先看电脑音量；音量正常还听不到，把浏览器整个退出（Mac 上按 Command + Q）再打开。</p><div class="acts" style="margin:0;align-items:center"><button class="btn" data-beeptest="1">试听</button>${beepMsg?`<span class="note">${beepMsg==='running'?'浏览器的声音是开着的：要是没听到，看看电脑音量、有没有静音、有没有接了别的耳机。':`浏览器没让网页出声（${htmlEsc(beepMsg)}）：再点一次「试听」；还不行的话看看浏览器对这个网页是不是设了"不允许自动播放"。`}</span>`:''}</div></section>
  <div class="acts" style="justify-content:center;margin:4px 0 8px"><button class="btn pri" data-save="1" style="min-width:10em">保存</button></div>
</div>`;
}

// ===== 为什么、怎么改（2026-10-13 补：需求 9）=====
// 真产品里：问题从数据算出（反而晚、偶发慢、大头），"为什么、怎么改"由 AI 读代码 + 这次数据写，效果都是估算
const REPVAR={a:{label:'A 问题卡片',idea:'A 问题卡片：每个问题一张卡，先说为什么、对用户有什么影响，再列可选方案（效果、代价、工作量、找谁），最后一句建议。最接近验证 06 样例报告，从上往下读完就能拍板。'},
 b:{label:'B 勾方案',idea:'B 勾方案：把所有问题的方案摊在一张表里，负责人直接勾要做的，下面自动汇总预计效果、要找谁、哪些有代价要权衡，还能生成一份待办发给相关的人。重在"拍板"这个动作。'},
 c:{label:'C 点哪段看哪段',idea:'C 点哪段看哪段：先选一段，旁边说清这段在做什么、用了多久、正不正常；有问题的段再给为什么和方案。适合想弄懂整条链路的人，也能看"这段为什么不用改"。'}};
let repVar='a',fixPick=null,fixTodo=false,segSel=null;
// 和上次比（10-20 改）：差值要同时大于 0.1 秒、大于上次的 10%，才说快了、慢了，否则就说"持平"，
// 不说快了慢了（10-17 用户定；规则和后台 judge.vs_prev 一样）
function cmpTxt(now,prev,noise){const d=now-prev;if(Math.abs(d)<=Math.max(.1,Math.abs(prev)*.1))return '<span class="note">持平</span>';
 return d<0?`<span class="down">↓ 快了 ${f(-d)} 秒</span>`:`<span class="up">↑ 慢了 ${f(d)} 秒</span>`}
function prevLine(){const P=JREP&&JREP.prev;if(!P)return '';if(P.why)return `<p class="note" style="margin:0">${htmlEsc(P.why)}</p>`;
 // 每条线都比（测全程时"最后一声"主要看回答有多长，只比它会看错）
 const L=(JREP.lines||[]).filter(m=>P.lines[m.name]!=null&&m.med!=null);if(!L.length)return '';
 let RL=[];try{RL=repScene().lines||[]}catch(e){}const nm=m=>(RL.find(l=>l.name===m.name)||{}).end||String(m.name).replace(/^到/,'');
 const items=L.map(m=>`${htmlEsc(nm(m))} ${f(P.lines[m.name])} → ${f(m.med)} 秒 ${cmpTxt(m.med,P.lines[m.name],(P.noise_lines||{})[m.name])}`).join('；');
 return `<p style="margin:0"><b>比上次（${htmlEsc(P.measured_at)}，${P.n} 轮）：</b>${items}</p>`}
// 标题旁的小标签：和上一次测量比（两页都显示）。主线 = 挂体验标准的那条，没有就第一条判了快慢的；悬停看每条线的数
function vsTag(){const P=JREP&&JREP.prev;if(!(JREP&&!JREP._none&&repKey().startsWith('real'))||!P||P.why||!P.lines)return '';
 const L=(JREP.lines||[]).filter(m=>m.vs&&m.verdict!=='看回答长短');if(!L.length)return '';
 let RL=[];try{RL=repScene().lines||[]}catch(e){}const nm=m=>(RL.find(l=>l.name===m.name)||{}).end||String(m.name).replace(/^到/,'');
 const main=L.find(m=>m.exp_std)||L[0],c=main.vs.change,txt=c==='持平'?'持平':`${c} ${f(Math.abs(main.vs.d))} 秒`;
 const tip=[`和上一次测量（${P.measured_at}，${P.n} 轮）比中位数：`,...L.map(m=>`${nm(m)} ${f(m.vs.prev)} → ${f(m.med)} 秒，${m.vs.change==='持平'?'持平':m.vs.change+' '+f(Math.abs(m.vs.d))+' 秒'}`),'差要同时大于 0.1 秒和上次的 10%，才算快了、慢了。'].map(htmlEsc).join('<br>');
 return `<span class="vstag tip tip-r ${c==='慢了'?'up':c==='快了'?'down':''}" tabindex="0">较上次 ${txt}<i class="tipbox">${tip}</i></span>`}
function staleBar(){if(!(JREP&&!JREP._none&&repKey().startsWith('real')))return '';return prevLine()}  // 报告顶上：和上次比的那一句
// 负责人看的文字不出现代码：括号里带文件名、行号、函数名的整段去掉（码表格式检查也会拦，这里兜底早先存下的建议）
const noCode=t=>String(t||'').replace(/\s*[（(][^（）()]*?(?:[\w/.-]+\.(?:c|cc|cpp|cxx|h|hpp)\b|第\s*\d+\s*行|[A-Za-z_]\w*\(\))[^（）()]*?[）)]/g,'');
function activeFixes(D){const out=[];decisions(D).forEach(d=>{const F=(D.S.fixes||[])[cardOf(D.S.fixes||[],d)];if(F&&!out.some(o=>o.when===F.when))out.push({...F,title:d.t,lv:d.lv,why:noCode(plain(F.why)),impact:noCode(plain(F.impact)),rec:noCode(plain(F.rec)),opts:F.opts.map(o=>({...o,name:noCode(plain(o.name)),effect:noCode(plain(o.effect)),cost:noCode(plain(o.cost)),wn:noCode(plain(o.wn))}))})});return out}
// 工作量怎么分（10-21 用户定）：表下一行小字，不放收起块、不用浮窗
const ESTNOTE='<p class="note" style="margin:6px 0 0">工作量：<span class="wk wk-s">小</span> 改一处　<span class="wk wk-m">中</span> 改几处，或要另一方配合　<span class="wk wk-l">大</span> 新做一块，或要长期维护　<span class="wk wk-u">不确定</span> 要先查清原因</p>';
function optTable(F,fi,pick){
  return `<div class="scroll"><table><thead><tr>${pick?'<th></th>':''}<th>方案</th><th>效果（估算）</th><th>代价</th><th>工作量</th><th>找谁</th></tr></thead><tbody>${F.opts.map((o,oi)=>{const id=fi+'-'+oi;return `<tr>${pick?`<td><input type="checkbox" data-pick="${id}" ${fixPick.has(id)?'checked':''} aria-label="选这个方案"></td>`:''}<td><b>${o.name}</b>${o.rec?' <span class="chip c-ok">建议</span>':''}${o.risk?' <span class="chip c-warn">要权衡</span>':''}</td><td>${o.effect}</td><td>${o.cost}</td><td><span class="wk wk-${{'小':'s','中':'m','大':'l'}[o.work]||'u'}">${o.work}</span><br><span class="note">${String(o.wn||'').replace(/^(小|中|大|不确定)\s*[：:，,]\s*/,'')}</span></td><td>${o.who}</td></tr>`}).join('')}</tbody></table></div>${pick?'':ESTNOTE}`;
}
function initPick(FX){fixPick=new Set();FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(o.rec)fixPick.add(fi+'-'+oi)}))}  // 不再勾选（10-21）：每次都按建议方案算；以前只在第一次算，卡片还没到时算成空的，有的演示一直没有消息
// 结论建议、待办里的文字：把"P2-P5""P4→P5""P7"这类编号换成中文名（技术细节里照原样）
function plain(t){if(!t)return t;const D=build(page==='report'&&JREP&&JREP._key===repKey()&&!JREP._none?repScene():SCENES[scene]);
 const seg={};Object.entries(D.S.segs).forEach(([k,g])=>{seg[k]=g.name;const [a,b]=String(g.code||'').split('→').map(x=>x.trim());if(a&&b){seg[a+'-'+b]=g.name;seg[a+'→'+b]=g.name}});
 const pt={};(repKey().startsWith('real')&&IDENT&&IDENT.page?IDENT.page.steps:FF[scene].steps||[]).forEach(x=>pt[x.p]=x.n);
 if(repKey().startsWith('real')&&JREP&&JREP.scenario)Object.assign(pt,JREP.scenario.point_names||{});  // 你自己加的步骤（P21 起）
 // 括号里的编号前面已有中文名，直接去掉；没括号的换成中文名
 return String(t).replace(/\s*[（(]\s*P\d+\s*(?:[-→～~]\s*P\d+\s*)?[）)]/g,'')
  .replace(/\b(P\d+)\s*[-→～~]\s*(P\d+)\b/g,(m,a,b)=>seg[a+'-'+b]?`“${seg[a+'-'+b]}”`:pt[a]&&pt[b]?`“${pt[a]}”到“${pt[b]}”`:m)
  .replace(/\b(P\d+)\b/g,(m,a)=>pt[a]?`“${pt[a]}”`:m)}
// 导出：把这份报告存成一个 HTML 文件（样式和"结论建议 / 技术细节"切换都在文件里，断网能开，不引用外面的文件）。
// 按钮去掉、勾选换成 ☑ / ☐（导出那一刻勾的）、待办展开；"改完以后 / 还原"不放进去。
function expStrip(h,pre){const d=document.createElement('div');d.innerHTML=h;
  d.querySelectorAll('.stale,.expdone').forEach(x=>x.remove());
  d.querySelectorAll('section').forEach(x=>{const t=(x.querySelector('h2')||{}).textContent||'';if(t.startsWith('改完以后')||t.startsWith('设备上现在是'))x.remove()});
  d.querySelectorAll('input[type=checkbox],input[type=radio]').forEach(i=>{const s=document.createElement('span');s.textContent=i.checked?'☑':'☐';s.className='ck';i.replaceWith(s)});
  d.querySelectorAll('button').forEach(b=>{if(b.dataset.jump){const a=document.createElement('a');a.href='#'+pre+b.dataset.jump;a.className='lnk';a.textContent=b.textContent;b.replaceWith(a)}else if(b.dataset.copy)b.dataset.copy=pre+b.dataset.copy;else if(!b.dataset.code)b.remove()});  // 复制按钮留着，导出的报告里也能复制
  d.querySelectorAll('[id]').forEach(x=>x.id=pre+x.id);d.querySelectorAll('.flash').forEach(x=>x.classList.remove('flash'));
  d.querySelectorAll('details').forEach(x=>x.removeAttribute('open'));return d.innerHTML}  // 导出也默认收起，点开能看（决策 98）
// 导出存到哪、存完的提示（一直在，直到你关掉）
let EXP={dir:'',saved:null};
const expWhat=()=>{if(!realFlash())return '';const w=IDENT.page.mapWhat||hSay||'';return w===WHOLE||w.startsWith('全程')?'全程':w};  // 文件名用你自己写的场景说法
function expDone(){return EXP.saved?`<section class="panel expdone" role="status"><div class="acts" style="justify-content:space-between;align-items:center;margin:0;flex-wrap:wrap"><span>✓ 已导出：<span class="code">${htmlEsc(EXP.saved)}</span></span><span style="display:flex;gap:8px"><button class="pill" data-expshow="1">在访达里显示</button><button class="pill" data-expclose="1">关掉</button></span></div></section>`:''}
async function exportStart(){EXP.state='';EXP.err='';try{EXP.dir=(await (await fetch('/api/report/export-dir'+(repKey().startsWith('real')?'':'?demo='+encodeURIComponent(scene)))).json()).dir}catch(e){}pop='export';render()}
// 测完自动存一份报告（报告关掉、码表重启后，「以前测过的」里还能打开）：等"为什么、怎么改"写完再存，每次测量只存一次
let AUTOSAVED='';
function autoSave(){if(page!=='report'||!repKey().startsWith('real')||!JREP||JREP._none||!JREP.measured_at||AUTOSAVED===JREP.measured_at)return;
 if(ADV?ADV.status==='running':D0ok())return;AUTOSAVED=JREP.measured_at;if(EXP.state==='ok'&&EXP.saved)return;setTimeout(()=>exportReport(),300)}
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
<p class="expnote">这是码表导出的报告：一个文件，断网也能打开，可以直接发给别人。数字由码表按这次的数据直接算出，不经过 AI；"为什么、怎么改"的文字是 AI 读代码写的。${log}</p></div>
<div id="tip" hidden></div><script>${tipShow.toString()}${todoCopy.toString()}${codeTog.toString()}document.addEventListener('click',e=>{if(todoCopy(e)||codeTog(e))return;const a=e.target.closest('a[href^="#"]');const el=a&&document.getElementById(a.getAttribute('href').slice(1));const d=el&&el.querySelector('details');if(d&&/fix-[0-9]+$/.test(el.id))d.open=true});document.addEventListener('mouseover',tipShow);document.addEventListener('focusin',tipShow);document.addEventListener('scroll',()=>{document.getElementById('tip').hidden=true},true);function sw(v){document.getElementById('v-boss-body').hidden=v!=='b';document.getElementById('v-dev-body').hidden=v!=='d';document.getElementById('tb-b').setAttribute('aria-pressed',v==='b');document.getElementById('tb-d').setAttribute('aria-pressed',v==='d');scrollTo(0,0)}<\/script></body></html>`;
  let r,j;try{r=await fetch('/api/report/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({html,what,demo:real?null:scene,folder:folder||null})});j=await r.json()}catch(e){r={ok:false};j={detail:'码表后台没有回应，看看运行码表的窗口是不是关了。'}}
  if(!r.ok){EXP.state='fail';EXP.err=j.detail||'导出没成功';toast('✕ '+EXP.err);render();return}
  {const t=document.getElementById('toast');if(t&&t.classList.contains('t-bad'))t.hidden=true}  // 上一次没导出成功的红色提示，这次成功了就收起
  EXP.saved=j.path;EXP.state='ok';EXP.partial=!!(ADV&&ADV.status!=='done'&&repKey().startsWith('real'));histLoad();render();return j.path}
// 协作沟通（10-21 用户定）：每人一段消息。服务器、网络、第三方可能是别家公司，消息一律按外部对接人写：
// 不带我们的源码位置，不用码表的内部说法（码表、达标率、线名），附设备外几段的每轮数据方便对方对日志；发给固件研发的另附改法和代码位置
const isFw=w=>/固件/.test(w);
function todoText(w,chosen,FX,D){const mine=chosen.filter(o=>o.who===w),fw=isFw(w),[dev,what]=String(D.S.crumb).split(' · ');
  const at=D.S.real&&JREP&&JREP.measured_at?`${JREP.measured_at} 开始，`:'';
  // 每轮数据：设备外的几段，表头写从哪个点到哪个点（几段的起点可能相同，写清楚免得对方相加）；一般不到 0.01 秒的段不列
  const outS=D.allSegs.filter(s=>((D.J&&D.J.segments.find(x=>x.k===s.k))||{}).outside&&s.med>=0.01),rows=D.rounds.map((r,i)=>[r.n,...outS.map(s=>s.v[i]==null?'—':s.v[i].toFixed(2))].join(' | '));
  const tbl=!fw&&outS.length?`\n\n每轮数据（秒，设备上记到的时刻之差）：\n轮 | ${outS.map(s=>String(s.code).split('→').map(p=>plain(p.trim()).replace(/^“|”$/g,'')).join(' → ')).join(' | ')}\n${rows.join('\n')}`:'';
  return `【${dev}响应时间测量，请帮忙处理】\n\n你好，我们测了 ${dev}从${what}的时间（${at}连续 ${D.n} 轮），有 ${mine.length} 件事想请你们帮忙：\n\n`+mine.map((o,k)=>{const F=FX[o.fi];
    return `${k+1}. ${noCode(plain(o.ask||o.name))}\n   怎么算解决：${noCode(plain(F.done||F.dev.verify))}`+(fw?`\n   改法：${plain(o.how)}`:'')}).join('\n\n')+tbl+`\n\n改好后告诉我们，我们按同样条件再测一次对比。`}
// 勾了的方案；同一个人做同一件事出现在几张卡片里（10-20 真测：「思考中」表情两张卡片都有），待办只算一件，标上解决哪几个问题
function pickedOpts(FX){const out=[],seen={};FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(!fixPick.has(fi+'-'+oi))return;const k=o.who+'|'+o.name;if(seen[k]){seen[k].fis.push(fi);return}out.push(seen[k]={...o,fi,fis:[fi]})}));return out}
const pNos=o=>'问题 '+o.fis.map(i=>i+1).join('、');
function myPick(D,FX){initPick(FX);
  if(!FX.length)return '';  // 没有要改的问题：不显示
  const chosen=pickedOpts(FX),who=[...new Set(chosen.map(o=>o.who))];
  // 协作沟通（10-21 用户定名、默认收起）：收起时也看得出要找谁、几件
  return `<div class="panel" id="my-pick" style="border-left:3px solid var(--speak);padding-top:10px;padding-bottom:10px"><details class="fixmore"><summary style="display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap"><span><b>协作沟通</b>　发给：${who.map(w=>`${w}（${chosen.filter(o=>o.who===w).length} 件）`).join('、')}</span></summary><p class="note" style="margin:8px 0">每个问题按「建议」方案，按人合成一段，复制后发给对方。发给设备以外的人（服务器、网络等）的不带源码位置，可以直接转给外部对接人。</p>
   ${who.map((w,wi)=>`<div class="todo1"><div class="todoh"><b>发给：${w}</b><button class="pill copy" data-copy="todo-${wi}" aria-label="复制发给${w}的消息">${COPY_IC}复制</button></div><pre id="todo-${wi}" data-who="${w}">${todoText(w,chosen,FX,D)}</pre></div>`).join('')}</details></div>`;
}
function fixA(D){const FX=activeFixes(D);initPick(FX);
  return FX.map((F,fi)=>`<div class="panel" id="fix-${fi}" style="border-left:3px solid ${F.lv===0?'var(--bad)':'var(--warn)'};padding-top:10px;padding-bottom:10px">
   <details class="fixmore"><summary style="display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap">${lvTag(F.lv)}<span>问题 ${fi+1}：<b>${F.title}</b>${(F.opts.find(o=>o.rec)||F.opts[0])?`　建议：${(F.opts.find(o=>o.rec)||F.opts[0]).name}`:''}</span><span hidden>${fi}</span></summary>
   <p style="margin:0 0 6px"><b>为什么：</b>${F.why}</p><p style="margin:0 0 10px"><b>对用户的影响：</b>${F.impact}</p>
   ${optTable(F,fi,false)}<p style="margin:10px 0 0"><b>建议：</b>${F.rec}</p></details></div>`).join('')+myPick(D,FX);
}
function fixB(D){const FX=activeFixes(D);
  if(!fixPick){fixPick=new Set();FX.forEach((F,fi)=>F.opts.forEach((o,oi)=>{if(o.rec)fixPick.add(fi+'-'+oi)}))}
  const chosen=pickedOpts(FX);
  const who={};chosen.forEach(o=>(who[o.who]=who[o.who]||[]).push(o.name));
  const rows=FX.map((F,fi)=>`<h3 id="fix-${fi}" style="margin:14px 0 4px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">${lvTag(F.lv)}问题 ${fi+1}：${F.title}</h3><p class="note" style="margin:0 0 6px">${F.why}</p>${optTable(F,fi,true)}`).join('');
  const sum=chosen.length?`<ul style="margin:6px 0">${chosen.map(o=>`<li>${pNos(o)}：${o.name}。预计${o.effect}。</li>`).join('')}</ul>
   <p style="margin:6px 0"><b>要找：</b>${Object.entries(who).map(([w,a])=>`${w}（${a.length} 件）`).join('、')}</p>
   ${chosen.some(o=>o.risk)?`<p style="margin:6px 0;color:var(--warn)"><b>要你权衡：</b>${chosen.filter(o=>o.risk).map(o=>`${o.name}，${o.cost}`).join('；')}。</p>`:''}
   ${FX.some((F,fi)=>!chosen.some(o=>o.fis.includes(fi)))?`<p style="margin:6px 0;color:var(--bad)">还有问题一个方案都没选：${FX.map((F,fi)=>chosen.some(o=>o.fis.includes(fi))?'':`问题 ${fi+1}`).filter(Boolean).join('、')}。不选也可以，等于先不处理。</p>`:''}
   <div class="acts" style="margin-top:8px"><button class="btn pri" data-todo="1">${fixTodo?'收起待办':'生成待办，发给相关的人'}</button></div>
   ${fixTodo?`<pre style="white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;font-size:13.5px;margin:10px 0 0">${Object.entries(who).map(([w,a])=>`【${w}】\n${chosen.filter(o=>o.who===w).map(o=>`· ${o.name}（${pNos(o)}：${FX[o.fi].title}）\n  做完的标准：${FX[o.fi].dev.verify}`).join('\n')}`).join('\n\n')}\n\n做完后用码表按同样的条件再测一次，看前后对比。</pre><p class="note">样子里只演示文字；真产品可以复制，或存成文件。</p>`:''}`
   :'<p class="note">还没勾任何方案。</p>';
  return `<div class="panel">${rows}</div><div class="panel" style="border-left:3px solid var(--speak)"><h3 style="margin:0 0 4px">你勾了 ${chosen.length} 个方案</h3>${sum}</div>${ESTNOTE}`;
}
function segJudge(D,s){if(D.J){const j=D.J.segments.find(x=>x.k===s.k);if(j){if(j.bad)return ['数据有问题','c-bad'];if(j.thin)return [`数据不足（${j.count}/${j.of} 轮）`,'c-warn'];if(j.spiky)return ['偶发特别慢','c-warn'];return [VTXT[j.verdict]||j.verdict,VCLS[j.verdict]||'c-gray']}}const spk=s.max>s.med*2.5&&s.max-s.med>.3;return spk?['偶发特别慢','c-warn']:['只记录用时','c-gray']}
function fixC(D){const FX=activeFixes(D);
  if(!segSel||!D.S.segs[segSel])segSel=FX.length?FX[0].seg:D.allSegs[0].k;
  const s=D.allSegs.find(x=>x.k===segSel),[jv,jc]=segJudge(D,s),fi=FX.findIndex(F=>F.seg===segSel),F=FX[fi];
  const btns=D.allSegs.map(x=>{const [v,c]=segJudge(D,x),hit=FX.some(F=>F.seg===x.k);return `<button class="btn" data-seg="${x.k}" aria-pressed="${x.k===segSel}" style="${x.k===segSel?'border-color:var(--fg);font-weight:600':''}">${x.name} <span class="chip ${hit&&c==='c-ok'?'c-warn':c}">${hit&&c==='c-ok'?'有问题':v}</span></button>`}).join('');
  return `<div class="panel"><p class="note" style="margin:0 0 8px">按发生顺序，点一段看详情：</p><div class="acts">${btns}</div></div>
   <div class="panel" id="fix-${fi}" style="border-left:3px solid ${F?(F.lv===0?'var(--bad)':'var(--warn)'):'var(--ok)'}"><h3 style="margin:0 0 6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">${F?lvTag(F.lv):''}${s.name} <span class="chip ${jc}">${jv}</span> <span class="note">谁负责：${s.owner}</span></h3>
   <p style="margin:0 0 6px"><b>这段在做什么：</b>${s.what}。</p>
   <p class="num" style="margin:0 0 10px">一般 ${f(s.med)} 秒，最慢 ${f(s.max)} 秒</p>
   ${F?`<p style="margin:0 0 6px"><b>为什么是问题：</b>${F.why}</p><p style="margin:0 0 10px"><b>对用户的影响：</b>${F.impact}</p>${optTable(F,fi,false)}<p style="margin:10px 0 0"><b>建议：</b>${F.rec}</p>`:'<p style="margin:0"><b>不用改。</b>这段没有偶发特别慢的轮，也没比上次慢。</p>'}</div>`;
}
// 怎么改（10-21 用户定）：没方案就整块不显示；"数据"改"推算"（表里有的数不再重复）；几个问题共用的方案只列一次，标对应哪几个问题；
// 查原因的方案不写改动大小、风险（AI 写"—"）
function fixDev(D){const FX=activeFixes(D);initPick(FX);if(!FX.length)return '';
  const opts=[];FX.forEach((F,fi)=>F.opts.forEach(o=>{const n=String(o.name).trim(),x=opts.find(y=>y.key===n);if(x){x.for.push(fi+1);x.rec=x.rec||o.rec}else opts.push({...o,key:n,for:[fi+1]})}));
  const has=v=>v&&!/^[—\-－]+$/.test(String(v).trim());
  return `<section class="panel"><h2>怎么改</h2><p class="note" style="margin:-4px 0 10px">改法是 AI 读代码后给的思路，没有实现、没有测过。</p>${FX.map((F,fi)=>`<div style="margin-bottom:14px"><h3 style="margin:0 0 6px;font-size:15px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">${lvTag(F.lv)}问题 ${fi+1}：${F.title}</h3>
   <table><tbody><tr><th style="width:7em">推算</th><td>${F.dev.data}</td></tr><tr><th>原因</th><td>${F.dev.cause}</td></tr><tr><th>怎么复测</th><td>${F.dev.verify}</td></tr></tbody></table></div>`).join('')}
   <h3 style="margin:4px 0 6px;font-size:15px">方案</h3>${opts.map(o=>`<div class="optdev ${o.rec?'on':''}"><b>${o.name}</b>${o.rec?' <span class="chip c-ok">建议</span>':''} <span class="note">${FX.length>1?`对应问题 ${o.for.join('、')} · `:''}找谁：${o.who}</span><ul class="notes"><li>改法：${o.how}</li>${has(o.size)?`<li>改动大小：${o.size}</li>`:''}${has(o.rk)?`<li>风险：${o.rk}</li>`:''}</ul></div>`).join('')}</section>`;
}
// 只记录、不判快慢（10-26）：长短看回答说了多少字的线（比如到回到聆听）和只在它上面的段，不进图和结论，但数照写
function recOnlyBox(D){const R=D.S.recOnly,J=D.J;if(!R||!J||!(R.lines.length||R.segments.length||(R.parallel||[]).length))return '';
 const sc=J.scenario||{},SG=sc.segments||{},jl=n=>(J.lines||[]).find(x=>x.name===n),js=k=>(J.segments||[]).find(x=>x.k===k);
 const cell=x=>x&&x.med!=null?`<td class="r">${f(x.med)}</td><td class="r">${f(x.min)}</td><td class="r">${f(x.max)}</td><td class="r">${x.count!=null?`${x.count}/${x.of!=null?x.of:D.n}`:''}</td>`:'<td class="r" colspan="4">没有数</td>';
 const rows=R.lines.map(n=>{const l=jl(n)||{},end=l.end||n;
   return `<tr class="grp"><td ${SANS}><b>${l.measure?htmlEsc(n):`${htmlEsc(D.S.startShort)}到${htmlEsc(end)}`}</b></td><td ${SANS}><span class="chip c-gray">只记录、不判快慢</span></td>${cell(l)}</tr>`}).join('')
  +R.segments.map(k=>{const g=SG[k]||{},x=js(k);return `<tr><td ${SANS}>${htmlEsc(g.name||k)}</td><td class="note" ${SANS}>${htmlEsc(g.owner||'')}</td>${cell(x)}</tr>`}).join('')
  +((R.parallel||[]).length?`<tr class="grp"><td colspan="6" ${SANS}><b>并行的段</b> <span class="note">和上面几条线同时在走（从中间某一步分出去），不在哪条线从起点到终点的那一串上，不画进图、不加进合计</span></td></tr>`
   +R.parallel.map(k=>{const g=SG[k]||{},x=js(k);return `<tr><td ${SANS}>${htmlEsc(g.name||k)}</td><td class="note" ${SANS}>${htmlEsc(g.owner||'')}</td>${cell(x)}</tr>`}).join(''):'');
 return `<section class="panel scroll" id="sec-rec"><h2>只记录、不判快慢</h2><table class="num segtab"><thead><tr><th class="segn">线 / 段</th><th class="own">谁负责</th><th class="r">一般<br>（中位数）</th><th class="r">最快</th><th class="r">最慢</th><th class="r">有数的轮</th></tr></thead><tbody>${rows}</tbody></table>
 <p class="note" style="margin:8px 0 0">${R.lines.length?'线：长短主要看这次回答说了多少字，不判快慢，也不画进上面的图、不进结论；':''}数照样从原始日志算，单位秒。</p></section>`}
function reportPage(D){
  if(mode==='dev')return dev(D);
  return `<div class="stack">${expDone()}<div><div class="titlerow">${titleHtml(D)}${vsTag()}</div><p class="sub">${subline(D)}</p>${D.S.real&&JREP?checksBox(JREP):''}${D.S.real&&JREP?`<details class="fold"><summary>数据怎么来的、起点怎么算</summary>${qualityLine(JREP.quality,JREP)+startLagLine(D.S.startShort,JREP.start_lag,startGap(JREP))+mergedLine(JREP.merged)}</details>`:''}${D.n<10?`<p class="note" style="margin:4px 0 0;color:var(--bad)">只测了 ${D.n} 轮，适合跑流程，数字不作数；要看一般多快、偶尔出的问题，建议测到 20 轮。</p>`:''}</div>
  <section class="panel"><h2>${(typeof plainOn==='function'&&plainOn(D))?'结论和建议':'要你拍板'}</h2>${decideList(D)}</section>
  <section class="panel" id="sec-tl"><h2>${(typeof plainOn==='function'&&plainOn(D))?'时间花在哪':'一次的计时图'}</h2>${timeline(D)}</section>${(typeof plainOn==='function'&&plainOn(D))?plainRefs(D):''}
  <section class="stack"><h2 style="margin:8px 0 -6px">为什么、怎么改</h2>${D.S.real&&ADV&&ADV.status==='done'&&ADV.called===false&&ADV.at?`<p class="note" style="margin:8px 0 0">沿用 ${htmlEsc(ADV.at)} 写的：要改的问题和那次一样，没再调用大模型。文中的秒数是那次的，这次的数看上面。</p>`:''}${D.S.real&&(!ADV||ADV.status!=='done')?advBox():fixA(D)}</section>
  <h2 style="margin:12px 0 -6px">依据</h2>
  <section class="panel" id="sec-tiers"><h2>${(typeof plainOn==='function'&&plainOn(D))?'用时和标准':'用时和达标率'}</h2>${tiers(D)}${stdNote(D)}</section>${recOnlyBox(D)}
  <section class="panel" id="sec-rounds"><h2>${D.n} 轮逐轮</h2>${roundsChart(D)}</section>${retestEntry()}${restoreBox(scene)}</div>`;
}

let page='home',mode='boss',scene='',homeVar='m',hStage=0,hSay='';
// 网址 #页面名 记着在哪一页（D 组）：打开时先不画，等按后台状态接上进度（uiRestore）再画，免得先闪一下空白首页
// 这个标签页开着时刷新，sessionStorage 里的记号还在；关了浏览器或标签页再打开，记号没了，算新的一轮
const FRESH=(()=>{try{const f=!sessionStorage.getItem('fsw-open');sessionStorage.setItem('fsw-open','1');return f}catch(e){return !location.hash}})();
let booting=true,LASTP='';const HASH0=(()=>{try{const h=decodeURIComponent(location.hash.slice(1));return PAGES.some(p=>p.id===h)?h:''}catch(e){return ''}})();
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
const bgFailed=()=>!!(BG&&BG.state==='failed'),bgWaitTool=()=>!!(BG&&BG.state==='waiting_tool'),bgToolErr=()=>bgFailed()&&!!BG.cause;
// 不是源码的问题（编译工具没加载起来、组件没下载下来）：标题按原因说，正文去掉和标题重复的那句
// 缺哪个工具（后台从报错里认出来、或编译前自检查出来的）就点名：编译工具没准备好（缺 cmake），不是你的源码问题
const TOOLT=(c,m)=>c==='network'?'组件没下载下来，不是源码的问题':m&&m.length?`编译工具没准备好（缺 ${m.map(htmlEsc).join('、')}），不是你的源码问题`:'编译工具没加载起来，不是源码的问题',toolSay=m=>String(m||'').replace(/^.*?不是(你的)?源码(的)?问题[。：]/,'');
const lnTxt=l=>+l>0?`第 ${l} 行`:'行号待核对';  // 大模型没给出行号时记成 0（识别时已标需核对）
const htmlEsc=t=>String(t||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const BGTIME=()=>{if(BG&&BG.seconds!=null){const t=Math.round(BG.seconds);return t>=60?`${Math.floor(t/60)} 分 ${t%60} 秒`:`${t} 秒`}return FF[scene].bg};
const bgBody=()=>({work:REAL.work,chip:REAL.chip,board:REAL.board,idf_min:REAL.idf_min,project_dir:REAL.project_dir});  // 带上项目：源码没变就用上次编好的原版（决策 134）
function bgStartReal(resume){clearInterval(bgT);bgT=null;clearInterval(bgPoll);bgSrc=hSrc;bgSec=0;bgDur=100;BG=resume&&BG?BG:{state:'running',pct:0};  // resume：刷新后接着看后台正在编的，不重新开始
 const body=bgBody();
 const show=s=>{BG=s;bgSec=s.state==='done'?100:(s.pct||0);bgPill();if(pop==='bg')morphHTML(document.getElementById('popdlg'),popHTML());if(page==='home'&&hStage===3&&stepsRead===2)document.getElementById('dlg').innerHTML=modalHTML()};
 const tick=async()=>{let s;try{s=await (await fetch('/api/bgbuild')).json();if(s.state==='waiting_tool'||s.state==='idle'||s.work!==body.work)s=await api('/api/bgbuild/start',body)}catch(e){return}
  show(s);
  if(s.state==='done'||s.state==='failed'){clearInterval(bgPoll);bgPoll=null;if(s.state==='done'){bgShownDone=Date.now();setTimeout(bgPill,5100)}
   if(page==='flash'||page==='steps'||(page==='home'&&hStage===3))render()}};
 if(resume)tick();else api('/api/bgbuild/start',body).then(show).catch(()=>{});bgPoll=setInterval(tick,1000)}
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
 if(runView()){render();return}
 const b=document.getElementById('readbox');if(b)morphHTML(b,readBox())}
let freshNext=false;  // 「重新分析」：这一次不沿用上次找的结果，让大模型重新读
async function identStart(retry){clearInterval(readT);stepsRead=1;readSec=0;readFound=0;IDENT=null;if(bgSrc!==hSrc)bgStart();
 const r=await fetch(retry?'/api/identify/retry':'/api/identify/start',{method:'POST',headers:{'Content-Type':'application/json'},body:retry?'{}':JSON.stringify({work:REAL.work,what:hSay,project_dir:REAL.project_dir,fingerprint:REAL.fingerprint,chip:CHIP(),reuse:reuseLast&&!freshNext})});freshNext=false;
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
  ${stepsRead===2?foundList():`${readFound?`<div class="list">${STEPS.slice(0,readFound).map(x=>`<div class="srow"><b>${x.n}</b><span class="note">${x.f} ${lnTxt(x.l)}</span></div>`).join('')}</div>`:''}
  <p class="note">只发了代码，没发任何"标准答案"；结果先存在本机，源码没变下次直接用，不再花钱调用。关掉这页不影响，读完会留着。</p>`}`}
// 没法知道真实进度的等待（大模型读代码）：按一般用时估一个进度，最多到 95%，等完了才满（决策 105）
const estPct=(sec,typ)=>Math.min(95,Math.round(100*(1-Math.exp(-sec/typ))));
function readBoxReal(llm){const J=IDENT,fail=stepsRead===3,done=stepsRead===2,found=J.found||[],pick=J.stage==='pick';
 const st=done?4:fail?(pick?(found.length?3:2):1):!pick&&readSec<6?0:pick?(found.length?3:2):1;
 const row=(i,t,d)=>`<li class="istep ${i<st?'ok':i===st?(fail?'bad':'cur'):''}"><b>${i<st?'✓':fail&&i===st?'!':i+1}</b><span>${t}<small>${d}</small></span></li>`;
 if(J.reused)return `<ol class="isteps">${row(0,'挑出要读的代码','码表自己做，不用大模型')}${row(1,J.carried?'代码改过，但计时点那几处和上次一样：直接用上次的结果':'源码和要测的都没变，直接用上次的结果','没有调用大模型，不花钱（设置页可以关掉这个）')}${row(2,'列出找到的步骤',`共 ${STEPS.length} 个`)}</ol>${foundList()}`;
 return `${(J.carry_changed||[]).length?`<p class="note" style="margin:0 0 8px;color:var(--warn)">代码改过，而且这几处和上次不一样，所以重新找：${J.carry_changed.map(htmlEsc).join('、')}</p>`:''}<ol class="isteps">
  ${row(0,'挑出要读的代码',`码表自己做，不用大模型：${htmlEsc(REAL.name)} 复制好的那份源码，大模型只能读、改不了任何文件`)}
  ${row(1,`${llm} 理清流程`,`先理清一轮里设备依次做了什么，码表核对过行号再往下。${st===1?`已等 ${mmss(readSec)}，`:''}${st===1?`<span class="pbar"><i style="width:${estPct(readSec,90)}%"></i></span>进度条是按一般用时估的，不是真实进度`:''}`)}
  ${row(2,`${llm} 选计时点`,`从理清的流程里选位置。${st===2?`已等 ${mmss(readSec)}，两步合计一般 2～5 分钟。`:''}`)}
  ${row(3,'在这里列出找到的步骤',`${done?'找完了，共':'已找到'} ${done?STEPS.length:found.length} 个`)}</ol>
  ${done?foundList():`${found.length?`<div class="list">${found.map((n,k)=>{const at=((J.at||[])[k]||'').match(/([^/\s]+):(\d+)$/);return `<div class="srow"><b>${htmlEsc(n)}</b>${at?`<span class="note">${htmlEsc(at[1])} 第 ${at[2]} 行</span>`:''}</div>`}).join('')}</div>`:''}`}
  ${J.net&&!fail&&!done?`<p class="note" style="color:var(--warn)">${htmlEsc(J.net)}</p>`:''}
  ${fail?`<p class="note" style="color:var(--bad)">✕ ${J.message||'没找完'}</p>${J.failed_log?`<p class="note">大模型这次的原话和码表查出的问题已存下（已去掉密钥）：<code style="word-break:break-all">${htmlEsc(J.failed_log)}</code>，报问题时附上它。</p>`:''}<div class="acts" style="justify-content:flex-start;margin:6px 0"><button class="btn pri" data-idretry="1">重试</button><button class="btn" data-go="settings">到设置页换大模型</button>${/no_start|no_end/.test(J.kind||'')?'<button class="btn" data-mclose="1">换个说法写</button>':''}</div>`:''}
  <p class="note">只给它读代码，没给任何"标准答案"；结果存在本机，源码和要测的都没变，下次直接用，不再花钱调用。关掉这页不影响，读完会留着。</p>`}
function foundList(){const F=FF[scene],rows=STEPS,mid=rows.filter(x=>x.sure!=='高').length;
 return `${REAL&&IDENT&&IDENT.page&&IDENT.page.warn?`<div class="why" style="border-left-color:var(--bad);margin-top:12px">${htmlEsc(IDENT.page.warn)}</div>`:''}<p style="margin:12px 0 4px">找到 <b>${rows.length}</b> 个关键步骤：${mid} 个需要你核对，${rows.length-mid} 个可信度高。</p>
   <ol class="found">${rows.map(x=>`<li>${x.n} <span class="note">${x.f} ${lnTxt(x.l)}${x.line==='face'?' · '+F.ln.face:''}${x.sure!=='高'?' · <span style="color:var(--warn)">需核对</span>':''}</span></li>`).join('')}</ol>
   ${F.sideAsk?`<p class="note">另外发现：${F.sideAsk.found}。标「${F.ln.face}」的 ${STEPS.filter(x=>x.line==='face').length} 个是顺带发现的，默认也测，下一页可以去掉。</p>`:''}`}
let bgShownDone=0;
const BGTIP='原始代码：你选的那份源码，码表复制一份，一个字没改、没插计时点。<br>点「开始找关键步骤」时开始编，这时源码和要测的内容都定了。插完计时点后的「编译」要用它，只重编改过的几个文件（验证期实测：从头编 119 秒，只重编 80 秒）。<br>没编完，只有「编译」那一步会先等它，其他步骤照常做。你原来的文件夹不动。';
function bgPill(){const el=document.getElementById('bgpill');if(!el)return;let h='';
 if(bgSrc&&hSrc){if(bgFailed())h=`<button class="bgp warn" data-bgpop="1">${bgToolErr()?TOOLT(BG.cause,BG.missing).split('，')[0]:'原始代码编译没通过'}</button>`;else if(!envComp()||bgWaitTool())h=`<button class="bgp warn" data-bgpop="1">后台编原始代码：等编译工具装好</button>`;
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
// 识别后的结构检查（所有模型共用）：没过的标红，拿不准的只提示；你改过位置的，原来的检查结果不再适用，不显示
const chkTxt=x=>edits[x.p]?'':(x.chk||[]).map(c=>`<br><span style="color:var(${c.level==='fail'?'--bad':'--warn'})">${c.level==='fail'?'结构检查没通过':'结构提示'}：${htmlEsc(c.text)}</span>`).join('');
const chkBox=()=>{if(!(REAL&&IDENT&&IDENT.page))return '';const st=(IDENT.page.steps||[]).filter(x=>!edits[x.p]&&!dropped.has(x.p)),f=st.filter(x=>x.struct_ok===false).length,t=st.reduce((a,x)=>a+(x.chk||[]).filter(c=>c.level!=='fail').length,0);
 return f||t?`<div class="why" style="border-left-color:var(${f?'--bad':'--warn'})">码表结构检查：${f?`<b>${f} 个计时点插下去会落进条件分支或循环里</b>（只在条件成立时才记到），请核对或修改位置；`:''}${t?`另有 ${t} 条提示（可能一轮记很多次、在编译开关里、或一段两头执行时机不同），`:''}见下面各步骤的说明。</div>`:''};
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
 if(addSt===3){const g=REAL&&addGuess?addGuess:A.addGuess;return `<div class="edit" style="margin-left:0">${head}<p style="margin:8px 0 4px">AI 找到的位置：<span class="code">${g.f}</span> ${lnTxt(g.l)}</p><p class="note" style="margin:0 0 6px">${g.why}。AI 推测的，请核对。</p>
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
   <div class="note"><span class="code">${x.f}</span> ${lnTxt(x.l)} · ${x.why}${x.note?`<br><span style="color:var(--warn)">要留意：${x.note}</span>`:''}${chkTxt(x)}</div></div>
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
 <div class="detail"><b>${sel.n}</b> ${sureChip(sel)}<div class="note"><span class="code">${sel.f}</span> ${lnTxt(sel.l)} · ${sel.why}${sel.note?`<br><span style="color:var(--warn)">要留意：${sel.note}</span>`:''}${chkTxt(sel)}</div>
  <div class="pills" style="margin-top:6px">${dropped.has(sel.p)?`<span class="note">已删除</span><button class="pill" data-undel="${sel.p}">恢复</button>`:`<button class="pill" data-editp="${sel.p}">修改位置</button><button class="pill" data-del="${sel.p}">删除</button>`}</div></div></details>`}
function stepsC(){const segRow=(sg,c)=>{const off=dropped.has(sg.a)||dropped.has(sg.b);return `<div class="cseg ${off?'off':''}"><span class="cbar" style="background:${sg.out?'var(--est)':c}"></span><div><b>${sg.t}</b> <span class="note">${sg.who}${sg.out?' · 设备外':''}</span><div class="note">从"${S_N[sg.a].n}"到"${S_N[sg.b].n}"</div></div></div>`};
 return `<section class="panel"><h2>按这些步骤，用时会切成这几段</h2><p class="note" style="margin-top:0">报告里就按这几段算用时、判快慢。段切得不对，说明步骤找得不对。灰色是设备以外的段，码表只能量、改不了。</p>
  <h3 class="h3"><i class="dot" style="background:var(--speak)"></i>${FF[scene].mainTitle}</h3>${SEGS.map(x=>segRow(x,'var(--speak)')).join('')}
  ${useFace()?`<h3 class="h3"><i class="dot" style="background:var(--face)"></i>${FF[scene].sideTitle}</h3>${FSEGS.map(x=>segRow(x,'var(--face)')).join('')}`:''}
  <details style="margin-top:10px"><summary class="note" >代码位置（${live().length} 个）</summary>${STEPS.filter(x=>useFace()||x.line!=='face').map(x=>`<div class="srow"><b>${x.n}</b><span class="note">${x.f} ${lnTxt(x.l)} ${x.sure!=='高'?'· <span style="color:var(--warn)">需核对</span>':''}</span></div>`).join('')}</details></section>`}
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
 const hold=autoHold&&stepsRead===2?`<div class="why" style="border-left-color:var(--warn)"><b>自动执行在这里停下：</b>${htmlEsc(autoHold)}</div>`:'';
 if(stepsRead===2)prepKick();
 if(stepsRead!==2)return `<div class="stack"><section class="panel"><h1 style="font-size:22px">${stepsRead===3?'没找完':'正在读代码找关键步骤'}</h1><p class="note" style="margin-top:0">测的是：${hSay?hSay.replace(/^从/,'').replace('到',' → '):FF[scene].measure}　固件：${hSrc||FF[scene].pick.dir}</p><div id="readbox">${readBox()}</div></section></div>`;
 const warn=(REAL&&IDENT&&IDENT.page&&IDENT.page.warn?`<div class="why" style="border-left-color:var(--bad)">${htmlEsc(IDENT.page.warn)}</div>`:'')+((REAL&&IDENT?IDENT.provider:setLLM)==='deepseek'?`<div class="why" style="border-left-color:var(--warn)">这次用的是 DeepSeek，验证期它 9 个只找对 4 个，请每条都仔细核对。</div>`:'');
 const body=stepsB()+stepsA();
 const n=live().length,ch=dropped.size;
 return `<div class="stack">${hold}${warn}${chkBox()}<div class="status"><span><i class="dot" style="background:var(--ok)"></i>${REAL&&IDENT?(IDENT.reused?reuseTxt():`读完了，用了 ${mmss(IDENT.seconds)}，调用 1 次 ${usedLLM()}`):`读完了，用了 1 分 10 秒，调用 1 次 ${usedLLM()}`}</span><span><i class="dot" style="background:var(--ok)"></i>读代码认出来：${REAL?`${htmlEsc(REAL.name)}，${CHIP()}`:FF[scene].kind}</span><button class="pill" data-reread="1">重看读取过程</button></div>
 ${body}
 ${flow==='first'&&flowMax>=2?`<section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0"><span class="note">步骤没改过，上次插好的计时点还在。改了步骤才需要重新插。</span><button class="btn pri" data-go="flash" data-keepflow="1">回到插计时点和烧录</button></div></section></div>`:`<section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0"><span class="note">确认后，码表马上把 ${n} 个计时点插进它复制的那份代码${ch||Object.keys(edits).length||added.length?'；你的删除、修改、添加都会记下来':''}。${prepNote()}</span><button class="btn pri" data-goins="1" >确认，插入计时点</button></div></section></div>`}`}

// ===== 插计时点和烧录 =====（数字来自验证 02：4 个文件 +154 −0；编译 119 秒/增量 80 秒；程序多 784 字节）
const FST=()=>{const F=FF[scene];return [
 ['复制一份源码',`开始找关键步骤时已经复制好${bgDone()?'、原始代码也编好了':''}；在这份上改，你原来的文件夹一个字都不动`,`已复制 ${F.files} 个文件到 ~/firmware-stopwatch-data/${F.slug}/work`],
 ['让 AI 插计时点',FSWJ&&FSWJ.reused?'计时点那几处和上次一样，用上次的插入方案，没调用大模型（设置页可以关掉）；码表自己检查"只加不删"':FSWJ&&FSWJ.prepared?'用你核对步骤时 AI 在后台提前准备好的插法（调用 1 次大模型），不用再等；码表自己检查"只加不删"':FSWJ&&FSWJ.redo?`${htmlEsc(FSWJ.redo)}（这一步一共调用 ${FSWJ.calls} 次大模型）；码表自己检查"只加不删"`:`按你确认的步骤插入计时语句，再调用一次大模型；码表自己检查"只加不删"`,FSWJ&&FSWJ.view?`改了 ${FSWJ.view.files} 个文件：新增 ${FSWJ.view.added} 行，删除 ${FSWJ.view.deleted} 行 ✓`:F.ins],
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
// 试一次要人做什么：用 AI 读代码写的操作说明；没有就按平常用法用一次（不拿计时点名字当操作说明）
// 以前找关键步骤时 AI 会把设备的回应写进操作说明（比如"听到'叮'后"），猜错了人会一直等：换成不说设备怎么回应
const actClean=t=>t.replace(/(等到?|听到|听见|看到|看见)[^，,；;。]*?(之?后|以后)[，,]?/g,'').replace(/[，,]\s*([；;。]|$)/,'$1');  // 操作说明只留人做的动作：设备怎么回应（响一声、亮灯）在不同状态下不一样，不写
const trySay=()=>{if(!realFlash())return FF[scene].big;const act=actClean((IDENT.page.act||'').trim());if(act)return htmlEsc(act);const w=IDENT.page.mapWhat||hSay||'';return /说|话|聊/.test(w)?'先喊唤醒词，再说一句':'按平常的用法完整使用一次设备'};
// 计时点编号和后台一样：P3 → P3；你自己加的 U1、U2 → P21、P22
const fswId=p=>{if(/^P\d+$/.test(p))return p;const u=live().filter(x=>!/^P\d+$/.test(x.p)).map(x=>x.p);return 'P'+(20+u.indexOf(p)+1)};
const fswSteps=()=>live().map(x=>({p:x.p,n:x.n,path:x.path||x.f,l:x.l,line:x.line,why:x.why||'',where:x.where||'',once:x.once||'',...(x.opt?{opt:true}:{}),...(x.follow?{follow:true}:{})}));  // follow：跟着起点记（识别时码表标的）
function fswApply(j){FSWJ=j;const PH={insert:1,build:2,flash:3,try:4};
 fl.step=PH[j.phase]||1;fl.pct=j.pct||0;fl.log=j.log||'';fl.wait=false;fl.to=4;
 if(j.status==='running')fl.st=1;else if(j.status==='waiting_device'){fl.st=4;fl.log='没看到设备'}
 else if(j.status==='inserted'){fl.st=3;fl.step=2}
 else if(j.status==='ready_try'){fl.st=6;fl.step=4}
 else if(j.status==='failed'){fl.st=5;fl.err=j.err.kind}
 else if(j.status==='stopped'&&page==='flash'&&fl.st!==0){  // 后台被别处停下（不是点这页的「停止测量」）：页面跟着停，回首页说明
  clearInterval(fswT);fswT=null;fl.st=0;AUTO=false;clearInterval(runT);runT=null;page='home';hStage=1;
  toast('测量已在后台停止（不是在这个页面点的停止），已回到首页；要接着测，重新点「开始测量」');render();window.scrollTo(0,0);return}
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
 if(E.wrong_chip)return box('板子选错了',htmlEsc(E.message),E.error,`<button class="btn pri" data-go="home">回首页换板子</button>`);  // 芯片对不上：重烧没用，要换板子
 if(E.cause)return box(TOOLT(E.cause,E.missing),htmlEsc(toolSay(E.message)),E.error,`<button class="btn pri" data-flx="${E.kind==='orig'?'origretry':E.kind==='burn'?'reburn':'rebuild'}">重试</button>${E.cause==='tool'?'<button class="btn" data-go="settings">到设置页</button>':''}`);
 if(E.kind==='insert')return box('计时点没插上',E.message,E.error||'',`<button class="btn pri" data-fl="redo">重新插入</button>`);
 if(E.kind==='orig')return box('原版编译没通过，不是码表改的',`你选的源码本身在这台电脑上编不过（编译工具、依赖或板子配置的问题），和插的计时点无关，烧录前要先解决。码表不替你改代码。`,E.error,`<button class="btn" data-bgpop="1">看详情</button>`);
 if(E.kind==='build')return E.step?box('编译没通过：是码表插的计时点引起的',`原版在找关键步骤时已经编译通过，所以问题出在插进去的计时点：「${htmlEsc(E.step_name||E.step)}」那一处（${htmlEsc(E.where)}）。<br>码表自己的问题，不用你改代码。`,E.error,`<button class="btn pri" data-flx="drop">撤掉这处计时点，重新编译</button><button class="btn" data-flx="move">回去换个位置</button>`)
  :box('编译没通过',`${htmlEsc(E.message)}${E.error?'。报错里没对上是哪一处计时点，看下面的报错，或者重新插入。':''}`,E.error,`<button class="btn pri" data-flx="rebuild">重新编译</button><button class="btn" data-fl="redo">重新插入</button>`);
 if(E.kind==='burn'){  // 按失败阶段说：没连上 / 口被占着 = 什么都没写；只有真的开始写了才说"可能写了一半"（标题、说明由后台按阶段给）
  const rec=(E.summary||E.log)?`<p class="note" style="margin:4px 0 0">烧录记录：${htmlEsc(E.summary||'')}${E.log?`<br>完整输出存在 <code>${htmlEsc(E.log)}</code>`:''}</p>`:'';
  const t=E.title||(E.stage==='write'?'烧录中断：写到一半断了':'烧录没做成');
  const d=E.message&&E.message!=='烧录中断'?E.message:'烧录工具报错了，看下面的报错。点「重新烧录」再试一次。';
  return box(htmlEsc(t),htmlEsc(d)+rec,E.error,`<button class="btn pri" data-flx="reburn">重新烧录</button>`)}
 return box('试一次没收到计时点',`${htmlEsc(E.message)}${E.why?`<details class="lvwhy" style="margin-top:6px"><summary>可能的原因</summary><p class="note" style="margin:6px 0 0">${htmlEsc(E.why)}</p></details>`:''}${E.offer_restore?'<br>试了几次都不行：可以先还原成原版，设备回到原来的样子，不会坏。':''}`,E.error,`<button class="btn pri" data-flx="retry">再试一次</button>${E.offer_restore?'<button class="btn" data-hrestore="'+htmlEsc(REAL.project_dir)+'">还原成原版</button>':''}`)}
function flErr(){if(realFlash()&&FSWJ&&FSWJ.err)return flErrReal();const F=FF[scene],C=CC(),L=live(),p=L[Math.min(2,L.length-1)];
 const E={orig:['原版编译没通过，不是码表改的',`你选的源码本身在这台电脑上编不过，和插的计时点无关。${htmlEsc(BG&&BG.message)}`,htmlEsc(BG&&BG.error),`<button class="btn" data-bgpop="1">看详情</button>`],build:['编译没通过：是码表插的计时点引起的',`原版在找关键步骤时已经编译通过，所以问题出在插进去的计时点。<br>AI 看了报错：「${p.n}」那个计时点（${p.f} ${lnTxt(p.l)}）插进了一个宏定义的中间，编译器不认。<br>码表自己的问题，不用你改代码。`,`${p.f}:${p.l}: error: expected ';' before 'FSW_MARK'`,`<button class="btn pri" data-flx="drop">撤掉这个计时点，重新编译</button><button class="btn" data-flx="move">回去换个位置</button>`],
  burn:['烧录中断：写到一半断了','已经开始往设备里写了，中途连接断了，多半是 USB 线松了或换了接口。<br>设备上现在可能是写了一半的固件，开不了机是正常的，<b>不会坏</b>：插好线再烧一次就行。','A fatal error occurred: The chip stopped responding.',`<button class="btn pri" data-flx="reburn">重新烧录</button>`],
  try:['试一次没收到计时点',`烧录成功，设备也重启了，但${F.trig}后 15 秒内没收到计时点。常见原因：<br>1. 没触发到：再${C.act}一次试试。<br>2. 串口被别的软件占着（比如别的串口监视器）：关掉它。<br>3. 设备在反复重启：点「完整日志」看。`,'（15 秒内没有新输出）',`<button class="btn pri" data-flx="retry">再试一次</button>`]}[fl.err];
 return `<div class="errbox"><b>${E[0]}</b><p style="margin:6px 0">${E[1]}</p><div class="ilog"><span class="note">报错</span><code>${E[2]}</code>${fullLogBtn()}</div>${fullLog()}<div class="acts" style="margin:8px 0 0">${E[3]}</div></div>`}
function flList(from=0,to=4){const n=to-from+1;
 const li=FST().slice(from,to+1).map(([t,d],j)=>{const i=from+j,done=fl.st===2||(fl.st>=1&&i<fl.step)||(fl.st===3&&i<=fl.step-1),cur=(fl.st===1||fl.st===4||fl.st===5||fl.st===6)&&i===fl.step,bad=(fl.st===4||fl.st===5)&&cur;
  return `<li class="istep ${done?'ok':cur?(bad?'bad':'cur'):''}"><b>${done?'✓':bad?'!':j+1}</b><span>${t}<small>${d}</small>${cur&&fl.st===1&&i===2&&fl.wait?`<small style="color:var(--warn)">先等后台把原始代码编完：已编 ${bgPct()}%。编完自动接着编，不用点。</small><span class="pbar"><i style="width:${bgPct()}%;background:var(--warn)"></i></span>`:cur&&fl.st===1&&(i===2||i===3)?`<span class="pbar"><i style="width:${fl.pct}%"></i></span>`:''}${cur&&fl.st===1&&i!==4?stepLog():''}${bad&&fl.st===5?flErr():''}${bad&&fl.st===4?`<small style="color:var(--warn)">没看到设备。用 USB 线插上设备，插上会自动继续，不用点。</small>${REAL?'':'<button class="pill" data-fl="plug">演示：设备已插上</button>'}`:''}</span></li>`}).join('');
 const say=FF[scene].big;
 const N=realFlash()&&FSWJ?FSWJ.need||FSWJ.total||live().length:live().length,got=realFlash()&&FSWJ?(FSWJ.got||[]).length:Math.min(N,Math.round(fl.pct/100*N));
 const big=fl.st===6&&to>=4?`<div class="bigask"><b>试一次：${trySay()}</b><span class="note">设备烧完会重启。等它准备好（有屏幕的，等回到待命画面），再点「准备好了」；点了以后最多等 ${FSWJ&&FSWJ.try_secs||45} 秒。只是确认计时点能收到，不算进数据。</span><button class="btn pri" data-flx="trygo">准备好了</button></div>`
  :fl.st===1&&fl.step===4&&to>=4?(realFlash()?tryBox(got,N):`<div class="bigask"><b>试一次：${say}</b><span class="note">${FF[scene].bigNote}只是确认计时点能收到，不算进数据。</span><span class="note">已收到 <b class="num">${got}</b> / ${N} 个计时点</span></div>`):fl.st===2&&to>=4?`<div class="bigask ok"><b>✓ 试通了</b><span class="note">${got<N&&realFlash()?`收到 ${got} / ${N} 个计时点。没收到：${tryMissing().join("、")||"—"}。这一次没走到这一步（有的计时点只在某些情况下才走到，比如喊醒那一下设备不会判断你说完了），不影响多轮实测`:`${N} 个计时点都收到了`}，设备准备好了。</span></div>`:'';
 return `<ol class="isteps">${li}</ol>${big}`}
// 编译时码表自己改正了计时点头文件的引用路径（找不到头文件）：如实写一句，不让用户去点"重新插入"
const incFixed=()=>realFlash()&&FSWJ&&FSWJ.inc_fixed?`<small style="color:var(--ok)">✓ ${htmlEsc(FSWJ.inc_fixed.message)}</small>`:'';
const stepLog=()=>`${incFixed()}<div class="ilog" style="margin:6px 0 0"><code>${htmlEsc(fl.log)||'…'}</code>${fullLogBtn()}</div>${fullLog()}`;
// 试一次（决策 107、108）：烧完直接开始听，码表自己看设备开机没有，不用人判断"准备好了"；告诉人现在做什么，一句话
const tryAct=()=>{if(realFlash()&&IDENT&&IDENT.page&&(IDENT.page.act||'').trim())return trySay();const w=(IDENT&&IDENT.page&&IDENT.page.mapWhat)||hSay||'';return /说|话|聊|问|答/.test(w)?'喊唤醒词，再说一句话（比如"一加一等于几"）':trySay()};
const USB_QUIET=40;  // 试一次这么多秒调试口一个字都没有：提示拔插 USB（口还在但卡住了，拔插就好，10-21 真测）
function tryBox(got,N){const J=FSWJ||{},now=Date.now()/1000,up=J.dev_seen,since=up?Math.round(now-up):0,idle=up&&!got&&since>30;
 if(!up&&J.boot_quiet)return `<div class="bigask"><b>设备可能已经开机了：${/^现在/.test(tryAct())?tryAct():'现在对设备'+tryAct()}</b><span class="note">没看到它的开机输出（有的设备开机后不出声，或者开机那几行在码表连上之前就打完了）。看设备已经回到待命画面，就直接做一次；码表收到计时点就算通。</span>${!J.heard&&J.try_started&&now-J.try_started>USB_QUIET?`<span class="note" style="color:var(--warn)">已经 ${Math.round(now-J.try_started)} 秒，调试口一个字都没收到。你已经做过一次、设备也有反应的话，多半是电脑上的 USB 口卡住了：拔下 USB 线，等 2 秒再插回去，码表会自己接上，不用点什么。</span>`:''}${fullLogBtn()}${fullLog()}</div>`;  // 不一直转圈等"开机"；口卡住（在但读不到东西，10-21 真测）提示拔插
 if(!up)return `<div class="bigask"><b><span class="spin"></span> 设备刚烧完，正在重启</b><span class="note">等它开机（有屏幕的会回到待命画面），码表会自己看到，不用点什么。开机后这里会告诉你做什么。</span></div>`;
 return `<div class="bigask"><b>${/^现在/.test(tryAct())?tryAct():'现在对设备：'+tryAct()}</b>
  <span class="note">设备已经开机了。只是确认计时点能收到，不算进数据。</span>
  ${idle?`<span class="note" style="color:var(--warn)">已经 ${since} 秒没收到计时点：设备可能还在联网，或者还没准备好。请现在再做一次。</span>`:''}
  <span class="note">已收到 <b class="num">${got}</b> / ${N} 个计时点${J.try_started?`　剩余 <b class="num">${mmss(Math.max(0,TRY_SECS-(now-J.try_started)))}</b>，超时判未通过`:''}</span>${got>=Math.max(2,N/2)&&got<N&&(J.missing||[]).length?`<span class="note">还差「${J.missing.map(htmlEsc).join('」「')}」：设备还在做的话先等它做完，这几步可能就到了；它已经做完还差，就再完整做一次（${tryAct()}），码表会继续收。</span>`:''}${J.skip_note?`<span class="note" style="color:var(--warn)">${htmlEsc(J.skip_note)}。码表还在听：再完整做一次还是这样，就不用等满时间，直接停下告诉你。</span>`:''}${fullLogBtn()}${fullLog()}</div>`}  // 设备原始输出不摆在明面上，想看点"完整日志"（决策 139）
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
 ${(FSWJ.checks||[]).map(c=>`<p class="note" style="margin:6px 0 0;color:var(--warn)">⚠ ${htmlEsc(c)}</p>`).join('')}
 ${FSWJ.notes&&FSWJ.notes.length?`<details style="margin-top:8px"><summary class="note">AI 插的时候提醒的 ${FSWJ.notes.length} 件事</summary><ol class="notes" style="font-size:14px">${FSWJ.notes.map(n=>`<li>${htmlEsc(n)}</li>`).join('')}</ol></details>`:''}
 <p class="note">总开关在 <span class="code">${V.header.split('/').pop()}</span>：关掉后固件和原版大小相同、逻辑相同。要完全回到原版，用"还原"，会逐字节比对。</p>`}
function diffView(){if(realFlash()&&FSWJ&&FSWJ.view)return diffViewReal();const rows=live(),F=FF[scene];const num=p=>p.replace(/\D/g,'')||'0';
 const one=(k,i,t,sub,lines)=>`<div class="drow"><span class="num">${i}</span><div><b>${t}</b> <span class="note">${sub}</span></div><button class="pill" data-dopen="${k}">${diffOpen.has(k)?'收起代码':'看代码'}</button>${diffOpen.has(k)?`<pre class="diff">${lines.map(([k2,t2])=>`<span class="${k2==='+'?'add':k2==='…'?'more':''}">${k2==='…'?'':k2} ${t2.replace(/</g,'&lt;')}</span>`).join('\n')}</pre>`:''}</div>`;
 return `<div class="status" style="margin-bottom:8px"><span><i class="dot" style="background:var(--ok)"></i>只加不删：${F.diffSum}，删除 0 行（码表逐行核对）</span><span><i class="dot" style="background:var(--ok)"></i>${rows.length} 个计时点都插上了，和你确认的一一对应</span></div>
 <p class="note" style="margin:0 0 4px">每处只加两行：一行说明、一行记时间。"+"开头的是新加的。</p>
 <div class="dlist">${one('hdr','＋','新文件 fw_stopwatch.h','计时点工具，+120 行；总开关也在这里',DIFF[0].lines)}
 ${rows.map((x,i)=>one(x.p,i+1,x.n,`${x.f} ${lnTxt(x.l)} · +2 行`,[[' ',`    …（${lnTxt(x.l)}原有代码，不动）`],['+',`    // 计时点 ${x.p}：${x.n}`],['+',`    FSW_MARK(${num(x.p)}, 0);`]])).join('')}</div>
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
function sceneSwitch(k){if(k===scene)return;colStop();col={st:0,att:0,got:[],phase:0};scene=k;fixPick=null;segSel=null;fixTodo=false;
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
// 试听时把声音通道关掉重开：Safari 偶尔会卡在"开着但不出声"（状态是 running，喇叭没声音，2026-10-16 实测），重开能救回来（决策 136）
function resetAudio(){try{if(actx)actx.close()}catch(e){}actx=null}
let dings=0,beepMsg='',beepBlocked=false;  // 决策 132：该响的时候浏览器的声音没开：页面上显示一个按钮，点一下就开
function ding(bad){if(!beep)return;try{actx=actx||new (window.AudioContext||window.webkitAudioContext)();if(actx.state==='running')beepBlocked=false;else{actx.resume();setTimeout(()=>{if(actx.state!=='running'&&!beepBlocked){beepBlocked=true;beepLog('声音被浏览器关着');if(page==='collect')render()}},400)}dings++;if(dings<=3||dings%10===0)beepLog('响');const o=actx.createOscillator(),g=actx.createGain();o.frequency.value=bad?330:880;g.gain.value=.25;o.connect(g);g.connect(actx.destination);o.start();o.stop(actx.currentTime+(bad?.35:.12))}catch(e){}}
function beepBtn(){return `${beepBlocked&&beep&&!(actx&&actx.state==='running')?'<button class="btn" data-beeptest="1" style="border-color:var(--warn);color:var(--warn)">🔇 提示音被浏览器关着，点这里打开</button>':''}<span style="display:inline-flex;gap:10px;align-items:center"><button class="tgl" role="switch" aria-checked="${beep}" data-beep="1"><span class="trk"></span>提示音${beep?'开':'关'}</button>${beep?'<button class="pill" data-beeptest="1">试听</button>':''}</span>`}
function beepLog(what){try{fetch('/api/client-log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({what,state:actx?actx.state:'none',rate:actx?actx.sampleRate:0,dings,ua:navigator.userAgent,hidden:document.hidden})})}catch(e){}}
function beepNote(){return CX().beepNote}
const MIN_ROUNDS=3;  // 至少几轮能出报告（3～9 轮只适合跑流程，报告注明）
const colN=AUTO_ROUNDS;
let colVar='b',col={st:0,att:0,got:[],phase:0},colT=null;
const CC=()=>COLLECT[scene];
// 真流程：每轮做什么（/api/rounds 按你写的"测什么"挑模板）、线和问题（后台多轮实测状态里这次真的场景），不借演示数据
let RCT=null,COLJ=null;
const realCol=()=>realFlash()&&FSWJ&&FSWJ.status==='done';
const CX=()=>realCol()&&RCT?RCT:COLLECT[scene];
async function loadRCT(){const P=IDENT.page,st=(P.measure||'').split('→')[0].trim(),names=Object.fromEntries((P.steps||[]).map(x=>[x.p,x.n]));
 try{const t=await (await fetch('/api/rounds?'+new URLSearchParams({what:hSay===WHOLE?`${P.measure||''} ${P.mainTitle||''}`:hSay||P.mapWhat||'',start:st}))).json();
  const opt=new Set((P.steps||[]).filter(x=>x.opt).map(x=>x.p));const pk=(P.main||[]).filter(k=>!opt.has(k)&&live().some(x=>x.p===k)&&names[k]);RCT={...t,pk,ph:[...pk.map(k=>names[k]),'这一轮记好了'],probs:(COLJ&&COLJ.problems||[]).map(x=>({k:x.key,t:x.name,c:x.level==='bad'?'bad':'warn'}))};if(page==='collect')render()}catch(e){}}
// 体验标准出处末尾的原文网址很长、中间没有断点，会把卡片撑出页面（真测第 29 次）：换成一个短的「原文链接」
const srcHtml=t=>htmlEsc(t).replace(/https?:\/\/[^\s，。；）]+/g,u=>`<a href="${u}" target="_blank" rel="noopener">原文链接</a>`);
const expTarget=D=>{const ls=(D&&D.J&&D.J.scenario&&D.J.scenario.lines)||[];return ((ls.find(x=>x.first)||ls[ls.length-1])||{}).name};  // 体验标准挂在哪条线（和后台 with_default_experience 一样）
const noExp=D=>(D&&D.J&&D.J.scenario&&D.J.scenario.exp_skip)||'这个场景没有体验标准';  // 起点比人做完晚一截时说清为什么不比
function realD(){const P=IDENT.page,[,mEnd]=(P.measure||'起点 → 终点').split('→').map(x=>x.trim()),sShort=startLabel(P,COLJ&&COLJ.scenario||{start:{point:(P.main||[]).find(k=>!(P.steps||[]).some(x=>x.p===k&&x.opt))}}),sEnd=P.sideAsk?P.sideAsk.q:'';
 const L=COLJ&&COLJ.lines&&COLJ.lines.length?COLJ.lines:(sEnd?[{name:sEnd},{name:mEnd}]:[{name:mEnd}]);
 const lines=L.map((l,i)=>({name:l.name,end:i<L.length-1?(sEnd||l.name):mEnd,ok:null,slow:Infinity,col:LCOL[i]}));  // 合理范围清单去掉了（10-19）：测的时候不画范围带
 return {lines,S:{startShort:sShort,exp:COLJ&&COLJ.experience?{fair:COLJ.experience.fair}:null},rounds:okRounds().map(r=>({ends:r.e}))}}
const DX=()=>realCol()?realD():build(SCENES[scene]);
const PH=()=>CX().ph;
// 步骤条：真测时按这一轮真正到了哪几个点涂绿（有的点设备要到一轮结束才打出来，按个数从左往右涂会停在中间、还把没到的涂绿）；加粗第一个还没到的
function phCls(){const ph=PH(),pk=CX().pk,h=col.have;if(!pk||!h)return ph.map((p,i)=>[p,i<col.phase?'ok':i===col.phase?'cur':'']);
 // 从左往右涂到已经到了的最靠后那一步（10-19 用户："上面的字变绿会乱变"）：设备要到一轮结束才打出头两步，打断后那一轮"收到开始说话"空着，
 // 按"到了哪几个"涂会中间先绿、头上后绿、中间缺一块；后面的步到了，前面的一定已经过去了。这一轮还没收尾时，最后一步只加粗不涂绿
 const s=new Set(h),far=Math.max(-1,...pk.map((k,i)=>s.has(k)?i:-1)),cur=Math.min(far+1,pk.length-1);
 return ph.map((p,i)=>[p,i<pk.length?(i===cur?'cur':i<=far?'ok':''):''])}
// 多轮实测：后台一行行读设备输出（没真源码时回放演示数据的设备输出），按轮切分、算用时、查问题；这里每 0.3 秒问一次
let colES=null;
function colStop(){if(colT){clearInterval(colT);colT=null}if(colES){colES.close();colES=null}}
// 收进度：先用服务器推送（网页在后台也能马上收到、马上"叮"）；推送断了就退回每 0.3 秒查一次（决策 102）
function colWatch(){colStop();const poll=()=>{if(!colT)colT=setInterval(async()=>{try{colApply(await (await fetch('/api/collect')).json())}catch(e){}},300)};
 if(!window.EventSource){poll();return}
 try{colES=new EventSource('/api/collect/events');colES.onmessage=e=>{try{colApply(JSON.parse(e.data))}catch(x){}};
  colES.onerror=()=>{if(colES){colES.close();colES=null}if(col.st===1)poll()}}catch(e){poll()}}
function colApply(j){COLJ=j;if(realCol()&&RCT)RCT.probs=(j.problems||[]).map(x=>({k:x.key,t:x.name,c:x.level==='bad'?'bad':'warn'}));const C=CX(),before=col.got.length;if(j.status==='done'||j.status==='stopped'||j.status==='suspect'){fetchReport();if(realFlash())histLoad()}
 col.got=(j.rounds||[]).map(r=>r.ok?{n:r.n,e:r.ends,probs:C.probs.filter(p=>(r.probs||[]).includes(p.k)),att:r.att,segs:r.segs}:{bad:true,att:r.att,missing:r.missing||[]});
 col.att=col.got.length?col.got[col.got.length-1].att:0;col.last=j.last||'';col.msg=j.status==='paused'||j.status==='waiting_device'?(j.message||'没看到设备：用 USB 线插上，插上会自动开始'):(j.half_hint||j.hint||'');  // 调试口开着却没有计时点：说是哪种情况，不停下
 const [got,need]=j.progress||[0,1],n=PH().length-1;col.phase=got>=need?n:Math.min(n-1,Math.floor(got*n/need));col.have=got>=need?null:j.have||null;
 // 收好一轮不响，只有没收全才低响一声：测全程时一轮要等设备把回答整段放完才算收好，那时再"叮"一声人只会觉得提示音晚了
 for(const x of col.got.slice(before))if(x.bad)ding(true)
 if(j.status==='done'){colStop();col.st=2;if(realCol())autoRestoreWatch()}else if(j.status==='suspect'){colStop();col.st=4}else if(j.status==='stopped'&&col.st!==3){colStop();col.st=3}
 if(page==='collect')render()}
async function colStart(keep){colStop();if(keep){col.st=1;col.phase=0;col.rd=false}else col={st:1,att:0,got:[],phase:0,sc:scene};ADV=null;clearInterval(advT);advT=null;fixPick=null;
 const real=realFlash()&&FSWJ&&FSWJ.status==='done';
 const body=real?{page:IDENT.page,steps:fswSteps(),source:'device',project_dir:REAL.project_dir,rounds:colN,resume:!!keep,what:hSay||IDENT.page.mapWhat||''}  // 按实际插进去的步骤收（后台再按插入记录核对）
  :{demo:scene,rounds:colN,resume:!!keep,interval:.12,drop_at:keep?0:9};
 try{const r=await fetch('/api/collect/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok){toast('✕ '+(j.detail||'没开始'));col.st=0;render();return}colApply(j)}catch(e){toast('✕ 码表后台没有回应');return}
 if(beep){try{actx=actx||new (window.AudioContext||window.webkitAudioContext)();actx.resume&&actx.resume()}catch(e){}}  // 点开始时就把声音准备好，网页在后台也能响
 colWatch()}
const okRounds=()=>col.got.filter(x=>!x.bad);
const missTxt=x=>x.missing&&x.missing.length?`没收全：缺「${x.missing.join('」「')}」`:CX().miss;
function issues(x){if(x.bad)return `<span class="chip c-gray">${missTxt(x)}，不计入，自动补收一轮</span>`;
 return x.probs.map(p=>`<span class="chip c-${p.c}">${p.t}</span>`).join(' ')||'<span class="note">正常</span>'}
function colSetup(){const C=CX(),D=DX();return `${justFlashed?`<div class="okbar">✓ 计时版已烧进设备，试的那一次 ${realFlash()&&FSWJ?FSWJ.total:live().length} 个计时点都收到了。下面开始多轮实测。</div>`:''}<section class="panel"><h1 style="font-size:22px">多轮实测：${C.cnt} ${colN} ${C.unit}</h1>
 <p style="margin:4px 0 0">同样的事做很多轮，码表从设备输出里记下每一轮各段用了多久。刚才试的那一次不算。</p>
 <div class="status" style="margin:6px 0 4px">${REAL&&!devNow().on?'<span><i class="dot" style="background:var(--warn)"></i>没看到设备：用 USB 线插上，或按电源键开机（闲置会自动关机），检测到会自动继续</span>':`<span><i class="dot" style="background:var(--ok)"></i>${C.dev}已连上${REAL?' '+htmlEsc(devNow().port):''}，计时点输出正常</span>`}<span><i class="dot" style="background:var(--ok)"></i>${C.mode}</span><span><i class="dot" style="background:var(--ok)"></i>量 ${D.lines.length} 条线：${D.lines.map(l=>D.S.startShort+' → '+l.end).join('；')}</span></div>
 <dl class="cform">
  <dt>谁来${C.act}</dt><dd><p style="margin:0">${C.who}</p><p class="note" style="margin:4px 0 0">${C.why}</p></dd>
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
 const sq=x=>{const p=x.probs&&x.probs[0];return x.bad?`<i class="sq bad" tabindex="0" data-tip="${tipOf(x)}"></i>`:`<i class="sq" tabindex="0" data-tip="${tipOf(x)}" style="background:${p?`var(--${p.c})`:'var(--ok)'}"></i>`};  // 没收全的格子画斜线，和还没测的空格分得开（真测第 29 次：以为还差一格没测）
 return `<section class="panel" style="text-align:center">
 ${col.st===1?`<p class="note" style="margin:0">第 ${cur+1} 轮 / 共 ${colN} 轮</p><p style="font-size:15px;margin:10px 0 4px">${col.phase===0?C.say:C.said}</p><p style="font-size:26px;font-weight:600;margin:0 0 16px">"${C.items[cur%C.items.length]}"</p>
 <div class="phs">${phCls().map(([p,c])=>`<span class="${c}">${p}</span>`).join('<b>→</b>')}</div>`:col.st===4?`<p style="font-size:20px;font-weight:600">已暂停：第 1 轮自动校验没通过</p>`:col.st===3?`<p style="font-size:20px;font-weight:600">已停止：测了 ${k} / ${colN} 轮</p>`:`<p style="font-size:20px;font-weight:600">测完了</p>`}
 <div class="sqs">${col.got.map(sq).join('')}${[...Array(Math.max(0,colN-k))].map(()=>'<i class="sq"></i>').join('')}</div>
 <p class="note" style="margin-bottom:2px">一格是一轮，颜色表示这轮有没有出问题（不是快慢）。鼠标移到格子上看这轮各段用时。</p><p class="note"><i class="dot" style="background:var(--ok)"></i>正常　${C.probs.map(p=>`<i class="dot" style="background:var(--${p.c})"></i>${p.t}　`).join('')}<i class="sq bad" style="width:10px;height:10px;vertical-align:-1px"></i> 没收全，不计入，自动补一轮　<i class="sq" style="width:10px;height:10px;vertical-align:-1px"></i> 还没测</p>
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
const COLVAR={a:{label:'A 逐轮表',idea:'A 逐轮表：每收一轮加一行（切换表情、开始播放、有没有问题），最新的在上面。信息最全，适合研发盯着看。'},
 b:{label:'B 跟着做',idea:'B 跟着做（推荐）：大字提示下一步做什么、现在到哪一步，一轮一格小方块、出问题变色；下面能展开 A 的逐轮明细。人眼要看着设备，页面只需扫一眼。'},
 c:{label:'C 边测边画',idea:'C 边测边画：每收一轮在图上多两个点，"一般用时"线跟着变，收到一半就能看出稳不稳。'}};
function collectPage(){
 if(col.st===0)return `<div class="stack">${colSetup()}</div>`;
 if(col.st===4)return `<div class="stack">${suspectBox()}${colB()}</div>`;
 const body={a:colA,b:colB,c:colC}[colVar]();
 const g=okRounds(),bad=col.got.length-g.length;
 return `<div class="stack">${col.st===1&&col.msg?`<div class="why" style="border-left-color:var(--warn)">${htmlEsc(col.msg)}</div>`:''}${col.st===1?`<div class="status" style="justify-content:flex-end">${beepBtn()}</div>`:''}${body}
 <section class="panel"><div class="acts" style="justify-content:space-between;align-items:center;margin:0">${col.st===3?(()=>{const k=g.length,can=k>=MIN_ROUNDS,to=flow==='retest'&&rtC().steps[3][1]?['compare','查看前后对比']:['report','查看报告'];return `<span class="note">已停止。测了的 ${k} 轮都留着，可以接着测。${can?'也可以先看结果，会注明只测了 '+k+' 轮。':''}</span><span style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end"><button class="btn" data-go="${to[0]}" ${can?'':blk('[data-col="resume"]',`至少测 ${MIN_ROUNDS} 轮才能看结果`)}>${to[1]}</button><button class="btn" data-col="restart">从头重测</button><button class="btn pri" data-col="resume">接着测（从第 ${k+1} 轮）</button></span>`})():`${col.st===1?'<span style="display:flex;gap:12px;align-items:center;flex-wrap:wrap"><button class="btn" data-col="stop">停止实测</button><span class="note">可以随时停，停了能接着测，已测的都留着。</span></span>':`<span class="note">${col.st===2?`测完 ${g.length} 轮${bad?`（另有 ${bad} 轮没收全，没算）`:''}；原始日志已存。`:'可以随时停，停了能接着测，已测的都留着。'}</span>`}<span style="display:flex;gap:8px;align-items:center">${flow==='retest'&&rtC().steps[3][1]?`<button class="btn pri" data-go="compare" ${col.st===2?'':blk('[data-col="stop"]','测完才能看')}>查看前后对比</button>`:`<button class="btn pri" data-go="report" ${col.st===2?'':blk('[data-col="stop"]','测完才能看')}>查看报告</button>`}</span>`}</div></section></div>`}

// ===== 前后对比 =====（通用：两次结果都用报告页同一套计算；改后数据是模拟的）

function afterScene(k){const S=JSON.parse(JSON.stringify(SCENES[k]));for(const s in RUNS[k].afterSegs)S.segs[s].v=RUNS[k].afterSegs[s];return S}
let cmpVar='c';
const flat=(a,b)=>Math.abs(b-a)<Math.max(.03,a*.05);
function pchip(a,b){if(a.pass_rate==null||b.pass_rate==null)return '';const d=Math.round((b.pass_rate-a.pass_rate)*100);return !d?'<span class="note">→ 持平</span>':d>0?`<span class="down">↑ 多了 ${d} 个百分点</span>`:`<span class="up">↓ 少了 ${-d} 个百分点</span>`}  // 达标率：高了是好事，颜色和用时反过来
function dchip(a,b){if(flat(a,b))return '<span class="note">→ 持平</span>';const d=b-a;return d<0?`<span class="down">↓ 快了 ${f(-d)} 秒</span>`:`<span class="up">↑ 慢了 ${f(d)} 秒</span>`}
const TIERS=[['一般','med'],['最差一次','max']];
function cmpData(){const B=build(SCENES[scene]),A=build(afterScene(scene));return {B,A,R:RUNS[scene]}}
function cmpHead(){const {B,A}=cmpData();
 return A.lines.map((l,i)=>{const b=B.lines[i],m=flat(b.med,l.med);
  const s1=m?`一般持平（${f(b.med)} → ${f(l.med)} 秒）`:`一般${l.med<b.med?'快':'慢'}了 ${f(Math.abs(l.med-b.med))} 秒（${f(b.med)} → ${f(l.med)}）`;
  const s2=l.pass_rate==null||b.pass_rate==null?'':l.pass_n===b.pass_n&&l.count===b.count?`，达标率持平（${passPct(l)}）`:`，达标率 ${passPct(b)} → ${passPct(l)}`;
  return `<b>${l.end}</b>：${s1}${s2}。`}).join('<br>')}
function fair(){const {R}=cmpData(),C=COLLECT[scene];
 return `<div class="status"><span><i class="dot" style="background:var(--ok)"></i>两次都按同样的 20 ${C.unit}${C.act}</span><span><i class="dot" style="background:var(--ok)"></i>计时点相同</span>${flow==='retest'&&rtCase==='tool'?'<span><i class="dot" style="background:var(--warn)"></i>编译工具不同（v6.1 → v6.2），变化不一定都来自你改的那一处</span>':''}<span><i class="dot" style="background:var(--warn)"></i>网络、服务器负载码表看不到，请自己保证差不多</span></div>`}
function cmpPick(){const R=RUNS[scene];return `<section class="panel"><h1 style="font-size:22px">前后对比</h1>
 <div class="pickrow"><label>改前<select><option>${flow==='retest'?RT[scene].last+'　上一次（自动选的）':R.before}</option></select></label><span class="note">→</span><label>改后<select><option>${flow==='retest'?'今天　这次改完再测':R.after}</option></select></label></div>
 <p class="note" style="margin:6px 0">这次改了什么（${rtWhat?'改完再测时码表先写、你确认过的':'你填'}，报告里会带上）：<b>${rtWhat||R.what}</b></p>${fair()}</section>`}
function cmpA(){const {B,A}=cmpData();
 const tier=`<table class="rtab"><thead><tr><th></th>${A.lines.map(l=>`<th colspan="3">${l.end}</th>`).join('')}</tr><tr><th></th>${A.lines.map(()=>'<th>改前</th><th>改后</th><th>变化</th>').join('')}</tr></thead><tbody>${TIERS.map(([t,k])=>`<tr><td>${t}</td>${A.lines.map((l,i)=>`<td class="num">${f(B.lines[i][k])}</td><td class="num">${f(l[k])}</td><td>${dchip(B.lines[i][k],l[k])}</td>`).join('')}</tr>`).join('')}<tr><td>达标率</td>${A.lines.map((l,i)=>`<td class="num">${passPct(B.lines[i])||'—'}</td><td class="num">${passPct(l)||'—'}</td><td>${pchip(B.lines[i],l)}</td>`).join('')}</tr></tbody></table>`;
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
 <p class="note">浅色是改前，深色是改后；段名旁的 ↓ ↑ 表示这一段快了还是慢了。数字取各轮的中位数。</p></section>`}
function cmpC(){const {B,A}=cmpData();const L=A.lines.length-1,lb=B.lines[L],la=A.lines[L];
 const T=niceT(Math.max(...B.rounds.flatMap(r=>r.ends),...A.rounds.flatMap(r=>r.ends))/1.1),W=900,X0=90,X1=860,sx=t=>X0+(X1-X0)*t/T,st=step(T);
 let g='';for(let t=0;t<=T+1e-9;t+=st)g+=`<line x1="${sx(t)}" x2="${sx(t)}" y1="10" y2="104" stroke="var(--soft)"/><text x="${sx(t)}" y="122" text-anchor="middle" font-size="12" fill="var(--muted)">${+t.toFixed(2)} 秒</text>`;
 [[B,'改前',30,.45],[A,'改后',80,1]].forEach(([D,lab,y,op])=>{g+=`<text x="${X0-12}" y="${y+4}" text-anchor="end" font-size="13" fill="var(--fg)">${lab}</text>`;
  D.rounds.forEach(r=>g+=`<circle cx="${sx(r.ends[L])}" cy="${y}" r="6" fill="${D.lines[L].col}" opacity="${op}"/>`);
  g+=`<line x1="${sx(D.lines[L].med)}" x2="${sx(D.lines[L].med)}" y1="${y-14}" y2="${y+14}" stroke="var(--fg)" stroke-width="2"/>`});
 const card=([t,k])=>`<div class="tcard"><span class="note">${t}</span><b class="num">${f(la[k])} 秒</b><span>${dchip(lb[k],la[k])}</span></div>`;
 return `<section class="panel"><h2>结论</h2><p style="font-size:16px;line-height:1.7;margin:4px 0 12px">${cmpHead()}</p>
 <div class="tcards">${TIERS.map(card).join('')}${la.pass_rate==null?'':`<div class="tcard"><span class="note">达标率</span><b class="num">${passPct(la)}</b><span>${pchip(lb,la)}</span></div>`}</div><p class="note">上面几格是"${la.end}"那条线。</p>
 <h3 class="h3">${A.rounds.length} 轮"${la.end}"的用时，一个点一轮</h3><div class="scroll"><svg viewBox="0 0 ${W} 130" style="min-width:640px;width:100%" role="img" aria-label="改前改后每轮分布">${g}</svg></div>
 <p class="note">竖线是一般用时。点挤得越紧，说明越稳定。</p></section>`}
const CMPVAR={a:{label:'A 对照表',idea:'A 对照表：用时、达标率和每一段，改前、改后、变化三列，箭头标快慢。最全，适合研发查是哪一段变了。'},
 b:{label:'B 叠图',idea:'B 叠图：报告里那张计时图，改前（浅）改后（深）叠在一起，段名旁带 ↓ ↑。一眼看出变化落在哪一段。'},
 c:{label:'C 结论先行',idea:'C 结论先行：先一句话说改完变快没有，再给几个大数字和箭头，最后是每轮分布（点挤得紧说明稳）。负责人只看上面就能拍板。'}};
function comparePage(){return `<div class="stack">${flow==='retest'?`<section class="panel" style="border-color:var(--ok)"><b>改完再测完成。</b><span class="note">　下面是和上一次的对比；这次的完整报告也已生成。</span><div class="acts" style="margin:8px 0 0"><button class="btn" data-go="report">看这次的完整报告</button><button class="btn" data-go="home">回首页</button></div></section>`:''}${cmpPick()}${cmpC()}<details class="panel"><summary style="cursor:pointer"><b>对照表</b>　<span class="note">用时、达标率和每一段的改前、改后、变化</span></summary>${cmpA().replace(/^<section class="panel"><h2>对照表<\/h2>/,'').replace(/<\/section>$/,'')}</details></div>`}



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
 if(page==='flash'){if(fl.st===4)return ['没看到设备：用 USB 线插上设备，或按电源键开机（闲置会自动关机），检测到会自动继续','[data-fl="plug"]'];
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
 const tag=flow==='retest'||remeasure?'改完再测':'第一次测',P=flow==='retest'?pos(L2,page):pos(L1,page);
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
const reuseTxt=()=>IDENT&&IDENT.carried?`代码改过，但计时点那几处和上次一样：直接用上次的关键步骤${IDENT.carried.moved?`（${IDENT.carried.moved} 处行号跟着挪了）`:''}，没调用大模型`:'源码和要测的都没变，直接用了上次的结果，没调用大模型';
const tookTxt=llm=>REAL&&IDENT?(IDENT.reused?reuseTxt():`用时 ${mmss(IDENT.seconds)}，调用 1 次 ${llm}`)+(REAL.aiCalled?`；选源码时扫「AI 推荐」另调用了 1 次`:''):`用时 1 分 10 秒，调用 1 次 ${llm}`;
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
   <ol class="found">${rows.map(x=>`<li>${x.n} <span class="note">${x.f} ${lnTxt(x.l)}${x.line==='face'?' · '+F.ln.face:''}${x.sure!=='高'?' · <span style="color:var(--warn)">需核对</span>':''}</span></li>`).join('')}</ol>
   ${F.sideAsk?`<p class="note">另外发现：${F.sideAsk.found}。标「${F.ln.face}」的 ${STEPS.filter(x=>x.line==='face').length} 个是顺带发现的，默认也测，下一页可以去掉。</p>`:''}
   <div class="acts" style="justify-content:flex-end"><button class="btn pri" data-mnext="1">下一步</button></div>`}
function popHTML(){const F=FF[scene],C=CC();
 const box=(t,body,acts)=>`<h2>${t}</h2>${body}<div class="acts">${acts}</div>`;
 if(pop==='export'){const busy=ADV&&ADV.status==='running'&&repKey().startsWith('real');
  if(EXP.state==='saving')return box('导出报告','<p><span class="spin"></span> 正在存……</p>','');
  if(EXP.state==='ok')return box('导出报告',`<p>✓ 已导出：</p><p class="code" style="overflow-wrap:anywhere">${htmlEsc(EXP.saved)}</p>${EXP.partial?'<p class="note" style="color:var(--warn)">导出时"为什么、怎么改"还没写完，这份里没有建议。写完后再导出一份。</p>':''}`,`<button class="btn pri" data-ropen="${htmlEsc(EXP.saved)}">打开看看</button><button class="btn" data-pop="close">关闭</button>`);
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
 if(pop==='bg'&&bgToolErr())return box(TOOLT(BG.cause,BG.missing),`<p>${htmlEsc(toolSay(BG.message))}</p>${BG.error?`<div class="ilog"><span class="note">报错</span><code>${htmlEsc(BG.error)}</code></div>`:''}<p class="note">找关键步骤照常做；烧录前解决就行。</p>`,`${BG.cause==='tool'?'<button class="btn" data-go="settings">到设置页</button>':''}<button class="btn pri" data-bgretry="1">重试</button>`);
 if(pop==='bg'&&bgFailed())return box('原版编译没通过，不是码表改的',`<p>${htmlEsc(String(BG.message||'').replace(/^原版编译没通过，不是码表改的：/,''))}</p><div class="ilog"><span class="note">报错</span><code>${htmlEsc(BG.error)}</code></div><p class="note">找关键步骤照常做；烧录前要先让原版编得过。</p>`,`<button class="btn pri" data-pop="close">知道了</button>`);
 if(pop==='bg'){const d=bgDone(),no=!envComp()||bgWaitTool();return box(d?'✓ 原始代码已编好':no?'后台编原始代码：等编译工具装好':`后台在编原始代码：${bgPct()}%`,`${d||no?'':`<span class="pbar" style="display:block;margin:0 0 12px"><i style="width:${bgPct()}%"></i></span>`}<ul class="notes" style="font-size:14px;color:var(--fg)">${BGTIP.split('<br>').map(x=>`<li>${x}</li>`).join('')}</ul>${no?'<p class="note">编译工具装好后自动开始，不用点。</p>':''}`,`<button class="btn pri" data-pop="close">知道了</button>`)}
 if(pop==='colre')return box('从头重测？',`<p>会清掉已测的 ${okRounds().length} 轮，从第 1 轮重新开始。</p><p class="note">只是想补几轮，用「接着测」就行。</p>`,`<button class="btn" data-pop="close">取消</button><button class="btn pri" data-col="restart2">清掉，从头测</button>`);
 if(pop==='inst')return box('编译工具装好了',`<p>乐鑫编译工具 v6.1 装好了。</p><p class="note">都准备好后，点设置页最下面的「保存」。</p>`,`<button class="btn pri" data-pop="close">知道了</button>`);
 if(pop==='env'){const m=missing(envBack==='flash'?'comp':null);return box(envBack==='flash'?'编译前还缺编译工具':'还缺这些环境',`<p>${envBack==='flash'?'插好计时点了，编译这个固件要用下面这样，你电脑上还没准备好：':`这个固件要用到下面 ${m.length} 样，你电脑上还没准备好：`}</p><ul class="notes" style="font-size:15px;color:var(--fg)">${m.map(([,t,w])=>`<li><b>${t}</b><br><span class="note">${w}</span></li>`).join('')}</ul>`,`<button class="btn" data-pop="close">关闭</button>${m.some(x=>!(x[0]==='set-idf'&&inst.st===1))?'<button class="btn pri" data-envgo="1">配置环境</button>':''}`)}
 return ''}
let flowMax=0;
function flowReset(){flowMax=0}
// 回看首页时换了固件或板子：真改了东西，这次测量的步骤条作废，后面要重新做（停在回看不作废）
function newRun(){if(flow!=='first'||flowMax<1)return;flow=null;flowReset();toast('换了固件或板子：后面的步骤要重新做')}
function stepsChanged(){if(flow==='first'&&flowMax>1){flowMax=1;flStop();fl={st:0,step:0,pct:0,log:''};diffOk=false;colStop();col={st:0,att:0,got:[],phase:0};toast('步骤改了：插计时点和烧录、多轮实测要重新做')}}
function flowAdvance(){if(flow!=='first')return;const ci=FLOW1.findIndex(x=>x[0]===page);if(ci>flowMax)flowMax=ci}  // 走到哪一步（以前在画老步骤条时顺带记，测量页不画它，刷新后接不上）
function flowBar(){if(flow!=='retest')return '';let st;
 if(flow==='first'||!flow){if(!FLOW1.some(x=>x[0]===page))return '';st=FLOW1.map(([id,n])=>[id,n,0])}
 else{const C0=rtC(),pre=page==='retest'&&rtSrc<2,C={steps:C0.steps.map(x=>pre?[x[0],1]:x)};const ids=['retest','steps','flash','collect','compare'];if(!ids.includes(page))return '';st=[['retest','查改了什么',0],['steps','找关键步骤',!C.steps[0][1]],['flash','插计时点和烧录',!C.steps[1][1]],['collect','多轮实测',0],['compare','前后对比',!C.steps[3][1]]]}
 const ci=st.findIndex(x=>x[0]===page);if(flow&&ci>flowMax)flowMax=ci;const mx=flow?flowMax:ci;
 return `<ol class="flow" aria-label="测量步骤"><span class="note" style="margin-right:4px">${flow==='retest'||remeasure?'改完再测':'第一次测'}：</span>${st.map(([id,n,sk],i)=>`<li>${sk?`<span class="fs skip">${n}（跳过）</span>`:i===ci?`<span class="fs cur ${page==='flash'&&(fl.st===5||fl.st===4)?'bad':''}">${page==='flash'&&(fl.st===5||fl.st===4)?'! ':i<mx?'✓ ':''}${n}</span>`:i<mx?`<button class="fs done" data-go="${id}" data-keepflow="1" title="回去看看，已做的不会清掉">✓ ${n}</button>`:i===mx?`<button class="fs goon" data-go="${id}" data-keepflow="1" title="回到做到一半的这一步">${n}（做到这里）</button>`:`<span class="fs">${n}</span>`}</li>`).join('')}</ol>${flow&&ci<mx?`<p class="note" style="margin:-4px 0 10px">在回看已做完的「${st[ci][1]}」，后面已做的都留着。只有在这里改了东西，后面的步骤才要重做。</p>`:''}`}
let RESTORE=null;
// 拔了线还原：插上后后台自动烧回原版，这里跟着看结果
let restoreT=null;
function restoreWatch(){clearInterval(restoreT);restoreT=setInterval(async()=>{try{const j=await (await fetch('/api/fsw')).json();const R=j.restore;if(R&&!R.waiting&&!R.running){clearInterval(restoreT);restoreT=null;RESTORE=R;onDev=R.flashed?'orig':onDev;if(R.auto)rsCard(R);else toast((R.flashed?'✓ ':'✕ ')+(R.say||''));render()}}catch(e){}},1500)}
// 10 轮收齐后码表自己还原：正中一个提醒框（不挡报告），烧完停几秒自己消失；没烧上就一直在，报告底部留着按钮
let rsHideT=null,rsAutoT=null,rsFor='';
function rsCard(R){let el=document.getElementById('rsbox');if(!el){el=document.createElement('div');el.id='rsbox';el.setAttribute('role','status');document.body.appendChild(el)}clearTimeout(rsHideT);
 const bad=!R.running&&!R.flashed&&!R.waiting;el.className='rsbox '+(bad?'t-bad':R.flashed?'t-ok':'t-warn');
 el.innerHTML=R.running?'<span class="spin"></span> 正在还原固件，设备会重启一次，不用操作。':R.flashed?'✓ 已还原成原版，设备上跑的是你原来的程序。':R.waiting?'设备没插着，原版还没烧回去：插上设备就自动烧，不用再点。':
  `<div>✕ ${htmlEsc(String(R.say||'原版没烧上').replace(/^源码已还原，/,''))}<br><span class="note">报告底部可以再点「还原成原版」。</span></div><button class="tclose" data-rsclose="1" aria-label="关掉这个提示">×</button>`;
 el.hidden=false;if(R.flashed||R.waiting)rsHideT=setTimeout(()=>{el.hidden=true},R.flashed?3000:6000)}
document.addEventListener('click',e=>{if(e.target.closest('[data-rsclose]'))document.getElementById('rsbox').hidden=true});
function autoRestoreWatch(){const k=(COLJ&&COLJ.raw_log)||'';if(rsAutoT||(k&&k===rsFor))return;rsFor=k;const sc=scene;restoring=true;rsCard({running:true});
 rsAutoT=setInterval(async()=>{try{const R=(await (await fetch('/api/fsw')).json()).restore;if(!R||R.running)return;
  clearInterval(rsAutoT);rsAutoT=null;restoring=false;RESTORE=R;restored[sc]=true;onDev=R.flashed?'orig':onDev;rsCard(R);render();if(R.waiting)restoreWatch()}catch(e){}},1000)}
let restoring=false;  // 还原中：写建议时页面会不停重画，按钮不能一重画就变回去（决策 139）
function restoreBox(sc){const r=restored[sc];if(restoring&&!r)return `<section class="panel"><h2>设备上现在是：计时版</h2><p class="note" style="margin:0"><span class="spin"></span> 正在还原：先把源码放回原样，再把原版烧回设备，一般半分钟左右，不用再点。</p></section>`;if(r&&RESTORE&&realFlash()){const R=RESTORE;return `<section class="panel"><h2>设备上现在是：${R.flashed?'原版':'计时版（源码已还原）'}</h2><p class="note" style="margin:0">${R.same?`✓ 源码已还原，和你原来的源码逐字节比对一致（${R.files} 个文件）。`:R.same===false?`✕ 源码还原后和你原来的源码有 ${R.diff.length} 个文件不一样：${R.diff.map(htmlEsc).join('、')}。`:'源码已还原。'}${R.flashed?`<br>✓ 已把原版烧回设备（${htmlEsc(R.port)}）。`:R.waiting?'<br><span class="spin"></span> 设备没插着，原版还没烧回去：插上设备就自动烧，不用再点。':R.error?`<br><span style="color:var(--bad)">✕ ${htmlEsc((R.say||'原版没烧上').replace(/^源码已还原，/,''))}</span>${R.detail?`<details class="lvwhy"><summary>详情</summary><code>${htmlEsc(R.detail)}</code></details>`:''}`:'<br>原版还没烧回去。'}</p>${R.flashed||R.waiting?'':`<div class="acts" style="margin:8px 0 0"><button class="btn" data-restore="${sc}">还原成原版</button></div>`}</section>`}
 return `<section class="panel"><h2>设备上现在是：${r?'原版':'计时版'}</h2>${r?'<p class="note" style="margin:0">✓ 已还原成原版，和你的原版逐字节比对一致。</p>':`<p class="note" style="margin:0 0 8px">计时版每过一个计时点会多打印一行，交货、演示前请还原。<br>还原是直接烧读代码时编好的那份原版，烧完逐字节比对。还要接着测就先别还原。</p><div class="acts" style="margin:0"><button class="btn" data-restore="${sc}">还原成原版</button></div>`}</section>`}
function retestEntry(){const real=realFlash()&&REAL&&REAL.project_dir;  // 真测：走首页那条"改完了，再测一次"（带上次的源码、板子、要测什么）；演示数据照旧
 return `<section class="panel"><h2>改完以后</h2><p class="note" style="margin:0 0 8px">照上面的建议改完代码、换了模型或升级了版本，点这里再测一次。<br>${real?'计时点那几处代码没动，就不再调用大模型找步骤、插计时点；测完报告里直接和这次比。':'码表先查改了什么，能跳过的步骤自动跳过，测完直接和这次比。'}</p><div class="acts" style="margin:0"><button class="btn pri" ${real?'data-rtreal="1"':`data-retest="${scene}"`}>改完了，再测一次</button></div></section>`}

function rtExtra(sc,k){const P=PROJ.find(p=>p.scene===sc),R=RT[sc],u=COLLECT[sc];const same=R.same;
 if(k==='gone')return {label:'文件夹没了',checks:[['bad','源码',`找不到 ${P.path}（可能挪走或删了）`],['est','计时点','没有源码，查不了'],['ok','设备','插着，上面还是上次烧的计时版']],
  route:'上次的固件文件夹找不到了。两条路，选一条：',choice:[['重新选文件夹','代码挪了地方就选新位置。码表会比对是不是同一个项目，不是会先问你。','home'],['直接用设备上的版本测','你这次只改了服务器或网络、没改固件时用。设备上已经是计时版，不用代码也能测。','collect']],
  steps:[['找关键步骤',0,'沿用上次确认的步骤（存在码表的项目文件夹里，不跟代码走）'],['插计时点和烧录',0,'直接用设备上现有的计时版'],['多轮实测',1,`同样 20 ${u.unit}${u.act}，句子清单也存在码表里，没丢`],['前后对比',1,`上一次（${R.last}）自动当"改前"`]],what:'固件没改（源码找不到，按你说的）。服务器那边：',whatHint:same.whatHint};
 if(k==='tool')return {label:'编译工具变了',checks:[['warn','源码','改了 1 个文件'],['ok','计时点','都在原位'],['warn','编译工具',`上次用 ESP-IDF v6.1，这次电脑上是 v6.2`],['warn','设备','代码改了，要重新烧']],
  route:'照样能测。编译工具换了版本，底层代码也跟着变了，对比时会单独标出来，变快变慢不一定都是你改的那一处带来的。',
  steps:R.code.steps,what:R.code.what+'另：编译工具 v6.1 → v6.2。',whatHint:R.code.whatHint};
 if(k==='nodata')return {label:'上次数据没了',checks:[['ok','源码','找到了'],['ok','计时点','都在原位'],['bad','上次数据',`上次（${R.last}）的测量数据找不到了（被删或挪走）`]],
  route:'没有上次的数据，就没法对比。改成按"第一次测"走：步骤、句子清单还在，都沿用，测完出一份新报告。',
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
function render(){if(booting||!scene||!SCENES[scene])return;applyScene();
  if(page==='report'&&(!JREP||JREP._key!==repKey()))fetchReport();
  if(page==='report'&&JREP&&JREP._key===repKey()&&repKey().startsWith('real')&&!ADV&&D0ok())advStart();
  const D=build(page==='report'&&JREP&&JREP._key===repKey()&&!JREP._none?repScene():SCENES[scene]);
  document.getElementById('nav-home').toggleAttribute('aria-current',page==='home');document.getElementById('nav-set').toggleAttribute('aria-current',page==='settings');
  flowAdvance();morphHTML(document.getElementById('flowbar'),runView()?'':flowBar());bgPill();
  const isReport=page==='report',isHome=page==='home';
  document.getElementById('verseg').hidden=!isReport;document.getElementById('v-exp').hidden=!isReport;
  const PN=realFlash()?htmlEsc(pName):(PROJ.find(p=>p.scene===scene)||{}).name;morphHTML(document.getElementById('crumb'),isReport?`${flow?'':'<button class="lnk2" data-go="home">首页</button> › '}${String(D.S.crumb).replace(/\d+ 轮\s*$/,D.n+' 轮')}${D.S.real?'':'<span class="mock">模拟数据，不是实测</span>'}`:page==='range'?(RLP?'<button class="lnk2" data-rlback="1">设置</button> › 合理范围清单':`<button class="lnk2" data-go="report" data-keepflow="1">${PN} 的报告</button> › 合理范围`):page==='compare'&&!flow?`<button class="lnk2" data-go="home">首页</button> › ${PN} › 前后对比`:'');
  let body;
  if(!isHome)LASTP='';
  const RV=runView();
  if(RV)body=runPage();
  else if(isHome)body=(LASTP?`<section class="panel" style="margin-bottom:16px"><div class="acts" style="justify-content:space-between;align-items:center;margin:0"><span>上次停在「${htmlEsc(PNAME(LASTP))}」</span><button class="btn pri" data-go="${LASTP}" data-keepflow="1">接着看</button></div></section>`:'')+HOME[homeVar].html();
  else if(isReport)body=reportPage(D);
  else if(page==='settings')body=settingsPage();
  else if(page==='steps')body=stepsPage();
  else if(page==='flash')body=flashPage();
  else if(page==='collect')body=collectPage();
  else if(page==='compare')body=comparePage();
  else if(page==='retest')body=retestPage();
  else body=`<div class="panel todo">这一页还没做样子，按顺序下一步做。</div>`;
  const PG=(RV?'run':page)+'|'+mode,mEl=document.getElementById('main'),same=mEl.dataset.pg===PG&&mEl.firstElementChild;
  if(same)morphHTML(mEl.firstElementChild,body);else mEl.innerHTML=`<div class="view">${body}</div>`;  // 换页才整页换（带淡入），同一页只改变了的地方
  if(mEl.dataset.pg!==PG)mEl.dataset.pg=PG;
  const md=document.getElementById('modal');md.hidden=RV||!(isHome&&hStage>=2);
  if(!md.hidden)morphHTML(document.getElementById('dlg'),modalHTML());
  const pp=document.getElementById('pop');pp.hidden=!pop;if(pop)morphHTML(document.getElementById('popdlg'),popHTML());
  // 点不了的按钮：包一层，"还差：……"用小字放在按钮正下方（决策 143，全码表统一）
  document.querySelectorAll('[data-need]').forEach(b=>{const p=b.parentElement;if(p&&p.classList.contains('needwrap')){const g=p.querySelector('.gate');if(g)g.textContent='还差：'+b.dataset.msg;return}const w=document.createElement('span');w.className='needwrap';b.before(w);w.appendChild(b);const g=document.createElement('span');g.className='gate';g.textContent='还差：'+b.dataset.msg;w.appendChild(g)});
  {const m=document.getElementById('main');const need=page==='steps'&&stepsRead===2&&alsoFace===null?'#sideask':null;const el=need&&m.querySelector(need);if(el)el.classList.add('need')}
  document.getElementById('v-boss').setAttribute('aria-pressed',String(mode==='boss'));
  document.getElementById('v-dev').setAttribute('aria-pressed',String(mode==='dev'));
  syncFlow();syncHash();uiSave();autoTick();autoSave();
  if(isHome&&!RV&&REAL)devChipCheck();
}
// 步骤条同步给后台：后台记下走到哪一步、哪步出错、哪些跳过（刷新网页、以后接真功能都靠它）
let lastKind='',lastSync='';
function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
function syncFlow(){if(!flow)return;
 const ids=flow==='retest'?['retest','steps','flash','collect','compare']:FLOW1.map(x=>x[0]);
 let skipped=[];if(flow==='retest'&&!(page==='retest'&&rtSrc<2)){const C=rtC();skipped=[['steps',!C.steps[0][1]],['flash',!C.steps[1][1]],['compare',!C.steps[3][1]]].filter(x=>x[1]).map(x=>x[0])}
 const err=page==='flash'&&(fl.st===4||fl.st===5)?(fl.st===4?'没看到设备':{build:'编译没通过',burn:'烧录没做成',try:'试一次没收到计时点'}[fl.err]):null;
 const b={reached:flowMax,viewing:ids.includes(page)?page:null,error:err,skipped},k=JSON.stringify(b),kind=flow+'/'+scene;
 if(k===lastSync&&kind===lastKind)return;lastSync=k;
 (kind!==lastKind?post('/api/flow/start',{kind:flow,demo:scene}):Promise.resolve()).then(()=>post('/api/flow/sync',b)).catch(()=>{});lastKind=kind}
// ===== 刷新、后退不丢进度（D 组）=====
// 换页写进网址（#页面名），浏览器的后退、前进在码表里换页；页面进度存到后台 /api/ui，刷新或新开标签页时接上，
// 后台任务（找关键步骤、编原版、插计时点和烧录、多轮实测、写建议）还在跑就接着显示进度
let histPage=null,navPop=false;
function syncHash(){if(page===histPage)return;const url=location.pathname+location.search+'#'+page;
 try{if(histPage===null||navPop)history.replaceState({fsw:page},'',url);else history.pushState({fsw:page},'',url)}catch(e){}histPage=page;navPop=false}
const PNAME=p=>(FLOW1.find(x=>x[0]===p)||[0,(PAGES.find(x=>x.id===p)||{}).name||p])[1];
// 这一页现在能不能打开：没做到的、做到了但后台已经没有那一步的进度（比如码表重开过）的，都不能直接打开
function canView(p){if(p==='home'||p==='settings')return true;
 const L=flow==='retest'?['retest','steps','flash','collect','compare']:flow==='first'?FLOW1.map(x=>x[0]):[],i=L.indexOf(p),real=!!(REAL&&REAL.work)&&flow==='first';
 if(i<0&&['steps','flash','collect'].includes(p))return false;
 if(i>flowMax)return false;
 if(real){if(p==='steps')return stepsRead>=1;if(p==='flash')return !!(IDENT&&IDENT.page);if(p==='collect')return !!(FSWJ&&FSWJ.status==='done');if(p==='report')return okRounds().length>=MIN_ROUNDS}
 return true}
// 去不了就往前找最近一个能接着做的
function canViewNear(p){const L=flow==='retest'?['retest','steps','flash','collect','compare']:FLOW1.map(x=>x[0]);let i=L.indexOf(p);if(i<0)i=0;
 for(;i>=0;i--)if(canView(L[i]))return L[i];return 'home'}
window.addEventListener('popstate',()=>{if(booting)return;let h='';try{h=decodeURIComponent(location.hash.slice(1))}catch(e){}if(!PAGES.some(p=>p.id===h)||h===page)return;
 navPop=true;pop=null;if(hStage>=2)hStage=1;page=canView(h)?h:canViewNear(h);render();window.scrollTo(0,0)});
function uiSnap(){const ed={};for(const p of Object.keys(edits)){const x=[...(STEPS||[]),...added].find(r=>r.p===p);if(x)ed[p]=[x.f,x.l]}
 return {v:1,scene,page,mode,flow,flowMax,hStage:hStage>=4?1:hStage,hSrc,hSay,pName,hBoardQ,REAL,bgSrc,alsoFace,dropped:[...dropped],added,ed,mapSel,diffOk,flashVar,colVar,adv:ADV&&ADV.started||null,rtCase,remeasure,last:page==='home'?LASTP:'',auto:AUTO,autoHold,autoHoldKind,autoT0,autoDrop}}
let uiLast='',uiT=null;
const uiSend=s=>fetch('/api/ui?key='+encodeURIComponent(DEMO),{method:'POST',headers:{'Content-Type':'application/json'},body:s}).catch(()=>{});
function uiSave(){let s;try{s=JSON.stringify(uiSnap())}catch(e){return}if(s===uiLast)return;uiLast=s;try{sessionStorage.setItem('fsw-ui',s)}catch(e){}  // 这个标签页自己也存一份：别的标签页开了新的一轮，这里刷新也不丢
 clearTimeout(uiT);uiT=setTimeout(()=>{uiT=null;uiSend(s)},250)}
window.addEventListener('pagehide',()=>{if(!uiT)return;clearTimeout(uiT);uiT=null;try{navigator.sendBeacon('/api/ui?key='+encodeURIComponent(DEMO),new Blob([uiLast],{type:'application/json'}))}catch(e){uiSend(uiLast)}});
// 按后台的流程进度拼一份页面进度（和 uiSnap 同样的字段）；old：网页自己存的那份（别的源码），设置类的选择沿用
function srvSnap(S,old){const o=old&&old.v===1?old:{};
 return {v:1,scene:SCENES[o.scene]?o.scene:scene,page:S.page,mode:'boss',flow:'first',flowMax:S.stage,hStage:1,hSrc:S.hSrc,hSay:S.what,pName:S.pName,hBoardQ:S.hBoardQ,
  REAL:S.REAL,bgSrc:S.bg?S.hSrc:'',alsoFace:true,dropped:[],added:[],ed:{},mapSel:o.mapSel,diffOk:S.stage>=2&&S.fsw!=='inserted',flashVar:'b',colVar:'b',adv:null,
  rtCase:o.rtCase,remeasure:false,last:'',auto:true,autoHold:'',autoHoldKind:'',autoT0:S.at?S.at*1000:0}}
async function uiRestore(){const get=async u=>{try{const r=await fetch(u);return r.ok?await r.json():null}catch(e){return null}};
 const U=await get('/api/ui?key='+encodeURIComponent(DEMO)),J=(U&&U.jobs)||{};let u=U&&U.ui;
 let own=false;if(!FRESH){try{const m=JSON.parse(sessionStorage.getItem('fsw-ui')||'null');if(m&&m.v===1){u=m;own=true}}catch(e){}}
 // 后台知道的流程进度（U.server）：流程不是从网页走的（比如按接口走完），网页存的那份是空的或是别的源码，就按后台的接上；
 // 同一份源码、后台走得更远就跟到后台那一步。不再停在首页、像回到了老界面（10-25 真机）。
 // 这个标签页自己存的那份（刷新）照旧优先：别的标签页开了新的一轮，这里刷新也不丢
 const S=U&&U.server;let fromSrv=false;
 if(S&&S.REAL&&S.stage>=1&&!own){const same=u&&u.v===1&&u.REAL&&u.REAL.work===S.work;
  if(!same){u=srvSnap(S,u);fromSrv=true}else if((u.flowMax||0)<S.stage||u.hSay!==S.what){u={...u,flow:'first',flowMax:Math.max(u.flowMax||0,S.stage),hSay:S.what,page:S.page};fromSrv=true}}
 if(u&&u.v===1&&!SCENES[u.scene]&&fromSrv)u.scene=scene;
 if(!u||u.v!==1||!SCENES[u.scene]){page=HASH0==='settings'?'settings':'home';return}
 // 关了浏览器或标签页再打开：算新的一轮，从空白首页开始；只有实测、烧录还在后台跑着时，或者流程不是从这个网页走的（按后台接上的），
 // 才接上，首页顶上给「接着看」
 if(FRESH&&!fromSrv){const cj=await get('/api/collect'),fj=await get('/api/fsw'),run=x=>!!x&&['running','waiting_device'].includes(x.status);if(!run(cj)&&!run(fj)){page='home';return}}
 scene=u.scene;applyScene();mode=u.mode||'boss';flow=u.flow||null;flowMax=u.flowMax||0;hStage=u.hStage||0;hSrc=u.hSrc||'';hSay=u.hSay||'';pName=u.pName||pName;hBoardQ=u.hBoardQ||'';
 REAL=u.REAL||null;if(REAL&&!REAL.ai)REAL.ai=[];hScan=REAL?2:0;bgSrc=u.bgSrc||'';mapSel=u.mapSel||mapSel;flashVar=u.flashVar||flashVar;colVar=u.colVar||colVar;rtCase=u.rtCase||rtCase;remeasure=!!u.remeasure;
 // 测量页接着显示、接着自动走（10-26）：存过的照存的；老的存档没有这一项时，第一次测（flow first）就是测量页流程
 AUTO=flow==='first'&&(u.auto===undefined?true:!!u.auto);autoHold=AUTO?String(u.autoHold||''):'';autoHoldKind=autoHold?String(u.autoHoldKind||''):'';autoT0=u.autoT0||Date.now();autoBusy='';autoDrop=Array.isArray(u.autoDrop)?u.autoDrop:[];
 // 找完关键步骤那种停下（含老存档里按模型名停下的"这次用的是 DeepSeek……"）不照搬：接上后按码表自己的核对重新算，没问题就接着走（10-26）
 if(autoHoldKind==='ident'||(!autoHoldKind&&/^这次用的是 DeepSeek/.test(autoHold))){autoHold='';autoHoldKind=''}
 const keep=()=>{dropped=new Set(u.dropped||[]);added=u.added||[];alsoFace=u.alsoFace===undefined?true:u.alsoFace;
  for(const [p,v] of Object.entries(u.ed||{})){const x=[...STEPS,...added].find(r=>r.p===p);if(x&&v){x.f=v[0];x.l=v[1];edits[p]=true}}};
 const quiet=j=>{const b=beep;beep=false;try{colApply(j)}finally{beep=b}};  // 接上已测的轮：不再一轮轮"叮"
 const colResume=j=>{COLJ=j;col={st:['running','waiting_device','paused'].includes(j.status)?1:0,att:0,got:[],phase:0,sc:scene};quiet(j);if(col.st===1)colWatch()};
 if(REAL&&REAL.work){
  if(bgSrc&&J.bgbuild===REAL.work){BG=await get('/api/bgbuild');bgStartReal(true)}else if(bgSrc)bgStartReal();
  const ij=flow==='first'&&J.identify===REAL.work?await get('/api/identify'):null;
  if(ij&&ij.state&&ij.state!=='idle'&&ij.what===hSay){if(ij.state==='running'){stepsRead=1;identApply(ij);readT=setInterval(async()=>{try{identApply(await (await fetch('/api/identify')).json())}catch(e){}},1000)}else identApply(ij);if(ij.state==='done')keep()}
  else if(hStage>=3)hStage=1;  // 找关键步骤的进度没了：回到首页"下一步"那里重新开始
  if(IDENT&&IDENT.page&&flowMax>=2&&J.fsw===REAL.work){const fj=await get('/api/fsw');
   if(fj&&fj.status&&fj.status!=='idle'){fswApply(fj);diffOk=fj.status==='inserted'?false:!!u.diffOk;if(['running','waiting_device','ready_try'].includes(fj.status))fswPoll();if(fj.status==='done'){onDev='fsw';await loadRCT()}
    const R=fj.restore;if(R&&R.running)autoRestoreWatch();else if(R){RESTORE=R;restored[scene]=true;onDev=R.flashed?'orig':onDev;if(R.waiting)restoreWatch()}}}
  if(realCol()&&flowMax>=3){const cj=await get('/api/collect');if(cj&&cj.status!=='idle'&&!cj.demo&&String(cj.raw_log||'').startsWith(REAL.project_dir)){COLJ=cj;await loadRCT();colResume(cj)}}
  if(u.adv&&okRounds().length){const aj=await get('/api/advise');if(aj&&aj.started===u.adv){ADV=aj;if(aj.status==='running')advWatch()}}}
 else{stepsRead=flow&&flowMax>=1?2:0;if(stepsRead===2)keep();if(hStage>=3)hStage=1;
  if(flow&&flowMax>=3){fl={st:2,step:5,pct:0,log:''};diffOk=true;onDev='fsw'}
  const cj=await get('/api/collect');if(cj&&cj.status!=='idle'&&cj.demo===scene&&flow&&flowMax>=3)colResume(cj)}
 const want=HASH0&&HASH0!==u.page&&canView(HASH0)?HASH0:u.page||'home';
 page=canView(want)?want:canViewNear(want);
 if(page!==want){const L=flow==='retest'?['retest','steps','flash','collect','compare']:FLOW1.map(x=>x[0]),i=L.indexOf(page);if(flow&&i>=0)flowMax=Math.min(flowMax,i)}  // 接不上的那几步不再标成做完
 if(page!==want&&!FRESH)setTimeout(()=>toast(`上次在「${PNAME(want)}」，这一步的进度没能接上，先回到「${PNAME(page)}」，从这里接着做。`),300);
 // 后台还在跑的那种新打开：进首页，顶上一条「接着看」回到上次那页；刷新还停在原页
 if(FRESH&&page!=='home'&&page!=='settings'){LASTP=page;page='home';hStage=Math.min(hStage,1)}
 else if(page==='home'&&u.last&&u.last!=='home'&&canView(u.last))LASTP=u.last}
// 复制按钮（10-21 用户定）：点了换成对勾"已复制"，2 秒后变回来；只改按钮本身，不重画页面，导出的报告里也能用
const COPY_IC='<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><rect x="5" y="5" width="9" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
function todoCopy(e){const b=e.target.closest&&e.target.closest('[data-copy]');if(!b)return false;const el=document.getElementById(b.dataset.copy);if(!el)return true;const t=el.textContent;
  const old=()=>{const a=document.createElement('textarea');a.value=t;a.style.position='fixed';a.style.opacity='0';document.body.appendChild(a);a.select();try{document.execCommand('copy')}catch(_){}a.remove()};
  try{navigator.clipboard.writeText(t).catch(old)}catch(_){old()}
  if(!b.dataset.html)b.dataset.html=b.innerHTML;b.innerHTML='<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2"/></svg>已复制';b.classList.add('done');
  clearTimeout(b._t);b._t=setTimeout(()=>{b.innerHTML=b.dataset.html;b.classList.remove('done')},2000);return true}
function tipShow(e){const el=e.target.closest&&e.target.closest('[data-tip]'),T=document.getElementById('tip');if(!el){T.hidden=true;return}T.innerHTML=el.dataset.tip;T.hidden=false;const r=el.getBoundingClientRect(),w=T.offsetWidth,h=T.offsetHeight;let x=r.left+r.width/2-w/2,y=r.top-h-8;if(y<4)y=r.bottom+8;x=Math.max(4,Math.min(x,innerWidth-w-4));T.style.left=x+'px';T.style.top=y+'px'}
document.addEventListener('mouseover',tipShow);document.addEventListener('focusin',tipShow);document.addEventListener('scroll',()=>{document.getElementById('tip').hidden=true},true);
document.addEventListener('click',e=>{
  const nd=e.target.closest('[data-need]');if(nd){if(nd.dataset.needgo&&nd.dataset.needgo!==page){if(hStage>=2)hStage=1;page=nd.dataset.needgo;render();window.scrollTo(0,0);return}const g=nd.parentElement.querySelector('.gate');if(g)g.classList.add('hit');nudge(nd.dataset.need);return}
  if(e.target.closest('#pop button')){pop=null;document.getElementById('pop').hidden=true}
  if(e.target.closest('[data-envgo]')){envBack=page==='flash'?'flash':'home';envMark=true;hStage=Math.min(hStage,1);page='settings';render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-save]')){const sb=e.target.closest('[data-save]');const ki=document.getElementById('set-key'),kv=ki?ki.value.trim():'';sb.disabled=true;
   fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({llm_provider:setLLM,key:setLLM!=='cc'&&kv&&!kv.startsWith('•')?kv:null,data_dir:dataDraft,reuse:reuseLast})}).then(async r=>{const j=await r.json();if(!r.ok){sb.disabled=false;toast('✕ '+(j.detail||'没保存上'));return}
   SAVED=j.settings;keyDraft='';dataDraft=null;compPending=inst.st===1;envMark=false;toast(compPending?'✓ 已保存。编译工具还在后台装，装好自动变绿':'✓ 保存成功');const back=hSrc?envBack:'home';envBack='home';setTimeout(()=>{page=back;if(hStage>=2)hStage=1;render();window.scrollTo(0,0)},compPending?1800:1000)}).catch(()=>{sb.disabled=false;toast('✕ 码表后台没有回应，看看运行码表的窗口是不是关了')});return}
  if(e.target.closest('[data-cc]')){CCST=null;render();fetch('/api/env').then(r=>r.json()).then(j=>{CCST=j.claude_code;render()});return}
  const ib=e.target.closest('[data-idf]');if(ib){idfErr='';if(ib.dataset.idf==='find'){ib.textContent='正在查找……';fetch('/api/env').then(r=>r.json()).then(j=>{IDFS=(j.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);TOOLS=j.tools||null;toast(IDFS.length?`✓ 找到 ${IDFS.length} 个`:'没找到乐鑫编译工具');render()});return}
   api('/api/source/pick',{kind:'dir'}).then(async p=>{if(!p.path)return;const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({idf_path:p.path})});const j=await r.json();if(!r.ok){idfErr=j.detail;render();return}
    SAVED=j.settings;const e2=await (await fetch('/api/env')).json();IDFS=(e2.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);TOOLS=e2.tools||null;toast('✓ 已记下这个位置');render()});return}
  if(e.target.closest('[data-datadir]')){api('/api/source/pick',{kind:'dir'}).then(p=>{if(p.path){dataDraft=p.path;render()}});return}
  if(e.target.closest('[data-autostart]')){autoStart();return}
  if(e.target.closest('[data-autostop]')){autoStop();return}
  if(e.target.closest('[data-autofix]')){autoHoldKind='fix';const SS=(COLJ&&COLJ.suspect)||[];api('/api/collect/choice',{choice:'fix'}).catch(()=>{});  // 选了什么记进这次的结果（报告里写）
   autoHold=SS.some(x=>x.kind)?`测量停下来了：${SS.map(x=>x.why).join('；')}`:'第 1 轮自动校验没通过：'+SS.map(x=>`「${x.name}」${fms(Math.abs(x.seconds))} 秒`).join('、')+'。请检查这一段起止两个计时点的位置，改好后重新插入。';page='steps';render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-mclose]')){hStage=1;render();return}
  if(e.target.closest('[data-mfind]')){const pn=document.getElementById('h-pname');if(pn&&pn.value.trim())pName=pn.value.trim().replace(/[<>&"]/g,'');autoStart();window.scrollTo(0,0);return}  // 老的"开始找关键步骤"也走测量页（10-26）
  if(e.target.closest('[data-cdskip]')){clearInterval(cdT);cdT=null;cd=0;hStage=4;render();return}
  if(e.target.closest('[data-mnext]')){clearInterval(cdT);cdT=null;cd=0;hStage=1;page='steps';mode='boss';render();window.scrollTo(0,0);return}
  if(e.target.closest('[data-goins]')&&flow==='first'){autoHold='';autoBusy='';AUTO=true}  // 核对完确认插入：自动执行接着往下走
  if(e.target.closest('[data-reanalyze]')){freshNext=true;autoStart();window.scrollTo(0,0);return}
  if(e.target.closest('[data-autoresume]')){AUTO=true;autoHold='';autoBusy='';render();return}
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
  if(e.target.closest('[data-bgretry]')&&REAL&&REAL.work){pop=null;bgStartReal();render();return}
  const pc=e.target.closest('[data-pop]');if(pc){render();if(pc.dataset.nudge)nudge(pc.dataset.nudge);return}
  const g=e.target.closest('[data-go]');if(g){const to=g.dataset.go;
if(to==='home')LASTP='';  // 点首页＝不接着看了，那条提示收起
if(flow==='first'&&(to==='flash'||to==='collect'))autoHold='';  // 核对完没改步骤、回到后面那步：不再停着
  const pn=document.getElementById('h-pname');if(pn&&pn.value.trim())pName=pn.value.trim().replace(/[<>&"]/g,'');if(hStage>=2&&to==='steps'){autoStart();window.scrollTo(0,0);return}if(hStage>=2)hStage=1;if(to==='home'&&!g.dataset.keepflow&&flow!=='first')flow=null;/* 第一次测时点首页只是回看，步骤条留着（D 组） */page=to;if(!g.dataset.keepflow)mode='boss';render();window.scrollTo(0,0);return}
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
   if(a==='reset'){newRun();clearInterval(bgT);bgT=null;bgSrc='';hStage=0;hSrc='';hScan=0;REAL=null;hBoardQ='';render();return}
   if(a==='paste'){const v=(document.getElementById('h-paste').value||'').trim();if(!v){hErr='先粘贴一个路径。';render();return}realPick({path:v});return}
   api('/api/source/pick',{kind:a==='zip'?'zip':'dir'}).then(r=>{if(r.path)realPick({path:r.path})}).catch(()=>{hErr='打不开选择窗口，可以把路径粘贴到下面的框里。';render()});return}
  const pr=e.target.closest('[data-pickroot]');if(pr){realPick({path:pr.dataset.pickroot});return}
  const rs=e.target.closest('[data-restore]');if(rs&&realFlash()&&rs.closest('#main')&&page==='report'){if(restoring)return;restoring=true;render();  // 决策 109：一定问后台，结果说清楚
   api('/api/fsw/restore',{project_dir:REAL.project_dir}).then(j=>{restoring=false;if(j.detail){toast('✕ '+j.detail);render();return}
    RESTORE=j;restored[rs.dataset.restore]=true;onDev=j.flashed?'orig':onDev;toast((j.flashed?'✓ ':j.waiting?'':'✕ ')+(j.say||'已还原'));render();if(j.waiting)restoreWatch()}).catch(()=>{restoring=false;toast('✕ 码表后台没有回应，看看运行码表的窗口是不是关了');render()});return}
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
   else if(a==='origretry'){bgStartReal();api('/api/bgbuild/start',bgBody()).then(()=>fswPost('/api/fsw/run',{from:'build'})).catch(()=>{})}  // 先重新编原版，再接着编计时版
   else if(a==='rebuild')fswPost('/api/fsw/run',{from:'build'});else if(a==='reburn')fswPost('/api/fsw/run',{from:'flash'});else if(a==='retry'||a==='trygo')fswPost('/api/fsw/run',{from:'try'});
   render();return}
  if(fx){const a=fx.dataset.flx;demoErr='';
   if(a==='drop'){const L=live();stepsChanged();diffOk=true;dropped.add(L[Math.min(2,L.length-1)].p);flowMax=Math.max(flowMax,2);toast('已撤掉这个计时点，重新编译');flRun(2,4)}
   else if(a==='move'){const L=live();editP=L[Math.min(2,L.length-1)].p;fl={st:0,step:0,pct:0,log:''};diffOk=false;page='steps';render();window.scrollTo(0,0);setTimeout(()=>{const el=document.getElementById('ed-f');if(el){el.scrollIntoView({block:'center'});el.focus()}},50);return}
   else if(a==='reburn')flRun(3,4);else if(a==='retry')flRun(4,4);
   render();return}
  const df=e.target.closest('[data-df]');if(df){diffFile=+df.dataset.df;render();return}
  const od=e.target.closest('[data-ondev]');if(od){onDev=od.dataset.ondev;flRun(2,3);render();return}
  if(e.target.closest('[data-openlog]')){if(repKey().startsWith('real')&&JREP&&JREP.raw_log)api('/api/open-folder',{path:JREP.raw_log}).then(j=>toast(j.ok?'✓ 已在访达里打开':'✕ '+(j.detail||'打不开')));else toast('演示数据没有原始日志文件：真测完才有');return}
  if(todoCopy(e))return
  if(e.target.closest('#v-exp')){exportStart();return}
  if(e.target.closest('[data-expdir]')){api('/api/source/pick',{kind:'dir'}).then(p=>{if(p&&p.path){EXP.dir=p.path;render()}});return}
  if(e.target.closest('[data-expgo]')){pop='export';EXP.state='saving';render();exportReport(EXP.dir);return}  // 对话框留着，存好、没存成都在框里说
  if(e.target.closest('[data-expwait]')){pop='export';EXP.state='wait';render();return}
  if(e.target.closest('[data-expshow]')){api('/api/open-folder',{path:EXP.saved}).then(j=>toast(j.ok?'✓ 已在访达里打开':'✕ '+(j.detail||'打不开')));return}
  if(e.target.closest('[data-expclose]')){EXP.saved=null;render();return}
  const hr=e.target.closest('[data-hrestore]');if(hr){hr.disabled=true;hr.textContent='正在还原……';  // 码表重开过也认得：按项目文件夹里存的插入记录还原
   api('/api/fsw/restore',{project_dir:hr.dataset.hrestore}).then(j=>{toast(j.detail?'✕ '+j.detail:(j.flashed?'✓ ':j.waiting?'':'✕ ')+(j.say||'已还原'));if(j.waiting)restoreWatch();histLoad().then(render)});return}
  const ro=e.target.closest('[data-ropen]');if(ro){api('/api/report/open',{path:ro.dataset.ropen}).then(j=>toast(j.ok?'✓ 已用浏览器打开这份报告':'✕ '+(j.detail||'打不开')));return}
  if(e.target.closest('[data-rtreal]')){retestFrom({path:(REAL.body&&REAL.body.path)||hSrc,what:hSay||(IDENT&&IDENT.page&&IDENT.page.mapWhat)||''});return}
  const hrt=e.target.closest('[data-hretest]');if(hrt){const h=HIST.find(x=>x.project_dir===hrt.dataset.hretest);if(h)retestFrom(h);return}
  const ho=e.target.closest('[data-hopen]');if(ho){api('/api/history/open',{path:ho.dataset.hopen}).then(j=>toast(j.ok?'✓ 已用浏览器打开这次的报告':'✕ '+(j.detail||'打不开')));return}
  const jp=e.target.closest('[data-jump]');if(jp){const id=jp.dataset.jump;if(id.startsWith('fix-')&&repVar==='c'){const F=activeFixes(build(SCENES[scene]))[+id.slice(4)];if(F){segSel=F.seg;render()}}const el=document.getElementById(id);if(el){if(id.startsWith('fix-')){const dt=el.querySelector('details');if(dt)dt.open=true}  // 点「详情 → 问题 N」：卡片展开着给看（10-20 用户真测）
   el.scrollIntoView({behavior:'smooth',block:'center'});el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash')}return}
  const rpv=e.target.closest('[data-repvar]');if(rpv){repVar=rpv.dataset.repvar;render();return}
  if(e.target.closest('[data-fulllog]')){showFull=!showFull;render();return}
  const td=e.target.closest('[data-todo]');if(td){fixTodo=!fixTodo;render();return}
  const sgb=e.target.closest('[data-seg]');if(sgb){segSel=sgb.dataset.seg;render();return}
  const cmv=e.target.closest('[data-cmpvar]');if(cmv){cmpVar=cmv.dataset.cmpvar;render();return}
  const cv=e.target.closest('[data-colvar]');if(cv){colVar=cv.dataset.colvar;render();return}
  if(e.target.closest('[data-beeptest]')){resetAudio();unlockAudio();beepBlocked=false;ding(false);setTimeout(()=>{beepMsg=actx?actx.state:'none';beepLog('试听');render()},400);return}
  if(e.target.closest('[data-beep]')){beep=!beep;render();return}
  const ca=e.target.closest('[data-col]');if(ca){unlockAudio();const a=ca.dataset.col;if(a==='go')colStart();else if(a==='stop'){colStop();col.st=3;api('/api/collect/stop',{}).then(colApply)}else if(a==='resume'){if(col.st===4)autoBusy='';colStart(true)}else if(a==='restart'){pop='colre'}else if(a==='restart2'){colStart(false)}render();return}
  if(e.target.closest('[data-resume]')){page=flow==='first'&&envBack!=='home'?envBack:'home';hStage=Math.min(hStage,1);envBack='home';render();window.scrollTo(0,0);return}  // 不再回老的"确认要测的内容"窗口（那里走的是老的一页一页流程）
  const tk=e.target.closest('[data-test]');if(tk){const i=document.getElementById('set-key'),v=i?i.value.trim():'';keyErr='';if(v&&!v.startsWith('•'))keyDraft=v;tk.disabled=true;tk.textContent='正在测试……';
   api('/api/llm/test',{provider:setLLM,key:v&&!v.startsWith('•')?v:null}).then(r=>{keyTested=!!r.ok;keyErr=r.ok?'':r.message;render()}).catch(()=>{keyErr='码表后台没有回应，看看运行码表的窗口是不是关了。';render()});return}
  const fil=e.target.closest('[data-fill]');if(fil){const i=document.getElementById('h-what');if(i)i.value=fil.dataset.fill;hSay=fil.dataset.fill;return}
});
document.addEventListener('input',e=>{if(e.target.id==='h-boardq'){hBoardQ=e.target.value;hBoardOpen=true;hBoardHi=0;render()}});
document.addEventListener('focusin',e=>{if(e.target.id==='h-boardq'&&!hBoardOpen){hBoardOpen=true;render()}});
document.addEventListener('focusout',e=>{if(e.target.id==='h-boardq')setTimeout(()=>{if(hBoardOpen&&document.activeElement&&document.activeElement.id!=='h-boardq'){hBoardOpen=false;render()}},150)});
document.addEventListener('mousedown',e=>{const o=e.target.closest&&e.target.closest('[data-pickboard]');if(o){e.preventDefault();boardPick(o.dataset.pickboard)}});
document.addEventListener('keydown',e=>{if(e.target.id!=='h-boardq'||!REAL)return;const list=boardOk(hBoardQ);
 if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();hBoardOpen=true;hBoardHi=Math.max(0,Math.min(list.length-1,hBoardHi+(e.key==='ArrowDown'?1:-1)));render();const h=document.querySelector('#h-blist .hi');h&&h.scrollIntoView({block:'nearest'})}
 else if(e.key==='Enter'&&hBoardOpen&&list.length){e.preventDefault();boardPick(list[Math.min(hBoardHi,list.length-1)].id)}
 else if(e.key==='Escape'){hBoardOpen=false;render()}});
document.addEventListener('dragover',e=>{const z=e.target.closest&&e.target.closest('#dropzone');if(z){e.preventDefault();z.style.borderColor='var(--speak)'}});
document.addEventListener('dragleave',e=>{const z=e.target.closest&&e.target.closest('#dropzone');if(z)z.style.borderColor=''});
document.addEventListener('drop',e=>{const z=e.target.closest&&e.target.closest('#dropzone');if(!z)return;e.preventDefault();uploadDrop(e.dataTransfer.items)});
document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal'&&hStage===2){hStage=1;render()}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&pop){pop=null;render();return}if(e.key==='Escape'&&hStage===2){hStage=1;render()}});
document.addEventListener('change',e=>{if(e.target.id==='h-board'){return}if(e.target.dataset&&e.target.dataset.pick){const id=e.target.dataset.pick;fixPick.has(id)?fixPick.delete(id):fixPick.add(id);render();return}if(e.target.dataset&&e.target.dataset.sw){swOn=e.target.checked;return}if(e.target.dataset&&e.target.dataset.drop){const p=e.target.dataset.drop;dropped.has(p)?dropped.delete(p):dropped.add(p);render();return}if(e.target.id==='set-reuse'){reuseLast=e.target.checked;return}if(e.target.name==='llm'){setLLM=e.target.value;keyTested=false;keyErr='';keyDraft='';render()}});
document.getElementById('v-boss').addEventListener('click',()=>{mode='boss';render()});
document.getElementById('v-dev').addEventListener('click',()=>{mode='dev';render()});
(async function init(){
 const list=await (await fetch('/api/demo')).json();
 for(const d of list){const x=await (await fetch('/api/demo/'+encodeURIComponent(d.id))).json();SCENES[d.id]=x.S;FF[d.id]=x.F;COLLECT[d.id]=x.C;RUNS[d.id]=x.R;RT[d.id]=x.RT;if(!d.hidden)PROJ.push({...x.P,scene:d.id})}  // 只用来核对报告的演示数据（hidden）不进"以前测过的"
 scene=DEMO&&SCENES[DEMO]?DEMO:list[0].id;pName=FF[scene].pname;mapSel=FF[scene].mapSel;
 try{const e=await (await fetch('/api/env')).json();IDFS=(e.idf||[]).sort((a,b)=>vge(a.version,b.version)?-1:1);TOOLS=e.tools||null;CCST=e.claude_code||null;PORTS=e.ports||[]}catch(e){}
 try{SAVED=(await (await fetch('/api/state')).json()).settings;if(P2UI[SAVED.llm_provider])setLLM=P2UI[SAVED.llm_provider];reuseLast=SAVED.reuse_last!==false;setFirst=!SAVED.llm_provider}catch(e){}
 await histLoad();
 try{await uiRestore()}catch(e){page='home'}  // 接上次的进度（D 组）
 booting=false;
 portsWatch(true)
 render()})();
