// A cold #sample=S4 open: one failed request is retried; two show "Couldn't open the sample" and
// "Try again" reopens it. Runs web/app.js in jsdom with a scripted fetch.
import fs from 'node:fs';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
import * as R from '../web/results.js';
import * as P from '../web/pages.js';

const saved = JSON.parse(fs.readFileSync('samples/cached/S4.json', 'utf8'));
const html = fs.readFileSync('web/index.html', 'utf8');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/^import .*;$/gm, '');
const out = {};
for (const failures of [1, 2]) {
  const dom = new JSDOM(html, { url: 'https://synthetic.invalid/#sample=S4', runScripts: 'outside-only' });
  const w = dom.window;
  Object.assign(w, R, P, { scrollTo() {}, CSS: { escape: (x) => x } });
  let posts = 0;
  w.fetch = async (url, options) => {
    if (options.method === 'POST' && posts++ < failures) throw new TypeError('synthetic network failure');
    const data = options.method === 'POST' ? { job_id: 'synthetic', token: 'synthetic', mode: 'saved' }
      : { status: 'done', mode: 'saved', extraction: saved.quote, processing_complete: true, page_text: saved.pages };
    return { ok: true, status: 200, json: async () => data };
  };
  vm.runInContext(source, dom.getInternalVMContext());
  await new Promise((r) => setTimeout(r, 100));
  const read = (code) => vm.runInContext(code, dom.getInternalVMContext());
  const row = { view: read('state.view'), posts, message: w.document.querySelector('#problem-message').textContent };
  if (row.view === 'problem') {
    w.document.querySelector('#problem-retry').click();
    await new Promise((r) => setTimeout(r, 100));
    row.afterRetry = read('state.view');
  }
  out[`fail${failures}`] = row;
  dom.window.close();
}
console.log(JSON.stringify(out));
