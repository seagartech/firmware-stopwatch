import json
import shutil
import subprocess
from pathlib import Path
import pytest

@pytest.mark.skipif(not shutil.which('node'),reason='没有node')
@pytest.mark.parametrize('mode',['success','fallback','failure'])
def test_copy_waits_for_success_and_reports_failure(mode):
    source=(Path(__file__).parents[1]/'fsw/web/static/app.js').read_text();fn=source[source.index('function todoCopy(e){'):source.index('\nfunction tipShow')]
    js=r'''
const b={dataset:{copy:'text'},innerHTML:'复制',textContent:'复制',classList:{add(){},remove(){}}};let resolve,reject;
const pending=new Promise((r,j)=>{resolve=r;reject=j});
const navigator={clipboard:{writeText:()=>pending}};
const document={getElementById:()=>({textContent:'actual content'}),body:{appendChild(){}},createElement:()=>({style:{},select(){},remove(){}}),execCommand:()=>MODE==='fallback'};
const event={target:{closest:()=>b}};
'''.replace('MODE',json.dumps(mode))
    js+=fn+r'''
(async()=>{const handled=todoCopy(event),before=b.textContent;
 if(MODE==='success')resolve();else reject(Error('denied'));
 await pending.catch(()=>{});await Promise.resolve();await Promise.resolve();
 console.log(JSON.stringify({handled,before,after:b.textContent,unrelated:todoCopy({target:{closest:()=>null}})}));process.exit(0)})();
'''.replace('MODE',json.dumps(mode))
    out=json.loads(subprocess.check_output(['node','-e',js],text=True));assert out['handled'] and out['before']=='正在复制…' and out['unrelated'] is False
    assert out['after']==('复制失败，请重试' if mode=='failure' else '✓ 已复制')
