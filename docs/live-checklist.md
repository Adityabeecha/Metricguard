# Live checks for the hosted beta

Two short procedures for the owner after a deploy: a 15-minute walk through the live site, and a backup restore drill. Neither changes your real data.

## 1. Fifteen-minute live walk-through

Use two browsers: your normal one (your full account) and a private window, signed in with a Google or GitHub account whose email is **not** on `METRICGUARD_FULL_ACCESS_EMAILS` (the guest).

| # | Do this | You should see |
|---|---|---|
| 1 | Open `https://metricguard.vercel.app/api/v1/health` | `"status":"ok"`, `"database":"ok"`, and `server_errors` with `since_start` 0 or a small number |
| 2 | Private window: `/signup` | The guest terms (erased after 8 hours, 25 MB) above the buttons |
| 3 | Sign up as the guest | A workspace with the guest countdown notice |
| 4 | Click **Try with sample orders**, then **Compare with the sample orders** | The sample dataset is ready; New comparison opens with it chosen |
| 5 | Current: all records. Proposed: `status` = `Paid` (capital P) | "No 'Paid' in status. Did you mean 'paid'?" under the calculation |
| 6 | Fix to `paid` and click **Check and compare** | "N of 30 comparisons left today"; leave the page and open **Cases**: the run is under **Running now** |
| 7 | Open the result when it finishes | **Balanced ✓**; Where it sits; Concentration sentences; Day/Week/Month for a date column |
| 8 | Save as a case, then **Edit conclusion** | "Edited …" under the conclusion; the custody log shows the earlier text |
| 9 | **Replay & verify** | The replay checks table, every line "Same" |
| 10 | **Share** → public link (7 days); open it in a third window | Read-only case with "link expires …" and, after step 8, "Conclusion edited …" |
| 11 | Upload a ~20 MB CSV | The progress percentage reaches 100% and the checks start (confirms Vercel passes large uploads) |
| 12 | Your normal browser: **Settings → Beta admin** (needs your email in `METRICGUARD_ADMIN_EMAILS`) | Accounts (you full, the test account guest), sign-ups by day, any feedback |
| 13 | **Give full access** on the test guest | The private window's guest notice disappears after a refresh |
| 14 | Private window: **Settings → Account → Delete my account** | The summary of what will be erased; after DELETE you are signed out |
| 15 | Health URL again | Still `ok`; `server_errors.since_start` unchanged from step 1 |

If a step fails, note the step number and what you saw, and send it with **Feedback** (or paste it to the assistant).

## 2. Backup restore drill (Neon branch)

This proves a nightly backup can be loaded back. It uses a throwaway Neon branch, never the main database.

1. **Make a backup now.** On Northflank, run the backup job once (or run `python -m scripts.backup_db` in a shell on the service). The log ends with `backup ok: backups/db/<date>.jsonl.gz (N rows in M tables)`. Note N.
2. **Download it.** In the Cloudflare dashboard, open the R2 bucket, go to `backups/db/` (under your `METRICGUARD_S3_PREFIX`, if you set one), and download today's file.
3. **Create an empty Neon branch.** In Neon: Branches → Create branch → choose *"Schema only"* or create a branch and drop its tables. Copy its connection string. The restore refuses a database that already has rows.
4. **Restore into it** from this repository on your computer:
   ```bash
   .venv\Scripts\python -m scripts.backup_db --restore <downloaded file> --into "<branch connection string>"
   ```
   It prints `restore ok: N rows in M tables`. N should match step 1.
5. **Spot-check.** In the Neon SQL editor on the branch: `SELECT count(*) FROM users;` and `SELECT title FROM records WHERE kind = 'investigation' LIMIT 5;` look like production.
6. **Delete the branch** in Neon.

Datasets and evidence files are not in this backup; they live in R2 and are kept there. Sessions are not backed up, so after a real restore everyone signs in again.
