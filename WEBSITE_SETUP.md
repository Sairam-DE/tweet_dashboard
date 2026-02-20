# PulseBoard Always-On Setup (Windows)

Run this one time in PowerShell:

```powershell
cd d:\files\tweet_dashboard
powershell -ExecutionPolicy Bypass -File .\install_autostart_task.ps1
```

If you get `Access is denied`, open PowerShell as **Administrator** and run the same command.

After this, the website starts automatically at login.

Default URL:

- `http://localhost:8000`

For other devices on your LAN:

- `http://<your-computer-ip>:8000`

To stop auto-start:

```powershell
cd d:\files\tweet_dashboard
powershell -ExecutionPolicy Bypass -File .\remove_autostart_task.ps1
```
