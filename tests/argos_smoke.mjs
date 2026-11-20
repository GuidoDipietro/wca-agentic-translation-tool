const [base,debug,engine]=process.argv.slice(2);
const tab=await(await fetch(`${debug}/json/new?${encodeURIComponent('about:blank')}`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let seq=0;const pending=new Map(),errors=[];
ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);};
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function wait(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,70));}throw new Error('Timeout: '+expression);}
async function click(id){await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);}
async function type(id,value){await evaluate(`{const field=document.getElementById(${JSON.stringify(id)});field.value=${JSON.stringify(value)};field.dispatchEvent(new Event('input',{bubbles:true}));}true`);}
function assert(value,message){if(!value)throw new Error(message);}
const ready="typeof state!=='undefined' && state && connectionState";
try {
 await send('Page.enable');await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1360,height:1000,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:base});
 await wait(ready);
 await wait("[...document.getElementById('language').options].some(o=>o.value==='fr')");
 await evaluate("document.getElementById('language').value='fr';const p=document.getElementById('ai-provider');p.value='libretranslate';p.dispatchEvent(new Event('change'));true");
 await wait("document.getElementById('model').selectedOptions[0]?.textContent==='Set up Argos'");
 assert(await evaluate("document.getElementById('model').disabled && !canTranslate()"),'Argos is not usable before an address is saved');
 await click('connections-open');await wait("document.getElementById('connections-dialog').open");
 assert(await evaluate("document.getElementById('libretranslate-status').textContent==='Not installed · pipx install libretranslate'"),'Argos starts unconfigured');
 await type('libretranslate-url','ftp://example.org');await click('libretranslate-save');
 await wait("document.getElementById('error').textContent.includes('must start with http')");
 assert(await evaluate("document.getElementById('libretranslate-status').textContent==='Not installed · pipx install libretranslate'"),'A non-HTTP address is refused');
 await type('libretranslate-url',engine+'/');await click('libretranslate-save');
 await wait("document.getElementById('connection-message').textContent.includes('Argos settings saved')");
 assert(await evaluate(`document.getElementById('libretranslate-status').textContent==='✓ Server configured' && document.getElementById('libretranslate-url').value==='' && !document.body.textContent.includes(${JSON.stringify(engine)})`),'Saved address is shown as a status and never echoed');
 assert(await evaluate("document.getElementById('connection-summary').textContent.includes('Argos ✓')"),'Summary shows Argos');
 await click('connections-close');
 await wait("document.getElementById('model').value==='default' && !document.getElementById('model').disabled");
 await click('sync');await wait("!syncing && state.workspace.language==='fr' && state.workspace.ready && state.counts.pending===2");
 assert(await evaluate("document.getElementById('sync-current').textContent.includes('none approved') && state.counts.accepted===0"),'Argos proposals stay pending');
 assert(await evaluate("current().proposed[0]==='Bonjour, %{name}'"),'Argos proposal keeps the placeholder');
 assert(await evaluate("state.entries.find(e=>e.context==='count').proposed.join('|')==='Un cube|%{count} cubes|%{count} cubes'"),'Plural forms are laid out from the two English sources');
 await evaluate("window.beforeReload=true;location.reload();true");
 await wait("!window.beforeReload && "+ready);
 assert(await evaluate("document.getElementById('ai-provider').value==='libretranslate'"),'Provider choice is remembered');
 await click('connections-open');await wait("document.getElementById('connections-dialog').open");
 await type('libretranslate-url','');await click('libretranslate-save');
 await wait("document.getElementById('libretranslate-status').textContent==='Not installed · pipx install libretranslate'");
 assert(await evaluate("!canTranslate() && !document.getElementById('connection-summary').textContent.includes('Argos')"),'Emptying the address disconnects Argos');
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK Argos: unconfigured → refused address → saved address → translate with placeholders and plurals → remembered provider → cleared address.');
} finally {await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
