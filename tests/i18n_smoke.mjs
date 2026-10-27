const [base,debug]=process.argv.slice(2);
const tab=await(await fetch(`${debug}/json/new?${encodeURIComponent('about:blank')}`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let seq=0;const pending=new Map(),errors=[];
ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);};
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function wait(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,70));}throw new Error('Timeout: '+expression);}
async function click(id){await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);}
function assert(value,message){if(!value)throw new Error(message);}
try {
 await send('Page.enable');await send('Runtime.enable');await send('Page.navigate',{url:base});
 await wait("typeof state!=='undefined' && state && !catalogBusy()");
 assert(await evaluate("document.documentElement.lang==='en' && document.querySelector('h1').textContent==='AI proposes. You choose the words.'"),'English is the default');
 assert(await evaluate("document.getElementById('search').placeholder.includes('delegate') && document.getElementById('ui-en').getAttribute('aria-pressed')==='true'"),'English attributes and accessible selection');
 await click('sync');await wait('syncing');await click('ui-es');await wait("uiLanguage==='es'");
 await wait('!syncing && state.workspace.ready && !catalogBusy()');
 assert(await evaluate("document.documentElement.lang==='es-AR' && document.querySelector('h1').textContent==='La IA propone. Vos elegís las palabras.' && document.getElementById('search').placeholder.includes('delegado')"),'Spanish static labels and attributes');
 assert(await evaluate("document.getElementById('sync-current').textContent.includes('listas para tu revisión') && document.getElementById('sync-stages').textContent.includes('Preparar')"),'Server progress and stages are localized');
 assert(await evaluate("document.getElementById('language').value==='es_419' && current().proposed[0]==='Copiado al portapapeles'"),'Target language and proposal unchanged');
 await click('accept');await wait('state.counts.accepted===1 && saving===0');
 await click('edit');await evaluate("document.querySelector('.edit-form').value='Mi borrador %{name}';document.querySelector('.edit-form').dispatchEvent(new Event('input',{bubbles:true}));");
 await click('ui-en');await wait("uiLanguage==='en' && !dirty && !saving");
 assert(await evaluate("document.querySelector('.edit-form').value==='Mi borrador %{name}' && state.counts.accepted===1 && state.counts.pending===2 && current().source[0]==='Welcome, %{name}'"),'Switch flushes a draft and preserves sources and decisions');
 await click('accept');await wait('state.counts.edited===1 && saving===0');
 // An invalid proposal exercises server validation messages and export-editor drafts.
 await evaluate("select(state.entries.find(e=>e.context==='demo->welcome').key)");await click('edit');
 await evaluate("document.querySelector('.edit-form').value='Sin variable';document.querySelector('.edit-form').dispatchEvent(new Event('input',{bubbles:true}));");
 await click('accept');await wait('!saving && !dirty');await click('export');await wait('exportReport?.errors.length===1');
 assert(await evaluate("document.getElementById('validation').textContent.includes('placeholders differ')"),'English server validation');
 await evaluate("document.querySelector('.correct-check').click();document.querySelector('.export-edit-form').value='Corrección %{name}';document.querySelector('.export-edit-form').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('confirm-pending').checked=true;");
 await click('ui-es');await wait("uiLanguage==='es'");
 assert(await evaluate("document.querySelector('.export-edit-form').value==='Corrección %{name}' && exportDrafts.size===1 && document.getElementById('confirm-pending').checked && document.getElementById('validation').textContent.includes('placeholders distintos') && document.querySelector('.save-correction').textContent==='Guardar corrección'"),'Export drafts, acknowledgments and localized validation survive switching');
 assert(await evaluate("document.getElementById('user-guide').getAttribute('href')==='/USER_GUIDE.es.md' && document.getElementById('user-guide').textContent==='Guía de uso'"),'Guide follows interface language');
 await evaluate("document.querySelector('.cancel-correction').click()");await click('close-export');
 await send('Page.navigate',{url:base});await wait("typeof state!=='undefined' && state && !catalogBusy()");
 assert(await evaluate("uiLanguage==='es' && document.documentElement.lang==='es-AR' && state.counts.accepted===1 && state.counts.edited===1"),'Language and review decisions survive reload');
 await click('ui-en');await wait("uiLanguage==='en'");
 assert(await evaluate("document.querySelector('h1').textContent==='AI proposes. You choose the words.' && document.getElementById('user-guide').getAttribute('href')==='/USER_GUIDE.md'"),'Switching back restores English');
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK i18n: defaults, live switching, server messages, drafts, validation, guide and persistence.');
} finally {await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
