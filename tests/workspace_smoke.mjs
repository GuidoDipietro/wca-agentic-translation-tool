import {readFile, writeFile} from 'node:fs/promises';
const [base, debug, component]=process.argv.slice(2);
const tab=await(await fetch(`${debug}/json/new?${encodeURIComponent('about:blank')}`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let sequence=0;const pending=new Map(),errors=[];
ws.onmessage=event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);};
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));return result.result.value;}
async function wait(expression){for(let i=0;i<120;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,70));}throw new Error('Timeout: '+expression);}
async function click(id){await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);}
function assert(value,message){if(!value)throw new Error(message);}
try {
 await send('Page.enable');await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1360,height:1000,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:base});
 await wait("typeof state!=='undefined' && state && state.workspace && !document.getElementById('workspace-panel').hidden");
 assert(await evaluate('state.workspace.active_id===null'),'Clean repository opens without PO files');
 await wait('!catalogBusy()');
 await evaluate(`document.getElementById('component').value=${JSON.stringify(component || 'regulations')};document.getElementById('component').dispatchEvent(new Event('change'))`);
 await wait('!catalogBusy() && !document.getElementById("sync").disabled');
 await evaluate("document.getElementById('manual-mode').checked=true;document.getElementById('manual-mode').dispatchEvent(new Event('change'))");
 await click('sync');await wait('state.workspace.sessions.length===1 && !document.getElementById("sync").disabled');
 assert(await evaluate('state.workspace.candidate_count===0'),'First complete base has no fabricated work');
 await evaluate("document.getElementById('manual-mode').checked=true;document.getElementById('manual-mode').dispatchEvent(new Event('change'))");
 await click('sync');await wait('state.workspace.sessions.length===2 && !document.getElementById("sync").disabled');
 assert(await evaluate('state.workspace.candidate_count===2 && !state.workspace.ready'),'New session prepares candidate entries');
 const session=await evaluate('state.workspace.active_id');const folder=await evaluate('state.workspace.folder');
 await click('copy-prompt');await wait('document.getElementById("prompt-details").open');
 assert(await evaluate('document.getElementById("agent-prompt").value.includes("proposals.json")'),'Agent prompt is available');
 const work=JSON.parse(await readFile(`${folder}/work.json`,'utf8'));
 const proposals={version:1,session_id:work.session_id,base_sha256:work.base_sha256,entries:work.entries.map(e=>({key:e.key,target:e.context==='greeting'?['Hola revisada %s']:['Nueva clave'],note:'Agent note'}))};
 await writeFile(`${folder}/proposals.json`,JSON.stringify(proposals));
 await click('load-proposals');await wait('state.workspace.ready && state.entries.length===2 && state.counts.pending===2');
 assert(await evaluate('document.getElementById("metadata").textContent.includes("Agent note")'),'Agent notes are displayed');
 await click('accept');await wait('state.counts.accepted===1 && saving===0');
 await click('accept');await wait('state.counts.accepted===2 && saving===0');
 const result=await evaluate('api("/api/export",{revision:state.revision})');
 assert(result.filename.endsWith(component==='payload'?'.json':component==='locales'?'.yaml':'.po'),'Correct native export format');
 if(component==='payload') {const data=JSON.parse(result.content);assert(data.new.message==='Nueva clave' && data.stable.message==='Sin tocar','Payload JSON preserves keys and messages');}
 assert(result.ok && result.content.includes('Nueva clave') && !result.content.includes('#, fuzzy'),'Manual export contains approvals and clears fuzzy');
 await send('Page.navigate',{url:base});await wait("typeof state!=='undefined' && state && state.counts.accepted===2 && !document.getElementById('sync').disabled");
 await evaluate("document.getElementById('manual-mode').checked=true;document.getElementById('manual-mode').dispatchEvent(new Event('change'))");
 await click('sync');await wait('state.workspace.sessions.length===3 && !document.getElementById("sync").disabled');
 await evaluate(`document.getElementById('sessions').value=${JSON.stringify(session)}`);await click('open-session');
 await wait(`state.workspace.active_id===${JSON.stringify(session)} && state.counts.accepted===2`);
 const previousSession=await evaluate('state.workspace.active_id');
 await click('sync');await wait('!syncing && !document.getElementById("error").hidden');
 assert(await evaluate(`state.workspace.active_id===${JSON.stringify(previousSession)}`),'Failed sync preserves the previous session');
 assert(await evaluate('document.getElementById("workspace-status").textContent.includes("The last session has been preserved")'),'Failed sync identifies the preserved session');
 // A failed sync also refreshes the catalog; retry only once the button is usable.
 await wait('!catalogBusy() && !document.getElementById("sync").disabled');
 await click('sync');await wait(`!syncing && state.workspace.active_id!==${JSON.stringify(previousSession)}`);
 assert(await evaluate('document.getElementById("error").hidden'),'Successful retry clears the previous error banner');
 assert(await evaluate('!document.getElementById("sync-indicator").classList.contains("failed")'),'Successful retry clears the failure indicator');
 assert(await evaluate('state.workspace.created_at && document.getElementById("workspace-detail").textContent.includes("Base downloaded:")'),'The downloaded base has a visible timestamp');
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'Workspace fits mobile screen');
 assert(errors.length===0,'No browser exceptions: '+errors.join('; '));
 console.log('OK browser: empty repository → current sync → new keys → prompt → proposals → review → export → restore session.');
} finally {await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
