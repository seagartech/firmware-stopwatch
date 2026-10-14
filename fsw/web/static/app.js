// 固件码表网页。只认通用数据：一个起点 + 几条线，每条线按顺序几段。
// 这一版用演示数据走通流程；真读源码、真编译、真串口按任务逐个接进来。
'use strict';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const f2 = x => x.toFixed(2);
const sum = a => a.reduce((x, y) => x + y, 0);
const sorted = a => [...a].sort((x, y) => x - y);
const median = a => { const s = sorted(a), m = s.length / 2; return s.length % 2 ? s[m | 0] : (s[m - 1] + s[m]) / 2; };
const p90 = a => sorted(a)[Math.ceil(a.length * 0.9) - 1];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const failDemo = new URLSearchParams(location.search).get('fail');

const S = {
  state: null, demos: [], demo: null, page: 'home', back: 'home',
  home: { demo: null, data: null, ask: '', confirm: false },
  ui: {},
};

// ===== 和后台说话 =====
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || '出错了');
  return data;
}
async function loadDemo(id) { return id ? api('/api/demo/' + id) : null; }
async function setState(st) {
  S.state = st;
  if (st.demo && (!S.demo || S.demo.id !== st.demo)) S.demo = await loadDemo(st.demo);
  render();
}
async function flowDo(action, step, extra = {}) {
  try { await setState(await api('/api/flow/' + action, { step, ...extra })); }
  catch (e) { toast(e.message); }
}

// ===== 小工具 =====
let toastT = null;
function toast(m) {
  const t = $('#toast'); t.textContent = m; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 2600);
}
function go(page) { S.page = page; location.hash = page; render(); window.scrollTo(0, 0); }
function pri(label, act, missing = [], center = false) {
  return `<div class="pri-row ${center ? 'center' : ''}"><button class="btn pri" data-act="${act}" ${missing.length ? 'disabled' : ''}>${label}</button>${missing.length ? `<span class="lack">还差：${missing.join('、')}</span>` : ''}</div>`;
}
const flow = () => S.state && S.state.flow;
const stepOf = key => flow() && flow().steps.find(s => s.key === key);
const reviewing = key => stepOf(key) && stepOf(key).status === 'done';
const ui = key => (S.ui[key] = S.ui[key] || {});
// 改了已经做完的步骤：先告诉后台，让后面的步骤变回未完成
async function mutate(key, fn) {
  if (reviewing(key)) {
    resetAfter(key);
    await flowDo('change', key);
  }
  fn(); render();
}
function resetAfter(key) {
  const keys = flow().steps.map(s => s.key);
  keys.slice(keys.indexOf(key) + 1).forEach(k => { delete S.ui[k]; });
}

// ===== 统计（按通用数据算） =====
function lineTotals(D, line) {
  return D.segments[line.segments[0]].values.map((_, r) => sum(line.segments.map(k => D.segments[k].values[r])));
}
function roundColor(D, r) {
  let c = 'g';
  for (const l of D.lines) {
    const t = sum(l.segments.map(k => D.segments[k].values[r]));
    if (t > l.slow) return 'r';
    if (t > l.range[1]) c = 'y';
  }
  return c;
}
function judge(v, range, slow) {
  return v > slow ? ['慢', 'c-bad'] : v > range[1] ? ['偏慢', 'c-warn'] : ['正常', 'c-ok'];
}

// ===== 顶部、步骤条、提示 =====
function renderChrome() {
  const inFlow = S.page === 'flow' && flow();
  $('#nav-home').toggleAttribute('aria-current', S.page === 'home');
  if (S.page === 'home') $('#nav-home').setAttribute('aria-current', 'page');
  $('#nav-settings').toggleAttribute('aria-current', S.page === 'settings');
  if (S.page === 'settings') $('#nav-settings').setAttribute('aria-current', 'page');
  const bar = $('#flowbar');
  bar.hidden = !inFlow;
  $('#notice').innerHTML = '';
  if (!inFlow) return;
  const F = flow();
  bar.innerHTML = F.steps.map((s, i) => {
    const n = i + 1;
    let cls = 'fs', dis = '', label = `${n} ${s.name}`;
    if (s.status === 'done') { cls += ' done'; label += ' ✓'; }
    else if (s.status === 'skipped') { cls += ' skip'; dis = 'disabled'; }
    else if (s.status === 'todo') dis = 'disabled';
    if (s.status === 'doing' || s.status === 'error') {
      if (F.viewing === s.key) cls += ' cur';
      else { cls += ' goon'; label += '（做到这里）'; }
      if (s.status === 'error') cls += ' cur bad';
    } else if (F.viewing === s.key) cls += ' cur';
    return `<li><button class="${cls}" data-act="view" data-step="${s.key}" ${dis} aria-current="${F.viewing === s.key ? 'step' : 'false'}" data-status="${s.status}">${label}</button></li>`;
  }).join('');
  if (F.notice) $('#notice').innerHTML = `<div class="notice">⚠ ${esc(F.notice)}</div>`;
}

// ===== 首页 =====
function lights() {
  const st = S.state.settings, p = st.llm_provider;
  const llmOK = p === 'claude-code' || (p && st.has_key[p]);
  const parts = [
    `<span class="${llmOK ? 'ok' : 'bad'}">大模型${llmOK ? '' : '（没配好）'}</span>`,
    `<span class="off">设备（没插）</span>`,
  ];
  if (S.home.data) parts.push(`<span class="off">乐鑫 ESP-IDF ${esc(S.home.data.project.idf)} 或更新（还没查）</span>`);
  return { html: `<div class="lights">${parts.join('<span class="sep">|</span>')}</div>`, llmOK };
}

const PAGES = {
  home: () => {
    const H = S.home, D = H.data, L = lights(), F = flow();
    const missing = [];
    if (!D) missing.push('选固件源码');
    if (!H.ask.trim()) missing.push('写要测从什么到什么');
    if (!L.llmOK) missing.push('大模型没配好（去设置）');
    const going = F && !F.finished ? `<section class="panel"><div class="acts" style="justify-content:space-between;align-items:center"><span>接着刚才的测量：<b>${esc(S.demo.project.name)}</b>，做到「${esc(F.steps.find(s => s.key === F.current).name)}」</span><button class="btn" data-act="resume">接着做</button></div></section>` : '';
    return `<div class="stack">${going}
    <section class="panel"><h1 style="font-size:22px">开始一次测量</h1>${L.html}
      <div class="steps" style="margin-top:14px">
        <div><div class="stepn">第 1 步</div><h2>选固件源码</h2>
        ${D ? `<dl class="kv"><dt>项目</dt><dd>${esc(D.project.name)}</dd><dt>认出来的</dt><dd>${esc(D.project.chip)}；要乐鑫 ESP-IDF ${esc(D.project.idf)}</dd><dt>源码</dt><dd>${esc(D.project.pick.dir)}</dd></dl><div class="acts" style="margin-top:8px"><button class="pill" data-act="unpick">换一个</button></div>`
          : `<div class="drop"><span>把固件源码文件夹拖到这里，也可以选文件夹、选压缩包、粘贴路径</span><span class="note">这一版还不能读真源码，先用演示数据走一遍：</span><div class="acts" style="justify-content:center">${S.demos.map(d => `<button class="btn" data-act="pick" data-demo="${d.id}">演示：${esc(d.label)}</button>`).join('')}</div></div>`}</div>
        <div><div class="stepn">第 2 步</div><h2>要测从什么到什么</h2>
          <input type="text" id="ask" style="width:100%" placeholder="比如：从说完话到开口" value="${esc(H.ask)}">
          ${D ? `<div class="pills" style="margin-top:8px">${D.ask_examples.map(a => `<button class="pill" data-act="fill" data-text="${esc(a)}">${esc(a)}</button>`).join('')}</div>` : ''}
        </div>
      </div>
      ${pri('开始找关键步骤', 'confirm', missing)}
    </section>
    <section class="panel"><h2>以前测过的</h2><div class="list">${S.demos.map(d => `<div class="lrow"><span style="flex:1;padding:10px 2px">${esc(d.label)}演示项目</span><button class="pill" data-act="retest" data-demo="${d.id}">改完再测</button></div>`).join('')}</div>
      <p class="note">演示数据。真项目做完测量后会列在这里，还原成原版也在这里。</p></section></div>`;
  },

  settings: () => {
    const st = S.state.settings, p = S.ui.set_p !== undefined ? S.ui.set_p : st.llm_provider;
    const needKey = p === 'claude-api' || p === 'deepseek';
    const hasKey = needKey && st.has_key[p];
    const opt = (v, t) => `<label><input type="radio" name="llm" value="${v}" ${p === v ? 'checked' : ''} data-act="setp"> ${t}</label>`;
    return `<div class="stack">
    <section class="panel" id="set-llm"><h2>大模型（找关键步骤用）</h2><div class="radio">
      ${opt('claude-api', 'Claude（填 API 密钥）')}${opt('claude-code', 'Claude（本机已登录的 Claude Code，不用密钥）')}${opt('deepseek', 'DeepSeek（填密钥）')}</div>
      ${p === 'deepseek' ? '<p class="lack">DeepSeek 识别准确率较低，请仔细核对关键步骤。</p>' : ''}
      ${needKey ? `<div class="field" style="margin-top:10px"><label for="key">密钥${hasKey ? '（已经填过，不改就留空）' : ''}</label>
        <div class="inrow"><input type="password" id="key" autocomplete="off"><button class="btn" data-act="testkey">测试密钥</button></div>
        <details ${hasKey ? '' : 'open'}><summary>密钥是什么、去哪拿</summary><p class="note">密钥是大模型网站给你的一串字符，码表用它代你调用大模型读代码，按用量付费。注册账号 → 充值 → 在"API 密钥"页面新建一个 → 复制粘贴到上面。有 Claude 会员和 Claude Code 的，可以选第二项，不用密钥。</p></details></div>` : ''}
    </section>
    <section class="panel" id="set-idf"><h2>乐鑫编译工具</h2><div class="field"><label for="idf">ESP-IDF 位置（空着就自动找；找不到时码表帮你装）</label><input type="text" id="idf" value="${esc(st.idf_path)}"></div></section>
    <section class="panel" id="set-dev"><h2>设备连接</h2><div class="field"><label for="port">调试口（空着就自动找）</label><input type="text" id="port" value="${esc(st.serial_port)}"></div></section>
    <section class="panel"><h2>测量结果放在哪</h2><div class="field"><label for="ddir">文件夹</label><input type="text" id="ddir" value="${esc(st.data_dir)}"></div></section>
    ${pri('保存', 'save', [], true)}</div>`;
  },

  diff: () => {
    const D = S.demo, U = ui('diff'), R = D.retest, done = reviewing('diff');
    if (done) U.phase = 'result';
    if (!U.phase) U.phase = 'ask';
    if (U.phase === 'ask') return `<section class="panel"><h1 style="font-size:22px">改好的代码在哪</h1>
      <div class="field"><label for="newsrc">默认是上次的文件夹；也能拖新文件夹、压缩包进来，或粘贴路径</label><input type="text" id="newsrc" value="${esc(D.project.pick.dir)}"></div>
      ${pri('查一遍', 'diffcheck')}</section>`;
    if (U.phase === 'checking') return `<section class="panel"><h1 style="font-size:22px"><span class="spin"></span> 正在查改了什么</h1><p class="note">查源码、计时点、设备……</p></section>`;
    const skipped = flow().steps.filter(s => R.skip.includes(s.key)).map(s => s.name);
    return `<section class="panel"><h1 style="font-size:22px">查完了：${esc(R.route)}</h1>
      <dl class="kv"><dt>改了什么</dt><dd>${esc(R.what)}</dd><dt>源码</dt><dd>${R.route === '固件没改' ? '和上次一样' : '有改动，计时点都接得上'}</dd><dt>设备</dt><dd>已连接（演示）</dd><dt>可以跳过</dt><dd>${skipped.map(esc).join('、') || '无'}</dd></dl>
      ${done ? '' : pri('下一步', 'diffnext')}</section>`;
  },

  identify: () => {
    const D = S.demo, U = ui('identify'), done = reviewing('identify');
    if (done) U.phase = 'list';
    if (!U.phase) { U.phase = 'reading'; U.sec = 0; startReading(); }
    if (U.phase === 'reading') return `<section class="panel"><h1 style="font-size:22px"><span class="spin"></span> 正在读代码找关键步骤</h1><p class="note">已等 ${U.sec} 秒。后台同时在编原始代码。</p></section>`;
    const steps = D.steps.concat(U.added || []);
    if (U.phase === 'found') return `<section class="panel"><h1 style="font-size:22px">找完了</h1>
      <dl class="kv"><dt>用时</dt><dd>${U.sec} 秒</dd><dt>后台编译</dt><dd>✓ 原始代码已编好（${esc(D.background_build)}）</dd><dt>关键步骤</dt><dd>${steps.length} 个，其中 ${steps.filter(s => s.sure !== '高').length} 个需核对</dd></dl>
      ${pri('下一步', 'idnext')}</section>`;
    U.dropped = U.dropped || [];
    const row = s => {
      const off = U.dropped.includes(s.id);
      return `<div class="steprow ${off ? 'off' : ''}"><div><b>${esc(s.name)}</b> ${s.sure === '高' ? '<span class="chip c-ok">可信度高</span>' : '<span class="chip c-warn">需核对</span>'}<div class="loc">${esc(s.file)}:${esc(s.line)}</div><div class="note">${esc(s.why)}</div>${s.note ? `<div class="lack">拿不准：${esc(s.note)}</div>` : ''}</div>
        <div class="acts" style="align-items:start"><button class="pill" data-act="idedit" data-id="${s.id}">修改位置</button><button class="pill" data-act="iddrop" data-id="${s.id}">${off ? '恢复' : '删除'}</button></div></div>`;
    };
    const unsure = steps.filter(s => s.sure !== '高'), sure = steps.filter(s => s.sure === '高');
    const left = steps.filter(s => !U.dropped.includes(s.id)).length;
    return `<div class="stack">
      <section class="panel"><h2>需核对（${unsure.length}）</h2>${unsure.map(row).join('') || '<p class="note">没有</p>'}</section>
      <section class="panel"><details><summary><b>可信度高（${sure.length}）</b>，默认都测</summary>${sure.map(row).join('')}</details></section>
      <section class="panel"><h2>顺带发现的另一条线</h2><p style="margin:0">${esc(D.side_line.found)}。${esc(D.side_line.why)}</p>
        <p class="note">你要测的：${esc(D.side_line.main)}；推荐也测：${esc(D.side_line.side)}</p>
        <button class="pill" data-act="idside">${U.sideOff ? '恢复测这条' : '这条不测'}</button></section>
      <section class="panel"><h2>添加步骤</h2><div class="inrow"><input type="text" id="addstep" placeholder="用一句话说要加什么，AI 去找位置"><button class="btn" data-act="idadd">添加</button></div></section>
      ${done ? '' : pri('确认，插入计时点', 'idok', left ? [] : ['至少留一个步骤'])}</div>`;
  },

  probes: () => {
    const D = S.demo, U = ui('probes'), st = stepOf('probes'), done = reviewing('probes');
    if (done) U.phase = 'ok';
    if (!U.phase) { U.phase = 'inserting'; setTimeout(() => { if (U.phase === 'inserting') { U.phase = 'review'; render(); } }, 900); }
    const dev = `<p class="note" style="margin:0 0 10px">设备：已连接（演示）</p>`;
    if (st.status === 'error') return `${dev}<div class="errbox"><b>${esc(S.state.flow.error)}</b><p style="margin:6px 0 0">原始代码在找关键步骤时已经编译通过，所以问题出在插进去的这处计时点。撤掉它，其余计时点照常。</p></div>${pri('撤掉这处，重新编译', 'prretry')}`;
    if (U.phase === 'inserting') return `${dev}<section class="panel"><h1 style="font-size:22px"><span class="spin"></span> AI 正在插计时点</h1><p class="note">只加不删，码表逐处核对。</p></section>`;
    if (U.phase === 'review') return `${dev}<section class="panel"><h1 style="font-size:22px">插好了，先看改动</h1><p>${esc(D.changes_summary)}</p>
      <div class="list">${D.changes.map(c => `<div class="lrow"><span style="flex:1;padding:8px 2px" class="num">${esc(c.file)}</span><span class="chip c-est">${esc(c.tag)}</span></div>`).join('')}</div>
      <div class="acts" style="margin-top:10px"><button class="btn" data-act="prredo">重新插入</button></div>
      ${pri('确认改动，编译并烧录', 'prbuild')}</section>`;
    if (U.phase === 'building') return `${dev}<section class="panel"><h1 style="font-size:22px">${U.pct < 100 ? '正在编译（只重编改过的文件）' : '正在烧录'}</h1>
      <div class="progress"><i style="width:${U.pct < 100 ? U.pct : U.flash}%"></i></div><p class="note num">${U.pct < 100 ? esc(D.build_line.replace('{n}', Math.round(U.pct * 16))) : `烧录 ${U.flash}%`}</p></section>`;
    if (U.phase === 'try') return `${dev}<div class="bigask"><b>${esc(D.trigger.big)}</b><span class="note">${esc(D.trigger.note)} 这一次只是试，确认计时点有输出，不算数据。</span><span>收到 <b class="num">${U.got || 0}</b> 个计时点</span></div><div class="log" id="trylog">${esc((U.log || []).join('\n'))}</div>`;
    return `${dev}<div class="okbar">✓ 计时版已烧进设备，试的那一次 ${U.got || 0} 个计时点都收到了</div>${done ? '' : '<p class="note">马上进入多轮实测……</p>'}`;
  },

  measure: () => {
    const D = S.demo, U = ui('measure'), done = reviewing('measure');
    if (done) U.phase = 'done';
    if (!U.phase) Object.assign(U, { phase: 'prep', rounds: 20, beep: true, got: [] });
    const squares = () => `<div class="rsq">${Array.from({ length: U.rounds }, (_, i) => `<i class="${U.got[i] || ''}" title="第 ${i + 1} 轮"></i>`).join('')}</div><p class="note">一格一轮。绿：这轮没问题；黄、红：这轮有一段超出合理范围或偏慢；灰：还没测。</p>`;
    if (U.phase === 'prep') return `<section class="panel"><h1 style="font-size:22px">准备多轮实测</h1>
      <dl class="kv"><dt>谁来做</dt><dd>由你${esc(D.trigger.short)}，码表只提示和计时</dd>
      <dt>测几轮</dt><dd><input type="text" id="rounds" value="${U.rounds}" style="width:6em"> 轮</dd>
      <dt>清单</dt><dd>码表给的固定清单，改完重测用同一份</dd>
      <dt>提示音</dt><dd><label><input type="checkbox" id="beep" ${U.beep ? 'checked' : ''}> 收好一轮"叮"一声</label></dd></dl>
      ${pri('开始实测', 'msstart')}</section>`;
    if (U.phase === 'run' || U.phase === 'stopped') {
      const k = U.got.length;
      return `<section class="panel">${U.phase === 'run' ? `<div class="bigask"><b>第 ${k + 1} 轮：${esc(D.trigger.big)}</b></div>` : `<h1 style="font-size:22px">已停止，测了 ${k} 轮</h1>`}
        ${squares()}
        ${U.phase === 'run' ? '<div class="acts"><button class="btn" data-act="msstop">停止实测</button></div>'
          : `${pri(`接着测（从第 ${k + 1} 轮）`, 'msgoon')}<div class="acts" style="margin-top:8px"><button class="btn" data-act="msreport" ${k >= 10 ? '' : 'disabled'}>${flow().kind === 'retest' ? '查看前后对比' : '查看报告'}</button>${k >= 10 ? '' : '<span class="lack">还差：至少测 10 轮</span>'}</div>`}</section>`;
    }
    const L = D.lines.map(l => { const t = lineTotals(D, l).slice(0, U.got.length); const half = t.slice(0, Math.ceil(t.length / 2)); return Math.abs(median(half) - median(t)) < 0.05; });
    const bad = U.got.filter(c => c !== 'g').length;
    const nextName = flow().kind === 'retest' ? '查看前后对比' : '查看报告';
    const few = bad >= 1 && bad <= 3 && U.got.length < 30;  // 偶尔出的问题只看到 1～3 次，测满 30 轮不再催
    return `<section class="panel"><h1 style="font-size:22px">测完了，${U.got.length} 轮</h1>${squares()}
      <h2>测的够不够</h2><p style="margin:0">${L.every(x => x) ? '一般用时稳了' : '一般用时还没稳'}；有问题的轮看到 ${bad} 次${bad && bad <= 3 ? '，还偏少' : ''}。</p>
      ${done ? '' : few ? `${pri('再测 10 轮（建议）', 'msmore')}<div class="acts" style="margin-top:8px"><button class="btn" data-act="msreport">${nextName}</button></div>` : pri(nextName, 'msreport')}</section>`;
  },

  report: () => {
    const D = S.demo, U = ui('report'), tab = U.tab || 'a';
    const tabs = `<div class="seg" role="tablist"><button aria-pressed="${tab === 'a'}" data-act="rtab" data-tab="a">结论建议</button><button aria-pressed="${tab === 'b'}" data-act="rtab" data-tab="b">技术细节</button></div>`;
    const stats = D.lines.map(l => { const t = lineTotals(D, l); return { l, med: median(t), p90: p90(t), max: Math.max(...t) }; });
    const head = `${esc(D.start.short)} ` + stats.map(s => `${f2(s.med)} 秒${esc(s.l.phrase)}`).join('，');
    const bottom = `<section class="panel"><div class="acts" style="justify-content:space-between;align-items:center"><span>设备上现在是：<b>计时版</b></span><span class="acts"><button class="btn" data-act="restore">还原成原版</button><button class="btn" data-act="retest" data-demo="${D.id}">改完再测</button></span></div></section>`;
    const done = reviewing('report') || flow().finished;
    const finish = done ? '' : pri('完成，回首页', 'rpdone');
    if (tab === 'b') return `<div class="stack">${tabs}
      <section class="panel"><h2>测量条件</h2><dl class="kv">${D.tech.conditions.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></section>
      <section class="panel scroll"><h2>分段用时</h2><table class="num"><thead><tr><th>段</th><th>谁负责</th><th class="r">一般</th><th>合理范围</th><th>代码位置</th></tr></thead><tbody>
      ${Object.entries(D.segments).map(([k, s]) => `<tr><td>${esc(s.name)}</td><td>${esc(s.owner)}</td><td class="r">${f2(median(s.values))}</td><td>${s.range.join('～')}</td><td>${esc(D.tech.locations[k] || '')}</td></tr>`).join('')}</tbody></table></section>
      <section class="panel"><h2>原始日志</h2><button class="btn" data-act="openlog">打开所在文件夹</button></section>${bottom}${finish}</div>`;
    return `<div class="stack">${tabs}
      <section class="panel"><h1>${head}</h1>${D.experience ? `<p class="note">体验标准：${esc(D.experience.label)}</p>` : ''}${timeline(D)}</section>
      <section class="panel"><h2>要你拍板</h2><ul class="plain">${D.fixes.map(x => `<li><b>${esc(D.segments[x.segment].name)}</b>：${esc(x.impact)}<br><span class="note">建议：${esc(x.rec)}</span></li>`).join('')}</ul></section>
      <section class="panel"><h2>依据</h2><table class="num"><thead><tr><th>线</th><th class="r">一般</th><th class="r">10 次最慢</th><th class="r">最差</th><th>判定</th></tr></thead><tbody>
      ${stats.map(s => { const [t, c] = judge(s.med, s.l.range, s.l.slow); return `<tr><td>${esc(s.l.name)}</td><td class="r">${f2(s.med)}</td><td class="r">${f2(s.p90)}</td><td class="r">${f2(s.max)}</td><td><span class="chip ${c}">${t}</span></td></tr>`; }).join('')}</tbody></table>
      <p class="note">${esc(D.fine)}</p></section>${bottom}${finish}</div>`;
  },

  compare: () => {
    const D = S.demo, R = D.retest, done = reviewing('compare') || flow().finished;
    const after = (k, v) => v * (R.factor[k] || 1);
    const rows = D.lines.map(l => {
      const before = median(lineTotals(D, l));
      const now = median(D.segments[l.segments[0]].values.map((_, r) => sum(l.segments.map(k => after(k, D.segments[k].values[r])))));
      const d = now - before, flat = Math.abs(d) < 0.03 || Math.abs(d) / before < 0.05;
      const a = flat ? ['→ 持平', 'arrow-flat'] : d < 0 ? ['↓ 快了', 'arrow-down'] : ['↑ 慢了', 'arrow-up'];
      return `<tr><td>${esc(l.name)}</td><td class="r">${f2(before)}</td><td class="r">${f2(now)}</td><td class="${a[1]}">${a[0]} ${flat ? '' : f2(Math.abs(d)) + ' 秒'}</td></tr>`;
    });
    return `<div class="stack"><section class="panel"><h1 style="font-size:22px">前后对比</h1><p class="note">这次改了什么：${esc(R.what)}</p>
      <table class="num"><thead><tr><th>线</th><th class="r">改前一般</th><th class="r">改后一般</th><th>变化</th></tr></thead><tbody>${rows.join('')}</tbody></table>
      <p class="note">变化小于 0.03 秒或 5% 算持平，不报假进步。条件一样才比：同一份清单、同样的计时点、同一份合理范围。</p></section>
      ${done ? '' : pri('完成，回首页', 'cmpdone')}</div>`;
  },
};

// 计时图：画最接近中位数的那一轮，几条线从同一个起点出发
function timeline(D) {
  const tot = D.lines.map(l => lineTotals(D, l)), meds = tot.map(median);
  let best = 0, bd = Infinity;
  tot[0].forEach((_, r) => { const d = sum(tot.map((t, i) => Math.abs(t[r] - meds[i]))); if (d < bd) { bd = d; best = r; } });
  const T = Math.max(...tot.map(t => t[best])) * 1.15, W = 900, X0 = 150, X1 = 860, sx = t => X0 + (X1 - X0) * t / T, gap = 70, H = 40 + D.lines.length * gap;
  const cols = ['var(--face)', 'var(--speak)', 'var(--warn)', 'var(--muted)'];
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="计时图"><line x1="${X0}" y1="20" x2="${X0}" y2="${H - 10}" stroke="var(--line)"/><text x="${X0}" y="14" font-size="13" fill="var(--muted)" text-anchor="middle">${esc(D.start.short)}</text>`;
  D.lines.forEach((l, i) => {
    let t = 0; const y = 40 + i * gap;
    svg += `<text x="${X0 - 10}" y="${y + 15}" font-size="14" text-anchor="end" fill="var(--fg)">${esc(l.name)}</text>`;
    l.segments.forEach((k, j) => {
      const v = D.segments[k].values[best], x = sx(t), w = Math.max(2, sx(t + v) - x);
      svg += `<rect x="${x}" y="${y}" width="${w}" height="22" fill="${cols[i]}" opacity="${j % 2 ? 0.55 : 0.9}"><title>${esc(D.segments[k].name)} ${f2(v)} 秒</title></rect>`;
      t += v;
    });
    svg += `<text x="${sx(t) + 6}" y="${y + 15}" font-size="13" fill="var(--fg)">${esc(l.end)} ${f2(t)} 秒</text>`;
  });
  return `${svg}</svg><p class="note">画的是真实的一轮：第 ${best + 1} 轮，各条线都最接近一般用时。</p>`;
}

// ===== 长任务（演示用的假进度） =====
async function startReading() {
  const U = ui('identify');
  for (let i = 0; i < 8; i++) {
    await sleep(250);
    if (U.phase !== 'reading') return;
    U.sec = (i + 1) * 2;
    if (S.state.flow && S.state.flow.viewing === 'identify') render();
  }
  U.phase = 'found'; render();
}

async function runBuild() {
  const U = ui('probes');
  Object.assign(U, { phase: 'building', pct: 0, flash: 0 });
  for (U.pct = 0; U.pct < 100; U.pct += 10) {
    render(); await sleep(120);
    if (failDemo === 'probes' && !U.failedOnce && U.pct >= 60) {
      U.failedOnce = true; U.phase = 'review';
      await flowDo('fail', 'probes', { message: `编译没通过：${S.demo.changes[1] ? S.demo.changes[1].file : S.demo.changes[0].file} 里插的计时点引起的` });
      return;
    }
  }
  for (U.flash = 0; U.flash <= 100; U.flash += 20) { render(); await sleep(100); }
  U.phase = 'try'; U.got = 0; U.log = []; render();
  const es = new EventSource(`/api/device/stream?demo=${S.demo.id}&rounds=1&interval=0.08`);
  es.onmessage = async e => {
    if (e.data === 'END') {
      es.close(); U.phase = 'ok'; render();
      await sleep(1500);
      if (stepOf('probes').status === 'doing') await flowDo('complete', 'probes');
      return;
    }
    U.log.push(e.data);
    if (/^FSW,P/.test(e.data)) U.got++;
    render();
  };
}

function runMeasure() {
  const U = ui('measure');
  U.phase = 'run'; render();
  const from = U.got.length;
  const es = new EventSource(`/api/device/stream?demo=${S.demo.id}&rounds=${U.rounds - from}&start=${from}&interval=0.015`);
  U.es = es;
  let inRound = false;
  es.onmessage = e => {
    if (e.data === 'FSW,RESET' || e.data === 'END') {
      if (inRound) U.got.push(roundColor(S.demo, U.got.length % 20));
      inRound = true;
      if (e.data === 'END') { es.close(); U.phase = 'done'; }
      render();
    }
  };
}

// ===== 按钮 =====
const ACTS = {
  view: el => flowDo('view', el.dataset.step),
  resume: () => go('flow'),
  pick: async el => { S.home.demo = el.dataset.demo; S.home.data = await loadDemo(el.dataset.demo); render(); },
  unpick: () => { S.home.demo = null; S.home.data = null; render(); },
  fill: el => { S.home.ask = el.dataset.text; render(); },
  confirm: () => {
    const D = S.home.data;
    $('#modal').innerHTML = `<div class="modal"><div class="dlg"><h2>开始找关键步骤</h2>
      <div class="field"><label for="pname">项目名称（可改）</label><input type="text" id="pname" value="${esc(D.project.name)}"></div>
      <p class="note">固件源码位置：${esc(D.project.pick.dir)}<br>要测：${esc(S.home.ask)}</p>
      <div class="acts" style="justify-content:flex-end"><button class="btn" data-act="close">关闭</button><button class="btn pri" data-act="start">开始找关键步骤</button></div></div></div>`;
  },
  close: () => { $('#modal').innerHTML = ''; },
  start: async () => {
    $('#modal').innerHTML = ''; S.ui = {};
    await setState(await api('/api/flow/start', { kind: 'first', demo: S.home.demo }));
    go('flow');
  },
  retest: async el => {
    S.ui = {};
    await setState(await api('/api/flow/start', { kind: 'retest', demo: el.dataset.demo }));
    go('flow');
  },
  setp: el => { S.ui.set_p = el.value; render(); },
  testkey: () => toast('测试密钥要等大模型接上后才能用'),
  save: async () => {
    const v = id => ($('#' + id) || {}).value;
    const p = S.ui.set_p !== undefined ? S.ui.set_p : S.state.settings.llm_provider;
    await setState(await api('/api/settings', { llm_provider: p, key_entered: !!(v('key') || '').trim(), idf_path: v('idf'), serial_port: v('port'), data_dir: v('ddir') }));
    delete S.ui.set_p;
    toast('保存成功'); go(S.back);
  },
  diffcheck: async () => { ui('diff').phase = 'checking'; render(); await sleep(900); ui('diff').phase = 'result'; render(); },
  diffnext: () => flowDo('complete', 'diff', { skip: S.demo.retest.skip }),
  idnext: () => { ui('identify').phase = 'list'; render(); },
  iddrop: el => mutate('identify', () => { const U = ui('identify'), id = el.dataset.id; U.dropped = U.dropped.includes(id) ? U.dropped.filter(x => x !== id) : [...U.dropped, id]; }),
  idedit: el => mutate('identify', () => toast(`演示：「${S.demo.steps.concat(ui('identify').added || []).find(s => s.id === el.dataset.id).name}」的位置改好了`)),
  idside: () => mutate('identify', () => { ui('identify').sideOff = !ui('identify').sideOff; }),
  idadd: () => {
    const t = ($('#addstep').value || '').trim();
    if (!t) return toast('先写一句要加什么');
    mutate('identify', () => { const U = ui('identify'); U.added = U.added || []; U.added.push({ id: 'A' + (U.added.length + 1), name: t, file: '（AI 找到的位置）', line: '?', why: '你加的步骤', sure: '中', note: '位置是 AI 按你那句话找的，请核对' }); });
  },
  idok: () => flowDo('complete', 'identify'),
  prredo: () => mutate('probes', () => { ui('probes').phase = undefined; }),
  prbuild: () => runBuild(),
  prretry: async () => { await flowDo('retry', 'probes'); runBuild(); },
  msstart: () => { const U = ui('measure'); U.rounds = Math.max(1, parseInt($('#rounds').value, 10) || 20); U.beep = $('#beep').checked; runMeasure(); },
  msstop: () => { const U = ui('measure'); if (U.es) U.es.close(); U.phase = 'stopped'; render(); },
  msgoon: () => runMeasure(),
  msmore: () => { ui('measure').rounds += 10; runMeasure(); },
  msreport: () => flowDo('complete', 'measure'),
  rtab: el => { ui('report').tab = el.dataset.tab; render(); },
  restore: () => toast('还原成原版：烧后台编好的原版并逐字节比对（后面接上）'),
  openlog: () => toast('打开原始日志所在文件夹（后面接上）'),
  rpdone: async () => { await flowDo('complete', 'report'); go('home'); },
  cmpdone: async () => { await flowDo('complete', 'compare'); go('home'); },
  back: () => flowDo('view', flow().current),
};

document.addEventListener('click', e => {
  const nav = e.target.closest('[data-nav]');
  if (nav) {
    if (nav.dataset.nav === 'settings' && S.page !== 'settings') S.back = S.page;
    if (nav.dataset.nav === 'home' && flow() && !flow().finished) S.back = 'home';
    return go(nav.dataset.nav);
  }
  const el = e.target.closest('[data-act]');
  if (el && !el.disabled && ACTS[el.dataset.act]) {
    if (el.type === 'radio') return ACTS[el.dataset.act](el);
    e.preventDefault(); ACTS[el.dataset.act](el);
  }
});
// 输入要测什么：只在"空 ↔ 有字"变化、且输入法打完字时才重画，免得打断中文输入
function onAsk(e) {
  if (e.target.id !== 'ask' || e.isComposing) return;
  const was = !!S.home.ask.trim();
  S.home.ask = e.target.value;
  if (was !== !!S.home.ask.trim()) { render(); const a = $('#ask'); a.focus(); a.setSelectionRange(a.value.length, a.value.length); }
}
document.addEventListener('input', onAsk);
document.addEventListener('compositionend', onAsk);

// ===== 画页面 =====
function render() {
  if (!S.state) return;
  if (S.page === 'flow' && !flow()) S.page = 'home';
  renderChrome();
  let html;
  if (S.page === 'flow') {
    const F = flow(), key = F.viewing;
    const look = reviewing(key) && F.current ? `<div class="review"><span>这一步已经做完，现在是回看。改了这里的东西，后面的步骤要重新做。</span><button class="btn pri" data-act="back">回到做到这里</button></div>` : '';
    html = look + PAGES[key]();
  } else html = PAGES[S.page]();
  $('#view').innerHTML = html;
}

(async function init() {
  S.demos = await api('/api/demo');
  const h = location.hash.slice(1);
  if (['home', 'settings', 'flow'].includes(h)) S.page = h;
  await setState(await api('/api/state'));
})();
