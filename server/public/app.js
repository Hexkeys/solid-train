(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);

  const loginEl = $('#login'), deskEl = $('#desk');
  const form = $('#loginForm'), idInput = $('#devId'), pwInput = $('#devPw');
  const connectBtn = $('#connectBtn'), errEl = $('#err'), statusEl = $('#status');
  const canvas = $('#screen'), ctx = canvas.getContext('2d');
  const screenbox = $('#screenbox'), cursorEl = $('#cursor');
  const kbInput = $('#kbInput');

  let ws = null;
  let connected = false;
  let lastError = '';
  let deviceId = '';
  const cur = { x: 0.5, y: 0.5 };

  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

  // ------------------------------------------------------------- login
  try {
    const saved = localStorage.getItem('remotepad.id');
    if (saved) idInput.value = saved;
  } catch (_) {}

  idInput.addEventListener('input', () => {
    const d = idInput.value.replace(/\D/g, '').slice(0, 9);
    idInput.value = d.replace(/(\d{3})(?=\d)/g, '$1 ');
  });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const id = idInput.value.replace(/\D/g, '');
    if (id.length !== 9) return showError('The computer ID has 9 digits.');
    try { localStorage.setItem('remotepad.id', idInput.value); } catch (_) {}
    connect(id, pwInput.value);
  });

  function showError(msg) {
    errEl.textContent = msg || '';
  }

  function connect(id, pw) {
    showError('');
    lastError = '';
    deviceId = id;
    connectBtn.disabled = true;
    connectBtn.textContent = 'Connecting…';
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const sock = new WebSocket(`${proto}://${location.host}/ws/viewer`);
    sock.binaryType = 'blob';
    ws = sock;
    sock.onopen = () => sock.send(JSON.stringify({ t: 'auth', id, password: pw }));
    sock.onmessage = (ev) => {
      if (sock !== ws) return;
      if (typeof ev.data === 'string') {
        try { onText(JSON.parse(ev.data)); } catch (_) {}
      } else {
        onFrame(ev.data);
      }
    };
    sock.onclose = () => {
      if (sock !== ws) return;
      ws = null;
      endSession(lastError || (connected ? 'The connection was closed.' : 'Could not connect to the server.'));
    };
    sock.onerror = () => {};
  }

  function onText(m) {
    if (m.t === 'ok') {
      connected = true;
      pwInput.value = '';
      loginEl.hidden = true;
      deskEl.hidden = false;
      deskEl.classList.remove('live');
      screenbox.classList.remove('live');
      statusEl.textContent = 'Connected to ' + idInput.value;
      if (m.size && m.size.w && m.size.h) setAspect(m.size.w / m.size.h);
      sendQuality();
    } else if (m.t === 'error') {
      lastError = m.msg || 'Error';
    } else if (m.t === 'size') {
      if (m.w && m.h) setAspect(m.w / m.h);
    } else if (m.t === 'cur') {
      if (!padActive) setCursor(m.x, m.y);
    }
  }

  function endSession(msg) {
    connected = false;
    releaseEverything(false);
    deskEl.hidden = true;
    deskEl.classList.remove('live', 'screen-only');
    loginEl.hidden = false;
    connectBtn.disabled = false;
    connectBtn.textContent = 'Connect';
    showError(msg);
  }

  function disconnect() {
    const s = ws;
    ws = null;
    if (s) { try { s.close(); } catch (_) {} }
    endSession('');
  }

  // ------------------------------------------------------------ screen
  let decoding = false, pending = null;
  async function onFrame(blob) {
    if (decoding) { pending = blob; return; }
    decoding = true;
    try {
      const bmp = await createImageBitmap(blob);
      if (canvas.width !== bmp.width || canvas.height !== bmp.height) {
        canvas.width = bmp.width;
        canvas.height = bmp.height;
        setAspect(bmp.width / bmp.height);
      }
      ctx.drawImage(bmp, 0, 0);
      if (bmp.close) bmp.close();
      if (!screenbox.classList.contains('live')) {
        screenbox.classList.add('live');
        deskEl.classList.add('live');
      }
    } catch (_) {}
    decoding = false;
    if (pending) { const b = pending; pending = null; onFrame(b); }
  }

  function setAspect(r) {
    if (r > 0.3 && r < 6) deskEl.style.setProperty('--ar', r.toFixed(4));
  }

  function setCursor(x, y) {
    cur.x = clamp01(+x || 0);
    cur.y = clamp01(+y || 0);
    cursorEl.style.left = (cur.x * 100).toFixed(2) + '%';
    cursorEl.style.top = (cur.y * 100).toFixed(2) + '%';
  }

  function send(o) {
    if (ws && ws.readyState === 1 && connected) ws.send(JSON.stringify(o));
  }

  const QUALITY = {
    low: { quality: 40, width: 960, fps: 8 },
    med: { quality: 55, width: 1280, fps: 10 },
    high: { quality: 75, width: 1920, fps: 12 },
  };
  function sendQuality() {
    const q = QUALITY[$('#quality').value] || QUALITY.med;
    send({ t: 'q', quality: q.quality, width: q.width, fps: q.fps });
  }
  $('#quality').addEventListener('change', sendQuality);

  // --------------------------------------------------------- move queue
  let moveRaf = 0, moveTo = null;
  function queueMove(x, y) {
    moveTo = { x: clamp01(x), y: clamp01(y) };
    if (moveRaf) return;
    moveRaf = requestAnimationFrame(() => {
      moveRaf = 0;
      if (moveTo) send({ t: 'mv', x: +moveTo.x.toFixed(5), y: +moveTo.y.toFixed(5) });
      moveTo = null;
    });
  }

  const btnName = (b) => (b === 2 ? 'right' : b === 1 ? 'middle' : 'left');

  // ------------------------------- the screen itself: real mouse / touch
  function norm(e) {
    const r = canvas.getBoundingClientRect();
    return { x: clamp01((e.clientX - r.left) / r.width), y: clamp01((e.clientY - r.top) / r.height) };
  }
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  canvas.addEventListener('pointerdown', (e) => {
    if (!connected) return;
    e.preventDefault();
    try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
    const p = norm(e);
    setCursor(p.x, p.y);
    send({ t: 'bt', b: btnName(e.button), d: true, x: p.x, y: p.y });
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!connected) return;
    if (e.pointerType !== 'mouse' && e.buttons === 0) return;
    const p = norm(e);
    setCursor(p.x, p.y);
    queueMove(p.x, p.y);
  });
  const canvasUp = (e) => {
    if (!connected) return;
    const p = norm(e);
    send({ t: 'bt', b: btnName(e.button), d: false, x: p.x, y: p.y });
  };
  canvas.addEventListener('pointerup', canvasUp);
  canvas.addEventListener('pointercancel', canvasUp);
  canvas.addEventListener('wheel', (e) => {
    if (!connected) return;
    e.preventDefault();
    sendWheel(e.deltaX, e.deltaY);
  }, { passive: false });

  function sendWheel(dx, dy) {
    const n = (v) => (v === 0 ? 0 : Math.sign(v) * Math.max(1, Math.round(Math.abs(v) / 100)));
    const x = n(dx), y = n(dy);
    if (x || y) send({ t: 'wh', dx: x, dy: y });
  }

  // ------------------------------------------- the mouse (right of screen)
  const mouseEl = $('#mouse');
  let padActive = false;

  // body = touchpad
  const mBody = $('#mBody');
  let pad = null;
  mBody.addEventListener('pointerdown', (e) => {
    if (!connected) return;
    e.preventDefault();
    mBody.setPointerCapture(e.pointerId);
    pad = { x: e.clientX, y: e.clientY, t: performance.now(), moved: 0 };
    padActive = true;
  });
  mBody.addEventListener('pointermove', (e) => {
    if (!pad) return;
    const dx = e.clientX - pad.x, dy = e.clientY - pad.y;
    pad.x = e.clientX; pad.y = e.clientY;
    pad.moved += Math.abs(dx) + Math.abs(dy);
    const r = canvas.getBoundingClientRect();
    const sens = 1.7;
    setCursor(cur.x + (dx / r.width) * sens, cur.y + (dy / r.height) * sens);
    queueMove(cur.x, cur.y);
  });
  const padEnd = (e) => {
    if (!pad) return;
    const tap = pad.moved < 6 && performance.now() - pad.t < 300;
    pad = null;
    padActive = false;
    if (tap && e.type === 'pointerup') {
      send({ t: 'bt', b: 'left', d: true });
      send({ t: 'bt', b: 'left', d: false });
    }
  };
  mBody.addEventListener('pointerup', padEnd);
  mBody.addEventListener('pointercancel', padEnd);

  // left / right buttons
  function wireMouseButton(id, name) {
    const el = $(id);
    el.addEventListener('pointerdown', (e) => {
      if (!connected) return;
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      el.classList.add('down');
      send({ t: 'bt', b: name, d: true });
    });
    const up = () => {
      if (!el.classList.contains('down')) return;
      el.classList.remove('down');
      send({ t: 'bt', b: name, d: false });
    };
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  }
  wireMouseButton('#mBtnL', 'left');
  wireMouseButton('#mBtnR', 'right');

  // wheel: drag up/down to scroll, tap = middle click
  const mWheel = $('#mWheel');
  let wheel = null;
  mWheel.addEventListener('pointerdown', (e) => {
    if (!connected) return;
    e.preventDefault();
    mWheel.setPointerCapture(e.pointerId);
    wheel = { y: e.clientY, acc: 0, moved: 0, t: performance.now() };
  });
  mWheel.addEventListener('pointermove', (e) => {
    if (!wheel) return;
    const dy = e.clientY - wheel.y;
    wheel.y = e.clientY;
    wheel.moved += Math.abs(dy);
    wheel.acc += dy;
    while (Math.abs(wheel.acc) >= 14) {
      const dir = Math.sign(wheel.acc);
      send({ t: 'wh', dx: 0, dy: dir });
      wheel.acc -= dir * 14;
    }
  });
  const wheelEnd = (e) => {
    if (!wheel) return;
    const tap = wheel.moved < 5 && performance.now() - wheel.t < 350;
    wheel = null;
    if (tap && e.type === 'pointerup') {
      send({ t: 'bt', b: 'middle', d: true });
      send({ t: 'bt', b: 'middle', d: false });
    }
  };
  mWheel.addEventListener('pointerup', wheelEnd);
  mWheel.addEventListener('pointercancel', wheelEnd);
  mouseEl.addEventListener('wheel', (e) => {
    if (!connected) return;
    e.preventDefault();
    sendWheel(e.deltaX, e.deltaY);
  }, { passive: false });
  mouseEl.addEventListener('contextmenu', (e) => e.preventDefault());

  // --------------------------------------------- physical keyboard
  const IGNORE = new Set(['Unidentified', 'Process', 'Dead', 'Compose']);
  document.addEventListener('keydown', (e) => {
    if (!connected || IGNORE.has(e.key)) return;
    if (e.target === kbInput && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) return; // beforeinput handles it
    if (e.target && /^(INPUT|SELECT)$/.test(e.target.tagName)) return;
    e.preventDefault();
    send({ t: 'kd', k: e.key });
  });
  document.addEventListener('keyup', (e) => {
    if (!connected || IGNORE.has(e.key)) return;
    if (e.target === kbInput && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) return;
    if (e.target && /^(INPUT|SELECT)$/.test(e.target.tagName)) return;
    e.preventDefault();
    send({ t: 'ku', k: e.key });
  });
  window.addEventListener('blur', () => releaseEverything(true));
  document.addEventListener('visibilitychange', () => { if (document.hidden) releaseEverything(true); });

  // phone / tablet system keyboard
  kbInput.value = '  ';
  kbInput.addEventListener('beforeinput', (e) => {
    if (!connected) return;
    const t = e.inputType;
    if (t === 'insertText' || t === 'insertCompositionText' || t === 'insertFromPaste') {
      if (e.data) send({ t: 'tx', s: e.data });
    } else if (t === 'deleteContentBackward') {
      send({ t: 'kd', k: 'Backspace' });
      send({ t: 'ku', k: 'Backspace' });
    } else if (t === 'insertLineBreak' || t === 'insertParagraph') {
      send({ t: 'kd', k: 'Enter' });
      send({ t: 'ku', k: 'Enter' });
    }
    e.preventDefault();
  });
  $('#btnPhoneKb').addEventListener('click', () => {
    kbInput.value = '  ';
    kbInput.focus();
    kbInput.setSelectionRange(2, 2);
  });

  // ---------------------------------------------------- on-screen keyboard
  // [label, key, shifted label, flex]
  const L = (c, s) => [c.toUpperCase(), c, s, 1];
  const ROWS = [
    [['Esc', 'Escape', null, 1.4], ...[1,2,3,4,5,6,7,8,9,10,11,12].map((n) => ['F' + n, 'F' + n, null, 1]), ['Del', 'Delete', null, 1.2]],
    [L('`', '~'), L('1', '!'), L('2', '@'), L('3', '#'), L('4', '$'), L('5', '%'), L('6', '^'), L('7', '&'), L('8', '*'), L('9', '('), L('0', ')'), L('-', '_'), L('=', '+'), ['⌫', 'Backspace', null, 1.6]],
    [['Tab', 'Tab', null, 1.5], L('q'), L('w'), L('e'), L('r'), L('t'), L('y'), L('u'), L('i'), L('o'), L('p'), L('[', '{'), L(']', '}'), L('\\', '|')],
    [['Caps', 'CapsLock', null, 1.8], L('a'), L('s'), L('d'), L('f'), L('g'), L('h'), L('j'), L('k'), L('l'), L(';', ':'), L("'", '"'), ['Enter', 'Enter', null, 2]],
    [['Shift', 'Shift', null, 2.3], L('z'), L('x'), L('c'), L('v'), L('b'), L('n'), L('m'), L(',', '<'), L('.', '>'), L('/', '?'), ['Shift', 'Shift', null, 2.3]],
    [['Ctrl', 'Control', null, 1.3], ['Win', 'Meta', null, 1.2], ['Alt', 'Alt', null, 1.2], ['', ' ', null, 6], ['Alt', 'Alt', null, 1.2], ['◀', 'ArrowLeft', null, 1], ['▲', 'ArrowUp', null, 1], ['▼', 'ArrowDown', null, 1], ['▶', 'ArrowRight', null, 1]],
  ];
  const MODS = new Set(['Shift', 'Control', 'Alt', 'Meta']);
  const latched = new Set();
  const keyEls = []; // { el, def }

  const oskRows = $('#oskRows');
  ROWS.forEach((row, ri) => {
    const rowEl = document.createElement('div');
    rowEl.className = 'osk-row';
    row.forEach((def) => {
      const [label, key, shifted, flex] = def;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'k' + (ri === 0 ? ' f' : '');
      b.style.flexGrow = String(flex);
      b.textContent = label;
      b.dataset.key = key;
      b.setAttribute('aria-label', label || 'Space');
      wireKey(b, key);
      rowEl.appendChild(b);
      keyEls.push({ el: b, def });
    });
    oskRows.appendChild(rowEl);
  });

  function refreshLabels() {
    const shift = latched.has('Shift');
    for (const { el, def } of keyEls) {
      if (def[2]) el.textContent = shift ? def[2] : def[0];
      if (MODS.has(def[1])) el.classList.toggle('latched', latched.has(def[1]));
    }
  }

  function wireKey(btn, key) {
    let delay = 0, rep = 0, held = false;
    const stop = () => { clearTimeout(delay); clearInterval(rep); delay = rep = 0; };
    btn.addEventListener('pointerdown', (e) => {
      if (!connected) return;
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      if (MODS.has(key)) {
        if (latched.has(key)) { latched.delete(key); send({ t: 'ku', k: key }); }
        else { latched.add(key); send({ t: 'kd', k: key }); }
        refreshLabels();
        return;
      }
      held = true;
      btn.classList.add('down');
      send({ t: 'kd', k: key });
      if (key !== 'CapsLock') {
        delay = setTimeout(() => { rep = setInterval(() => send({ t: 'kd', k: key }), 45); }, 450);
      }
    });
    const up = () => {
      if (!held) return;
      held = false;
      stop();
      btn.classList.remove('down');
      send({ t: 'ku', k: key });
      if (latched.size) { // one-shot modifiers
        for (const m of latched) send({ t: 'ku', k: m });
        latched.clear();
        refreshLabels();
      }
    };
    btn.addEventListener('pointerup', up);
    btn.addEventListener('pointercancel', up);
  }

  function releaseEverything(notify) {
    latched.clear();
    refreshLabels();
    stopAllHeld();
    if (notify) send({ t: 'ra' });
  }
  function stopAllHeld() {
    document.querySelectorAll('.k.down, .mbtn.down').forEach((el) => el.classList.remove('down'));
  }

  // ------------------------------------------------------------ toolbar
  $('#btnQuit').addEventListener('click', disconnect);
  $('#btnFocus').addEventListener('click', (e) => {
    const on = deskEl.classList.toggle('screen-only');
    e.currentTarget.classList.toggle('on', on);
    e.currentTarget.textContent = on ? 'Show keyboard' : 'Screen only';
  });
  $('#btnFull').addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
  });
})();
