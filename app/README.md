# The Living App (command bridge)

A real local server (FastAPI) serving an interactive dashboard. Click to complete
todos, add inline, and talk to the brain console — all live, no 10-min wait.

## Run
```bash
cd ~/Claude/second-brain
./serve.sh                 # http://localhost:8765  (Mac only)
./serve.sh 0.0.0.0         # also reachable on your LAN / Tailscale
```

## Phone access anywhere (Tailscale)
1. Install Tailscale on the Mac and the iPhone (same account); both show a
   `100.x.y.z` IP.
2. Run `./serve.sh 0.0.0.0`.
3. On the phone, browse to `http://<mac-tailscale-ip>:8765`. Add to Home Screen.
Works from anywhere, encrypted, no port-forwarding. (The static iCloud dashboard
still works offline as a fallback.)

## The brain console
Bottom-right. Command mode works now, free + offline:
- `add call the lawyer tomorrow at 2pm p1`
- `done lawyer`  ·  `what's my day`  ·  `inbox`  ·  `schedule`
Type `help` for the full list.

**Full natural-language mode:** drop a key into `second-brain/.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```
Then the console understands anything and can add/triage/reschedule in one breath.
(The chat-ingest hook can use the same key — there's no `claude` CLI on this Mac.)

## API (for future panels / integrations)
`GET /api/state` · `POST /api/todo` · `POST /api/todo/{id}/complete` ·
`/reschedule` · `/triage` · `POST /api/chat`

## Always-on (optional, your hands — like the launchd timer)
To keep it running across reboots, wrap `serve.sh` in a LaunchAgent the same way
as `com.jarvis.secondbrain.plist`. Ask and I'll generate the plist.

## Life areas
Defined in `store/areas.json`. `status:"live"` areas compute a metric from the
store; `status:"connect"` areas are placeholders until a data source is wired
(Finance→Stripe/GHL, Health→Apple Health export, etc.). No fake numbers — they
stay greyed until real.
