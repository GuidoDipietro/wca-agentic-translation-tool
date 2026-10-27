// Se ejecuta desde test_browser.py. Usa el WebSocket incorporado de Node >=22.
'use strict';
const [base, debug] = process.argv.slice(2);
const tab = await (await fetch(`${debug}/json/new?${encodeURIComponent('about:blank')}`, {method:'PUT'})).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let seq=0;const pending=new Map(),errors=[];
ws.onmessage=event=>{const msg=JSON.parse(event.data);if(msg.id){const task=pending.get(msg.id);pending.delete(msg.id);msg.error?task.reject(new Error(JSON.stringify(msg.error))):task.resolve(msg.result);}if(msg.method==='Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.text);};
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));return result.result.value;}
async function wait(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,70));}throw new Error('Timeout: '+expression);}
async function click(id){await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);}
function assert(value,message){if(!value)throw new Error(message);}
try{
 await send('Page.enable');await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1360,height:1000,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:base});
 await wait("typeof state !== 'undefined' && state && document.querySelectorAll('.entry').length===2");
 assert(await evaluate("state.counts.pending===2"),'Initial pending count');
 await click('accept');await wait('state.counts.accepted===1 && saving===0');
 await wait("current().context==='plural'");
 assert(await evaluate("current().context==='plural'"),'Accept advances automatically');
 await click('reject');await wait('state.counts.rejected===1 && saving===0');
 await click('edit');await wait("document.querySelectorAll('.edit-form').length===2");
 await evaluate("document.querySelectorAll('.edit-form').forEach((e,i)=>{e.value=i===0?'Un cubo revisado\\nSegunda línea':'Cubos revisados';e.dispatchEvent(new Event('input',{bubbles:true}));})");
 await wait("!dirty && saving===0 && current().decision.draft && current().decision.status==='pending'");
 await send('Page.navigate',{url:base});await wait("typeof state!=='undefined' && state && state.counts.pending===1");
 await evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");
 await wait("document.querySelectorAll('.edit-form').length===2");
 assert(await evaluate("document.querySelector('.edit-form').value==='Un cubo revisado\\nSegunda línea'"),'Draft survived browser reload');
 await evaluate("document.querySelector('.edit-form').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true}))");
 await wait('state.counts.edited===1 && saving===0');
 assert(await evaluate("current().context==='plural'"),'Approval of last entry stays on last entry');
 const output=await evaluate("api('/api/export',{revision:state.revision})");
 assert(output.ok && output.content.includes('Cubos revisados'),'Export with edited plurals');
 await click('pending');await wait('state.counts.pending===1 && saving===0');
 await click('export');await wait("document.getElementById('export-dialog').open");
 assert(await evaluate("!document.getElementById('pending-ack').hidden"),'Pending warning visible');
 await click('close-export');
 await evaluate('select(state.entries[0].key)'); await click('edit');
 await wait("document.querySelectorAll('.edit-form').length===1");
 await evaluate("document.querySelector('.edit-form').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true}))");
 await wait("current().context==='plural' && state.entries[0].decision.status==='edited' && saving===0");
 // The pending filter must not cause the next entry to be skipped.
 await click('pending');await wait('saving===0 && current().decision.status==="pending"');
 await evaluate('select(state.entries[0].key)');await click('pending');await wait('saving===0 && state.counts.pending===2');
 await evaluate("document.getElementById('filter').value='pending';document.getElementById('filter').dispatchEvent(new Event('change'))");
 await click('accept');await wait("current()?.context==='plural' && state.counts.pending===1 && saving===0");
 await click('accept');await wait("!current() && state.counts.pending===0 && saving===0");
 assert(await evaluate("!document.getElementById('empty').hidden"),'Completed pending filter shows empty state');
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'),'No horizontal overflow on mobile');
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK browser: decisions, shortcuts, drafts, reload, plurals, export, warnings and mobile.');
}finally{await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
