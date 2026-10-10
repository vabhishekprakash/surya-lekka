import fs from 'node:fs';
import vm from 'node:vm';
import * as R from '../web/results.js';
import pack from '../web/te.js';

// Execute the production app functions verbatim, using an in-memory DOM and no network.
const source = fs.readFileSync('./web/app.js', 'utf8');
// adapted: fixedTexts is the production helper that now builds the fixed labels inside the fallback
// adapted (Block 21): fixNumber and fieldFor are what resultsContext now hands "Fix a number"
const functions = ['viewLang', 'resultsContext', 'renderResults', 'applyLanguage', 'fixedTexts', 'fixNumber', 'fieldFor', 'setLang',
  'pageButton', 'quoteButton', 'boxesFor', 'openResults', 'go', 'show', 'canShow'];
const code = functions.map(name => {
  const match = source.match(new RegExp('function ' + name + '\\([\\s\\S]*?\\n\\}'));
  if (!match) throw new Error(name);
  return match[0];
}).join('\n');
function node(tag, attrs = {}, ...children) {
  return { tag, attrs, dataset: {}, textContent: attrs.text || '', children: children.flat().filter(x=>x!=null),
    hidden: false, setAttribute(k,v){this.attrs[k]=v;}, removeAttribute(k){delete this.attrs[k];},
    replaceChildren(...kids){this.children=kids;}, addEventListener(){}, focus(){}, select(){} };
}
function texts(n) {
  if (typeof n !== 'object') return String(n);
  return [n.textContent, n.attrs['aria-label'] || '', ...n.children.map(texts)].join('\n');
}
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const variants = ['normal_transition', 'image_transition', 'missing_fixed_key', 'missing_message_key', 'missing_vendor_parts'];
const output = {};
for (const variant of variants) {
  const lang = R.language(structuredClone(pack));
  const result = structuredClone(input);
  if (variant === 'missing_fixed_key') delete lang.ui['results.heading'];
  if (variant === 'missing_message_key') result.findings[0].message_key = 'round7.unknown';
  if (variant === 'missing_vendor_parts') delete result.vendor_message_lines;
  const elements = new Map();
  const fixed = node('h2'); fixed.dataset.i18n = 'results.heading';
  const state = {view: variant.endsWith('transition') ? 'review' : 'results', lang:'te',telugu:lang,
    shownLang:R.ENGLISH, mode:'saved',option:'',entryChecks:[], result:null,boxIndex:new Map(),
    pageText:{'1':'synthetic','2':'synthetic'},started:false};
  const $ = key => { if (!elements.has(key)) elements.set(key,node('div')); return elements.get(key); };
  const context = { ...R, state, el:node, $, $$: key=> key==='[data-i18n]' ? [fixed] : [],
    MODE_LABELS:{saved:'Saved reading'}, readingField:()=>null,confirmOperands(){},
    navigator:{clipboard:{writeText:async()=>{}}},pageImage:()=>variant === 'image_transition' ? 'data:image/jpeg;base64,AA==' : null,openPage(){},
    privacyLine:()=>R.UI_EN['foot.advice'], privacySettings:()=>['',false,'',false],
    window:{scrollTo(){}},history:{pushState(){}}, showManualEntryChecks(){},stopPolling(){},
    CSS:{escape:x=>x}};
  vm.createContext(context);
  vm.runInContext(code,context);
  let error = null;
  try { context.openResults(result); } catch(e) {error=e.constructor.name+': '+e.message;}
  output[variant] = {error,language:state.shownLang.code,notice:$('#lang-notice').textContent,
    fixed:fixed.textContent,groups:texts($('#results-groups')),vendor:texts($('#vendor-questions')),
    summary:$('#results-summary').textContent};
}
console.log(JSON.stringify(output));
