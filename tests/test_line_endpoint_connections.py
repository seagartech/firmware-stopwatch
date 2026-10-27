"""Generic side-endpoint ownership and branch-preserving report rewrites."""
import copy
import json
from pathlib import Path

import pytest

from fsw import identify, judge, measure, standards
from fsw.scenario import ScenarioError


@pytest.mark.parametrize('name, endpoint, observation', [
    ('门禁提示', '显示刷卡结果', '发送校验请求'),
    ('对话字幕', '显示识别结果', '发送音频包'),
])
def test_removed_result_does_not_lend_its_name_to_an_observation(name, endpoint, observation):
    base = dict(file='app.c', line=1, why='', sure='高', code='f();', loc_ok=True,
                where='before', once='once', func='f', doubt='', felt='', chain_step=None)
    r = {'steps': [dict(base, id='P1', name='输入完成', line_of='start'),
                   dict(base, id='P2', name='处理结果', line_of='main'),
                   dict(base, id='P3', name=observation, line_of='side')],
         'start': {'name': '输入完成'}, 'main': {'name': '主线', 'end': '处理结果'},
         'side': {'name': name, 'end': endpoint}, 'side_from': 'P2',
         'segments': [{'from': 'P1', 'to': 'P2', 'name': '处理'}],
         'side_segments': [{'from': 'P2', 'to': 'P9', 'name': '显示'},
                           {'from': 'P1', 'to': 'P3', 'name': '发送', 'added': True}]}
    old = copy.deepcopy(r)
    page = identify.to_page(r, '全程')
    assert r == old
    assert page['sideAsk']['q'] == observation
    assert page['branch']['from'] == 'P1'
    assert page['fsegs'][0]['sl'] == '到' + observation
    assert endpoint in page['lineNotes'][0] and '没有测到' in page['lineNotes'][0]
    sc = standards.real_scenario(page, None)
    side = next(l for l in sc['lines'] if l['name'] == '到' + observation)
    assert side['end'] == observation and side['segments'] == ['P1-P3']
    assert not any(l['name'] == name for l in sc['lines'])


def branch_scenario():
    return {'start': {'point': 'P1', 'name': '输入完成'}, 'last_points': ['P3'],
            'point_names': {'P1': '输入完成', 'P2': '首包', 'P3': '末包', 'P4': '返回结果', 'P5': '网络发送'},
            'segments': {k: {'from': a, 'to': b, 'name': k} for k, a, b in [
                ('P1-P2', 'P1', 'P2'), ('P2-P3', 'P2', 'P3'), ('P3-P4', 'P3', 'P4'), ('P3-P5', 'P3', 'P5')]},
            'lines': [{'name': '结果', 'end_point': 'P4', 'segments': ['P1-P2', 'P2-P3', 'P3-P4']},
                      {'name': '观测', 'end_point': 'P5', 'segments': ['P1-P2', 'P2-P3', 'P3-P5']}], 'problems': []}


@pytest.mark.parametrize('middle_late', [0.02, 0.07])
def test_merge_keeps_the_original_prefix_used_by_an_offshoot(middle_late):
    s = branch_scenario()
    r = {'ok': True, 'n': 1, 'segs': {'P1-P2': .01, 'P2-P3': .4, 'P3-P4': -middle_late, 'P3-P5': .8},
         'ends': [round(.41 - middle_late, 6), 1.21]}
    s2, rs, notes = judge.merge_overruns(s, [r])
    assert notes and s2['lines'][0]['segments'] == ['P1-P2', 'P2-P4']
    assert s2['lines'][1]['segments'] == ['P1-P2', 'P2-P3', 'P3-P5']
    assert rs[0]['ends'] == r['ends']
    assert rs[0]['segs']['P2-P3'] == .4 and 'P3-P4' not in rs[0]['segs']
    for line, end in zip(s2['lines'], rs[0]['ends']):
        assert round(sum(rs[0]['segs'][k] for k in line['segments']), 6) == end


def test_disconnected_report_line_is_an_error_not_a_sum():
    s = branch_scenario()
    s['lines'][1]['segments'] = ['P1-P2', 'P3-P5']
    with pytest.raises(ScenarioError, match='不能生成报告'):
        judge.merge_overruns(s, [])
    with pytest.raises(ScenarioError, match='接不上'):
        measure._path([('P1', 'P2'), ('P3', 'P5')])


def test_cached_totals_recomputed_when_same_count_has_a_different_path(tmp_path):
    spec = measure.Spec('P1', {'P1-P2': ['P1', 'P2']}, [{'name': '到结果', 'segs': [['P1', 'P2']]}])
    (tmp_path / 'rounds.json').write_text(json.dumps({'rule': measure.ROUND_RULE, 'rounds': [
        {'n': 1, 'segs': {'P1-P2': .37}, 'ends': [2.07]}]}))
    assert measure.run_rounds(tmp_path, spec)[0]['ends'] == [.37]


def test_report_endpoint_returns_a_visible_error_for_disconnected_paths(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fsw.web.app import create_app
    def broken(*args, **kwargs):
        raise ScenarioError('分段接不上，不能生成报告')
    client = TestClient(create_app(data_dir=tmp_path/'data', config_dir=tmp_path/'cfg'))
    monkeypatch.setattr(judge, 'merge_overruns', broken)
    response = client.get('/api/report?demo=door')
    # Find a bundled demo rather than depending on a particular source/device name.
    if response.status_code == 404:
        from fsw.web.app import load_demos
        response = client.get('/api/report', params={'demo': next(iter(load_demos()))})
    assert response.status_code == 422 and '接不上' in response.json()['detail']


def test_old_cache_fallback_does_not_reuse_a_total_based_only_on_line_count(tmp_path):
    spec = measure.Spec('P1', {'P1-P2': ['P1', 'P2']}, [{'name': '到结果', 'segs': [['P1', 'P2']]}])
    (tmp_path / 'rounds.json').write_text(json.dumps({'rule': measure.ROUND_RULE - 1, 'rounds': [
        {'n': 1, 'segs': {'P1-P2': .37}, 'ends': [2.07]}]}))
    (tmp_path / 'raw.log').write_text('')
    assert measure.run_rounds(tmp_path, spec)[0]['ends'] == [.37]
