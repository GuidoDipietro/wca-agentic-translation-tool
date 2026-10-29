'use strict';
let glossaryCatalog=null, glossaryIndex=-1, glossaryDirty=false, glossaryBusy=false, glossaryLastMessage='';
const glossaryFields=['source','preferred','forbidden','note'];
function glossaryMessage(message) {glossaryLastMessage=message;$('glossary-message').textContent=t(message);}
function glossaryDiscard() {return !glossaryDirty || window.confirm(t('Discard unsaved glossary changes?'));}
function renderGlossary() {
  if(!glossaryCatalog)return;
  $('glossary-message').textContent=t(glossaryLastMessage);
  const query=$('glossary-search').value.toLocaleLowerCase();
  $('glossary-summary').textContent=t('{0} · {1} terms · {2}',glossaryCatalog.language,glossaryCatalog.entries.length,t({local:'Personal glossary',bundled:'Default glossary',empty:'No configured terms'}[glossaryCatalog.origin]));
  $('glossary-list').replaceChildren();
  glossaryCatalog.entries.forEach((entry,index)=>{
    if(![...entry.source,...entry.preferred,...entry.forbidden,entry.note || ''].join('\n').toLocaleLowerCase().includes(query))return;
    const button=textNode('button',entry.source.join(' / ')+' → '+entry.preferred.join(' / '),'entry');
    button.type='button';button.classList.toggle('selected',index===glossaryIndex);button.setAttribute('aria-pressed',String(index===glossaryIndex));button.disabled=glossaryBusy;
    button.onclick=()=>{if(glossaryDiscard())selectGlossaryTerm(index);};
    $('glossary-list').append(button);
  });
  $('glossary-remove').disabled=glossaryIndex<0 || glossaryBusy;
}
function selectGlossaryTerm(index) {
  glossaryIndex=index;glossaryDirty=false;
  const entry=glossaryCatalog.entries[index];
  for(const field of glossaryFields)$('glossary-'+field).value=field==='note'?(entry?.note || ''):(entry?.[field] || []).join('\n');
  glossaryMessage('');renderGlossary();
}
async function glossaryAction(action) {
  if(glossaryBusy)return;
  glossaryBusy=true;$('glossary-editor').disabled=true;
  for(const id of ['glossary-add','glossary-import','glossary-export','glossary-close','glossary-search'])$(id).disabled=true;
  renderGlossary();
  try {await action();} catch(exc) {glossaryMessage(exc.message);}
  finally {
    glossaryBusy=false;$('glossary-editor').disabled=false;
    for(const id of ['glossary-add','glossary-import','glossary-export','glossary-close','glossary-search'])$(id).disabled=false;
    renderGlossary();
  }
}
async function saveGlossaryEntries(entries) {
  const document={version:1,language:glossaryCatalog.language,entries};
  glossaryCatalog=await api('/api/glossary',{language:document.language,document,revision:glossaryCatalog.revision});
}
$('glossary-open').onclick=()=>act(async()=>{
  if(!state?.workspace)throw new Error('A workspace session is required.');
  const language=$('language').value;
  glossaryCatalog=await api('/api/glossary?language='+encodeURIComponent(language));
  $('glossary-search').value='';selectGlossaryTerm(glossaryCatalog.entries.length?0:-1);
  $('glossary-dialog').showModal();
});
$('glossary-close').onclick=()=>{if(glossaryDiscard()){$('glossary-dialog').close();glossaryDirty=false;}};
$('glossary-dialog').addEventListener('cancel',event=>{if(glossaryBusy || !glossaryDiscard())event.preventDefault();else glossaryDirty=false;});
$('glossary-search').oninput=renderGlossary;
$('glossary-add').onclick=()=>{if(glossaryDiscard()){selectGlossaryTerm(-1);$('glossary-source').focus();}};
for(const field of glossaryFields)$('glossary-'+field).oninput=()=>{glossaryDirty=true;glossaryMessage('Unsaved glossary changes');};
$('glossary-form').onsubmit=event=>{
  event.preventDefault();
  glossaryAction(async()=>{
    const entry={};
    for(const field of ['source','preferred','forbidden'])entry[field]=$('glossary-'+field).value.split('\n').map(v=>v.trim()).filter(Boolean);
    entry.note=$('glossary-note').value;
    const entries=glossaryCatalog.entries.slice();
    const index=glossaryIndex<0?entries.length:glossaryIndex;
    entries[index]=entry;
    await saveGlossaryEntries(entries);selectGlossaryTerm(index);glossaryMessage('Glossary saved. Changes apply to new sessions.');
  });
};
$('glossary-remove').onclick=()=>glossaryAction(async()=>{
  if(glossaryIndex<0 || !window.confirm(t('Delete this glossary term?')))return;
  await saveGlossaryEntries(glossaryCatalog.entries.filter((_,index)=>index!==glossaryIndex));
  selectGlossaryTerm(-1);glossaryMessage('Glossary saved. Changes apply to new sessions.');
});
$('glossary-export').onclick=()=>{
  const {version,language,entries}=glossaryCatalog;
  const url=URL.createObjectURL(new Blob([JSON.stringify({version,language,entries},null,2)+'\n'],{type:'application/json;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=language+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('glossary-import').onclick=()=>{if(glossaryDiscard())$('glossary-file').click();};
$('glossary-file').onchange=()=>glossaryAction(async()=>{
  const file=$('glossary-file').files[0];$('glossary-file').value='';if(!file)return;
  if(file.size>2*1024*1024)throw new Error('The glossary exceeds the 2 MB limit.');
  const document=JSON.parse(await file.text());
  if(!window.confirm(t('Replace the configured glossary for this language with this file?')))return;
  glossaryCatalog=await api('/api/glossary',{language:glossaryCatalog.language,document,revision:glossaryCatalog.revision});
  selectGlossaryTerm(-1);glossaryMessage('Glossary saved. Changes apply to new sessions.');
});
window.addEventListener('beforeunload',event=>{if(glossaryDirty){event.preventDefault();event.returnValue='';}});
