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
    assert 'data-value="0.350000"' in out and '细虚线：中位数' in out


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
    for value in ('0.35 秒','0.50 秒','0.10 秒','0.30 秒'):
        assert value in out
    assert '只记录、不判快慢' in out and '同时发生' in out and '谁负责' not in out


def test_exported_controls_restore_saved_result_and_round_before_first_interaction():
    out=render(r'''
const listeners={},parts={},buttons=[0,1].map(i=>({dataset:{line:String(i)},disabled:false,active:i===1,setAttribute(k,v){if(k==='aria-pressed')this.active=v==='true'},querySelector(){return {textContent:''}},closest(){return box}}));
const select={value:'2',closest(q){return q==='.report-inspect'?box:q==='[data-role="round-select"]'?select:null}};
const box={dataset:{audience:'technical'},querySelector(q){if(q==='[data-role="report-data"]')return {textContent:JSON.stringify(data)};if(q==='[data-line][aria-pressed="true"]')return buttons.find(b=>b.active);if(q==='[data-role="round-select"]')return select;return parts[q]||(parts[q]={innerHTML:'',textContent:''})},querySelectorAll(){return buttons}};
global.document={addEventListener(k,f){listeners[k]=f}};C.install(()=>{});
listeners.change({target:select});const first=parts['[data-role="round-content"]'].innerHTML;
listeners.click({target:{closest(q){return q==='[data-line]'?buttons[0]:null}}});
console.log(JSON.stringify([first,parts['[data-role="round-content"]'].innerHTML]));
''')
    assert '首尾实测 0.90 秒' in out[0] and '第 2 次' in out[0]
    assert '首尾实测 0.50 秒' in out[1] and '第 2 次' in out[1]


def test_mean_reference_uses_raw_rounds_not_median_or_passing_proportion():
    out=render("data.lines[0].exp_std={fair:1,source:'平均 ≤1 秒'};data.lines[0].med=.2;data.rounds[0].ends[0]=.2;data.rounds[1].ends[0]=2.2;const m=C.model(data);console.log(JSON.stringify([C.referenceInfo(m,m.lines[0]),C.referenceText(m,m.lines[0]),C.comparison(m)]));")
    assert out[0]['avg']==pytest.approx(1.2)
    assert out[0]['over']==1
    assert '平均 1.20 秒，超过 1.00 秒' in out[1]
    assert '1/2 次（50%）' in out[2] and '不是标准通过率' in out[2]


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


def test_findings_distinguish_advice_status_from_measurement_quality():
    out=render("const m=C.model(data);console.log(JSON.stringify(['failed','running','done',null].map(s=>C.findings(m,s))));")
    assert '尚未就绪' in out[0] and '正在生成' in out[1] and '分析已完成' in out[2] and '尚未生成' in out[3]
    assert all('没有需要你处理' not in s and '不等于所有功能都已验证' in s for s in out)


def test_reference_and_anomaly_views_preserve_raw_payload_and_technical_names():
    out=render("data.scenario.point_names.ready='语音活动上报';const before=JSON.stringify(data),g=C.model(data,'general'),t=C.model(data);C.findings(g,'failed');C.comparison(g);console.log(JSON.stringify([JSON.stringify(data)===before,g.names.ready,t.names.ready,C.recordOnly(data,{lines:['提示'],segments:[],parallel:[]})]));")
    assert out[:3]==[True,'程序发出说话状态通知','语音活动上报']
    assert '<details><summary>查看完整过程及独立步骤的全部统计</summary>' in out[3]
    assert '0.35 秒' in out[3]


def test_result_alert_and_advice_entry_survive_without_an_all_clear_fallback():
    source=(STATIC/'app.js').read_text()
    function=source[source.index('function readableActions('):source.index('function readableSources(')]
    stubs="const activeFixes=()=>[{title:'方案'}],cardOf=(f,x)=>x.k==='line:开锁'?0:-1,recLine=()=> '已有建议',jump=(id,label)=>'<a href=\"#'+id+'\">'+label+'</a>';"
    code="const ReportLayout=C;"+stubs+"const decisions=()=>[{k:'main',lv:0,t:'过慢',d:'旧判断',src:['tiers','标准']},{k:'line:开锁',lv:1,t:'开锁',src:['tiers','标准']},{k:'problem',lv:4,t:'动作异常',d:'仍需处理',src:['rounds','记录']}];"+function+"const m=C.model(data);console.log(JSON.stringify(readableActions({},m)));"
    out=render(code)
    assert '亮绿灯' in out and '动作异常' in out and '仍需处理' in out
    assert '已有建议' in out and '#fix-0' in out and '#sec-tiers' in out
    assert '没有需要' not in out
