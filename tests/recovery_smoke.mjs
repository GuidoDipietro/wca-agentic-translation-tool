const [base, debug] = process.argv.slice(2);
const tab = await (
  await fetch(`${debug}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })
).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});
let sequence = 0;
const pending = new Map(),
  errors = [];
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const task = pending.get(message.id);
    pending.delete(message.id);
    message.error
      ? task.reject(new Error(JSON.stringify(message.error)))
      : task.resolve(message.result);
  }
  if (message.method === 'Runtime.exceptionThrown')
    errors.push(message.params.exceptionDetails.text);
};
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function wait(expression) {
  for (let i = 0; i < 150; i++) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 70));
  }
  throw new Error('Timeout: ' + expression);
}
async function click(id) {
  await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
}
function assert(value, message) {
  if (!value) throw new Error(message);
}
async function typeDraft(value) {
  await click('edit');
  await evaluate(
    `document.querySelector('.edit-form').value=${JSON.stringify(value)};document.querySelector('.edit-form').dispatchEvent(new Event('input',{bubbles:true}));clearTimeout(timer);`,
  );
}
try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: base });
  await wait(
    "typeof state!=='undefined' && state && connectionState && !catalogBusy() && !document.getElementById('sync').disabled",
  );
  await click('sync');
  await wait('!syncing && state.workspace.ready && !catalogBusy()');
  const key = await evaluate('selected');
  await typeDraft('Local unsaved version');
  // A separate HTTP operation advances server state without updating this tab.
  await evaluate(
    "api('/api/decision',{key:selected,decision:{status:'edited',values:['Saved in another tab']},revision:state.revision})",
  );
  await click('reload');
  await wait("document.getElementById('draft-recovery-dialog').open");
  assert(
    await evaluate(
      "dirty && document.querySelector('#draft-recovery-local textarea').value==='Local unsaved version' && document.querySelector('#draft-recovery-saved textarea').value==='Saved in another tab'",
    ),
    'Reload retains both versions without overwriting either',
  );
  await click('draft-recovery-cancel');
  assert(
    await evaluate("dirty && document.querySelector('.edit-form').value==='Local unsaved version'"),
    'Cancel preserves the unsaved editor',
  );
  await click('reload');
  await wait("document.getElementById('draft-recovery-dialog').open");
  await evaluate(
    "api('/api/decision',{key:selected,decision:{status:'rejected'},revision:draftRecovery.latest.revision},draftRecovery.latest)",
  );
  await click('draft-recovery-save');
  await wait("!!document.getElementById('draft-recovery-error').textContent");
  assert(
    await evaluate(
      "dirty && document.getElementById('draft-recovery-dialog').open && document.querySelector('#draft-recovery-local textarea').value==='Local unsaved version'",
    ),
    'A second concurrent change cannot overwrite server state or lose local text',
  );
  await click('draft-recovery-cancel');
  await click('reload');
  await wait("document.getElementById('draft-recovery-dialog').open");
  await click('draft-recovery-save');
  await wait("!dirty && !document.getElementById('draft-recovery-dialog').open");
  assert(
    await evaluate(
      "current().decision.status==='pending' && current().decision.draft[0]==='Local unsaved version' && document.querySelector('.edit-form').value==='Local unsaved version'",
    ),
    'Recovered text is saved as a pending draft',
  );
  await evaluate('window.recoveryReloadMarker=true');
  await send('Page.reload');
  await wait(
    "!window.recoveryReloadMarker && typeof current==='function' && typeof state!=='undefined' && state && current()?.decision.draft?.[0]==='Local unsaved version' && !catalogBusy()",
  );
  assert(
    await evaluate("current().decision.status==='pending'"),
    'Recovery persists without approving a translation',
  );

  await typeDraft('Discard this local version');
  await evaluate(
    "api('/api/decision',{key:selected,decision:{status:'edited',values:['Keep server version']},revision:state.revision})",
  );
  await click('reload');
  await wait("document.getElementById('draft-recovery-dialog').open");
  await click('draft-recovery-keep-saved');
  await wait("!dirty && !document.getElementById('draft-recovery-dialog').open");
  assert(
    await evaluate(
      "current().decision.status==='edited' && current().decision.values[0]==='Keep server version'",
    ),
    'Keeping the saved version preserves its decision',
  );

  await typeDraft('Draft after reopening the same session');
  await evaluate("api('/api/open',{target_id:state.workspace.active_id})");
  await click('reload');
  await wait("document.getElementById('draft-recovery-dialog').open");
  await click('draft-recovery-save');
  await wait("!dirty && !document.getElementById('draft-recovery-dialog').open");
  assert(
    await evaluate("current().decision.draft[0]==='Draft after reopening the same session'"),
    'Recovery uses the refreshed workspace revision',
  );

  await typeDraft('Do not move this draft to another session');
  await evaluate("api('/api/sync',{component:'payload',language:'es_419',provider:'manual'})");
  await click('reload');
  await wait("document.getElementById('draft-recovery-dialog').open");
  assert(
    await evaluate(
      "document.getElementById('draft-recovery-save').disabled && document.querySelector('#draft-recovery-local textarea').value==='Do not move this draft to another session'",
    ),
    'Changing sessions retains a copyable draft and blocks cross-session saving',
  );
  await click('draft-recovery-keep-saved');
  await wait("!dirty && !document.getElementById('draft-recovery-dialog').open && !catalogBusy()");
  assert(
    await evaluate(
      "state.workspace.component==='payload' && document.getElementById('component').value==='payload' && !state.entries.length",
    ),
    'Loading the new session updates its selectors',
  );
  const old = await evaluate(
    `api('/api/open',{target_id:state.workspace.sessions.find(item=>item.component==='locales').id})`,
  );
  assert(
    old.entries.find((entry) => entry.key === key).decision.draft[0] ===
      'Draft after reopening the same session',
    'Changing sessions never overwrites the old saved draft',
  );
  assert(errors.length === 0, 'Browser errors: ' + errors.join('; '));
  console.log(
    'OK recovery: concurrent decisions, cancel, retry, pending drafts, persistence and session isolation.',
  );
} finally {
  await fetch(`${debug}/json/close/${tab.id}`);
  ws.close();
}
