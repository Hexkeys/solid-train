# RemotePad

Control your own Windows computer from any web browser. Move the mouse on the web page and the real cursor moves; type on the page (or on the on-screen keyboard) and the real computer types.

```
 Browser (your Render site)  <--wss-->  Render server  <--wss-->  RemotePad-Agent.exe (your PC)
   pink monitor, keyboard, mouse         relays + checks password      captures screen, moves mouse, presses keys
```

The exe connects **out** to your server, so no router settings or port forwarding are needed.

```
server/   Node web app (deploy this on Render)  -> website + relay
agent/    Windows agent (Python)                -> built into RemotePad-Agent.exe by GitHub
render.yaml                                     -> one-click Render setup
.github/workflows/build-agent.yml               -> builds the exe automatically
```

## 1. Put it on GitHub

1. Create a new empty repository on GitHub (private is fine).
2. Upload everything in this folder (or `git init`, `git add .`, `git commit`, `git remote add origin ...`, `git push -u origin main`).
3. Open the **Actions** tab. The workflow **Build Windows agent (exe)** starts by itself (or press *Run workflow*).
4. When it is green, open the run and download the artifact **RemotePad-Agent** (it contains `RemotePad-Agent.exe`).
   - To get a permanent download link instead: create a release tag (`git tag v1.0.0 && git push --tags`). The exe is attached to the release automatically.

## 2. Deploy the website on Render

1. On [render.com](https://render.com): **New + > Blueprint**, pick your GitHub repo. Render reads `render.yaml` and creates the web service `remotepad`.
   (Manual alternative: New > Web Service, Root Directory `server`, Build `npm install`, Start `npm start`.)
2. Wait for the deploy. Your address will look like `https://remotepad-xxxx.onrender.com`.

## 3. Run the agent on the computer you want to control

1. Start `RemotePad-Agent.exe`. (Windows SmartScreen may warn because the exe is not code-signed: *More info > Run anyway*.)
2. Paste your Render address into **Server address** and press **Start sharing**. It is remembered for next time.
3. The window shows the **Computer ID** and **Password**.

## 4. Connect from anywhere

Open your Render address, enter the ID and password, press **Connect**.

| You do | The computer does |
| --- | --- |
| Move the mouse over the monitor | Real cursor moves |
| Click / right-click / scroll on the monitor | Real clicks / scrolling |
| Type on your keyboard, or click the on-screen keyboard | Real key presses (Ctrl+C, Alt+Tab, arrows... all work; Shift/Ctrl/Alt/Win on the on-screen keyboard stay held for the next key) |
| Phone/tablet: tap the monitor, or drag on the mouse picture like a touchpad (tap = click, wheel = scroll/middle click, L/R buttons) | Same |
| **Aa** button | Opens your phone's own keyboard (supports any language) |

Toolbar: picture quality (Low/Medium/High), *Screen only* (hides keyboard + mouse), full screen, Disconnect.

## Security

- Anyone who has the **ID + password has full control** of the computer. Treat them like a house key. Press **New** in the agent for a new password at any time (this also disconnects the current viewer).
- Sharing only exists while the agent window is open. The window turns to **"IN USE"** whenever someone is connected, and **Disconnect viewer** kicks them immediately.
- The server only stores a SHA-256 hash of the password in memory, never to disk, and locks out an ID/IP after 8 wrong attempts for 10 minutes.
- Use only on computers you own or are allowed to control.

## Good to know

- **Render free plan sleeps** after ~15 min without traffic; the first page load can take ~50 s. The agent reconnects by itself. For always-instant use pick a paid instance.
- Windows blocks injected input into **elevated (admin) windows**, the UAC prompt and the lock screen unless the agent itself runs as administrator (right-click the exe > Run as administrator). `Ctrl+Alt+Del` cannot be sent by any normal program.
- Only the **primary monitor** is shown.
- Run the agent without a window (console, from source): `python agent/remotepad_agent.py --headless https://your-app.onrender.com`

## Local testing

```
cd server && npm install && npm start      # http://localhost:3000
cd agent  && pip install -r requirements.txt && python remotepad_agent.py
python agent/test_agent_core.py            # unit tests for the agent logic
```
(In the agent, use `localhost:3000` as the server address.)
