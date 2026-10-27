'use strict';
const $ = id => document.getElementById(id);
const labels = {pending:'Pending', accepted:'Accepted', rejected:'Rejected', edited:'Edited'};
let state, selected, editing = false, dirty = false, timer, chain = Promise.resolve(), saving = 0;
let draftVersion = 0;
let syncing=false, syncStarted=0, syncTimer, connectionState;
let activeStageLabels, projectStatus=null, syncFailed=false;
const syncLabels=["Remote status","Files and units","Validation","Consistency","Glossary","Local session","Ready"];
let lastSyncEvent, lastError;
function formatDateTime(value) {return new Intl.DateTimeFormat(uiLanguage==='es'?'es-AR':'en-US',{dateStyle:'short',timeStyle:'short',hourCycle:'h23'}).format(new Date(value));}
function formatTime(value) {return new Intl.DateTimeFormat(uiLanguage==='es'?'es-AR':'en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(value));}
function syncStage(event) {
  lastSyncEvent=event;
  $('sync-indicator').hidden=false;
  $('sync-current').textContent=t(event.message);
  if(event.labels)activeStageLabels=event.labels;
  $('sync-progress').max=event.total || 7;
  $('sync-progress').value=event.stage;
  $('sync-stages').replaceChildren();
  (activeStageLabels || syncLabels).forEach((label,index)=>{const item=textNode('li',`${index+1}. ${t(label)}`);item.className=index+1<event.stage?'done':index+1===event.stage?'active':'';if(index+1===event.stage)item.append(textNode('small',formatTime(Date.now())));$('sync-stages').append(item);});
}
function syncStart() {
  $('error').hidden=true;
  syncing=true;activeStageLabels=null;syncStarted=Date.now();$('sync-indicator').classList.remove('failed');
  const tick=()=>{$('sync-elapsed').textContent=t("{0} s elapsed", Math.floor((Date.now()-syncStarted)/1000));};
  tick();syncTimer=setInterval(tick,1000);syncStage({stage:0,message:"Starting synchronization…"});
}
async function readSync(response) {
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result,streamError,recovery;
  function event(line) {if(!line.trim())return;const value=JSON.parse(line);if(value.error){streamError=value.error;recovery=value.recovery;}else if(value.result)result=value.result;else if(value.stage!==undefined)syncStage(value);}
  for(;;) {const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let end;while((end=buffer.indexOf('\n'))>=0){event(buffer.slice(0,end));buffer=buffer.slice(end+1);}if(done)break;}
  if(buffer.trim())event(buffer);if(streamError){if(recovery){state=recovery;selected=state.entries[0]?.key;editing=!!current()?.decision.draft;render();}throw new Error(streamError);}if(!result)throw new Error(t("The connection closed before synchronization completed. Reload to check whether the session was saved."));return result;
}

function error(message) { lastError=message; $('error').textContent = t(message); $('error').hidden = false; }
$('error').onclick = () => { $('error').hidden = true; };
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...data,...(state?.workspace ? {session_id:state.workspace.active_id,workspace_revision:state.workspace.revision} : {})})});
  if(response.ok && response.headers.get('Content-Type')?.includes('application/x-ndjson'))return readSync(response);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || t("The operation could not be completed."));
  return result;
}
function current() { return state.entries.find(e => e.key === selected); }
function visible() {
  const filter = $('filter').value, search = $('search').value.toLocaleLowerCase();
  return state.entries.filter(e => (filter === 'all' || (filter === 'conflict' ? e.conflict : e.decision.status === filter)) && (!search || [e.context,...e.source,...e.original,...e.proposed,...(e.decision.values || []),...(e.decision.draft || [])].join('\n').toLocaleLowerCase().includes(search)));
}
function textNode(tag, text, className) { const el = document.createElement(tag); el.textContent = text;if(className==='text')el.dir='auto'; if (className) el.className = className; return el; }
function showWorkspace() {
  const w=state.workspace;
  $('workspace-panel').hidden=!w;
  if(!w)return;
  $('demo-banner').hidden=!(w.demo || w.simulation);$('demo-banner').textContent=w.simulation?t("SIMULATION · copy of a real download with artificial gaps · do not upload exports"):t("DEMO · fictional data · no Weblate connection · do not upload these files");
  if(!$('component').dataset.initialized) {$('component').value=w.component || 'locales';setLanguage(w.language || 'es_419',w.language_name);$('component').dataset.initialized='true';}
  $('export').textContent=t("Download translation ↗");
  $('workspace-status').textContent=syncFailed ? t("Synchronization did not complete. The last session has been preserved.") : !w.active_id ? t("Prepare your first synchronization.") : !w.candidate_count ? t("No pending entries in {0} / {1} in this session.", w.component, w.language) : w.ready ? t("{0} proposals ready for review.", w.candidate_count) : t("{0} entries awaiting translation.", w.candidate_count);
  $('workspace-detail').textContent=w.demo ? t("Try reviewing the sample proposals.") : t("{0}ChatGPT proposes translations; you review them before downloading.", w.created_at ? t("Base downloaded: ")+formatDateTime(w.created_at)+'. ' : '');
  $('sessions').replaceChildren();
  if(!w.sessions.length) {const option=textNode('option',t("No sessions yet"));option.value='';$('sessions').append(option);}
  for(const session of w.sessions) {const option=textNode('option',t("{0} / {1} · {2} · {3} entries", session.component || 'regulations', session.language || 'es_419', formatDateTime(session.created_at), session.candidates));option.value=session.id;$('sessions').append(option);}
  $('sessions').value=w.active_id || '';
  $('open-session').disabled=!w.sessions.length;
  $('copy-prompt').disabled=!w.active_id || !w.candidate_count;
  $('load-proposals').disabled=!w.active_id || !w.candidate_count;
  $('export').disabled=!w.active_id || (!!w.candidate_count && !w.ready);
  $('agent-prompt').value=w.prompt;
  $('session-folder').textContent=w.folder ? t("Working folder: {0}", w.folder) : '';
  showWorkspaceWarnings();
  $('prompt-details').hidden=!w.active_id || !w.candidate_count;
  $('generation-status').textContent=w.ai_completed && !w.ready ? t("{0}/{1} proposals generated and saved.", w.ai_completed, w.candidate_count) : '';
  catalogStatus();
  $('empty').textContent=!w.active_id ? t("Sync with Weblate to get started.") : !w.candidate_count ? t("This session has no entries to translate in this component. Sync to check Weblate again.") : !w.ready ? t("Choose an AI connection and translate this session, or copy the prompt to use your agent.") : t("No entries match this filter.");
}
function showWorkspaceWarnings() {
  const warnings=[...(state?.workspace?.warnings || [])];
  if(projectStatus?.language===$('language').value && projectStatus.outside_supported>0) {
    warnings.push(t("The project has {0} pending strings; {1} are outside Locales, Regulations and Payload CMS. Those components may require other Weblate permissions.", projectStatus.pending, projectStatus.outside_supported));
  }
  $('workspace-warnings').textContent=warnings.map(item=>t(item)).join(' · ');
}
function showProgress() {
  showWorkspace();
  $('counts').replaceChildren();
  for (const [status, label] of Object.entries(labels)) {
    const span = document.createElement('span'); span.append(textNode('strong', state.counts[status]), t(label+' entries')); $('counts').append(span);
  }
  $('progress').max = Math.max(1, state.entries.length);
  $('progress').value = state.entries.length - state.counts.pending;
  $('total').textContent = state.entries.length;
  if (!dirty && !saving) $('save').textContent = t("✓ Progress saved locally");
  $('recent-status').textContent = state.recent ? t("Recent download loaded. It will be used as the export base.") : t("The base is this session’s download.");
  $('remove-recent').hidden = !state.recent;
}
function renderList() {
  const fragment = document.createDocumentFragment();
  const all = visible();
  for (const e of all) {
    const button = document.createElement('button'); button.className = 'entry' + (e.key === selected ? ' selected' : '');
    button.setAttribute('aria-current', e.key === selected ? 'true' : 'false');
    const label = textNode('small', ''); label.append(textNode('span', '●', 'dot ' + e.decision.status), t(labels[e.decision.status]) + (e.conflict ? t(" · CONFLICT") : ''));
    button.append(label, textNode('span', e.context || e.source[0], 'entry-title')); button.title = e.source[0];
    button.onclick = () => act(() => select(e.key)); fragment.append(button);
  }
  $('entries').replaceChildren(fragment);
  $('empty').hidden = all.length > 0;
  return all;
}
function diff(a, b) {
  const tokenize = value => value.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) || [];
  const left = tokenize(a), right = tokenize(b);
  if (left.length * right.length > 600000) return [[{text:a, changed:a !== b}], [{text:b, changed:a !== b}]];
  const dp = Array.from({length:left.length + 1}, () => new Uint32Array(right.length + 1));
  for (let i=left.length-1; i>=0; i--) for (let j=right.length-1; j>=0; j--) dp[i][j] = left[i] === right[j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j],dp[i][j+1]);
  const old=[], proposed=[]; let i=0,j=0;
  while (i<left.length || j<right.length) {
    if (i<left.length && j<right.length && left[i] === right[j]) { old.push({text:left[i++],changed:false}); proposed.push({text:right[j++],changed:false}); }
    else if (i<left.length && (j===right.length || dp[i+1][j]>=dp[i][j+1])) old.push({text:left[i++],changed:true});
    else proposed.push({text:right[j++],changed:true});
  }
  return [old, proposed];
}
function diffText(parts, className) { const box = textNode('div','','text'); for (const part of parts) box.append(textNode('span',part.text,part.changed ? className : '')); return box; }
function editorValues() { return [...document.querySelectorAll('.edit-form')].map(el => el.value); }
function renderEntry() {
  const e=current(); $('review').hidden = !e;
  if (!e) return;
  $('position').textContent = t("CHANGE {0} OF {1}", state.entries.indexOf(e)+1, state.entries.length);
  $('context').textContent = e.context || t("No context"); $('status').textContent = t(labels[e.decision.status]) + (e.conflict ? t(" · Conflict") : '');
  $('source').replaceChildren();
  e.source.forEach((value,i) => { if (e.source.length>1) $('source').append(textNode('div',i===0?t("Singular"):t("Plural"),'plural-label')); $('source').append(textNode('div',value,'text')); });
  $('forms').replaceChildren();
  const values = e.decision.draft || (e.decision.status==='edited' ? e.decision.values : e.proposed);
  e.original.forEach((old,i) => {
    if (e.original.length>1) $('forms').append(textNode('div',t("Plural form {0}", e.plural_forms?.[i] || i),'plural-label'));
    const [before,after] = diff(old,values[i]);
    const comparison = textNode('div','','comparison');
    const oldBox = textNode('section','','translation'), newBox = textNode('section','','translation proposal');
    oldBox.append(textNode('h3',t("Original translation")), diffText(before,'removed'));
    newBox.append(textNode('h3',e.decision.status==='edited' || e.decision.draft ? t("Your version") : t("Proposal")), diffText(after,'added'));
    comparison.append(oldBox,newBox); $('forms').append(comparison);
  });
  $('forms').append(textNode('p',t("Green: added text · Red strikethrough: removed text."),'legend'));
  if (editing) {
    const editor = textNode('section','','editor');
    editor.append(textNode('h3',t("Edit translation · draft saved automatically")));
    values.forEach((value,i) => {
      const label = textNode('label',e.original.length>1?t("Plural form {0}", e.plural_forms?.[i] || i):t("Your translation")); label.htmlFor = `form-${i}`;
      const input = document.createElement('textarea'); input.id=`form-${i}`; input.className='edit-form'; input.dir='auto'; input.value=value;
      input.addEventListener('input', () => { dirty=true; draftVersion++; $('save').textContent=t("Unsaved draft…"); clearTimeout(timer); timer=setTimeout(() => act(saveDraft),500); });
      editor.append(label,input);
    });
    const actions = textNode('div','','editor-actions');
    const approve = textNode('button',t("Approve my edit"),'primary'); approve.onclick = () => act(approveEdit);
    const close = textNode('button',t("Close editor")); close.onclick = () => act(async () => { await flush(); editing=false; renderEntry(); });
    actions.append(approve,close); editor.append(actions,textNode('p',t("The draft remains pending until you click “Approve my edit”."),'legend')); $('forms').append(editor);
  }
  const changedCurrent = e.current && JSON.stringify(e.current) !== JSON.stringify(e.original);
  $('conflict-panel').hidden = !changedCurrent;
  $('current').replaceChildren();
  if (changedCurrent) e.current.forEach((v,i) => { $('current').append(textNode('div',t("Form {0}", i),'plural-label'),textNode('div',v,'text')); });
  const resolution = e.decision.resolution;
  $('resolution').textContent = e.conflict ? t("Awaiting resolution.") : resolution==='current' ? t("Resolution: keep the recent version.") : resolution==='chosen' ? t("Resolution: use your decision.") : t("The recent version already matches your choice for this entry.");
  $('use-chosen').disabled = e.decision.status==='pending';
  $('accept').textContent=editing || e.decision.draft || e.decision.status==='edited' ? t("Save edit · A") : t("Accept · A");
  $('metadata').textContent = [t("Context: {0}", e.context || t("(empty)")),t("Developer comments:\n{0}", e.comment || t("(none)")),t("Translator comments:\n{0}", e.translator_comment || t("(none)")),t("References:\n{0}", e.references.map(([file,line])=>file+(line?':'+line:'')).join('\n') || t("(none)")),t("Flags: {0}", e.flags.join(', ') || t("(none)")), ...(e.reasons ? [t("Reasons: {0}", e.reasons.join(', '))] : []), ...(e.previous_source ? [t("Previous English:\n{0}", e.previous_source.join('\n'))] : []), ...(e.previous_translation ? [t("Previous translation:\n{0}", e.previous_translation.join('\n'))] : []), ...(e.note ? [t("Agent note:\n{0}", e.note)] : []), ...(e.simulation_reference ? [t("Reference before simulation:\n{0}", e.simulation_reference.join('\n'))] : [])].join('\n\n');
  $('decision-note').textContent = e.decision.draft ? t("You have a saved draft. It still needs approval.") : e.decision.status==='accepted' ? t("The proposal will be exported.") : e.decision.status==='edited' ? t("Your approved edit will be exported.") : t("The base translation will be kept (original or recent PO).");
  const filtered=visible(), index=filtered.findIndex(item=>item.key===selected);
  $('prev').disabled=index<=0; $('next').disabled=index<0 || index===filtered.length-1;
}
function render() { showProgress(); const list=renderList(); if (!list.some(e=>e.key===selected)) { selected=list[0]?.key; editing=!!current()?.decision.draft; } renderList(); renderEntry(); }
function mutate(path, data, refresh=true) {
  saving++; $('save').textContent=t("Saving…");
  const task=chain.then(async()=>{ state=await api(path,{...data,revision:state.revision}); if(refresh) render(); else {showProgress();renderList();} });
  chain=task.catch(()=>{}).finally(()=>{saving--;if(!saving&&!dirty) $('save').textContent=t("✓ Progress saved locally");});
  return task;
}
async function saveDraft() {
  clearTimeout(timer);
  if (!dirty || !current()) { await chain; return; }
  const version=draftVersion, entryKey=selected, values=editorValues();
  // Capture before awaiting so keystrokes are not lost while saving.
  await mutate('/api/decision',{key:entryKey,decision:{status:'pending',draft:values}},false);
  if (version===draftVersion) dirty=false;
  showProgress();
}
async function flush() { clearTimeout(timer); if(dirty) await saveDraft(); await chain; if(dirty) await saveDraft(); }
async function select(entryKey) { await flush(); selected=entryKey; editing=!!current()?.decision.draft; render(); }
async function navigate(direction) { await flush(); const list=visible();const index=list.findIndex(e=>e.key===selected); if(index+direction>=0 && index+direction<list.length) await select(list[index+direction].key); }
async function saveApproval(entryKey, decision) {
  // Choose the next entry before saving: the filter may remove the approved entry.
  const list=visible(), index=list.findIndex(e=>e.key===entryKey);
  const nextKey=list[index+1]?.key;
  await mutate('/api/decision',{key:entryKey,decision},false);
  editing=false;
  if(nextKey && visible().some(e=>e.key===nextKey)) {
    selected=nextKey; editing=!!current()?.decision.draft;
  }
  render();
  document.querySelector('.entry.selected')?.scrollIntoView({block:'nearest'});
}
async function decide(status) {
  if(status==='accepted' && editing)return approveEdit();
  await flush(); const e=current(); if(!e) return;
  editing=false;
  if(status==='accepted') {const values=e.decision.draft || (e.decision.status==='edited' ? e.decision.values : null);await saveApproval(e.key,values?{status:'edited',values}:{status});}
  else await mutate('/api/decision',{key:e.key,decision:{status}});
}
async function approveEdit() {
  if(!editing)return;
  const values=editorValues(); await flush(); const e=current(); editing=false;
  await saveApproval(e.key,{status:'edited',values});
}
async function act(fn) { try { await fn(); } catch(exc) { $('save').textContent=dirty?t("⚠ Unsaved draft"):t("⚠ Operation failed");error(exc.message); } }
$('accept').onclick=()=>act(()=>decide('accepted'));
$('reject').onclick=()=>act(()=>decide('rejected'));
$('pending').onclick=()=>act(()=>decide('pending'));
$('edit').onclick=()=>act(async()=>{await flush();editing=true;renderEntry();document.querySelector('.edit-form')?.focus();});
$('prev').onclick=()=>act(()=>navigate(-1)); $('next').onclick=()=>act(()=>navigate(1));
for(const id of ['search','filter']) $(id).addEventListener(id==='search'?'input':'change',()=>act(async()=>{await flush();render();}));
async function workspaceAction(path, data={}) {
  if((path==='/api/sync' || path==='/api/generate') && catalogBusy())return;
  await flush();
  const isSync=path==='/api/sync' || path==='/api/generate';
  if(isSync)syncStart();
  else $('error').hidden=true;
  const buttons=['language','model','manual-mode','generate','component','sync','open-session','copy-prompt','load-proposals','export'];
  buttons.forEach(id=>$(id).disabled=true);
  $('workspace-detail').textContent=path==='/api/sync' ? t("Downloading the current bases from Weblate…") : t("Loading the session…");
  try {state=await api(path,data);syncFailed=false;if(state.workspace.component)$('component').value=state.workspace.component;setLanguage(state.workspace.language,state.workspace.language_name);selected=state.entries[0]?.key;editing=!!current()?.decision.draft;render();}
  catch(exc) {if(isSync){syncFailed=true;$('sync-indicator').classList.add('failed');syncStage({stage:lastSyncEvent?.stage || 0,total:lastSyncEvent?.total || 7,message:exc.message});}throw exc;}
  finally {if(isSync){clearInterval(syncTimer);syncing=false;}buttons.forEach(id=>$(id).disabled=false);showWorkspace();if(path==='/api/sync'){ensureLanguages(true);ensureProjectStatus(true);}}
}
$('sync').onclick=()=>act(async()=>{
  if(provider()==='chatgpt' && !activeAccount()?.can_translate){await refreshConnections();$('connections-dialog').showModal();return;}
  await flush();await ensureModels();
  await workspaceAction('/api/sync',{component:$('component').value,language:$('language').value,provider:provider(),model:$('model').value});
});
$('open-session').onclick=()=>act(()=>workspaceAction('/api/open',{target_id:$('sessions').value}));
$('load-proposals').onclick=()=>act(()=>workspaceAction('/api/load-proposals'));
$('copy-prompt').onclick=()=>act(async()=>{
  $('prompt-details').open=true;
  try {await navigator.clipboard.writeText(state.workspace.prompt);$('save').textContent=t("✓ Prompt copied. Paste it into this project’s chat.");}
  catch { $('agent-prompt').focus();$('agent-prompt').select();error(t("The prompt is selected. Copy it with Ctrl+C and paste it into the chat.")); }
});
$('reload').onclick=()=>act(async()=>{await flush();state=await api('/api/state');render();});
$('keep-current').onclick=()=>act(async()=>{await flush();editing=false;await mutate('/api/decision',{key:selected,decision:{status:'rejected',resolution:'current'}});});
$('use-chosen').onclick=()=>act(async()=>{await flush();const e=current();if(e.decision.status==='pending')throw new Error(t("First accept, reject or approve an edit."));await mutate('/api/decision',{key:selected,decision:{...e.decision,resolution:'chosen'}});});
$('recent-file').onchange=()=>act(async()=>{
  await flush();const file=$('recent-file').files[0];if(!file)return;
  if(file.size>6*1024*1024)throw new Error(t("The file exceeds the 6 MB limit."));
  const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
  await mutate('/api/recent',{text}); $('recent-file').value='';
});
$('remove-recent').onclick=()=>act(async()=>{await flush();await mutate('/api/recent',{text:null});});
function download(result) {
  const url=URL.createObjectURL(new Blob([result.content],{type:result.filename.endsWith('.json')?'application/json;charset=utf-8':/\.ya?ml$/.test(result.filename)?'application/yaml;charset=utf-8':'text/x-gettext-translation;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=result.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  $('save').textContent=t("✓ File and summary exported");
}
document.addEventListener('keydown',event=>{
  if($('export-dialog').open || $('connections-dialog').open)return;
  if(syncing)return;
  if(event.ctrlKey && event.key==='Enter' && editing){event.preventDefault();act(approveEdit);return;}
  // Closing a dialog may return focus to a button. Shortcuts
  // remain active there; suspend them while typing or selecting values.
  if(event.ctrlKey||event.altKey||event.metaKey||event.shiftKey||['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.target.isContentEditable)return;
  const key=event.key.toLowerCase();
  const actions={arrowleft:()=>navigate(-1),arrowright:()=>navigate(1),a:()=>decide('accepted'),r:()=>decide('rejected'),e:async()=>{await flush();editing=true;renderEntry();document.querySelector('.edit-form')?.focus();}};
  if(actions[key]){event.preventDefault();act(actions[key]);}
});
window.addEventListener('beforeunload',event=>{if(dirty||saving||exportDrafts.size){event.preventDefault();event.returnValue='';}});
act(async()=>{await uiReady;state=await api('/api/state');selected=state.entries[0]?.key;editing=!!current()?.decision.draft;render();await refreshConnections();if(state.workspace){await ensureLanguages();ensureProjectStatus();}});

function setLanguage(code,name) {
  if(!code)return;
  if(![...$('language').options].some(option=>option.value===code)) {const option=textNode('option',`${languageName(code,name)} · ${code}`);option.value=code;$('language').append(option);}
  $('language').value=code;
}
function activeAccount() {return connectionState?.chatgpt.accounts.find(item=>item.id===connectionState.chatgpt.active);}
function provider() {return $('manual-mode').checked?'manual':state?.workspace?.demo?'demo':'chatgpt';}
function showAIOptions() {
  const manual=$('manual-mode').checked, demo=state?.workspace?.demo;
  $('manual-tools').hidden=!manual;
  $('model').disabled=manual || demo || syncing || catalogBusy() || !activeAccount()?.can_translate;
  const needed=state?.workspace?.active_id && !state.workspace.ready && state.workspace.candidate_count;
  $('generate').hidden=!needed || manual;
  $('generate').disabled=syncing || catalogBusy() || (!demo && !activeAccount()?.can_translate);
  $('generate').textContent=state?.workspace?.ai_completed?t("Continue translation"):t("Translate this review");
  if(!demo && !activeAccount()?.can_translate && !$('model').value)$('model').replaceChildren(new Option(t("Connect ChatGPT"),''));
  if(demo && !$('model').value){$('model').replaceChildren(new Option(t("Sample proposals"),''));}
}
const languageCache=new Map(), modelCache=new Map(), catalogTasks=new Map(), catalogMessages=new Map();
let catalogChain=Promise.resolve();
function catalogBusy(){return catalogTasks.size>0;}
function catalogStatus(){
  const busy=catalogBusy();
  $('catalog-status').textContent=[...catalogMessages.values()].filter(Boolean).map(item=>t(item)).join(' · ');
  $('catalog-status').classList.toggle('loading',busy);
  $('workspace-panel').setAttribute('aria-busy',String(busy));
  $('component').disabled=syncing || busy;
  $('language').disabled=syncing || busy;
  $('sync').disabled=syncing || busy || !languageCache.has($('component').value) || !$('language').value;
  $('sync').textContent=busy?t("Checking Weblate…"):t("Sync & Translate ↓");
  showAIOptions();
}
function catalogTask(key,label,load) {
  if(catalogTasks.has(key))return catalogTasks.get(key);
  catalogMessages.set(key,label);
  const task=catalogChain.then(load).then(()=>{catalogMessages.delete(key);}).catch(exc=>{catalogMessages.set(key,formatMessage("{0} Open the selector to retry.", [exc.message]));}).finally(()=>{catalogTasks.delete(key);catalogStatus();});
  catalogChain=task;catalogTasks.set(key,task);catalogStatus();return task;
}
async function ensureLanguages(force=false) {
  if(!state?.workspace || syncing)return;
  const component=$('component').value;
  if(languageCache.has(component) && !force)return;
  if(force)languageCache.delete(component);
  const previous=$('language').value;
  $('language').replaceChildren(new Option(t("Loading languages and pending counts…"),previous));
  return catalogTask('languages:'+component,formatMessage("Checking languages and pending counts for {0}…", [component]),async()=>{
    let result;
    try {result=await api('/api/languages?component='+encodeURIComponent(component));}
    catch(exc){if($('component').value===component)$('language').replaceChildren(new Option(t("Could not load languages · retry"),previous));throw exc;}
    languageCache.set(component,result.items);
    if($('component').value!==component)return;
    const code=$('language').value;
    $('language').replaceChildren(...result.items.map(item=>new Option(t("{0} · {1} · {2} pending", languageName(item.code,item.name), item.code, Math.max(0,item.total-item.translated)),item.code)));
    if(result.items.some(item=>item.code===code))$('language').value=code;
  });
}
async function ensureProjectStatus(force=false) {
  if(!state?.workspace || syncing || state.workspace.demo || state.workspace.simulation)return;
  const language=$('language').value;
  if(projectStatus?.language===language && !force)return;
  return catalogTask('project-status:'+language,"Checking project status…",async()=>{
    const result=await api('/api/project-status?language='+encodeURIComponent(language));
    if($('language').value===language){projectStatus=result.status;showWorkspaceWarnings();}
  });
}
function rememberedModel() {try{return localStorage.getItem('wca-model') || '';}catch{return '';}}
async function ensureModels() {
  const account=activeAccount();
  if(!state?.workspace || !account?.can_translate || syncing || provider()==='manual' || state.workspace.demo)return;
  if(modelCache.has(account.id))return;
  return catalogTask('models:'+account.id,"Loading models…",async()=>{
    const result=await api('/api/models?provider=chatgpt');
    if(!result.items.length)throw new Error(t("The account returned no available models."));
    if(activeAccount()?.id!==account.id)return;
    const wanted=$('model').value || rememberedModel() || connectionState.config.model;
    $('model').replaceChildren(...result.items.map(item=>new Option(item.name || item.id,item.id)));
    if(result.items.some(item=>item.id===wanted))$('model').value=wanted;
    modelCache.set(account.id,result.items);
  });
}
$('language').onfocus=()=>{ensureLanguages();};
$('language').onchange=()=>{showWorkspaceWarnings();ensureProjectStatus();};
$('model').onfocus=()=>{ensureModels();};
$('model').onchange=()=>{try{localStorage.setItem('wca-model',$('model').value);}catch{}};
$('component').onchange=()=>act(async()=>{
  const items=languageCache.get($('component').value);
  if(items){const previous=$('language').value;$('language').replaceChildren(...items.map(item=>new Option(t("{0} · {1} · {2} pending", languageName(item.code,item.name), item.code, Math.max(0,item.total-item.translated)),item.code)));if(items.some(item=>item.code===previous))$('language').value=previous;}
  else await ensureLanguages();
  await flush();ensureProjectStatus();catalogStatus();
});
$('manual-mode').onchange=()=>{showAIOptions();ensureModels();};
$('generate').onclick=()=>act(()=>workspaceAction('/api/generate',{provider:provider(),model:$('model').value}));
async function refreshConnections() {
  connectionState=await api('/api/connections');
  showConnections();
  if(connectionState.chatgpt.error)error(connectionState.chatgpt.error);
  await ensureModels();
}
function showConnections() {
  const active=activeAccount(), configured=connectionState.config.weblate;
  $('connection-summary').textContent=connectionState.demo?t("Demo without credentials"):`${configured?'Weblate ✓':t("Weblate token missing")} · ${active?.connected?'ChatGPT ✓':t("ChatGPT disconnected")}`;
  $('chatgpt-status').textContent=active?.connected?`${active.email}${active.can_translate?'':t(" · plan usage authorization required")}`:t("Disconnected");
  $('chatgpt-connect').textContent=active?.connected?t("Disconnect"):t("Connect ChatGPT");
  $('chatgpt-connect').classList.toggle('primary',!active?.connected);
  $('weblate-key').placeholder=configured?t("•••••••• · Token saved"):t("Paste your token");
  $('weblate-status').textContent=configured?t("✓ Token saved"):t("No token configured yet.");
  for(const id of ['chatgpt-connect','weblate-key','connections-save'])$(id).disabled=connectionState.demo;
  $('connection-message').textContent=t(connectionState.chatgpt.error || '') || (connectionState.demo?t("The demo does not use real connections."):'');
  showAIOptions();
}
$('connections-open').onclick=()=>act(async()=>{$('connections-dialog').showModal();await refreshConnections();});
$('connections-close').onclick=()=>$('connections-dialog').close();
$('connections-save').onclick=()=>act(async()=>{
  const values={};if($('weblate-key').value)values.WEBLATE_TOKEN=$('weblate-key').value;
  await api('/api/connections',{values});$('weblate-key').value='';languageCache.clear();
  await refreshConnections();$('connection-message').textContent=t("Token saved for this server session.");
  await ensureLanguages();
});
$('chatgpt-connect').onclick=()=>act(async()=>{
  await flush();
  if(activeAccount()?.connected){await api('/api/chatgpt/disconnect',{});modelCache.clear();$('model').replaceChildren(new Option(t("Connect ChatGPT"),''));await refreshConnections();return;}
  const result=await api('/api/chatgpt/start',{});window.location.assign(result.url);
});
const exportDrafts=new Map(), exportExpanded=new Set();
let exportReport, exportBusy=false;
function updateDownload() {
  $('download').disabled=exportBusy || !!exportReport?.errors.length || exportDrafts.size>0;
  document.querySelectorAll('#validation button,#validation textarea').forEach(field=>{field.disabled=exportBusy;});
}
async function refreshExport() {
  exportReport=await api('/api/validate-export',{revision:state.revision,mode:'changes'});
  validation(exportReport);
}
async function changeDismissal(item,dismissed,button) {
  if(exportBusy)return;
  exportBusy=true;updateDownload();button.textContent=dismissed?t("Dismissing…"):t("Restoring…");
  try {
    await flush();
    await mutate('/api/dismiss-check',{issue_id:item.id,dismissed});
    await refreshExport();
  } finally {
    exportBusy=false;updateDownload();
    if(button.isConnected)button.textContent=dismissed?t("Dismiss"):t("Restore");
  }
}
function validation(result) {
  const counts=result.counts;
  $('export-counts').textContent=t("{0} changes to download · {1} accepted · {2} edited", result.change_count ?? 0, counts.accepted, counts.edited);
  const test=state.workspace?.demo || state.workspace?.simulation;
  $('export-help').textContent=test?t("Test file: do not upload it to Weblate."):t("The file includes approved changes. Import it into Weblate with “Add as translation” or, for Locales, “Add as suggestion”. Merge translations instead of replacing the file.");
  $('validation').replaceChildren();
  const groups=new Map();
  for(const [items,severity] of [[result.errors || [],'error'],[result.warnings || [],'warning'],[result.dismissed || [],'dismissed']]) {
    for(const item of items){const group=groups.get(item.key) || [];group.push({...item,severity});groups.set(item.key,group);}
  }
  for(const [entryKey,issues] of groups) {
    const entry=state.entries.find(item=>item.key===entryKey);
    const card=textNode('section','','validation-card');card.dataset.key=entryKey || '';
    if(entry)card.append(textNode('h3',entry.context || entry.source[0]));
    for(const item of issues) {
      const row=textNode('div','',`validation-issue ${item.severity}`);row.dataset.issue=item.id;
      row.append(textNode('p',`${item.severity==='dismissed'?t("Dismissed"):item.severity==='error'?'Error':t("Warning")}: ${localizeIssueMessage(item)}`));
      if(item.dismissible){const dismiss=textNode('button',item.severity==='dismissed'?t("Restore"):t("Dismiss"));dismiss.className='dismiss-check';dismiss.onclick=()=>act(()=>changeDismissal(item,item.severity!=='dismissed',dismiss));row.append(dismiss);}
      card.append(row);
    }
    if(entry) {
      const actions=textNode('div','','actions');
      const correct=textNode('button',t("Correct here"));correct.className='correct-check';correct.hidden=exportExpanded.has(entryKey);correct.onclick=()=>{exportExpanded.add(entryKey);validation(exportReport);};
      const review=textNode('button',t("Go to review"),'quiet');review.onclick=()=>act(async()=>{await closeExport();$('filter').value='all';$('search').value='';await select(entryKey);});
      actions.append(correct,review);card.append(actions);
      if(exportExpanded.has(entryKey)) {
        const editor=textNode('div','','export-editor');
        entry.source.forEach(source=>editor.append(textNode('div',source,'text export-source')));
        const values=exportDrafts.get(entryKey) || entry.decision.draft || (entry.decision.status==='edited'?entry.decision.values:entry.proposed);
        const fields=[];
        values.forEach((value,index)=>{
          const label=textNode('label',values.length>1?t("Form {0}", entry.plural_forms?.[index] || index):t("Translation"));
          const field=document.createElement('textarea');field.className='export-edit-form';field.dir='auto';field.value=value;fields.push(field);label.append(field);editor.append(label);
          field.oninput=()=>{exportDrafts.set(entryKey,fields.map(input=>input.value));updateDownload();};
        });
        const save=textNode('button',t("Save correction"),'primary');save.classList.add('save-correction');
        save.onclick=()=>act(async()=>{
          exportBusy=true;updateDownload();
          try {
            const decision={status:'edited',values:fields.map(input=>input.value)};
            if(entry.decision.resolution)decision.resolution=entry.decision.resolution;
            await mutate('/api/decision',{key:entryKey,decision});exportDrafts.delete(entryKey);await refreshExport();
          } finally {exportBusy=false;updateDownload();}
        });
        const cancel=textNode('button',t("Cancel"));cancel.className='cancel-correction';
        cancel.onclick=()=>{exportDrafts.delete(entryKey);exportExpanded.delete(entryKey);validation(exportReport);};
        const actions=textNode('div','','actions');actions.append(save,cancel);editor.append(actions);card.append(editor);
      }
    }
    $('validation').append(card);
  }
  if(!groups.size)$('validation').append(textNode('p',t("✓ Automatic checks complete. Ready to download.")));
  $('pending-ack').hidden=!counts.pending;
  $('warning-ack').hidden=!(result.warnings || []).length;
  updateDownload();
}
async function closeExport() {
  for(const [entryKey,values] of exportDrafts){await mutate('/api/decision',{key:entryKey,decision:{status:'pending',draft:values}});}
  exportDrafts.clear();exportExpanded.clear();$('export-dialog').close();editing=!!current()?.decision.draft;render();
}
$('export').onclick=()=>act(async()=>{
  await flush();$('confirm-pending').checked=false;$('confirm-warnings').checked=false;
  await refreshExport();$('export-dialog').showModal();
});
$('close-export').onclick=()=>act(closeExport);
$('export-dialog').addEventListener('cancel',event=>{event.preventDefault();act(closeExport);});
$('download').onclick=()=>act(async()=>{
  if(exportDrafts.size)throw new Error(t("Save corrections before downloading."));
  exportBusy=true;updateDownload();
  try {
    const result=await api('/api/export',{revision:state.revision,mode:'changes',confirm_pending:$('confirm-pending').checked,confirm_warnings:$('confirm-warnings').checked});
    if(!result.ok){exportReport={...exportReport,...result};validation(exportReport);return;}
    download(result);$('export-dialog').close();exportExpanded.clear();
  } finally {exportBusy=false;updateDownload();}
});

async function changeUILanguage(code) {
  await uiReady;
  if (code===uiLanguage) return;
  // Flush the current review draft before rebuilding its editor. Export drafts
  // already live in exportDrafts and are restored by validation().
  await flush();
  uiLanguage=code;
  try { localStorage.setItem('wca-ui-language',code); } catch {}
  applyStaticLanguage();
  if (state) render();
  if (connectionState) showConnections();
  const items=languageCache.get($('component').value), language=$('language').value;
  if(items) {
    $('language').replaceChildren(...items.map(item=>new Option(t("{0} · {1} · {2} pending",languageName(item.code,item.name),item.code,Math.max(0,item.total-item.translated)),item.code)));
    $('language').value=language;
  } else if(catalogBusy()) $('language').replaceChildren(new Option(t("Loading languages and pending counts…"),language));
  if(lastSyncEvent)syncStage(lastSyncEvent);
  if(lastError && !$('error').hidden)$('error').textContent=t(lastError);
  if(exportReport && $('export-dialog').open)validation(exportReport);
  $('user-guide').href=uiLanguage==='es'?'/USER_GUIDE.es.md':'/USER_GUIDE.md';
}
for(const code of ['en','es'])$('ui-'+code).onclick=()=>act(()=>changeUILanguage(code));
uiReady.then(()=>{$('user-guide').href=uiLanguage==='es'?'/USER_GUIDE.es.md':'/USER_GUIDE.md';});

function localizeIssueMessage(item) {
  const message=t(item.message);
  if(uiLanguage!=='es' || !Number.isInteger(item.form))return message;
  const marker=`, form ${item.form}:`, position=message.lastIndexOf(marker);
  return position<0 ? message : message.slice(0,position)+`, forma ${item.form}:`+message.slice(position+marker.length);
}

function languageName(code,fallback) {
  try {
    return new Intl.DisplayNames([uiLanguage==='es'?'es-AR':'en'],{type:'language',fallback:'none'}).of(code.replaceAll('_','-')) || t(fallback || code);
  } catch { return t(fallback || code); }
}
