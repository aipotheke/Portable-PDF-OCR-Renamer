# Example: get the Windows exe

The Windows exe is **built automatically by GitHub Actions** on every push to
`main` — it is not committed to the repo (a ~55 MB binary does not belong in git,
and building on GitHub's real Windows machines is the only reliable way to get a
Windows exe from this project).

## Download the latest exe

1. Open the repo on GitHub: https://github.com/aipotheke/mistral
2. Click the **Actions** tab (top of the page).
3. Click the top (most recent) run named **Build Windows exe**.
4. Scroll down to **Artifacts** and click **PDF-OCR-Renamer-windows** to download
   the zip. Inside is `PDF-OCR-Renamer.exe`.

## Run it

- Copy `PDF-OCR-Renamer.exe` anywhere (USB stick, scanner folder, Desktop) — no
  Python or installation needed.
- On first run Windows may show a SmartScreen warning because the exe is
  unsigned: click **More info → Run anyway**.
- Double-clicking it starts the full app: web UI at
  http://127.0.0.1:8765 and a tray icon (Open UI / Pause-Resume / Quit).
- All files it creates (`config.json`, `app.log`, `app.lock`, `processed.json`,
  `processed/`, `md/`) live **next to the exe**, so a USB-stick setup keeps
  everything on the stick.
- First thing to do in the UI: enter your IONOS API key under Settings and save.

## Rebuild manually (optional)

If you prefer building on your own Windows PC:

```powershell
git clone https://github.com/aipotheke/mistral
cd mistral
pip install -e . pyinstaller
pyinstaller build.spec --noconfirm --clean
# -> dist\PDF-OCR-Renamer.exe
```
