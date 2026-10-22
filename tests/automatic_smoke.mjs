import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
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
async function pressKey(key,code,virtualKey,text){
 await send('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:virtualKey,...(text?{text}:{})});
 await send('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:virtualKey});
}
async function screenshot(name){if(!process.env.WCA_TEST_SCREENSHOTS)return;if(name==='panel')await evaluate('window.scrollTo(0,0)');await mkdir(process.env.WCA_TEST_SCREENSHOTS,{recursive:true});const shot=await send('Page.captureScreenshot',{format:'png'});await writeFile(join(process.env.WCA_TEST_SCREENSHOTS,name+'.png'),Buffer.from(shot.data,'base64'));}
try {
 await send('Page.enable');await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1360,height:1000,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:base});
 await wait("typeof state!=='undefined' && state && connectionState");
 await wait("[...document.getElementById('language').options].some(o=>o.value==='fr')");
 await evaluate("document.getElementById('language').value='fr'");
 await wait("document.getElementById('model').value==='test-model'");
 await click('sync');await wait("!syncing && state.workspace.language==='fr' && state.workspace.ready && state.counts.pending===2");
 assert(await evaluate("document.getElementById('sync-current').textContent.includes('ninguna aprobada')"),'Generation leaves proposals pending');
 assert(await evaluate("current().proposed[0]==='Bonjour, %{name}'"),'AI proposal is shown');
 await evaluate("catalogTask('slow-test','Catálogo de prueba…',()=>new Promise(resolve=>{window.releaseLookup=resolve;}));true");
 await click('accept');await wait('state.counts.accepted===1 && saving===0');
 assert(await evaluate("document.getElementById('error').hidden"),'Approval does not fail or wait for a pending catalog');
 await evaluate("window.releaseLookup();true");
 assert(await evaluate("current().plural_forms.join(',')==='one,many,other'"),'French plural forms are separate');
 await click('edit');await wait("document.querySelectorAll('.edit-form').length===3");
 await evaluate("const field=document.querySelector('.edit-form');field.value='Un cube révisé';field.dispatchEvent(new Event('input',{bubbles:true}));");
 await wait('!dirty && saving===0');await click('accept');
 await wait('state.counts.edited===1 && saving===0');
 const exported=await evaluate("api('/api/export',{revision:state.revision,mode:'changes'})");
 assert(exported.ok && exported.filename==='fr_reviewed.yml' && exported.content.includes('Un cube révisé') && !exported.content.includes('Déjà traduit'),'Native export contains user decisions');
 assert(await evaluate("!document.getElementById('translation-mode') && !document.getElementById('load-models') && !document.getElementById('load-languages') && !document.getElementById('export-mode')"),'No redundant selectors or fetch buttons');
 await screenshot('panel');
 await click('connections-open');await wait("document.getElementById('connections-dialog').open");
 assert(await evaluate("!document.getElementById('openai-key') && document.getElementById('weblate-key').value==='' && document.getElementById('weblate-key').placeholder.includes('Token guardado') && !document.body.textContent.includes('fake-test-key')"),'Credentials are never echoed');
 assert(await evaluate("document.querySelector('#connections-dialog .connection-card').querySelectorAll('button').length===1"),'ChatGPT has one account action');
 await screenshot('conexiones');
 await evaluate("document.getElementById('weblate-key').value='another-private-test-token'");await click('connections-save');
 await wait("document.getElementById('connection-message').textContent.includes('Token guardado para esta sesión')");
 assert(await evaluate("document.getElementById('weblate-key').value==='' && document.getElementById('weblate-status').textContent.includes('Token guardado') && document.getElementById('weblate-key').placeholder.includes('Token guardado')"),'Saved Weblate token is represented without echoing it');
 await click('connections-close');
 await evaluate("select(state.entries.find(e=>e.context==='greeting').key)");await click('edit');
 await evaluate("document.querySelector('.edit-form').value='Bonjour sans variable';document.querySelector('.edit-form').dispatchEvent(new Event('input',{bubbles:true}))");
 await click('accept');await wait("state.entries.find(e=>e.context==='greeting').decision.values?.[0]==='Bonjour sans variable' && saving===0");
 await evaluate("document.getElementById('export').focus()");
 await click('export');await wait("document.getElementById('export-dialog').open && exportReport.errors.length===1");
 assert(await evaluate("document.getElementById('download').disabled"),'Validation error initially blocks download');
 const unchangedRevision=await evaluate('state.revision');
 await evaluate("document.querySelector('.correct-check').click();document.querySelector('.export-edit-form').value='Borrador que se cancela';document.querySelector('.export-edit-form').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.cancel-correction').click()");
 assert(await evaluate("!document.querySelector('.export-edit-form') && !exportDrafts.size && !document.querySelector('.correct-check').hidden"),'Cancel closes only the correction editor');
 assert(await evaluate(`state.revision===${unchangedRevision} && state.entries.find(e=>e.context==='greeting').decision.values[0]==='Bonjour sans variable'`),'Cancel leaves the approved translation and revision unchanged');
 await evaluate("document.querySelector('.dismiss-check').click()");await wait("exportReport.errors.length===0 && exportReport.dismissed.length===1");
 assert(await evaluate("!document.getElementById('download').disabled"),'Dismissing the specific check enables download');
 await evaluate("document.querySelector('.correct-check').click();document.querySelector('.export-edit-form').value='Autre sans variable';document.querySelector('.export-edit-form').dispatchEvent(new Event('input',{bubbles:true}))");
 await evaluate("document.querySelector('.save-correction').click()");await wait("exportReport.errors.length===1 && !exportReport.dismissed.length && !exportBusy");
 assert(await evaluate("document.getElementById('download').disabled"),'Editing invalidates the previous dismissal');
 await screenshot('exportacion');
 await evaluate("document.querySelector('.export-edit-form').value='Bonjour, %{name}';document.querySelector('.export-edit-form').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.save-correction').click()");
 await wait("!exportReport.errors.length && !exportReport.warnings.length && !exportBusy");
 await click('download');await wait("!document.getElementById('export-dialog').open");
 await pressKey('ArrowLeft','ArrowLeft',37);
 await wait("current()?.context==='greeting'");
 const revisionAfterDownload=await evaluate('state.revision');
 await pressKey('a','KeyA',65);
 await wait(`current()?.context==='count' && state.revision>${revisionAfterDownload} && saving===0`);
 // También debe funcionar cuando el foco queda explícitamente sobre un botón.
 await evaluate("document.getElementById('export').focus()");
 await pressKey('ArrowLeft','ArrowLeft',37);await wait("current()?.context==='greeting'");
 await pressKey('ArrowRight','ArrowRight',39);await wait("current()?.context==='count'");
 await evaluate("document.getElementById('export').focus()");await click('export');
 await wait("document.getElementById('export-dialog').open");
 const revisionInDialog=await evaluate('state.revision');
 await pressKey('a','KeyA',65);
 assert(await evaluate(`state.revision===${revisionInDialog}`),'Review shortcuts stay inactive inside dialogs');
 await click('close-export');await wait("!document.getElementById('export-dialog').open");
 await pressKey('ArrowLeft','ArrowLeft',37);await wait("current()?.context==='greeting'");
 const corrected=await evaluate("api('/api/export',{revision:state.revision,mode:'changes'})");
 assert(corrected.ok && corrected.content.includes('Bonjour, %{name}') && !corrected.content.includes('Déjà traduit'),'Single download contains only approved corrected changes');
 await click('edit');await wait("document.querySelector('.edit-form')===document.activeElement");
 const typingKey=await evaluate('selected');const textBefore=await evaluate("document.querySelector('.edit-form').value");
 await pressKey('a','KeyA',65,'a');
 assert(await evaluate(`selected===${JSON.stringify(typingKey)} && editing && document.querySelector('.edit-form').value!==${JSON.stringify(textBefore)}`),'Typing A in the editor still edits text');
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),'Setup and review fit on mobile');
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK UX: listas automáticas → ChatGPT → aceptar edición → token guardado → dismiss → corregir en exportación → descarga de cambios.');
} finally {await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
