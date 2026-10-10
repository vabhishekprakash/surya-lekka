import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';  // adapted: the repo's pinned test dependency (tests/package.json)
import * as R from '../web/results.js';
import * as P from '../web/pages.js';
import pack from '../web/te.js';
const saved = JSON.parse(fs.readFileSync('samples/cached/S4.json', 'utf8'));
const html = fs.readFileSync('web/index.html', 'utf8');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/^import .*;$/gm, '');
const dom = new JSDOM(html, {url:'https://synthetic.invalid/#sample=S4', runScripts:'outside-only'});
const w=dom.window;
Object.assign(w, R, P, {scrollTo(){}, CSS:{escape:x=>x}});
const requests=[];
w.fetch=async (url, options)=>{
  requests.push([options.method,url]);
  const data=options.method==='POST' ? {job_id:'synthetic', token:'synthetic', mode:'saved'} :
    {status:'done', mode:'saved', extraction:saved.quote, processing_complete:true, page_text:saved.pages};
  return {ok:true,status:200,json:async()=>data};
};
vm.runInContext(source,dom.getInternalVMContext());
await new Promise(r=>setTimeout(r,50));
const read=code=>vm.runInContext(code,dom.getInternalVMContext());
const out={requests,view:read('state.view'),problem:w.document.querySelector('#problem-message').textContent};
assert.equal(out.view,'review',JSON.stringify(out));
assert.equal(requests[0][1],'/samples/S4');
const statuses=['consistent','inconsistent','needs_confirmation','out_of_scope'];
out.statuses={en:statuses.map(s=>R.UI_EN['status.'+s+'.badge']),te:statuses.map(s=>pack.ui['status.'+s+'.badge'])};
for(const words of Object.values(out.statuses)) {
  assert.ok(words.every(x=>typeof x==='string' && x.length));
  assert.equal(new Set(words).size,4);
}
w.syntheticResult=JSON.parse(fs.readFileSync(process.argv[2],'utf8')  /* adapted: input path from the test */);
w.syntheticPack=pack;
read('state.telugu=language(syntheticPack); openResults(syntheticResult)');
const english=w.document.querySelector('#view-results').textContent;
const money=text=>Array.from(text.matchAll(/₹[\d,.]+/g), m=>m[0]).sort();
read('state.lang="te"; renderResults()');
const telugu=w.document.querySelector('#view-results').textContent;
assert.deepEqual(money(telugu),money(english));
assert.ok(money(telugu).length>0);
assert.ok(!/\{\w+\}|undefined|NaN/.test(telugu));
out.rupeeAmounts=money(telugu);
read('delete state.telugu.ui["results.heading"]; renderResults()');
assert.equal(read('state.shownLang.code'),'en');
assert.equal(w.document.querySelector('#view-results').getAttribute('lang'),null);
assert.equal(w.document.querySelector('#view-results').textContent.replace(R.NOT_IN_TELUGU,''),english);
out.missingKeyFallback='entire results screen matches English baseline';
const fix=[...w.document.querySelectorAll('#view-results button')].find(b=>b.textContent==='Fix a number');
assert.ok(fix);
fix.click();
out.fixTarget=w.document.activeElement.tagName;
assert.equal(read('state.view'),'review');
console.log(JSON.stringify(out));
dom.window.close();
