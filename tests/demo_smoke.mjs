const [base,debug]=process.argv.slice(2);
const tab=await(await fetch(`${debug}/json/new?${encodeURIComponent('about:blank')}`,{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let seq=0;const pending=new Map(),errors=[];
ws.onmessage=event=>{const msg=JSON.parse(event.data);if(msg.id){const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(new Error(JSON.stringify(msg.error))):p.resolve(msg.result);}if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails.text);};
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function wait(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,70));}throw new Error('Timeout: '+expression);}
async function click(id){await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);}
function assert(value,message){if(!value)throw new Error(message);}
try{
 await send('Page.enable');await send('Runtime.enable');await send('Page.navigate',{url:base});
 await wait("typeof state!=='undefined' && state && state.workspace.demo");
 assert(await evaluate("!document.getElementById('demo-banner').hidden"),'Demo has a visible label');
 assert(await evaluate("formatTime(new Date(2026,0,1,23,7,9).getTime())==='23:07:09'"),'Stage timestamps use 24 hours');
 assert(await evaluate("formatDateTime(new Date(2026,0,1,23,7,9)).includes('23:07')"),'Session timestamps use 24 hours');
 await click('sync');
 await wait("syncing && Number(document.getElementById('sync-progress').value)>0 && Number(document.getElementById('sync-progress').value)<7");
 assert(await evaluate("!document.getElementById('sync-indicator').hidden && document.querySelectorAll('#sync-stages li').length===7"),'Real intermediate stages arrive before completion');
 await wait('!syncing && state.workspace.candidate_count===3');
 assert(await evaluate("document.getElementById('sync-progress').value===document.getElementById('sync-progress').max"),'Progress completes');
 await wait('state.workspace.ready && state.entries.length===3');
 await click('accept');await wait('state.counts.accepted===1 && saving===0');
 await click('edit');await wait("document.querySelectorAll('.edit-form').length===1");
 await evaluate("const field=document.querySelector('.edit-form');field.value='Hola, %{name}';field.dispatchEvent(new Event('input',{bubbles:true}));");
 await wait('!dirty && saving===0');
 await evaluate("document.querySelector('.edit-form').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true}))");
 await wait('state.counts.edited===1 && saving===0');
 assert(await evaluate('current().proposed.length===3'),'All three plural forms are present');
 await click('accept');await wait('state.counts.accepted===2 && saving===0');
 const result=await evaluate("api('/api/export',{revision:state.revision})");
 assert(result.ok && result.filename.startsWith('DEMO_') && result.content.includes('Hola, %{name}') && result.content.includes('Ya traducido'),'Demo exports approved changes with a marker');
 const suggestions=await evaluate("api('/api/export',{revision:state.revision,mode:'suggestions'})");
 assert(suggestions.ok && !suggestions.content.includes('Ya traducido'),'Suggestions omit untouched entries');
 await send('Page.navigate',{url:base});await wait("typeof state!=='undefined' && state && state.counts.edited===1 && state.counts.accepted===2");
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK demo: live stages, 24-hour times, review, editing, plurals, exports and persistence.');
}finally{await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
