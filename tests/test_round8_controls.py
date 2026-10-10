"""Independent passing controls for the remaining requested attack paths."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from test_api import aws, call, common, jobs, put_sample
from test_round6 import TYPED
from api import manual

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('kind,ip_var,day_var', [
    ('sample','IP_SAMPLE_DAILY_CAP','SAMPLE_DAILY_CAP'),
    ('typed','IP_TYPED_DAILY_CAP','TYPED_DAILY_CAP'),
])
@pytest.mark.parametrize('limit', ['ip','day','kill'])
def test_sample_and_typed_admission_limits(aws, monkeypatch, kind, ip_var, day_var, limit):
    put_sample(aws)
    before = common.table().scan()['Items']
    if limit == 'kill': monkeypatch.setenv('UPLOADS_ENABLED','false')
    else: monkeypatch.setenv(ip_var if limit == 'ip' else day_var,'0')
    code, body = (call(jobs.create_sample_job,path={'sample_id':'S1'}) if kind == 'sample'
                  else call(manual.handler,TYPED))
    assert code == (503 if limit == 'kill' else 429)
    assert common.table().scan()['Items'] == before

@pytest.mark.parametrize('kind,day_var,prefix', [
    ('sample','SAMPLE_DAILY_CAP','ipsample#'),('typed','TYPED_DAILY_CAP','iptyped#')])
def test_global_refusal_takes_no_new_ip_slot(aws,monkeypatch,kind,day_var,prefix):
    put_sample(aws)
    monkeypatch.setenv(day_var,'1')
    def invoke(ip):
        return (call(jobs.create_sample_job,path={'sample_id':'S1'},ip=ip) if kind=='sample'
                else call(manual.handler,TYPED,ip=ip))
    assert invoke('198.51.100.1')[0] in (200,201)
    assert invoke('198.51.100.2')[0] == 429
    assert len([i for i in common.table().scan()['Items'] if i['job_id'].startswith(prefix)]) == 1

def test_43_page_render_function_includes_every_omission():
    script = r'''
import fs from 'node:fs'; import vm from 'node:vm';
import {PagePlan} from './web/pages.js';
const source=fs.readFileSync('web/app.js','utf8');
const code=source.match(/async function renderPdf\([\s\S]*?\n\}/)[0];
const doc={numPages:43,getPage:async n=>{if(n===7)throw Error('unreadable');return {n,cleanup(){}};},destroy:async()=>{}};
const ctx={Uint8Array,loadPdfjs:async()=>({getDocument:()=>({promise:Promise.resolve(doc)})}),
pdfPageToJpeg:async p=>p.n===9?null:'synthetic-jpeg'};
vm.createContext(ctx);vm.runInContext(code,ctx);
const plan=new PagePlan(20);
await ctx.renderPdf({arrayBuffer:async()=>new ArrayBuffer(0)},plan,()=>{});
console.log(JSON.stringify(plan.request()));
'''
    run = subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT,capture_output=True,text=True)
    assert run.returncode == 0, run.stderr
    body = json.loads(run.stdout)
    nums,total,omitted=jobs._page_plan(body,body['page_count'])
    assert total==43 and len(nums)==20 and len(omitted)==23
    assert nums == [n for n in range(1,23) if n not in (7,9)]
    assert omitted[:2] == [{'page':7,'reason':'unreadable'},{'page':9,'reason':'too_large'}]

@pytest.mark.parametrize('status',[400,500])
def test_app_trace_exception_text_is_absent(status):
    from test_round6 import SENTINEL
    probe=SENTINEL.replace('http_status_code=400',f'http_status_code={status}')
    run=subprocess.run([sys.executable,'-c',probe],cwd=ROOT,capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    trace=json.loads(run.stdout.strip().splitlines()[-1])
    assert 'SYNTHETIC-SENSITIVE-TEXT-ROUND7' not in json.dumps(trace)
    assert 'cause' not in trace['subsegments'][0]

def test_three_telugu_outcomes_have_distinct_labels():
    script="import pack from './web/te.js'; console.log(JSON.stringify(['consistent','inconsistent','needs_confirmation'].map(s=>pack.ui['status.'+s+'.badge'])));"
    run=subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    assert run.returncode==0,run.stderr
    labels=json.loads(run.stdout)
    assert len(set(labels))==3 and all(labels)
