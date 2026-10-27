'use strict';
let uiLanguage = 'en';
try { if (localStorage.getItem('wca-ui-language') === 'es') uiLanguage = 'es'; } catch {}
let translations = {}, messagePatterns = [];
const formatMessage = (template, values) => template.replace(/\{(\d+)\}/g, (match, index) => index < values.length ? String(values[index]) : match);
const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function messagePattern(template) {
  const indexes = [];
  const pattern = template.split(/(\{\d+\})/).map(part => {
    if (/^\{\d+\}$/.test(part)) { indexes.push(Number(part.slice(1,-1))); return '([\\s\\S]*?)'; }
    return escapePattern(part);
  }).join('');
  return {pattern:new RegExp('^'+pattern+'$'), indexes};
}
// Only interface strings and explicit server message fields pass through this function.
// Sources, translations, comments, glossary terms and editor values remain untouched.
function t(message, ...values) {
  if (typeof message !== 'string') return message;
  if (values.length) return formatMessage(uiLanguage === 'es' ? translations[message] || message : message, values);
  if (uiLanguage === 'es' && translations[message]) return translations[message];
  const http = /^Weblate HTTP (\d+) at (\S+): ([\s\S]*)$/.exec(message);
  if (http) return `Weblate HTTP ${http[1]} ${uiLanguage==='es'?'en':'at'} ${http[2]}: ${t(http[3])}`;
  const glossary = /^Glossary not downloaded: ([\s\S]*)$/.exec(message);
  if (glossary) return t('Glossary not downloaded: ')+t(glossary[1]);
  const retry = /^([\s\S]*) · Retry (\d+)\/2 in ([\d.]+) s$/.exec(message);
  if (retry) return t('{0} · Retry {1}/2 in {2} s',t(retry[1]),retry[2],retry[3]);
  const correction = /^([\s\S]*) · correcting validation$/.exec(message);
  if (correction) return t('{0} · correcting validation',t(correction[1]));
  for (const entry of messagePatterns) {
    const match = entry.pattern.exec(message);
    if (!match) continue;
    const args = [];
    entry.indexes.forEach((index, position) => { args[index] = match[position+1]; });
    return formatMessage(uiLanguage === 'es' ? entry.es : entry.en, args);
  }
  return message;
}
// Capture the initial HTML labels once, before the application inserts translation data.
const staticLabels = [];
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
while (walker.nextNode()) {
  const node = walker.currentNode, label = node.textContent.trim();
  if (label) staticLabels.push({node,label,prefix:node.textContent.match(/^\s*/)[0],suffix:node.textContent.match(/\s*$/)[0]});
}
const staticAttributes = [...document.querySelectorAll('[aria-label],[title],[placeholder]')].flatMap(node =>
  ['aria-label','title','placeholder'].filter(name=>node.hasAttribute(name)).map(name=>({node,name,label:node.getAttribute(name)})));
function applyStaticLanguage() {
  document.documentElement.lang = uiLanguage === 'es' ? 'es-AR' : 'en';
  for (const {node,label,prefix,suffix} of staticLabels) if (node.isConnected) node.textContent = prefix+t(label)+suffix;
  for (const {node,name,label} of staticAttributes) if (node.isConnected) node.setAttribute(name,t(label));
  for (const code of ['en','es']) document.getElementById('ui-'+code).setAttribute('aria-pressed',String(uiLanguage===code));
}
const uiReady = fetch('/translations.json').then(response=>{
  if (!response.ok) throw new Error('Could not load interface translations.');
  return response.json();
}).then(catalog=>{
  translations=catalog;
  for (const [en,es] of Object.entries(catalog)) {
    if (en.includes('{0}')) messagePatterns.push({...messagePattern(en),en,es});
    // Sessions created before localization may contain Spanish warnings.
    messagePatterns.push({...messagePattern(es),en,es});
  }
  applyStaticLanguage();
}).catch(()=>{ uiLanguage='en'; applyStaticLanguage(); });
