'use strict';
const $ = id => document.getElementById(id);
const labels = {pending:'Pending', accepted:'Accepted', rejected:'Rejected', edited:'Edited'};
let state, selected, editing = false, dirty = false, timer, chain = Promise.resolve(), saving = 0;
let draftVersion = 0;
let draftRecovery = null, reloading = false;
let syncing=false, syncStarted=0, syncTimer, connectionState;
let nextStepSession=null;
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
  syncing=true;hideNextStep();activeStageLabels=null;syncStarted=Date.now();$('sync-indicator').classList.remove('failed');
  const tick=()=>{$('sync-elapsed').textContent=t("{0} s elapsed", Math.floor((Date.now()-syncStarted)/1000));};
  tick();syncTimer=setInterval(tick,1000);syncStage({stage:0,message:"Starting synchronization…"});
}
async function readSync(response) {
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result,streamError,recovery;
  function event(line) {if(!line.trim())return;const value=JSON.parse(line);if(value.error){streamError=value.error;recovery=value.recovery;}else if(value.result)result=value.result;else if(value.stage!==undefined)syncStage(value);}
  for(;;) {const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let end;while((end=buffer.indexOf('\n'))>=0){event(buffer.slice(0,end));buffer=buffer.slice(end+1);}if(done)break;}
  if(buffer.trim())event(buffer);if(streamError){if(recovery){state=recovery;resume();}throw new Error(streamError);}if(!result)throw new Error(t("The connection closed before synchronization completed. Reload to check whether the session was saved."));return result;
}

function error(message) { lastError=message; $('error').textContent = t(message); $('error').hidden = false; }
$('error').onclick = () => { $('error').hidden = true; };
async function api(path, data, requestState = state) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...data,...(requestState?.workspace ? {session_id:requestState.workspace.active_id,workspace_revision:requestState.workspace.revision} : {})})});
  if(response.ok && response.headers.get('Content-Type')?.includes('application/x-ndjson'))return readSync(response);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || t("The operation could not be completed."));
  return result;
}
function current() { return state.entries.find(e => e.key === selected); }
function stored(key) {try{return localStorage.getItem(key);}catch{return null;}}
function store(key,value) {try{localStorage.setItem(key,value);}catch{}}
function positionKey() { return 'wca-position:'+(state.workspace?.active_id || 'review'); }
function resumeKey() {
  const saved=stored(positionKey());
  return (state.entries.find(e=>e.key===saved) || state.entries.find(e=>e.decision.status==='pending') || state.entries[0])?.key;
}
function resume() {selected=resumeKey();editing=!!current()?.decision.draft;render();}
function visible() {
  const filter = $('filter').value, search = $('search').value.toLocaleLowerCase();
  return state.entries.filter(e => (filter === 'all' || (filter === 'conflict' ? e.conflict : e.decision.status === filter)) && (!search || [e.context,...e.source,...e.original,...e.proposed,...(e.decision.values || []),...(e.decision.draft || [])].join('\n').toLocaleLowerCase().includes(search)));
}
function textNode(tag, text, className) { const el = document.createElement(tag); el.textContent = text;if(className==='text')el.dir='auto'; if (className) el.className = className; return el; }
function showWorkspace() {
  const w=state.workspace;
  $('workspace-panel').hidden=!w;
  if(!w)return;
  $('demo-banner').hidden=!testSession();$('demo-banner').textContent=w.simulation?t("SIMULATION · copy of a real download with artificial gaps · do not upload exports"):t("DEMO · fictional data · no Weblate connection · do not upload these files");
  if(!$('component').dataset.initialized) {$('component').value=w.component || 'locales';setLanguage(w.language || 'es_419',w.language_name);$('component').dataset.initialized='true';}
  $('export').textContent=t("Download translation ↗");
  $('workspace-status').textContent=syncFailed ? t("Synchronization did not complete. The last session has been preserved.") : !w.active_id ? t("Prepare your first synchronization.") : !w.candidate_count ? t("No pending entries in {0} / {1} in this session.", w.component, w.language) : w.ready ? t("{0} proposals ready for review.", w.candidate_count) : t("{0} entries awaiting translation.", w.candidate_count);
  $('workspace-detail').textContent=w.demo ? t("Try reviewing the sample proposals.") : t("{0}AI proposes translations; you review them before downloading.", w.created_at ? t("Base downloaded: ")+formatDateTime(w.created_at)+'. ' : '');
  $('sessions').replaceChildren();
  if(!w.sessions.length) {const option=textNode('option',t("No sessions yet"));option.value='';$('sessions').append(option);}
  for(const session of w.sessions) {const option=textNode('option',t("{0} / {1} · {2} · {3} entries", session.component || 'regulations', session.language || 'es_419', formatDateTime(session.created_at), session.candidates));option.value=session.id;$('sessions').append(option);}
  $('sessions').value=w.active_id || '';
  $('open-session').disabled=!w.sessions.length;
  $('copy-prompt').disabled=!w.active_id || !w.candidate_count;
  $('load-proposals').disabled=!w.active_id || !w.candidate_count;
  $('reference-proposals').hidden=!w.reference || !w.candidate_count || w.reference_count!==w.candidate_count || w.ai_completed || w.ready;
  $('reference-proposals').textContent=t("Use {0} as proposals", w.reference?.language_name || '');
  $('export').disabled=!w.active_id || (!!w.candidate_count && !w.ready);
  $('agent-prompt').value=w.prompt;
  $('session-folder').textContent=w.folder ? t("Working folder: {0}", w.folder) : '';
  $('session-glossary').textContent=w.active_id?t("Session glossary: {0} terms.",w.glossary_count || 0):'';
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
  showNextStep();
}
const UPLOAD_HOW="Import it into Weblate with “Add as translation” or, for Locales, “Add as suggestion”. Merge translations instead of replacing the file.";
function testSession() {return !!(state.workspace?.demo || state.workspace?.simulation);}
function downloadedKey() {return 'wca-downloaded:'+state.workspace.active_id;}
function downloadedRevision() {try{return localStorage.getItem(downloadedKey());}catch{return null;}}
function nextStep() {
  const w=state.workspace, c=state.counts;
  if(!w?.active_id || !w.ready || nextStepSession!==w.active_id || syncing || !state.entries.length || c.pending)return null;
  const code=value=>value.replaceAll('-','_').toLowerCase();
  if($('component').value!==w.component || code($('language').value)!==code(w.language))return null;
  if(stored('wca-next-step-dismissed:'+w.active_id)===String(state.revision))return null;
  const downloaded=downloadedRevision()===String(state.revision);
  return !(c.accepted+c.edited)?'empty':testSession()?(downloaded?'test-done':'test'):downloaded?'upload':'download';
}
// Leads from the last decision to download, then to the manual Weblate upload.
function showNextStep() {
  const step=nextStep(), box=$('next-step'), button=$('next-download'), link=$('next-weblate'), w=state.workspace;
  box.hidden=!step;
  if(!step)return;
  const n=state.entries.length;
  $('next-step-text').textContent={
    empty:t("All entries reviewed. Nothing to upload: no entry was accepted or edited."),
    test:t("All {0} entries reviewed. Next step: download the test file. Do not upload it to Weblate.",n),
    'test-done':t("Test file downloaded. Never upload DEMO_ or SIMULATION_ files to Weblate."),
    download:t("All {0} entries reviewed. Next step: download the file, then upload it in Weblate.",n),
    upload:[t("File downloaded. If you haven't uploaded it yet, open Weblate, go to Files → Upload translation and pick the file."),t(UPLOAD_HOW),t("After uploading, click “I uploaded it” to close this reminder, or sync to check Weblate again.")].join(' '),
  }[step];
  button.hidden=step==='empty';
  button.textContent=step==='test' || step==='download'?t("Download translation ↗"):t("Download again");
  button.classList.toggle('primary',step==='test' || step==='download');
  link.hidden=step!=='upload' || !w.weblate_url;
  link.href=w.weblate_url || '';
  link.textContent=t("Open {0} in Weblate ↗",languageName(w.language,w.language_name));
  $('next-dismiss').textContent=t(step==='upload'?"I uploaded it":"Dismiss reminder");
}
function hideNextStep() {nextStepSession=null;showNextStep();}
$('next-dismiss').onclick=()=>{
  store('wca-next-step-dismissed:'+state.workspace.active_id,String(state.revision));
  hideNextStep();
};
function focusNextStep() {
  if($('next-step').hidden)return;
  const action=[$('next-download'),$('next-weblate')].find(el=>!el.hidden && el.classList.contains('primary'));
  if(!action)return;
  $('next-step').scrollIntoView({block:'nearest'});action.focus();
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
function flagLabel(flag) { return `${flag.term} → ${flag.replacement.join(' / ')}`; }
function flagText(value, flags, label) {
  const box = textNode('div','','text');
  if (label) box.append(textNode('div',label,'plural-label'));
  const terms = flags.map(flag => flag.term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const pattern = terms.length && new RegExp(`(?<![\\p{L}\\p{N}_])(${terms.join('|')})(?![\\p{L}\\p{N}_])`,'giu');
  let last = 0;
  for (const match of pattern ? value.matchAll(pattern) : []) {
    box.append(value.slice(last, match.index));
    const flag = flags.find(item => item.term.toLocaleLowerCase() === match[0].toLocaleLowerCase()) || flags[0];
    const mark = textNode('mark',match[0],'flag'); mark.title = t("Glossary: {0}", flagLabel(flag)); box.append(mark);
    last = match.index + match[0].length;
  }
  box.append(value.slice(last));
  return box;
}
function editorValues() { return [...document.querySelectorAll('.edit-form')].map(el => el.value); }
function renderEntry() {
  const e=current(); $('review').hidden = !e;
  if (!e) return;
  store(positionKey(),e.key);
  $('position').textContent = t("CHANGE {0} OF {1}", state.entries.indexOf(e)+1, state.entries.length);
  $('context').textContent = e.context || t("No context"); $('status').textContent = t(labels[e.decision.status]) + (e.conflict ? t(" · Conflict") : '');
  $('source').replaceChildren();
  e.source.forEach((value,i) => { if (e.source.length>1) $('source').append(textNode('div',i===0?t("Singular"):t("Plural"),'plural-label')); $('source').append(textNode('div',value,'text')); });
  $('reference-panel').hidden = !e.reference;
  $('use-reference').hidden = !e.reference;
  $('use-reference').replaceChildren(t("Use {0}", state.workspace?.reference?.language_name || '') + ' ', textNode('kbd','U'));
  if (e.reference) {
    $('reference-title').textContent = t("Reference · {0}", state.workspace.reference?.language || '');
    $('reference-text').replaceChildren(...e.reference.map((value,i) => flagText(value, e.reference_flags, e.plural_forms?.[i])));
  }
  $('forms').replaceChildren();
  const values = e.decision.draft || (e.decision.status==='edited' ? e.decision.values : e.proposed);
  e.original.forEach((old,i) => {
    if (e.original.length>1) $('forms').append(textNode('div',t("Plural form {0}", e.plural_forms?.[i] || i),'plural-label'));
    const fromReference = !old.trim() && !!e.reference;
    const [before,after] = diff(fromReference ? e.reference[i] : old,values[i]);
    const comparison = textNode('div','','comparison');
    const oldBox = textNode('section','','translation'), newBox = textNode('section','','translation proposal');
    oldBox.append(textNode('h3',fromReference ? t("Reference · {0}", state.workspace.reference.language) : t("Original translation")), diffText(before,'removed'));
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
  $('metadata').textContent = [t("Context: {0}", e.context || t("(empty)")),t("Developer comments:\n{0}", e.comment || t("(none)")),t("Translator comments:\n{0}", e.translator_comment || t("(none)")),t("References:\n{0}", e.references.map(([file,line])=>file+(line?':'+line:'')).join('\n') || t("(none)")),t("Flags: {0}", e.flags.join(', ') || t("(none)")), ...(e.reasons ? [t("Reasons: {0}", e.reasons.join(', '))] : []), ...(e.previous_source ? [t("Previous English:\n{0}", e.previous_source.join('\n'))] : []), ...(e.previous_translation ? [t("Previous translation:\n{0}", e.previous_translation.join('\n'))] : []), ...(e.note ? [t("Agent note:\n{0}", e.note)] : []), ...(e.reference_flags?.length ? [t("Glossary changes from the reference:\n{0}", e.reference_flags.map(flagLabel).join('\n'))] : []), ...(e.simulation_reference ? [t("Reference before simulation:\n{0}", e.simulation_reference.join('\n'))] : [])].join('\n\n');
  $('decision-note').textContent = e.decision.draft ? t("You have a saved draft. It still needs approval.") : e.decision.status==='accepted' ? t("The proposal will be exported.") : e.decision.status==='edited' ? t("Your approved edit will be exported.") : t("The base translation will be kept (original or recent PO).");
  const filtered=visible(), index=filtered.findIndex(item=>item.key===selected);
  $('prev').disabled=index<=0; $('next').disabled=index<0 || index===filtered.length-1;
}
function render() { showProgress(); const list=renderList(); if (!list.some(e=>e.key===selected)) { selected=list[0]?.key; editing=!!current()?.decision.draft; } renderList(); renderEntry(); }
function mutate(path, data, refresh=true) {
  saving++; $('save').textContent=t("Saving…");
  const task=chain.then(async()=>{ state=await api(path,{...data,revision:state.revision}); if(path==='/api/decision')nextStepSession=state.workspace?.active_id; if(refresh) render(); else {showProgress();renderList();} });
  chain=task.catch(()=>{}).finally(()=>{saving--;if(!saving&&!dirty) $('save').textContent=t("✓ Progress saved locally");});
  return task;
}
async function saveDraft() {
  clearTimeout(timer);
  if (draftRecovery) { await chain; return; }
  if (!dirty || !current()) { await chain; return; }
  const version=draftVersion, entryKey=selected, values=editorValues();
  // Capture before awaiting so keystrokes are not lost while saving.
  await mutate('/api/decision',{key:entryKey,decision:{status:'pending',draft:values}},false);
  if (version===draftVersion) dirty=false;
  showProgress();
}
async function flush() { clearTimeout(timer); if(dirty) await saveDraft(); await chain; if(dirty) await saveDraft(); }
function showDraftRecovery() {
  const recovery = draftRecovery;
  const entry = recovery.latest.entries.find((item) => item.key === recovery.key);
  $('draft-recovery-context').textContent = recovery.context;
  $('draft-recovery-help').textContent = recovery.canSave
    ? t(
        'Progress changed in another tab. Choose which version to keep. Your local version will be saved as a pending draft.',
      )
    : t(
        'The active session changed or this entry is no longer available. Copy your local draft before loading the saved version.',
      );
  for (const [id, values] of [
    ['draft-recovery-local', recovery.values],
    [
      'draft-recovery-saved',
      recovery.canSave
        ? entry.decision.draft ||
          (entry.decision.status === 'edited'
            ? entry.decision.values
            : entry.decision.status === 'accepted'
              ? entry.proposed
              : entry.original)
        : [],
    ],
  ]) {
    const container = $(id);
    container.replaceChildren();
    values.forEach((value, index) => {
      const label = textNode('label', values.length > 1 ? t('Form {0}', index) : t('Translation'));
      const field = document.createElement('textarea');
      field.value = value;
      field.readOnly = true;
      field.dir = 'auto';
      label.append(field);
      container.append(label);
    });
  }
  $('draft-recovery-save').disabled = !recovery.canSave;
  $('draft-recovery-error').textContent = '';
}
function applyReloadedState(updated, entryKey = selected) {
  state = updated;
  selected = entryKey;
  editing = !!current()?.decision.draft;
  if (state.workspace?.component) {
    $('component').value = state.workspace.component;
    setLanguage(state.workspace.language, state.workspace.language_name);
  }
  render();
  ensureLanguages();
  ensureProjectStatus();
}
async function reloadState() {
  if (reloading || draftRecovery) return;
  reloading = true;
  $('reload').disabled = true;
  clearTimeout(timer);
  try {
    await chain;
    const latest = await api('/api/state');
    clearTimeout(timer);
    if (dirty && current()) {
      const sameSession = latest.workspace?.active_id === state.workspace?.active_id;
      if (
        !sameSession ||
        latest.workspace?.revision !== state.workspace?.revision ||
        latest.revision !== state.revision
      ) {
        const entry = latest.entries.find((item) => item.key === selected);
        const values = editorValues();
        draftRecovery = {
          latest,
          key: selected,
          context: current().context || current().source[0],
          values,
          canSave: sameSession && !!entry && entry.original.length === values.length,
        };
        showDraftRecovery();
        $('draft-recovery-dialog').showModal();
        return;
      }
      await flush();
      // The save response already contains current progress. Keep the editor
      // intact if more keystrokes arrived while its draft was being saved.
      if (!dirty) applyReloadedState(state);
    } else applyReloadedState(latest);
  } finally {
    reloading = false;
    $('reload').disabled = false;
  }
}
function cancelDraftRecovery() {
  if (draftRecovery?.busy) return;
  draftRecovery = null;
  $('draft-recovery-dialog').close();
}
async function resolveDraftRecovery(saveLocal) {
  const recovery = draftRecovery;
  if (!recovery || recovery.busy || (saveLocal && !recovery.canSave)) return;
  recovery.busy = true;
  for (const id of ['draft-recovery-save', 'draft-recovery-keep-saved', 'draft-recovery-cancel'])
    $(id).disabled = true;
  try {
    const updated = saveLocal
      ? await api(
          '/api/decision',
          {
            key: recovery.key,
            decision: { status: 'pending', draft: recovery.values },
            revision: recovery.latest.revision,
          },
          recovery.latest,
        )
      : await api('/api/state');
    dirty = false;
    draftVersion++;
    draftRecovery = null;
    $('draft-recovery-dialog').close();
    $('error').hidden = true;
    applyReloadedState(updated, recovery.key);
  } catch (exc) {
    $('draft-recovery-error').textContent = t(exc.message);
  } finally {
    recovery.busy = false;
    $('draft-recovery-save').disabled = !recovery.canSave;
    $('draft-recovery-keep-saved').disabled = false;
    $('draft-recovery-cancel').disabled = false;
  }
}
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
  const pending=state.counts.pending;
  await flush(); const e=current(); if(!e) return;
  editing=false;
  if(status==='accepted') {const values=e.decision.draft || (e.decision.status==='edited' ? e.decision.values : null);await saveApproval(e.key,values?{status:'edited',values}:{status});}
  else await mutate('/api/decision',{key:e.key,decision:{status}});
  if(pending && !state.counts.pending)focusNextStep();
}
async function approveEdit() {
  if(!editing)return;
  const pending=state.counts.pending;
  const values=editorValues(); await flush(); const e=current(); editing=false;
  await saveApproval(e.key,{status:'edited',values});
  if(pending && !state.counts.pending)focusNextStep();
}
async function act(fn) { try { await fn(); } catch(exc) { $('save').textContent=dirty?t("⚠ Unsaved draft"):t("⚠ Operation failed");error(exc.message); } }
$('accept').onclick=()=>act(()=>decide('accepted'));
$('reject').onclick=()=>act(()=>decide('rejected'));
$('pending').onclick=()=>act(()=>decide('pending'));
async function startEditing() {await flush();editing=true;renderEntry();document.querySelector('.edit-form')?.focus();}
async function useReference() {
  await flush(); const e=current(); if(!e?.reference)return;
  await mutate('/api/decision',{key:e.key,decision:{status:'pending',draft:[...e.reference]}},false);
  if(!visible().some(item=>item.key===e.key))$('filter').value='pending';
  selected=e.key;render();
  await startEditing();
}
$('use-reference').onclick=()=>act(useReference);
$('edit').onclick=()=>act(startEditing);
$('prev').onclick=()=>act(()=>navigate(-1)); $('next').onclick=()=>act(()=>navigate(1));
for(const id of ['search','filter']) $(id).addEventListener(id==='search'?'input':'change',()=>act(async()=>{await flush();render();}));
async function workspaceAction(path, data={}) {
  if((path==='/api/sync' || path==='/api/generate') && catalogBusy())return;
  hideNextStep();
  await flush();
  const isSync=path==='/api/sync' || path==='/api/generate';
  if(isSync)syncStart();
  else $('error').hidden=true;
  const buttons=['language','reference','model','manual-mode','generate','reference-proposals','component','sync','open-session','copy-prompt','load-proposals','export'];
  buttons.forEach(id=>$(id).disabled=true);
  $('workspace-detail').textContent=path==='/api/sync' ? t("Downloading the current bases from Weblate…") : t("Loading the session…");
  try {state=await api(path,data);syncFailed=false;if(state.workspace.component)$('component').value=state.workspace.component;setLanguage(state.workspace.language,state.workspace.language_name);nextStepSession=state.workspace.active_id;resume();}
  catch(exc) {if(isSync){syncFailed=true;$('sync-indicator').classList.add('failed');syncStage({stage:lastSyncEvent?.stage || 0,total:lastSyncEvent?.total || 7,message:exc.message});}throw exc;}
  finally {if(isSync){clearInterval(syncTimer);syncing=false;}buttons.forEach(id=>$(id).disabled=false);showWorkspace();showNextStep();if(path==='/api/sync'){ensureLanguages(true);ensureProjectStatus(true);}}
}
$('sync').onclick=()=>act(async()=>{
  hideNextStep();
  if(AI[provider()] && !canTranslate()){await refreshConnections();$('connections-dialog').showModal();return;}
  await flush();await ensureModels();
  await workspaceAction('/api/sync',{component:$('component').value,language:$('language').value,reference:$('reference').value || null,provider:provider(),model:$('model').value});
});
$('open-session').onclick=()=>act(()=>workspaceAction('/api/open',{target_id:$('sessions').value}));
$('load-proposals').onclick=()=>act(()=>workspaceAction('/api/load-proposals'));
$('reference-proposals').onclick=()=>act(()=>workspaceAction('/api/reference-proposals'));
$('copy-prompt').onclick=()=>act(async()=>{
  $('prompt-details').open=true;
  try {await navigator.clipboard.writeText(state.workspace.prompt);$('save').textContent=t("✓ Prompt copied. Paste it into this project’s chat.");}
  catch { $('agent-prompt').focus();$('agent-prompt').select();error(t("The prompt is selected. Copy it with Ctrl+C and paste it into the chat.")); }
});
$('reload').onclick = () => act(reloadState);
$('draft-recovery-save').onclick = () => act(() => resolveDraftRecovery(true));
$('draft-recovery-keep-saved').onclick = () => act(() => resolveDraftRecovery(false));
$('draft-recovery-cancel').onclick = cancelDraftRecovery;
$('draft-recovery-dialog').addEventListener('cancel', (event) => {
  event.preventDefault();
  cancelDraftRecovery();
});
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
  if($('glossary-dialog').open || $('export-dialog').open || $('connections-dialog').open || $('draft-recovery-dialog').open)return;
  if(syncing)return;
  if(event.ctrlKey && event.key==='Enter' && editing){event.preventDefault();act(approveEdit);return;}
  // Closing a dialog may return focus to a button. Shortcuts
  // remain active there; suspend them while typing or selecting values.
  if(event.ctrlKey||event.altKey||event.metaKey||event.shiftKey||['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.target.isContentEditable)return;
  const key=event.key.toLowerCase();
  const actions={arrowleft:()=>navigate(-1),arrowright:()=>navigate(1),a:()=>decide('accepted'),r:()=>decide('rejected'),u:useReference,e:startEditing};
  if(actions[key]){event.preventDefault();act(actions[key]);}
});
window.addEventListener('beforeunload',event=>{if(dirty||saving||exportDrafts.size){event.preventDefault();event.returnValue='';}});
act(async()=>{await uiReady;state=await api('/api/state');resume();await refreshConnections();if(state.workspace){await ensureLanguages();ensureProjectStatus();}});

function setLanguage(code,name) {
  if(!code)return;
  if(![...$('language').options].some(option=>option.value===code)) {const option=textNode('option',`${languageName(code,name)} · ${code}`);option.value=code;$('language').append(option);}
  $('language').value=code;
  fillReference();
}
function activeAccount() {return connectionState?.chatgpt.accounts.find(item=>item.id===connectionState.chatgpt.active);}
function provider() {return $('manual-mode').checked?'manual':state?.workspace?.demo?'demo':$('ai-provider').value;}
// key scopes the model cache: per ChatGPT account, one entry for the local CLI.
const AI={
  chatgpt:{connect:()=>t("Connect ChatGPT"),ready:()=>!!activeAccount()?.can_translate,key:()=>activeAccount()?.id},
  'claude-code':{connect:()=>t("Sign in to Claude Code"),ready:()=>!!connectionState?.claude_code?.logged_in,key:()=>'claude-code'},
  libretranslate:{connect:()=>t("Set up Argos"),ready:()=>!!(connectionState?.libretranslate?.configured||connectionState?.libretranslate?.installed),key:()=>'libretranslate'},
};
function canTranslate() {return !!AI[provider()]?.ready();}
function connectLabel() {return (AI[provider()] || AI.chatgpt).connect();}
function showAIOptions() {
  const manual=$('manual-mode').checked, demo=state?.workspace?.demo;
  $('manual-tools').hidden=!manual;
  $('ai-provider').disabled=manual || demo || syncing || catalogBusy();
  $('model').disabled=manual || demo || syncing || catalogBusy() || !canTranslate();
  const needed=state?.workspace?.active_id && !state.workspace.ready && state.workspace.candidate_count;
  $('generate').hidden=!needed || manual;
  $('generate').disabled=syncing || catalogBusy() || (!demo && !canTranslate());
  $('generate').textContent=state?.workspace?.ai_completed?t("Continue translation"):t("Translate this review");
  if(!demo && !canTranslate() && !$('model').value)$('model').replaceChildren(new Option(connectLabel(),''));
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
    fillLanguages(result.items);
  });
}
function fillLanguages(items) {
  const previous=$('language').value;
  $('language').replaceChildren(...items.map(item=>new Option(t("{0} · {1} · {2} pending", languageName(item.code,item.name), item.code, Math.max(0,item.total-item.translated)),item.code)));
  if(items.some(item=>item.code===previous))$('language').value=previous;
  fillReference();
}
// Derived locales that default to a reference; an explicit choice (even None) overrides it.
const DEFAULT_REFERENCE={fr_ca:'fr'};
function fillReference() {
  const target=$('language').value;
  const remembered=stored('wca-reference:'+target) ?? (DEFAULT_REFERENCE[target.replace('-','_').toLowerCase()] || '');
  const items=(languageCache.get($('component').value) || []).filter(item=>item.code!==target);
  $('reference').replaceChildren(new Option(t("None · translate from English"),''),...items.map(item=>new Option(`${languageName(item.code,item.name)} · ${item.code}`,item.code)));
  $('reference').value=items.some(item=>item.code===remembered)?remembered:'';
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
function rememberedModel() {return stored('wca-model:'+provider()) || '';}
async function ensureModels() {
  const name=provider(), key=AI[name]?.key();
  if(!state?.workspace || !canTranslate() || syncing || state.workspace.demo)return;
  const fill=items=>{
    const wanted=$('model').value || rememberedModel() || connectionState.config.model;
    $('model').replaceChildren(...items.map(item=>new Option(item.name || item.id,item.id)));
    if(items.some(item=>item.id===wanted))$('model').value=wanted;
  };
  if(modelCache.has(key)){if(!$('model').value)fill(modelCache.get(key));return;}
  return catalogTask('models:'+key,"Loading models…",async()=>{
    const result=await api('/api/models?provider='+encodeURIComponent(name));
    if(!result.items.length)throw new Error(t("The account returned no available models."));
    if(provider()!==name || AI[name].key()!==key)return;
    fill(result.items);
    modelCache.set(key,result.items);
  });
}
$('language').onfocus=()=>{ensureLanguages();};
$('language').onchange=()=>{hideNextStep();fillReference();showWorkspaceWarnings();ensureProjectStatus();};
$('reference').onchange=()=>store('wca-reference:'+$('language').value,$('reference').value);
$('model').onfocus=()=>{ensureModels();};
$('model').onchange=()=>store('wca-model:'+provider(),$('model').value);
if(Object.hasOwn(AI,stored('wca-provider')))$('ai-provider').value=stored('wca-provider');
$('ai-provider').onchange=()=>act(async()=>{
  store('wca-provider',$('ai-provider').value);
  $('model').replaceChildren(new Option(connectLabel(),''));showAIOptions();await ensureModels();
});
$('component').onchange=()=>act(async()=>{
  hideNextStep();
  const items=languageCache.get($('component').value);
  if(items)fillLanguages(items);
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
  $('connection-summary').textContent=connectionState.demo?t("Demo without credentials"):`${configured?'Weblate ✓':t("Weblate token missing")} · ${active?.connected?'ChatGPT ✓':t("ChatGPT disconnected")}${connectionState.claude_code?.logged_in?' · Claude Code ✓':''}${connectionState.libretranslate?.configured||connectionState.libretranslate?.installed?' · Argos ✓':''}`;
  const claude=connectionState.claude_code;
  $('claude-status').textContent=!claude?.available?t("Not installed"):claude.logged_in?t("✓ Signed in · {0}",claude.subscription):t("Installed · not signed in");
  $('libretranslate-status').textContent=connectionState.libretranslate?.configured?t("✓ Server configured"):connectionState.libretranslate?.installed?t("Installed · starts on demand"):t("Not installed · pipx install libretranslate");
  $('chatgpt-status').textContent=active?.connected?`${active.email}${active.can_translate?'':t(" · plan usage authorization required")}`:active?t("{0} · disconnected",active.email):t("Disconnected");
  $('chatgpt-options').hidden=!active || active.connected;
  $('chatgpt-connect').textContent=active?.connected?t("Disconnect"):t("Connect ChatGPT");
  $('chatgpt-connect').classList.toggle('primary',!active?.connected);
  $('weblate-key').placeholder=configured?t("•••••••• · Token saved"):t("Paste your token");
  $('weblate-status').textContent=configured?t("✓ Token saved"):t("No token configured yet.");
  for(const id of ['chatgpt-connect','weblate-key','connections-save','libretranslate-url','libretranslate-key','libretranslate-save'])$(id).disabled=connectionState.demo;
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
// A touched address field is sent even when emptied, so it can clear the server; the key is sent only when typed.
$('libretranslate-url').oninput=()=>{$('libretranslate-url').dataset.touched='1';};
$('libretranslate-save').onclick=()=>act(async()=>{
  const values={};
  if($('libretranslate-url').dataset.touched)values.LIBRETRANSLATE_URL=$('libretranslate-url').value;
  if($('libretranslate-key').value)values.LIBRETRANSLATE_API_KEY=$('libretranslate-key').value;
  await api('/api/connections',{values});
  for(const id of ['libretranslate-url','libretranslate-key']){$(id).value='';delete $(id).dataset.touched;}
  modelCache.clear();await refreshConnections();$('connection-message').textContent=t("Argos settings saved for this server session.");
});
$('chatgpt-connect').onclick=()=>act(async()=>{
  await flush();
  if(activeAccount()?.connected){await api('/api/chatgpt/disconnect',{});modelCache.clear();$('model').replaceChildren(new Option(connectLabel(),''));await refreshConnections();return;}
  const account=activeAccount();
  const result=await api('/api/chatgpt/start',account && !$('chatgpt-new-account').checked ? {account_id:account.id} : {});window.location.assign(result.url);
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
  $('export-help').textContent=testSession()?t("Test file: do not upload it to Weblate."):t("The file includes approved changes.")+' '+t(UPLOAD_HOW);
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
function openExport() {
  return act(async()=>{
    await flush();$('confirm-pending').checked=false;$('confirm-warnings').checked=false;
    await refreshExport();$('export-dialog').showModal();
  });
}
$('export').onclick=openExport;
$('close-export').onclick=()=>act(closeExport);
$('next-download').onclick=openExport;
$('export-dialog').addEventListener('cancel',event=>{event.preventDefault();act(closeExport);});
$('download').onclick=()=>act(async()=>{
  if(exportDrafts.size)throw new Error(t("Save corrections before downloading."));
  exportBusy=true;updateDownload();
  try {
    const result=await api('/api/export',{revision:state.revision,mode:'changes',confirm_pending:$('confirm-pending').checked,confirm_warnings:$('confirm-warnings').checked});
    if(!result.ok){exportReport={...exportReport,...result};validation(exportReport);return;}
    download(result);$('export-dialog').close();exportExpanded.clear();
    nextStepSession=state.workspace?.active_id;
    try{localStorage.setItem(downloadedKey(),String(state.revision));}catch{}
    showNextStep();focusNextStep();
  } finally {exportBusy=false;updateDownload();}
});

async function changeUILanguage(code) {
  await uiReady;
  if (code===uiLanguage) return;
  // Flush the current review draft before rebuilding its editor. Export drafts
  // already live in exportDrafts and are restored by validation().
  await flush();
  uiLanguage=code;
  if(typeof renderGlossary==='function')renderGlossary();
  store('wca-ui-language',code);
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
  if(draftRecovery)showDraftRecovery();
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
