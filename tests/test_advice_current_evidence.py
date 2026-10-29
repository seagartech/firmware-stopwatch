"""Generic measurement/source binding, one useful option and exact numeric evidence."""
import copy
import json
from fsw import advise as A
from tests.test_advise import judged, recorded
from fsw.llm import FakeLLM


def test_bare_json_and_fenced_json_share_validation(tmp_path):
    d, j = judged('door')
    data=json.loads(recorded(d).split('```json\n')[1].split('```')[0])
    data['fixes'][0]['opts']=data['fixes'][0]['opts'][:1]
    r=A.advise(FakeLLM(json.dumps(data)),tmp_path,j,d['scenario'])
    assert r['ok'] and len(r['fixes'][0]['opts'])==1
    assert next(iter(r['fixes'][0]['facts'].values()))['average'] is not None


def test_values_source_and_rules_each_invalidate_cache(tmp_path):
    d,j=judged('door');src=tmp_path/'src';src.mkdir();file=src/'gate.c';file.write_text('unlock();')
    out=tmp_path/'advice';r=A.advise(FakeLLM(recorded(d)),src,j,d['scenario'],save_to=out)
    assert r['ok'] and A.saved_for(out,j,d['scenario'],A.source_revision(src))
    changed=copy.deepcopy(j);changed['measurement_digest']='other measured values'
    assert not A.saved_for(out,changed,d['scenario'],A.source_revision(src))
    file.write_text('validate(); unlock();')
    assert not A.saved_for(out,j,d['scenario'],A.source_revision(src))
    sc=copy.deepcopy(d['scenario']);sc['name']='changed interpretation'
    assert not A.saved_for(out,j,sc,r['source_revision'])


def test_explicit_primary_survives_result_reordering():
    d,j=judged('door');sc=copy.deepcopy(d['scenario']);sc['lines'][0]['first']=True
    first=sc['lines'][0]['name'];j['labels'].append({'kind':'line','line':first,'level':'建议改','why':'internal rule','k':'line:'+first})
    probs=A.problems(j,sc)
    assert next(p for p in probs if p['key']=='main')['what'].startswith(first)


def test_model_cannot_overwrite_program_evidence_or_claim_change_without_reference():
    d,j=judged('door');data=json.loads(recorded(d).split('```json\n')[1].split('```')[0]);f=data['fixes'][0]
    f['facts']={'main':{'average':999}};f['analysis_kind']='change';f['evidence_refs']=['not in source']
    cards,bad=A.check(data,j,d['scenario']);assert bad and '源码引用' in ''.join(bad)
    assert all(v.get('average') != 999 for v in cards[0]['facts'].values())


def test_prose_precision_is_not_rewritten():
    f={'why':'1.234567、2.345678秒','rec':'x','impact':'y'};A._tidy(f,[])
    assert f['why']=='1.234567、2.345678秒'


def current_gate_reply():
    d,j=judged('door');data=json.loads(recorded(d).split('```json\n')[1].split('```')[0]);f=data['fixes'][0]
    point=d['scenario']['segments'][f['seg']]['from']
    j['code']={point:{'path':'gate.c','ctx':[[10,'queue_unlock();'],[11,'drive_motor();']]}}
    d['scenario'].setdefault('points',{})[point]={'at':'gate.c|before|drive_motor();'}
    f.update(analysis_kind='investigate',why='等待区间偏长，原因尚未定位。',impact='开门等待变长。',rec='补记进入队列的时刻。')
    f['dev'].update(data='现有记录缺少入队边界。',change='新增入队观测，复用已有执行记录。')
    f['opts']=f['opts'][:1];f['opts'][0].update(anchors=[{'point':point,'code':'drive_motor();'}],observations=[{'candidate':'N1'}],how='按同一轮对齐入队与已有执行时刻。',work='小',rec=1)
    return d,j,data


def test_observations_are_bound_to_source_and_cannot_duplicate_existing_points():
    d,j,data=current_gate_reply();assert not A.check(copy.deepcopy(data),j,d['scenario'])[1]
    data['fixes'][0]['opts'][0]['observations'][0]['candidate']='not-available'
    assert '未测候选' in ''.join(A.check(data,j,d['scenario'])[1])


def test_current_numeric_narrative_is_rejected_but_program_evidence_remains():
    d,j,data=current_gate_reply();data['fixes'][0]['why']='平均99秒。'
    assert '重复了时间数值' in ''.join(A.check(data,j,d['scenario'])[1])


def test_source_error_gets_only_one_bounded_model_repair(tmp_path):
    d,j,good=current_gate_reply();bad=copy.deepcopy(good);bad['fixes'][0]['opts'][0]['observations'][0]['candidate']='not-available'
    class RepairModel:
        def __init__(self):self.calls=[]
        def investigate(self,prompt,*args,**kwargs):
            self.calls.append(prompt);return json.dumps(bad if len(self.calls)==1 else good)
    m=RepairModel();r=A.advise(m,tmp_path,j,d['scenario'],save_to=tmp_path/'advice')
    assert r['ok'] and len(m.calls)==2 and '未测候选' in m.calls[1]
    assert (tmp_path/'advice/reply-initial.txt').is_file()


def test_json_mode_is_scoped_to_advice_and_keeps_source_tools(tmp_path):
    from fsw.llm import DeepSeek
    model=DeepSeek('fake-test-key');bodies=[]
    def ask(body):bodies.append(body);return {'content':'{"fixes": []}'}
    model._ask=ask
    assert model.investigate_json('output JSON',tmp_path)=='{"fixes": []}'
    assert bodies[0]['response_format']=={'type':'json_object'} and bodies[0]['tools']
    bodies.clear();model.investigate('ordinary source review',tmp_path)
    assert 'response_format' not in bodies[0]
