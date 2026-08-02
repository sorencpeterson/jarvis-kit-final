# Google Calendar setup (one-time, ~10 min — your hands)

Auto-scheduling needs a Google OAuth credential. Do this once:

1. **Google Cloud Console** → create (or pick) a project.
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **APIs & Services → Credentials** → *Create credentials* → **OAuth client ID**
   → Application type **Desktop app** → create.
   - If asked to configure the consent screen: set it to **External**, add your own
     Google address as a **Test user** (keeps it in "Testing" so it doesn't need
     Google verification).
4. **Download** the client JSON, rename it `client_secret.json`, and drop it in:
   `second-brain/schedule/credentials/`
5. Install the libraries once:
   ```bash
   cd ~/Claude/second-brain && uv pip install google-api-python-client google-auth-oauthlib
   ```
6. First run opens a browser to grant access (scope is **events only**, not full
   calendar). A `token.json` is cached next to the secret; refreshes automatically.

```bash
uv run python schedule/gcal_write.py --list      # preview blocks
uv run python schedule/gcal_write.py --confirm   # create them
```

The `credentials/` folder holds secrets — treat it like the GHL `.env`, never share it.
