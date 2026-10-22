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
 await wait("typeof state!=='undefined' && state && connectionState");
 await click('connections-open');await wait("document.getElementById('connections-dialog').open");
 await click('chatgpt-connect');
 await wait("location.hostname==='localhost' && !!document.getElementById('provider-continue')");
 // Cometer una página de otro sitio reproduce los encabezados reales al volver.
 await click('provider-continue');
 await wait("location.hostname==='127.0.0.1' && location.pathname==='/' && typeof connectionState!=='undefined' && connectionState?.chatgpt?.active==='browser-account'");
 assert(await evaluate("document.title==='WCA Translation Studio'"),'OAuth returns to the application');
 assert(await evaluate("document.getElementById('connection-summary').textContent.includes('ChatGPT ✓')"),'Connected account is visible');
 assert(await evaluate("location.search==='' && !document.body.textContent.includes('Solicitud externa bloqueada')"),'Callback query is removed and navigation is accepted');
 assert(await evaluate("provider()==='chatgpt'"),'Connected ChatGPT is selected');
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK OAuth: conectar → proveedor en otro sitio → callback → pantalla principal y cuenta conectada.');
} finally {await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
