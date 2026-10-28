"""Product report policies exercised with a synthetic non-voice scenario."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'fsw/web/static'
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='没有 node')
BASE = r'''
const P=require('./report-rules.js'),V=require('./report-view.js'),R=require('./report-layout.js');
const data={measured_at:'fixture',scenario:{start:{point:'card',name:'读到卡'},point_names:{card:'读到卡',read:'取得卡号',open:'完成解锁',light:'亮灯'},points:{card:{at:'gate.c|before|read_card();'},read:{at:'gate.c|after|read_card();'},open:{at:'gate.c|after|unlock();'},light:{at:'gate.c|after|light();'}},last_points:[],segments:{read:{from:'card',to:'read',name:'读取卡号'},open:{from:'read',to:'open',name:'执行开锁'},light:{from:'read',to:'light',name:'更新灯光'}},lines:[{name:'提示',segments:['read','light']},{name:'解锁',segments:['read','open'],first:true}]},segments:[{k:'read',med:99},{k:'open',med:99},{k:'light',med:99}],lines:[{name:'提示',med:99},{name:'解锁',med:99,exp_std:{fair:.5,statistic:'mean',source:'合成测试条件'}}],rounds:[{n:2,segs:{read:.04,open:.1,light:.01},ends:[.05,.14]},{n:9,segs:{read:.04,open:.1,light:.01},ends:[.05,.14]},{n:17,segs:{read:1.4,open:.1,light:.01},ends:[1.41,1.5]}],code:{}};
const model=()=>V.model(data,'technical');
'''


def run(expr):
    result = subprocess.run(['node', '-e', BASE+'\n'+expr], cwd=STATIC,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_current_samples_replace_stale_statistics_and_choose_declared_primary():
    x = run("const m=model();console.log(JSON.stringify([m.lines[1].stat,P.choices(m).map(l=>l.name),m.segments.read]));")
    assert x[0]['avg'] == pytest.approx((.14+.14+1.5)/3)
    assert x[0]['med'] == .14 and x[0]['max'] == 1.5
    assert x[1] == ['解锁', '提示']
    assert x[2]['spiky_rounds'] == [17]


def test_missing_values_negative_values_and_zero_are_distinct():
    x=run("data.rounds[0].ends[1]=null;data.rounds[1].ends[1]=-1;data.rounds[2].ends[1]=0;console.log(JSON.stringify(model().lines[1].stat));")
    assert x['count'] == 1 and x['avg'] == 0 and x['min'] == 0
    assert x['bad_rounds'] == [9] and x['thin']


def test_completely_missing_segment_has_zero_count_without_saved_statistics():
    x=run("data.segments=[];data.rounds.forEach(r=>r.segs.read=null);console.log(JSON.stringify(model().segments.read));")
    assert x['count'] == 0 and x['med'] is None and x['thin'] and not x['spiky']


def test_grouping_uses_same_start_and_overlapping_intervals_not_equal_numbers():
    x=run("data.rounds[2].segs.open=1;data.rounds[2].segs.light=.5;data.rounds[2].ends=[1.9,2.4];const m=model();m.segments.open.spiky_rounds=[17];m.segments.light.spiky_rounds=[17];const a=P.review(m,data.rounds[2],R);m.defs.light.from='other';const b=P.review(m,data.rounds[2],R);console.log(JSON.stringify([a.groups.map(g=>g.items.map(x=>x.k)),b.groups.map(g=>g.items.map(x=>x.k))]));")
    assert {'open', 'light'} in [set(g) for g in x[0]]
    assert {'open', 'light'} not in [set(g) for g in x[1]]


def test_review_values_and_medians_are_current_and_payload_is_immutable():
    x=run("const before=JSON.stringify(data),m=model(),r=P.review(m,data.rounds[2],R);console.log(JSON.stringify([r.evidence,before===JSON.stringify(data)]));")
    assert x[1]
    evidence=next(e for e in x[0] if e['k']=='read')
    assert evidence['v']==1.4 and evidence['med']==.04 and evidence['count']==3


def test_source_metadata_follows_binding_and_occurrence_not_point_identifier():
    x=run("data.presentation={events:{events:{old:{at:'gate.c|after|unlock();',occurrence:'first',label:'执行开锁后'}}},phases:{boundaries:{old:{label:'执行开锁'}},resultNames:{old:'锁已打开'}}};const a=P.metadata(data);data.scenario.points.open.at='changed';console.log(JSON.stringify([a,P.metadata(data)]));")
    assert x[0]['phaseMeta']['resultNames']=={'open':'锁已打开'}
    assert x[1]['phaseMeta']['resultNames']=={}


def test_ambiguous_or_wrong_occurrence_binding_cannot_acquire_a_label():
    x=run("data.presentation={events:{events:{old:{at:'gate.c|after|unlock();',occurrence:'last',label:'末次输出'}}},phases:{resultNames:{old:'完成'}}};const a=P.metadata(data);data.scenario.last_points=['open'];const b=P.metadata(data);data.scenario.points.duplicate={at:data.scenario.points.open.at};data.scenario.last_points.push('duplicate');console.log(JSON.stringify([a,b,P.metadata(data)]));")
    assert not x[0]['phaseMeta']['resultNames']
    assert x[1]['phaseMeta']['resultNames']=={'open':'完成'}
    assert not x[2]['phaseMeta']['resultNames']


@pytest.mark.parametrize('event', [{}, {'at': 'gate.c|after|unlock();'}])
def test_semantic_label_requires_an_explicit_binding_and_occurrence(event):
    x=run('data.presentation={events:{events:{old:'+json.dumps(event)+'}},phases:{resultNames:{old:"锁已打开"}}};console.log(JSON.stringify(P.metadata(data)));')
    assert not x['phaseMeta']['resultNames']


def test_stage_averages_use_matching_complete_rounds_and_refuse_disconnected_paths():
    x=run("const m=model(),l=m.lines[1];data.rounds[0].segs.open=null;const a=P.stageGroups(m,l,{boundaries:{}});m.defs.open.from='wrong';let error=false;try{P.stageGroups(m,l,{boundaries:{}})}catch{error=true}console.log(JSON.stringify([a,error]));")
    assert x[0][0]['count']==2 and x[0][0]['avg']==pytest.approx((.14+1.5)/2)
    assert x[1]


def test_unannotated_stages_use_declared_execution_boundary_and_actual_names():
    x=run("data.scenario.segments.read.side='device';data.scenario.segments.open.side='outside';const m=model();console.log(JSON.stringify(P.stageGroups(m,m.lines[1],{boundaries:{}})));")
    assert [g['keys'] for g in x]==[['read'],['open']]
    assert [g['label'] for g in x]==['读到卡 → 取得卡号','取得卡号 → 完成解锁']


def test_zero_one_many_and_content_dependent_summary_results():
    x=run("const a=P.choices(model()).length;data.scenario.lines[1].content_dependent=true;const b=P.choices(model()).map(l=>l.name);data.scenario.lines[0].content_dependent=true;const c=P.choices(model()).length;data.scenario.lines=[];console.log(JSON.stringify([a,b,c,P.choices(model()).length]));")
    assert x==[2,['提示'],0,0]


def test_standard_label_requires_matching_source_value_and_statistic():
    # reference wording belongs to ReportView's facade, not the legacy report helper.
    js = (STATIC/'report-view.js').read_text()
    start=js.index('R.referenceMeaning=')
    end=js.index('R.referenceEvidence=',start)
    code=js[start:end]
    result=subprocess.run(['node','-e', 'const R={};'+code+"console.log(JSON.stringify([{e:{fair:2,source:'T/CESA 1045-2019'},mode:'mean'},{e:{fair:2,source:'合成数据'},mode:'mean'},{e:{fair:2,source:'T/CESA 1045-2019'},mode:'p90'}].map(R.referenceMeaning)))"],capture_output=True,text=True,check=True)
    x=json.loads(result.stdout)
    assert [r['cesa'] for r in x]==[True,False,False]


def test_modern_report_keeps_existing_advice_controls_and_live_actions():
    app=(STATIC/'app.js').read_text()
    fn=app[app.index('function modernReport('):app.index('\nfunction readableActions(')]
    code=r'''
const ReportView={shell:(data,options)=>options},ADV={status:'failed'};
const activeFixes=()=>[{}],advBox=()=>'<button data-advretry="1">retry</button>',
 fixA=()=>'<button data-copy="choice">copy</button>',fixDev=()=>'<div>technical options</div>',
 retestEntry=()=>'<button data-retest="1">retest</button>',restoreBox=()=>'<button data-restore="1">restore</button>',expDone=()=>'';
const scene='fixture',D={J:{raw_log:'fixture.log'},S:{real:true}};
'''
    result=subprocess.run(['node','-e',code+fn+'console.log(JSON.stringify(modernReport(D)));'],capture_output=True,text=True,check=True)
    x=json.loads(result.stdout)
    assert x['real']
    assert 'data-advretry' in x['adviceHtml'] and 'data-copy' in x['adviceHtml']
    assert 'technical options' in x['adviceHtml']
    assert all(k in x['actionsHtml'] for k in ['data-retest','data-restore','data-openlog'])


@pytest.mark.parametrize('count', [0, 1, 2, 3, 4, 7])
def test_compact_result_controls_follow_data_count_and_keep_stable_indices(count):
    x=run(f"const lines=Array.from({{length:{count}}},(_,i)=>({{index:i+8,endName:'结果<'+i+'>'}}));console.log(JSON.stringify(V.compactOptions(lines,9,'data-choice','data-select','选择结果')));")
    if count < 2:
        assert x == ''
    else:
        assert x.count('<button') == count and '<select' not in x
        assert 'data-choice="9"' in x and 'aria-pressed="true"' in x
        assert '结果&lt;1&gt;' in x


@pytest.mark.parametrize('left,right,expected', [(-20,50,10),(20,140,70),(10,90,30)])
def test_selected_button_reveal_moves_only_the_horizontal_strip(left,right,expected):
    x=run(f"const host={{clientWidth:100,scrollLeft:30,scrollTop:777,querySelector:()=>({{getBoundingClientRect:()=>({{left:{left},right:{right}}})}}),getBoundingClientRect:()=>({{left:0,right:100}})}};V.revealSelected(host);console.log(JSON.stringify([host.scrollLeft,host.scrollTop]));")
    assert x == [expected,777]


def test_floating_selector_only_appears_in_its_visible_controlled_range():
    x=run("const b={enabled:true,count:4,anchorBottom:140,endBottom:700,top:60,height:72};console.log(JSON.stringify([b,{...b,enabled:false},{...b,count:0},{...b,anchorBottom:141},{...b,endBottom:132}].map(V.floatingActive)));")
    assert x == [True,False,False,False,False]


def test_detail_selector_compacts_before_its_statistics_reach_the_header():
    x=run("const b={enabled:true,count:4,anchorTop:60,anchorBottom:320,endBottom:700,top:60,height:72,early:true};console.log(JSON.stringify([V.floatingActive(b),V.floatingActive({...b,anchorTop:61}),V.floatingActive({...b,endBottom:132})]));")
    assert x == [True,False,False]
