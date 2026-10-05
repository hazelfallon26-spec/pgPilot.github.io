PG app - how to run (needs only Python 3.10+; nothing else to install)
1) python3 server.py create-owner      # one time: creates your PG and owner login
2) python3 server.py run               # opens on http://127.0.0.1:8000  (also sends reminders + makes a daily backup)
Environment options: PGAPP_DB (database file), PORT, HOST, PGAPP_BACKUPS (backup folder), PGAPP_HTTPS=1 (when served over https)
Tests: python3 -m unittest
Not connected yet: real AI (complaints go to Needs review unless a category is picked), SMS/WhatsApp/voice, owner screens, off-server backups.
