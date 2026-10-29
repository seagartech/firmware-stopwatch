"""Report presentation rules: no device names, fixed result counts or measurement values."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'fsw/web/static'
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='没有 node')


def render(code):
    result = subprocess.run(['node', '-e', 'const C=require('+json.dumps(str(STATIC/'report-layout.js'))+');'+BASE+code], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


BASE = r'''
const data={n:2,measured_at:'test',scenario:{start:{point:'begin',name:'刷卡'},point_names:{begin:'刷卡',ready:'读取卡片',accepted:'校验通过',light:'亮绿灯',open:'门开始打开'},segments:{read:{from:'begin',to:'ready',name:'读取'},verify:{from:'ready',to:'accepted',name:'校验'},lamp:{from:'accepted',to:'light',name:'提示'},motor:{from:'accepted',to:'open',name:'开锁'}},lines:[{name:'提示',segments:['read','verify','lamp'],first:true},{name:'开锁',segments:['read','verify','motor']}]},segments:[{k:'read',med:.1,max:.2,count:2},{k:'verify',med:.2,max:.3,count:2},{k:'lamp',med:.05,max:.1,count:2},{k:'motor',med:.3,max:.5,count:2}],lines:[{name:'提示',med:.35,max:.5},{name:'开锁',med:.6,max:.9}],rounds:[{n:1,segs:{read:.1,verify:.2,lamp:.05,motor:.3},ends:[.35,.6]},{n:2,segs:{read:.2,verify:.3,lamp:0,motor:.4},ends:[.5,.9]}],code:{ready:{path:'src/reader.cc',l:8,func:'read',ctx:[[7,'before();'],[8,'read_card();'],[9,'after();']]}}};
'''


def test_gate_prefix_is_based_on_directed_identity_not_names_or_durations():
    out = render("const m=C.model(data);console.log(JSON.stringify(C.sharedPrefix(m)));")
    assert out['keys'] == ['read','verify']
    assert list(out['tails'].values()) == [['lamp'],['motor']]


@pytest.mark.parametrize('mutation', [
    "data.scenario.segments.verify.from='elsewhere'",
    "data.scenario.segments.verify.to='begin'",
    "data.scenario.lines[1].start_point='different'",
    "data.scenario.lines[1].end_point='wrong'",
])
def test_disconnected_cycles_and_endpoint_mismatch_are_not_grouped(mutation):
    assert render(mutation+";console.log(JSON.stringify(C.sharedPrefix(C.model(data)).keys));") == []


def test_same_name_and_value_are_not_proof_of_shared_steps():
    assert render("data.scenario.segments.other={...data.scenario.segments.read};data.scenario.lines[1].segments[0]='other';console.log(JSON.stringify(C.sharedPrefix(C.model(data)).keys));") == []


def test_ambiguous_boundary_stops_the_common_prefix():
    assert render("data.scenario.segments.verify.to_any=['accepted','alternative'];console.log(JSON.stringify(C.sharedPrefix(C.model(data)).keys));") == ['read']


def test_single_all_shared_and_zero_results_have_meaningful_states():
    out=render("const a=C.sharedPrefix(C.model({...data,scenario:{...data.scenario,lines:[]}}));data.scenario.lines[1].segments=['read','verify','lamp'];const b=C.sharedPrefix(C.model(data));data.scenario.lines.pop();const c=C.sharedPrefix(C.model(data));console.log(JSON.stringify([a,b,c]));")
    assert out[0]['keys']==[] and out[0]['note']
    assert out[1]['keys']==['read','verify','lamp'] and all(not x for x in out[1]['tails'].values())
    assert out[2]['status']=='single'


def test_rounds_use_original_precision_and_refuse_missing_negative_or_wrong_totals():
    out=render("const m=C.model(data),l=m.lines[0],r=data.rounds[0];const good=C.pathInfo(m,l,r);const bad=[{...r,ends:[.9,.6]},{...r,segs:{...r.segs,read:null}},{...r,segs:{...r.segs,read:-.1}}].map(r=>C.pathInfo(m,l,r).valid);console.log(JSON.stringify([good,bad,C.roundHtml(m,0,1)]));")
    assert out[0]['valid'] and out[0]['total']==pytest.approx(.35)
    assert out[1]==[False,False,False]
    assert '0.350000' in out[2]


def test_plot_encodes_time_on_x_and_one_row_per_round_without_connecting_rounds():
    out=render("console.log(JSON.stringify(C.roundsOverview(C.model(data))));")
    assert out.count('data-round=')==4 and '<path' not in out and '<polyline' not in out
    assert 'data-value="0.350000"' in out and '细虚线：平均用时' in out


def test_audience_filter_does_not_drop_internal_values_or_content_dependent_results():
    out=render("data.scenario.lines[1].actual=true;data.scenario.lines[1].was='开门显示';const g=C.model(data,'general'),t=C.model(data);console.log(JSON.stringify([g.visibleLines.length,t.visibleLines.length,C.valuesTable(data),C.extra(data)]));")
    assert out[:2]==[1,2]
    assert '0.900000' in out[2] and 'src/reader.cc' in out[3]


def test_technical_table_keeps_both_endpoints_context_and_comparison_without_scroll_wrapper():
    out=render("data.prev={measured_at:'earlier',segs:{read:.12}};data.segments[0].vs={change:'快了'};console.log(JSON.stringify(C.technicalTable(C.model(data),['read'])));")
    assert '<table class="technical-table">' in out and '<div class="scroll">' not in out
    assert '<td colspan="5">' in out and 'before();' in out and 'after();' in out
    assert '0.12 秒' in out and 'earlier' in out and '[object Object]' not in out


def test_card_count_and_axes_follow_new_data():
    out=render("data.scenario.lines.push({...data.scenario.lines[0],name:'备用提示'});data.lines.push({name:'备用提示',med:60,max:75});data.rounds.forEach(r=>r.ends.push(60));console.log(JSON.stringify([C.resultOptions(C.model(data)).length,C.summarySvg(C.model(data)),C.panel(data)]));")
    assert out[0]==3 and '60.00 秒' in out[1] and out[2].count('data-line=')==3
    assert 'result-choices' in out[2] and 'data-role="round-select"' in out[2]


def test_payload_and_code_are_escaped_and_vocabulary_does_not_change_input():
    out=render("data.scenario.point_names.light='<script>UDP 报文</script>';const before=JSON.stringify(data),html=C.panel(data);console.log(JSON.stringify([before===JSON.stringify(data),html,C.model(data,'general').names.light]));")
    assert out[0] and '\\u003cscript>' in out[1] and '&lt;script&gt;' in out[1]
    assert out[2]=='<script>数据消息</script>'


def test_gate_measured_result_is_not_hidden_as_if_it_were_variable_length_speech():
    out=render("data.scenario.lines[1].measure=true;const m=C.model(data,'general');console.log(JSON.stringify([m.main.index,C.summarySvg(m),C.roundsOverview(m),C.roundsOverview(m,{complete:true})]));")
    assert out[0]==0  # Explicit first response still wins.
    assert '门开始打开' in out[1] and out[2].count('data-round=')==4
    assert '没有逐次用时记录' in out[3]
    out=render("delete data.scenario.lines[0].first;data.scenario.lines[1].measure=true;console.log(JSON.stringify(C.model(data).main.index));")
    assert out==1


def test_record_only_explanation_keeps_all_values_without_internal_owner_columns():
    out=render("console.log(JSON.stringify(C.recordOnly(data,{lines:['提示'],segments:['read'],parallel:['motor']})));")
    for value in ('0.42 秒','0.50 秒','0.15 秒','0.35 秒'):
        assert value in out
    assert '只记录、不判快慢' in out and '独立步骤' in out and '谁负责' not in out


def test_exported_controls_restore_saved_result_and_round_before_first_interaction():
    out=render(r'''
const listeners={},parts={},buttons=[0,1].map(i=>({dataset:{line:String(i)},disabled:false,active:i===1,setAttribute(k,v){if(k==='aria-pressed')this.active=v==='true'},querySelector(){return {textContent:''}},closest(){return box}}));
const select={value:'2',closest(q){return q==='.report-inspect'?box:q==='[data-role="round-select"]'?select:null}};
const box={dataset:{audience:'technical'},querySelector(q){if(q==='[data-role="report-data"]')return {textContent:JSON.stringify(data)};if(q==='[data-line][aria-pressed="true"]')return buttons.find(b=>b.active);if(q==='[data-role="round-select"]')return select;return parts[q]||(parts[q]={innerHTML:'',textContent:''})},querySelectorAll(){return buttons}};
global.document={addEventListener(k,f){listeners[k]=f}};C.install(()=>{});
listeners.change({target:select});const first=parts['[data-role="round-content"]'].innerHTML;
listeners.click({target:{closest(q){return q==='[data-line]'?buttons[0]:null}}});
console.log(JSON.stringify([first,parts['[data-role="round-content"]'].innerHTML,parts['[data-role="cost-title"]'].textContent,parts['[data-role="total-scope"]'].textContent]));
''')
    assert '首尾实测 0.90 秒' in out[0] and '第 2 次' in out[0]
    assert '首尾实测 0.50 秒' in out[1] and '第 2 次' in out[1]
    assert out[2]=='亮绿灯｜首尾用时 · 平均 0.42 秒'
    assert out[3]=='总用时范围：「刷卡」→「亮绿灯」。'


def test_mean_reference_uses_raw_rounds_not_median_or_passing_proportion():
    out=render("data.lines[0].exp_std={fair:1,source:'平均 ≤1 秒'};data.lines[0].med=.2;data.rounds[0].ends[0]=.2;data.rounds[1].ends[0]=2.2;const m=C.model(data);console.log(JSON.stringify([C.referenceInfo(m,m.lines[0]),C.referenceText(m,m.lines[0]),C.comparison(m)]));")
    assert out[0]['avg']==pytest.approx(1.2)
    assert out[0]['over']==1
    assert '平均 1.20 秒，超过 1.00 秒' in out[1]
    assert '1/2 次（50%）' in out[2] and '单次观察' in out[2] and '达标率' not in out[2]


@pytest.mark.parametrize('source', ['每次响应不超过 1 秒', '第 75 百分位不超过 1 秒', '不应使用平均值判断', ''])
def test_unknown_statistical_basis_does_not_invent_an_average_requirement(source):
    out=render('data.lines[0].exp_std={fair:1,source:'+json.dumps(source)+'};const m=C.model(data);console.log(JSON.stringify(C.referenceText(m,m.lines[0])));')
    assert '统计口径尚未明确匹配' in out and '本次平均' not in out


@pytest.mark.parametrize('flag', ['bad', 'thin'])
def test_incomplete_or_bad_records_do_not_produce_a_reference_verdict(flag):
    out=render("data.lines[0].exp_std={fair:1,statistic:'mean'};data.lines[0]["+json.dumps(flag)+"]=true;const m=C.model(data);console.log(JSON.stringify(C.referenceText(m,m.lines[0])));")
    assert '暂不比较快慢' in out


def test_common_anomaly_is_visible_before_disclosure_and_round_is_not_hardcoded():
    out=render("data.segments[1].spiky=true;data.segments[1].spiky_rounds=[17];data.rounds[1].n=17;const m=C.model(data);console.log(JSON.stringify([C.sharedHtml(m,C.sharedPrefix(m)),C.roundsOverview(m)]));")
    assert out[0].index('第 17 次') < out[0].index('<details')
    assert '读取卡片 → 校验通过' in out[0] and '最慢 0.30 秒' in out[0]
    assert 'data-abnormal-round="17"' in out[1]


def test_findings_do_not_repeat_advice_status_or_add_normal_state_disclaimers():
    out=render("const m=C.model(data);console.log(JSON.stringify(['failed','running','done',null].map(s=>C.findings(m,s))));")
    assert len(set(out))==1
    assert all('修改建议' not in s and '需要留意' not in s and '不等于' not in s for s in out)


def test_reference_and_anomaly_views_preserve_raw_payload_and_technical_names():
    out=render("data.scenario.point_names.ready='语音活动上报';const before=JSON.stringify(data),g=C.model(data,'general'),t=C.model(data);C.findings(g,'failed');C.comparison(g);console.log(JSON.stringify([JSON.stringify(data)===before,g.names.ready,t.names.ready,C.recordOnly(data,{lines:['提示'],segments:[],parallel:[]})]));")
    assert out[:3]==[True,'程序发出说话状态通知','语音活动上报']
    assert '<details><summary>查看完整过程及独立步骤的全部统计</summary>' in out[3]
    assert '0.42 秒' in out[3]


def test_result_alert_and_advice_entry_survive_without_an_all_clear_fallback():
    source=(STATIC/'app.js').read_text()
    function=source[source.index('function readableActions('):source.index('function readableSources(')]
    stubs="const activeFixes=()=>[{title:'方案'}],cardOf=(f,x)=>x.k==='line:开锁'?0:-1,recLine=()=> '已有建议',jump=(id,label)=>'<a href=\"#'+id+'\">'+label+'</a>';"
    code="const ReportLayout=C;"+stubs+"const decisions=()=>[{k:'main',lv:0,t:'过慢',d:'旧判断',src:['tiers','标准']},{k:'line:开锁',lv:1,t:'开锁',src:['tiers','标准']},{k:'problem',lv:4,t:'动作异常',d:'仍需处理',src:['rounds','记录']}];"+function+"const m=C.model(data);console.log(JSON.stringify(readableActions({},m)));"
    out=render(code)
    assert '亮绿灯' not in out and '动作异常' in out and '仍需处理' in out
    assert '已有建议' in out and '#fix-0' in out and '#sec-tiers' in out
    assert '没有需要' not in out


def test_role_plots_keep_identical_points_but_general_explains_observed_range():
    out=render("data.lines[0].exp_std={fair:1,statistic:'mean'};console.log(JSON.stringify(['general','technical'].map(a=>C.roundsOverview(C.model(data,a)))));")
    assert all(x.count('data-value=') == 4 for x in out)
    assert '0.35 秒 ～ 0.50 秒' in out[0] and '第 2 次用时最长' in out[0]
    assert '不代表长期稳定性' not in out[0] and '长虚线：' not in out[0]
    assert '长虚线：' in out[1] and '悬停点位查看原始精度' in out[1]
    assert all('data-value="0.350000"' in x for x in out)


def test_range_handles_ties_missing_and_one_record_without_inventing_stability():
    out=render("data.rounds[0].ends=[null,-1];data.rounds[1].ends=[.5,.5];console.log(JSON.stringify(C.roundsOverview(C.model(data,'general'))));")
    assert out.count('data-value=') == 2 and '1 次记录，0.50 秒 ～ 0.50 秒' in out
    assert '次用时最长' not in out


def test_focus_uses_segment_identity_and_valid_values_and_keeps_ties():
    out=render("data.rounds.forEach(r=>r.segs.read=r.segs.verify);const m=C.model(data,'general');const a=C.costFocus(m,['read','verify']);data.segments[1].bad=true;const b=C.costFocus(C.model(data,'general'),['read','verify']);data.segments[0].thin=true;console.log(JSON.stringify([a,b,C.costFocus(C.model(data,'general'),['read','verify'])]));")
    assert '刷卡 → 读取卡片' in out[0] and '读取卡片 → 校验通过' in out[0]
    assert '读取卡片 → 校验通过' not in out[1]
    assert '不能据此确定原因' not in out[0] and '暂不判断' in out[2]


def test_general_fold_keeps_all_steps_and_common_anomalies_visible():
    out=render("data.segments[1].spiky=true;data.segments[1].spiky_rounds=[2];console.log(JSON.stringify(C.panel(data,'general')));")
    assert out.index('第 2 次') < out.index('id="shared-fold"')
    assert '查看这部分全部 1 步的用时' in out
    for name in ('刷卡 → 读取卡片', '读取卡片 → 校验通过', '校验通过 → 亮绿灯'):
        assert name in out


def test_technical_navigation_passes_destination_to_live_and_export_hosts():
    out=render("let callback;global.document={addEventListener(k,f){if(k==='click')callback=f}};let got;C.install(t=>got=t);callback({target:{closest(){return {dataset:{reportTarget:'sec-research-actions'}}}}});console.log(JSON.stringify(got));")
    assert out=='sec-research-actions'


def test_advice_and_next_step_distinguish_incomplete_analysis_and_bad_data():
    source=(STATIC/'app.js').read_text()
    helpers=source[source.index('function generalAdviceStatus('):source.index('function readableReport(')]
    out=render("let ADV={status:'failed'};const activeFixes=()=>[],advBox=()=>'<button data-advretry>重试</button>',fixDev=()=>'';"+helpers+"const D={S:{real:true},J:{checks:[{kind:'bad_line'}]}};console.log(JSON.stringify([generalAdviceStatus(),technicalAdvice(D),reportNextStep(D)]));")
    assert 'data-advretry' not in out[0] and '原因分析未完成' in out[0]
    assert 'data-advretry' in out[1] and '不直接证明根因' in out[1]
    assert '先请研发补齐' in out[2] and 'sec-research-actions' in out[2]


def test_result_total_uses_endpoint_statistic_not_sum_of_segment_medians():
    out=render("data.lines[0].med=.9;const m=C.model(data,'general');console.log(JSON.stringify([C.resultHeading(m,0),C.resultScope(m,0),C.panel(data,'general')]));")
    assert out[0]=='亮绿灯｜完整等待 · 平均 0.42 秒'
    assert out[1]=='总用时范围：「刷卡」→「亮绿灯」。'
    assert '单步平均 0.03 秒' in out[2] and '横条表示每一步的平均用时' in out[2]


def test_total_label_handles_content_dependent_bad_thin_and_missing_data():
    out=render("const results=[];data.lines[0].verdict='看回答长短';results.push(C.resultHeading(C.model(data,'general'),0));delete data.lines[0].verdict;for(const flag of ['bad','thin']){data.lines[0][flag]=true;results.push(C.resultHeading(C.model(data),0));delete data.lines[0][flag];}data.lines[0].med=null;data.rounds.forEach(r=>r.ends[0]=null);results.push(C.resultHeading(C.model(data,'general'),0));console.log(JSON.stringify(results));")
    assert out==['亮绿灯｜完整过程 · 平均 0.42 秒','亮绿灯｜首尾用时 · 平均 计时先后异常','亮绿灯｜首尾用时 · 平均 记录不足','亮绿灯｜完整等待 · 平均 未测到']


def test_both_audiences_compute_same_mean_from_valid_rounds_without_mutation():
    out=render("data.rounds=[{n:1,ends:[0],segs:{read:0}},{n:2,ends:[1],segs:{read:1}},{n:3,ends:[8],segs:{read:8}},{n:4,ends:[null],segs:{read:null}},{n:5,ends:[-1],segs:{read:-1}}];const before=JSON.stringify(data);const ms=['general','technical'].map(a=>C.model(data,a));console.log(JSON.stringify([ms.map(m=>[m.lines[0].stat.avg,m.segments.read.avg]),JSON.stringify(data)===before,ms.map(m=>C.resultHeading(m,0)),C.technicalTable(ms[1],['read'])]));")
    assert out[0]==[[3,3],[3,3]] and out[1]
    assert all('平均 3.00 秒' in h for h in out[2])
    assert 'data-label="平均用时">3.00 秒' in out[3] and 'data-label="中位数（补充）">0.10 秒' in out[3]


def test_no_raw_samples_does_not_relabel_saved_median_as_average():
    out=render("data.rounds=[];const m=C.model(data);console.log(JSON.stringify([m.lines[0].stat.avg,m.segments.read.avg,C.costHtml(m,0),C.resultHeading(m,0)]));")
    assert out[:2]==[None,None] and '单步平均 未测到' in out[2] and '平均 未测到' in out[3]


def test_anomalous_largest_step_is_not_duplicated_in_focus_and_issue_summary():
    out=render("data.segments[1].spiky=true;data.segments[1].spiky_rounds=[2];const m=C.model(data,'general');console.log(JSON.stringify([C.costFocus(m,['read','verify']),C.issueRows(m,['read','verify'])]));")
    assert out[0]=='' and '平均 0.25 秒' in out[1]


def test_standard_card_has_one_average_and_does_not_relabel_history():
    out=render("data.lines[0].exp_std={fair:1,statistic:'mean'};data.lines[0].vs={prev:.2,change:'慢了'};const m=C.model(data);console.log(JSON.stringify(C.comparison(m)));")
    assert out.count('0.42 秒')==1 and '<dt>平均</dt>' not in out
    assert '历史比较（原记录采用中位数）' in out and '0.20 秒 → 0.35 秒' in out


def test_action_summary_uses_mean_and_primary_reference_is_not_repeated():
    source=(STATIC/'app.js').read_text()
    function=source[source.index('function readableActions('):source.index('function readableSources(')]
    code="const ReportLayout=C,activeFixes=()=>[],cardOf=()=>-1,recLine=()=>'',jump=()=>'';const decisions=()=>[{k:'main',lv:0,t:'主线',d:'旧中位数'},{k:'spiky',seg:'read',lv:1,t:'读取偏慢',d:'平时0.1'}];"+function+"console.log(JSON.stringify(readableActions({},C.model(data))));"
    out=render(code)
    assert '平均 0.15 秒' in out and '平时0.1' not in out and '旧中位数' not in out


def test_complete_records_have_no_gap_banner_but_missing_samples_state_the_scope():
    out=render("const a=C.resultIssues(C.model(data,'general'));data.rounds[0].ends[1]=null;const b=C.resultIssues(C.model(data,'general'));console.log(JSON.stringify([a,b]));")
    assert out[0]==''
    assert '「门开始打开」有 1 次未测到，平均用时按其余 1 次有效记录计算' in out[1]
    assert '亮绿灯' not in out[1]


def test_endpoint_rename_is_not_a_missing_user_requested_result():
    out=render("data.scenario.lines[1].actual=true;data.scenario.lines[1].was='门已完全打开';data.checks=[{kind:'line_changed',line:'开锁'}];const m=C.model(data,'general');console.log(JSON.stringify([C.resultIssues(m),C.findings(m,'failed')]));")
    assert out[0]==''
    assert '门已完全打开' not in out[1]


def test_renamed_endpoint_still_reports_actual_bad_timing():
    out=render("data.scenario.lines[1].actual=true;data.scenario.lines[1].was='门已完全打开';data.lines[1].bad=true;console.log(JSON.stringify(C.resultIssues(C.model(data,'general'))));")
    assert '计时先后异常' in out and '门已完全打开' not in out


def test_statistics_explanation_is_only_in_technical_report():
    source=(STATIC/'app.js').read_text()
    general=source.split('function readableReport(D){',1)[1].split("let page='home'",1)[0]
    assert '这些数字怎么看' not in source
    assert '有效记录的用时相加' not in general
    assert '<summary>统计方法</summary>' in source


def test_bad_line_notice_is_not_duplicated_and_segment_issue_keeps_local_scope():
    out=render("data.lines[0].bad=true;data.checks=[{kind:'bad_line',line:'提示'},{kind:'thin',k:'motor',count:1,of:2}];console.log(JSON.stringify(C.resultIssues(C.model(data,'general'))));")
    assert out.count('亮绿灯')==1 and '计时先后异常' in out
    assert '校验通过 → 门开始打开' in out and '记录不足（1/2 次）' in out
    assert '这一步暂不判断快慢' in out and '请研发补测' in out


def test_findings_name_actual_slow_rounds_without_generic_anxiety_text():
    out=render("data.segments[0].spiky=true;data.segments[0].spiky_rounds=[9,3];console.log(JSON.stringify(C.findings(C.model(data,'general'),'failed')));")
    assert '第 3、9 次有步骤明显偏慢' in out and '查看对应步骤' in out
    assert '需留意' not in out and '建议尚未' not in out


def test_advice_survives_display_copy_of_main_and_uses_mean_title():
    source=(STATIC/'app.js').read_text()
    classifiers=source[source.index('const LV='):source.index('function decisions(D)')]
    advice=source[source.index('function activeFixes(D)'):source.index('// 工作量怎么分')]
    code=r'''
const htmlEsc=C.esc;const ReportLayout=C,plain=x=>x,noCode=x=>x,f=x=>String(x),roundList=x=>'';
const D={J:data,n:2,S:{fixes:[{when:['main'],why:'示例',impact:'等待',rec:'核对',opts:[]}]},
 lines:[{name:'提示',end:'亮绿灯',med:.35,segs:['read','verify','lamp']},{name:'开锁',end:'门开始打开',med:.6,segs:['read','verify','motor']}],
 allSegs:data.segments,biggest:data.segments[1],verdict:()=>['已记录',''],exp:null};
D.main={...D.lines[1]};
data.labels=[{kind:'line',line:'开锁',level:'必须改',why:'模拟条件'}];
'''+classifiers+'\nconst decisions=decisionsJ;\n'+advice+r'''
console.log(JSON.stringify([decisionsJ(D),activeFixes(D)]));
'''
    out=render(code)
    assert out[0][0]['k']=='main'
    assert len(out[1])==1 and out[1][0]['lv']==0
    assert out[1][0]['title']=='门开始打开 · 平均 0.75 秒'
