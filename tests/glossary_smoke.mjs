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
 const receive=ws.onmessage;let downloadName='';
 ws.onmessage=event=>{receive(event);const message=JSON.parse(event.data);if(message.method==='Page.javascriptDialogOpening')send('Page.handleJavaScriptDialog',{accept:true});if(message.method==='Browser.downloadWillBegin')downloadName=message.params.suggestedFilename;};
 await send('Browser.setDownloadBehavior',{behavior:'deny',eventsEnabled:true});
 await send('Page.enable');await send('Runtime.enable');await send('Page.navigate',{url:base});
 await wait("typeof state!=='undefined' && state && state.workspace.demo && typeof glossaryCatalog!=='undefined'");
 await click('glossary-open');await wait("document.getElementById('glossary-dialog').open");
 assert(await evaluate("glossaryCatalog.origin==='bundled' && glossaryCatalog.entries.length===2"),'Bundled terms loaded');
 await evaluate("document.getElementById('glossary-search').value='trainee';document.getElementById('glossary-search').dispatchEvent(new Event('input'))");
 assert(await evaluate("document.querySelectorAll('#glossary-list button').length===1"),'Term search filters list');
 await evaluate("document.querySelector('#glossary-list button').click();document.getElementById('glossary-preferred').value='Delegado en prácticas';document.getElementById('glossary-preferred').dispatchEvent(new Event('input'))");
 await click('glossary-save');await wait("!glossaryBusy && !glossaryDirty && glossaryCatalog.origin==='local'");
 assert(await evaluate("glossaryCatalog.entries[0].preferred[0]==='Delegado en prácticas'"),'Edited reference saved');
 await evaluate("changeUILanguage('es')");
 assert(await evaluate("document.getElementById('glossary-title').textContent==='Configurar glosario' && document.getElementById('glossary-preferred').value==='Delegado en prácticas'"),'Interface language preserves term data');
 await click('glossary-add');
 await evaluate("document.getElementById('glossary-source').value='<script>alert(1)</script>';document.getElementById('glossary-preferred').value='Texto seguro';document.getElementById('glossary-preferred').dispatchEvent(new Event('input'))");
 await click('glossary-save');await wait("!glossaryBusy && glossaryCatalog.entries.length===3");
 await evaluate("document.getElementById('glossary-search').value='';document.getElementById('glossary-search').dispatchEvent(new Event('input'))");
 assert(await evaluate("document.querySelectorAll('#glossary-list script').length===0 && document.getElementById('glossary-list').textContent.includes('<script>')"),'Terms remain literal text');
 // Another tab saves first: the editor must keep the pending input on conflict.
 await evaluate("api('/api/glossary',{language:glossaryCatalog.language,revision:glossaryCatalog.revision,document:{version:1,language:glossaryCatalog.language,entries:glossaryCatalog.entries}})");
 // Saving identical local content does not change the revision. Make an actual external edit.
 await evaluate("(async()=>{const c=await api('/api/glossary?language=es_419');c.entries[1].preferred=['Otro cubo'];await api('/api/glossary',{language:c.language,revision:c.revision,document:{version:1,language:c.language,entries:c.entries}});})()");
 await evaluate("document.getElementById('glossary-preferred').value='Borrador local';document.getElementById('glossary-preferred').dispatchEvent(new Event('input'))");
 await click('glossary-save');await wait("!glossaryBusy && document.getElementById('glossary-message').textContent.includes('cambió')");
 assert(await evaluate("glossaryDirty && document.getElementById('glossary-preferred').value==='Borrador local'"),'Stale edit preserves unsaved input');
 await evaluate("glossaryDirty=false");await click('glossary-close');await click('glossary-open');
 await wait("document.getElementById('glossary-dialog').open && glossaryCatalog.entries[1].preferred[0]==='Otro cubo'");
 await click('glossary-close');await click('sync');await wait('!syncing && state.workspace.ready');
 const id=await evaluate('state.workspace.active_id');
 assert(await evaluate("(async()=> (await api('/api/state')).workspace.glossary_count===3)()"),'Session records effective terms');
 await click('glossary-open');await wait("document.getElementById('glossary-dialog').open");
 await evaluate("document.getElementById('glossary-preferred').value='Referencia posterior';document.getElementById('glossary-preferred').dispatchEvent(new Event('input'))");await click('glossary-save');await wait('!glossaryBusy && !glossaryDirty');await click('glossary-close');
 assert(await evaluate('state.workspace.active_id')===id,'Editing configuration keeps the active review');
 await send('Page.navigate',{url:base});await wait("typeof state!=='undefined' && state && typeof glossaryCatalog!=='undefined'");
 await click('glossary-open');await wait("document.getElementById('glossary-dialog').open");
 assert(await evaluate("glossaryCatalog.entries[0].preferred[0]==='Referencia posterior'"),'Personal terms persist across reloads');
 // Export the saved personal catalog, then import a replacement through the file input.
 await click('glossary-export');
 for(let i=0;i<30 && !downloadName;i++)await new Promise(r=>setTimeout(r,50));
 assert(downloadName==='es_419.json','Export uses the normalized language filename');
 await evaluate(`(()=>{const input=document.getElementById('glossary-file');const transfer=new DataTransfer();transfer.items.add(new File([JSON.stringify({version:1,language:'fr',entries:[]})],'wrong.json',{type:'application/json'}));input.files=transfer.files;input.dispatchEvent(new Event('change'));})()`);
 await wait("!glossaryBusy && document.getElementById('glossary-message').textContent.includes('no coincide')");
 assert(await evaluate('glossaryCatalog.entries.length===3'),'Wrong-language import keeps the previous catalog');
 await evaluate(`(()=>{const input=document.getElementById('glossary-file');const transfer=new DataTransfer();transfer.items.add(new File([JSON.stringify({version:1,language:'es-419',entries:[{source:['Scrambler'],preferred:['Mezclador'],forbidden:[]}]})],'es_419.json',{type:'application/json'}));input.files=transfer.files;input.dispatchEvent(new Event('change'));})()`);
 await wait("!glossaryBusy && glossaryCatalog.entries.length===1 && glossaryCatalog.entries[0].source[0]==='Scrambler'");
 await evaluate("document.querySelector('#glossary-list button').click()");await click('glossary-remove');await wait('!glossaryBusy && glossaryCatalog.entries.length===0');
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 console.log('OK glossary: defaults, search, editing, literal text, Spanish UI, stale edits, sync, persistence, export, import and deletion.');
}finally{await fetch(`${debug}/json/close/${tab.id}`);ws.close();}
