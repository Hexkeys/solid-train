'use strict';
/*
 * RemotePad relay server.
 *
 *   Windows agent  --wss-->  /ws/agent   (sends JPEG frames, receives input events)
 *   Web viewer     --wss-->  /ws/viewer  (sends mouse/keyboard, receives frames)
 *
 * The server never stores anything. It only checks the access password
 * (the agent registers a SHA-256 hash of it) and forwards messages.
 */
const http = require('http');
const path = require('path');
const crypto = require('crypto');
const express = require('express');
const { WebSocketServer, WebSocket } = require('ws');

const PORT = parseInt(process.env.PORT || '3000', 10);

const ID_RE = /^[0-9]{9}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const VIEWER_ALLOWED = new Set(['mv', 'rm', 'bt', 'wh', 'kd', 'ku', 'tx', 'ra', 'q']);
const AGENT_ALLOWED = new Set(['size', 'cur']);

const MAX_FAILS = 8; // wrong passwords per window, per device id and per ip
const FAIL_WINDOW_MS = 10 * 60 * 1000;

const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Frame-Options', 'DENY');
  next();
});
app.get('/healthz', (_req, res) => res.type('text').send('ok'));
app.use(express.static(path.join(__dirname, 'public'), { maxAge: 0 }));

const server = http.createServer(app);

/** id -> { ws, pwHash, size, viewer } */
const agents = new Map();
/** key -> { count, resetAt } */
const fails = new Map();

const wssAgent = new WebSocketServer({ noServer: true, maxPayload: 8 * 1024 * 1024 });
const wssViewer = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 });

server.on('upgrade', (req, socket, head) => {
  let pathname = '';
  try {
    pathname = new URL(req.url, 'http://localhost').pathname;
  } catch (_) {
    socket.destroy();
    return;
  }
  const target = pathname === '/ws/agent' ? wssAgent : pathname === '/ws/viewer' ? wssViewer : null;
  if (!target) {
    socket.destroy();
    return;
  }
  target.handleUpgrade(req, socket, head, (ws) => {
    ws.clientIp = clientIp(req);
    target.emit('connection', ws, req);
  });
});

function clientIp(req) {
  const xff = req.headers['x-forwarded-for'];
  if (typeof xff === 'string' && xff.length) return xff.split(',')[0].trim();
  return req.socket.remoteAddress || 'unknown';
}

function sha256(s) {
  return crypto.createHash('sha256').update(s, 'utf8').digest('hex');
}

function safeEqualHex(a, b) {
  const ba = Buffer.from(a, 'hex');
  const bb = Buffer.from(b, 'hex');
  return ba.length === bb.length && crypto.timingSafeEqual(ba, bb);
}

function sendJson(ws, obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function tooManyFails(key) {
  const f = fails.get(key);
  if (!f) return false;
  if (Date.now() > f.resetAt) {
    fails.delete(key);
    return false;
  }
  return f.count >= MAX_FAILS;
}

function addFail(key) {
  const f = fails.get(key);
  if (!f || Date.now() > f.resetAt) fails.set(key, { count: 1, resetAt: Date.now() + FAIL_WINDOW_MS });
  else f.count += 1;
}

setInterval(() => {
  const now = Date.now();
  for (const [k, f] of fails) if (now > f.resetAt) fails.delete(k);
}, 60 * 1000).unref();

// ---------------------------------------------------------------- agents
wssAgent.on('connection', (ws) => {
  ws.isAlive = true;
  ws.on('pong', () => (ws.isAlive = true));
  let entry = null;

  const helloTimer = setTimeout(() => {
    if (!entry) ws.close(4001, 'hello timeout');
  }, 10000);

  ws.on('message', (data, isBinary) => {
    if (isBinary) {
      // JPEG frame -> viewer (drop if the viewer connection is congested)
      if (entry && entry.viewer && entry.viewer.readyState === WebSocket.OPEN) {
        if (entry.viewer.bufferedAmount < 512 * 1024) entry.viewer.send(data, { binary: true });
      }
      return;
    }
    let m;
    try {
      m = JSON.parse(data.toString());
    } catch (_) {
      return;
    }
    if (!m || typeof m.t !== 'string') return;

    if (!entry) {
      if (m.t !== 'hello') return;
      const id = String(m.id || '');
      const pw = String(m.pw || '');
      if (!ID_RE.test(id) || !HASH_RE.test(pw)) {
        sendJson(ws, { t: 'reject', reason: 'bad_hello' });
        ws.close(4002, 'bad hello');
        return;
      }
      const old = agents.get(id);
      if (old && old.ws.readyState === WebSocket.OPEN && !safeEqualHex(old.pwHash, pw)) {
        sendJson(ws, { t: 'reject', reason: 'id_in_use' });
        ws.close(4003, 'id in use');
        return;
      }
      if (old) {
        if (old.viewer) {
          sendJson(old.viewer, { t: 'error', msg: 'The computer reconnected. Please sign in again.' });
          old.viewer.close(4010, 'agent replaced');
        }
        try {
          old.ws.close(4000, 'replaced');
        } catch (_) {}
      }
      clearTimeout(helloTimer);
      entry = { ws, pwHash: pw, size: null, viewer: null };
      agents.set(id, entry);
      ws.agentId = id;
      sendJson(ws, { t: 'hello_ok' });
      return;
    }

    if (m.t === 'setpw') {
      if (HASH_RE.test(String(m.pw || ''))) {
        entry.pwHash = String(m.pw);
        kickViewer(entry, 'The access password was changed.');
      }
      return;
    }
    if (m.t === 'kick') {
      kickViewer(entry, 'The computer ended the session.');
      return;
    }
    if (AGENT_ALLOWED.has(m.t)) {
      if (m.t === 'size') entry.size = { w: Number(m.w) || 0, h: Number(m.h) || 0 };
      if (entry.viewer) sendJson(entry.viewer, m);
    }
  });

  ws.on('close', () => {
    clearTimeout(helloTimer);
    if (entry && agents.get(ws.agentId) === entry) {
      agents.delete(ws.agentId);
      if (entry.viewer) {
        sendJson(entry.viewer, { t: 'error', msg: 'The computer went offline.' });
        entry.viewer.close(4011, 'agent offline');
      }
    }
  });
  ws.on('error', () => {});
});

function kickViewer(entry, msg) {
  const v = entry.viewer;
  if (!v) return;
  entry.viewer = null;
  sendJson(v, { t: 'error', msg });
  v.close(4012, 'kicked');
  sendJson(entry.ws, { t: 'viewer', on: false });
}

// --------------------------------------------------------------- viewers
wssViewer.on('connection', (ws) => {
  ws.isAlive = true;
  ws.on('pong', () => (ws.isAlive = true));
  let entry = null;

  const authTimer = setTimeout(() => {
    if (!entry) ws.close(4001, 'auth timeout');
  }, 15000);

  ws.on('message', (data, isBinary) => {
    if (isBinary) return;
    let m;
    try {
      m = JSON.parse(data.toString());
    } catch (_) {
      return;
    }
    if (!m || typeof m.t !== 'string') return;

    if (!entry) {
      if (m.t !== 'auth') return;
      const id = String(m.id || '').replace(/\s+/g, '');
      const password = String(m.password || '');
      const idKey = 'id:' + id;
      const ipKey = 'ip:' + ws.clientIp;
      if (tooManyFails(idKey) || tooManyFails(ipKey)) {
        sendJson(ws, { t: 'error', msg: 'Too many failed attempts. Try again in a few minutes.' });
        ws.close(4029, 'rate limited');
        return;
      }
      const agent = ID_RE.test(id) ? agents.get(id) : null;
      // Same answer for "unknown id" and "wrong password" so ids cannot be probed.
      if (!agent || !safeEqualHex(agent.pwHash, sha256(password))) {
        addFail(idKey);
        addFail(ipKey);
        sendJson(ws, { t: 'error', msg: 'Wrong ID or password, or the computer is offline.' });
        ws.close(4004, 'denied');
        return;
      }
      clearTimeout(authTimer);
      if (agent.viewer) {
        sendJson(agent.viewer, { t: 'error', msg: 'Another viewer took over this session.' });
        agent.viewer.close(4013, 'bumped');
      }
      agent.viewer = ws;
      entry = agent;
      sendJson(ws, { t: 'ok', size: agent.size });
      sendJson(agent.ws, { t: 'viewer', on: true });
      return;
    }

    if (!VIEWER_ALLOWED.has(m.t)) return;
    if (entry.ws.readyState === WebSocket.OPEN) entry.ws.send(JSON.stringify(m));
  });

  ws.on('close', () => {
    clearTimeout(authTimer);
    if (entry && entry.viewer === ws) {
      entry.viewer = null;
      sendJson(entry.ws, { t: 'viewer', on: false });
    }
  });
  ws.on('error', () => {});
});

// Keep connections alive through proxies and drop dead ones.
setInterval(() => {
  for (const set of [wssAgent.clients, wssViewer.clients]) {
    for (const ws of set) {
      if (!ws.isAlive) {
        ws.terminate();
        continue;
      }
      ws.isAlive = false;
      try {
        ws.ping();
      } catch (_) {}
    }
  }
}, 25 * 1000).unref();

if (require.main === module) {
  server.listen(PORT, '0.0.0.0', () => console.log(`RemotePad server listening on :${PORT}`));
}

module.exports = { server, app };
