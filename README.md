# Campus Escape

Campus Escape is a Flask learning game with Python, C, and Logic sectors, a debugging lab, a Master Cipher Vault, player feedback, and an optional code duel arena.

## Run locally on Windows

1. Open this folder in VS Code.
2. In the integrated terminal, activate the virtual environment:

   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation in this terminal, run `Set-ExecutionPolicy -Scope Process RemoteSigned` and activate again. You can also skip activation and use `./venv/Scripts/python.exe` directly.
3. Install project dependencies if needed:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Start the app:

   ```powershell
   python app.py
   ```

5. Open `http://127.0.0.1:5000` in a browser.

The SQLite database and Flask signing key are created locally and excluded from Git. Do not share or commit them.

## Administrator setup

Administrator credentials are not defined in the source code. To create the first admin account in a fresh database, set these variables in the same PowerShell terminal **before the first launch**:

```powershell
$env:CAMPUS_ESCAPE_ADMIN_USERNAME = "ChooseYourAdminName"
$env:CAMPUS_ESCAPE_ADMIN_PIN = Read-Host "Choose a private four-digit PIN"
python app.py
```

Use a unique username. The app refuses to promote an existing player account. Once the admin account has been created, you can clear the variables with `Remove-Item Env:CAMPUS_ESCAPE_ADMIN_USERNAME` and `Remove-Item Env:CAMPUS_ESCAPE_ADMIN_PIN`. Keep the PIN private; a four-digit PIN is suitable only for a local classroom/demo deployment. Use a stronger authentication method before exposing the app to the public internet.

If you already have an administrator account in your local `campus_escape.db`, it retains its stored role. The application no longer creates or promotes a named account automatically.

## How to play

- **Map:** follow the Python → C → Logic route. Practice Terminal, Master Cipher Vault, and Certificate links are grouped below it.
- **Labs:** select an answer, use the hint when needed, and use `1`–`4` to choose an option or `Enter` to submit when the question is ready.
- **Debug Lab:** move between challenges, edit the code, run it, read the terminal output, and submit when it matches the goal.
- **PvP Arena:** choose **Find a player** to wait for another signed-in player or **Play with Cipher Bot** for a solo timed race. Run the starter code, fix its output, and submit. Only editor activity is shared with another player.
- **Dashboard:** review scores, sector status, achievements, and recent runs.
- **Feedback:** choose private Admin inbox or Community before sending. Titles are optional.
- **Admin:** manage player status and saved progress, inspect lab analytics, review community puzzles, export results, and publish map announcements. Reset and delete actions ask for confirmation. Administrator accounts cannot be reset or deleted from the player table.

## Troubleshooting

- **A button seems unresponsive:** refresh the page after restarting Flask, check the browser console for errors, and confirm the action message in the page's status area. Admin API changes require the current signed-in admin session and CSRF token.
- **Admin reset:** resets scores, lab clears, vault state, debugging XP, daily challenge XP, and active quiz/arena data. It does not delete the account or its community posts and feedback.
- **C code does not run:** C compilation uses the configured online Wandbox service and needs an internet connection. Python exercises run in the app's restricted interpreter.
- **A route redirects to login:** sign in again; the browser session may have expired or the account may have been suspended.

## Main routes

| Route | Purpose |
|---|---|
| `/` | Login and account creation |
| `/map` | Sector progression and final-mission links |
| `/dashboard` | Personal progress and recent activity |
| `/debug` | Python/C debugging challenge selection |
| `/arena` | Live player matchmaking or Cipher Bot practice |
| `/feedback` | Private Admin feedback and public community discussion |
| `/admin` | Administrator analytics and account/content management |

