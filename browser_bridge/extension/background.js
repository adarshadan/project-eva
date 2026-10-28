// Eva Browser Bridge -- the extension half (Manifest V3 service worker).
//
// Eva <-> native host (browser_bridge/native_host.py) <-> this worker <-> the page.
// Each request from Eva is {id, cmd, args}; each answer is {id, ok, result | error}.
// Nothing runs on a page unless Eva asks; the person's passwords are never typed or read.
//
// The native-messaging port keeps this worker alive while it is open (Chrome 116+); when the
// host goes away (Eva closed) it reconnects with a back-off, so starting Eva later just works.

const HOST = 'com.eva.browser_bridge';
let port = null;
let retryMs = 2000;

function connect() {
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    port = null;
    schedule();
    return;
  }
  port.onMessage.addListener(onRequest);
  port.onDisconnect.addListener(() => {
    port = null;
    schedule();
  });
  port.postMessage({ hello: true, browser: browserName(), version: chrome.runtime.getManifest().version });
  retryMs = 2000;
}

function schedule() {
  setTimeout(connect, retryMs);
  retryMs = Math.min(retryMs * 2, 60000);
}

function browserName() {
  const ua = navigator.userAgent;
  if (/Edg\//.test(ua)) return 'edge';
  if (/OPR\//.test(ua)) return 'opera';
  if (navigator.brave) return 'brave';
  if (/Vivaldi/.test(ua)) return 'vivaldi';
  return 'chrome';
}

function reply(id, ok, payload) {
  if (!port) return;
  try {
    port.postMessage(ok ? { id, ok: true, result: payload } : { id, ok: false, error: String(payload) });
  } catch (e) { /* the host went away mid-answer */ }
}

async function tabFor(args) {
  if (args && args.tab_id != null) return await chrome.tabs.get(Number(args.tab_id));
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error('no active tab');
  return tab;
}

async function inPage(tab, func, args) {
  if (!/^(https?|file):/.test(tab.url || '')) {
    throw new Error(`this page (${(tab.url || '').split('/')[0] || 'a browser page'}) cannot be read by extensions`);
  }
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args: [args || {}] });
  if (!res) throw new Error('the page did not answer');
  if (res.result && res.result.__error) throw new Error(res.result.__error);
  return res.result;
}

async function onRequest(msg) {
  if (!msg || msg.id == null) return;
  const { id, cmd, args } = msg;
  try {
    let result;
    switch (cmd) {
      case 'ping':
        result = { pong: true, browser: browserName() };
        break;
      case 'tabs': {
        const tabs = await chrome.tabs.query({});
        result = tabs.map(t => ({ tab_id: t.id, title: t.title, url: t.url, active: t.active, window_id: t.windowId }));
        break;
      }
      case 'activate': {
        const tab = await chrome.tabs.update(Number(args.tab_id), { active: true });
        await chrome.windows.update(tab.windowId, { focused: true });
        result = { tab_id: tab.id, title: tab.title, url: tab.url };
        break;
      }
      case 'go': {
        let tab;
        if (args.new_tab === false) {
          const cur = await tabFor(args);
          tab = await chrome.tabs.update(cur.id, { url: args.url, active: true });
        } else {
          tab = await chrome.tabs.create({ url: args.url, active: true });
        }
        await waitLoaded(tab.id, 15000);
        tab = await chrome.tabs.get(tab.id);
        result = { tab_id: tab.id, title: tab.title, url: tab.url };
        break;
      }
      case 'elements': {
        const tab = await tabFor(args);
        result = Object.assign({ tab_id: tab.id }, await inPage(tab, pageElements, args));
        break;
      }
      case 'describe': {
        const tab = await tabFor(args);
        result = Object.assign({ tab_id: tab.id }, await inPage(tab, pageDescribe, args));
        break;
      }
      case 'click': {
        const tab = await tabFor(args);
        result = Object.assign({ tab_id: tab.id }, await inPage(tab, pageClick, args));
        break;
      }
      case 'type': {
        const tab = await tabFor(args);
        result = Object.assign({ tab_id: tab.id }, await inPage(tab, pageType, args));
        break;
      }
      case 'read': {
        const tab = await tabFor(args);
        result = Object.assign({ tab_id: tab.id }, await inPage(tab, pageRead, args));
        break;
      }
      case 'scroll': {
        const tab = await tabFor(args);
        result = Object.assign({ tab_id: tab.id }, await inPage(tab, pageScroll, args));
        break;
      }
      default:
        throw new Error('unknown command ' + cmd);
    }
    reply(id, true, result);
  } catch (e) {
    reply(id, false, e && e.message ? e.message : e);
  }
}

function waitLoaded(tabId, ms) {
  return new Promise(resolve => {
    const done = () => { chrome.tabs.onUpdated.removeListener(listener); clearTimeout(timer); resolve(); };
    const listener = (id, info) => { if (id === tabId && info.status === 'complete') done(); };
    const timer = setTimeout(done, ms);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// ---- functions that run INSIDE the page (each is serialised on its own: no outside references) ----

function pageElements(args) {
  const SEL = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=checkbox], ' +
              '[role=radio], [role=tab], [role=menuitem], [role=option], [role=switch], [role=textbox], ' +
              '[role=combobox], [role=searchbox], [contenteditable=""], [contenteditable=true], [onclick]';
  let next = Number(document.documentElement.getAttribute('data-eva-next') || '1');
  const vw = window.innerWidth, vh = window.innerHeight;
  const out = [];
  const want = String(args.contains || '').toLowerCase().split(/\s+/).filter(Boolean);
  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label') || '';
    const labelled = el.getAttribute('aria-labelledby');
    let lab = '';
    if (labelled) lab = labelled.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    let forLabel = '';
    if (el.labels && el.labels.length) forLabel = Array.from(el.labels).map(l => l.innerText).join(' ');
    const text = (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') ? '' : (el.innerText || '');
    return (aria || lab || forLabel || text || el.getAttribute('placeholder') || el.getAttribute('title') ||
            el.getAttribute('alt') || (el.type === 'submit' || el.type === 'button' ? el.value : '') || '')
            .replace(/\s+/g, ' ').trim().slice(0, 140);
  };
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0) continue;
    let id = el.getAttribute('data-eva-id');
    if (!id) { id = 'e' + (next++); el.setAttribute('data-eva-id', id); }
    const name = nameOf(el);
    if (want.length && !want.every(w => name.toLowerCase().includes(w))) continue;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'input' ? (el.type || 'text') : tag);
    const e = { id, role, name,
                in_view: r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw,
                rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)] };
    if (el.disabled) e.disabled = true;
    if (tag === 'a' && el.href) e.href = el.href.slice(0, 200);
    if ((tag === 'input' || tag === 'textarea') && el.type !== 'password') e.value = String(el.value || '').slice(0, 80);
    if (el.type === 'password') e.password = true;
    if (el.type === 'checkbox' || el.type === 'radio') e.checked = !!el.checked;
    e.attrs = [el.getAttribute('name'), el.id, el.getAttribute('autocomplete'), el.getAttribute('placeholder')]
              .filter(Boolean).join(' ').slice(0, 120);
    out.push(e);
  }
  document.documentElement.setAttribute('data-eva-next', String(next));
  // what she can use first: in view before out of view, named before nameless
  out.sort((a, b) => (b.in_view - a.in_view) || ((b.name ? 1 : 0) - (a.name ? 1 : 0)));
  const max = Math.max(10, Math.min(400, Number(args.max || 150)));
  return { url: location.href, title: document.title, total: out.length, elements: out.slice(0, max) };
}

function pageDescribe(args) {
  const el = document.querySelector(`[data-eva-id="${String(args.element_id).replace(/"/g, '')}"]`);
  if (!el) return { __error: `element ${args.element_id} is not on the page any more -- list the elements again` };
  const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '')
               .replace(/\s+/g, ' ').trim().slice(0, 140);
  return { url: location.href, title: document.title, name, tag: el.tagName.toLowerCase(),
           type: el.type || '', password: el.type === 'password',
           attrs: [el.getAttribute('name'), el.id, el.getAttribute('autocomplete'), el.getAttribute('placeholder')]
                  .filter(Boolean).join(' ').slice(0, 120) };
}

function pageClick(args) {
  const el = document.querySelector(`[data-eva-id="${String(args.element_id).replace(/"/g, '')}"]`);
  if (!el) return { __error: `element ${args.element_id} is not on the page any more -- list the elements again` };
  if (el.disabled) return { __error: 'that control is disabled' };
  const before = location.href;
  el.scrollIntoView({ block: 'center', inline: 'center' });
  if (typeof el.focus === 'function') el.focus({ preventScroll: true });
  el.click();
  return { clicked: (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 80),
           url_before: before, url: location.href };
}

function pageType(args) {
  const el = document.querySelector(`[data-eva-id="${String(args.element_id).replace(/"/g, '')}"]`);
  if (!el) return { __error: `element ${args.element_id} is not on the page any more -- list the elements again` };
  if (el.type === 'password') return { __error: 'that is a password field: Eva never types passwords -- the person types it' };
  const text = String(args.text || '');
  el.scrollIntoView({ block: 'center' });
  el.focus();
  if (el.isContentEditable) {
    if (args.mode !== 'append') document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, text);
  } else {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;       // React / Vue see the change
    setter.call(el, args.mode === 'append' ? (el.value || '') + text : text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  const now = el.isContentEditable ? el.innerText : el.value;
  let submitted = false;
  if (args.submit) {
    const form = el.form;
    if (form && typeof form.requestSubmit === 'function') { form.requestSubmit(); submitted = true; }
    else {
      for (const t of ['keydown', 'keypress', 'keyup']) {
        el.dispatchEvent(new KeyboardEvent(t, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
      }
      submitted = true;
    }
  }
  return { verified: String(now || '').includes(text), submitted };
}

function pageRead(args) {
  const max = Math.max(500, Math.min(40000, Number(args.max_chars || 12000)));
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
  return { url: location.href, title: document.title, text: text.slice(0, max), truncated: text.length > max,
           length: text.length };
}

function pageScroll(args) {
  const dir = String(args.direction || 'down');
  const amount = Math.round(window.innerHeight * 0.8);
  if (dir === 'top') window.scrollTo(0, 0);
  else if (dir === 'bottom') window.scrollTo(0, document.body.scrollHeight);
  else window.scrollBy(0, dir === 'up' ? -amount : amount);
  return { y: Math.round(window.scrollY), height: document.body ? document.body.scrollHeight : 0 };
}

connect();
