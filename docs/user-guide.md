# MetricGuard user guide

This guide walks you through MetricGuard at **https://metricguard.vercel.app** from sign-up to sharing a finished answer. You don't need to be a data analyst. If you can export a spreadsheet, you can use it.

---

## 1. What MetricGuard does

Two numbers that should match often don't:

- Finance says October revenue was **₹12,40,000**. The sales dashboard says **₹12,55,000**.
- Yesterday's report showed **4,812 orders**. Today the same report shows **4,790** for the same day.

People usually spend hours finding out why. MetricGuard does it in minutes. It finds **exactly which records cause the difference**, and the pieces add up to the full gap, down to the last paisa or cent. Nothing is estimated or guessed by AI.

You get:

- **The answer:** how big the difference is and what causes it.
- **The records:** the actual rows behind it, which you can search and download.
- **Where it sits:** whether the difference is concentrated in one region, product, status, date and so on.
- **A saved case:** your conclusion, pinned to the exact data, which anyone can replay and check later.

---

## 2. What you need before you start

| You need | Example |
|---|---|
| A data file: **CSV**, **Excel (.xlsx)** or **Parquet** | `orders_october.csv` exported from your system |
| One column that **identifies each row** | `order_id`, `invoice_no`, `customer_id` |
| One **amount** column (if you're comparing totals) | `revenue`, `amount`, `price` |
| Optional: columns to **filter or group by** | `status`, `region`, `order_date`, `channel` |

A good file looks like this:

| order_id | order_date | status | region | revenue |
|---|---|---|---|---|
| A-1001 | 2026-10-01 | paid | North | 1250.00 |
| A-1002 | 2026-10-01 | refunded | South | 480.50 |
| A-1003 | 2026-10-02 | paid | North | 2200.00 |

**Tips for preparing the file**

- **One header row** at the top, then one row per record. Remove title rows, blank rows and total rows at the bottom.
- **The ID column must be unique.** If two rows share an ID, MetricGuard will tell you; fix it before comparing.
- **Amounts are plain numbers:** `1250.00`, not `₹1,250` or `1,250.00 INR`. You enter the unit (INR, USD, units) separately.
- **Dates in one format.** `2026-10-01` (year-month-day) is safest.
- **From Excel or Google Sheets:** use *File → Download / Save as → CSV*. MetricGuard can read `.xlsx` directly too, but only the first worksheet.
- **Empty cells** are treated as "blank". You choose how blanks are handled when you add the file.

---

## 3. Sign up and sign in

1. Open **https://metricguard.vercel.app** and click **Sign up** (or **Sign in** if you already have an account).
2. Choose **Continue with Google** or **Continue with GitHub**. There's no password to create.
3. You land in your own **personal workspace**. Nobody else can see it unless you invite them.

> **Want to look first?** Open the public sample case at **https://metricguard.vercel.app/demo/MG-F3A3**. No sign-in is needed. It shows a finished answer so you know what to expect.

### Guest accounts (free beta)

During the beta, new accounts may be **guest accounts**. If yours is one, you'll see a notice at the top of every page with a countdown.

- **Everything in a guest workspace is erased 8 hours after sign-up:** datasets, comparisons and cases. Download anything you want to keep (**Records (CSV)** on a case, see section 9).
- **Guest storage is limited to 25 MB in total.** That covers all your files and results together.
- You can sign in again afterwards. You get a fresh, empty workspace.

If you need a permanent account, ask the MetricGuard team using **Feedback** (section 14).

---

## 4. Find your way around

The left sidebar has everything:

| Sidebar item | What it's for |
|---|---|
| **Cases** | Your saved answers. Start here when you come back. |
| **New comparison** | Start a new investigation. |
| **Datasets** | The files and tables you've added. |
| **Metrics** | Named business numbers (e.g. "Net revenue") and their approved calculations. |
| **Changes** | How new data loads and definition changes moved your metrics. |
| **Settings** | Workspace name, members, usage, appearance and your account. |
| **Feedback** | Send a note to the MetricGuard team. |

Your account menu (your initials, top right) has **Settings** and **Sign out**. The theme switch next to it toggles light and dark mode.

---

## 5. The quickest first try: sample data

On a brand-new workspace, the home page shows **Get to your first case**.

1. Click **Try with sample orders**. MetricGuard adds a sample file of 5,000 orders and checks it. This takes a few seconds.
2. Click **Compare with the sample orders**. This opens **New comparison** with the sample already chosen.
3. Follow section 7 below. Try counting all orders against only the `paid` ones and see exactly which orders make the difference.

Once you're comfortable, add your own data.

---

## 6. Add your own data

### 6a. Upload a file (most common)

1. Go to **Datasets → Add dataset** (or **Upload a file** on the first-run page).
2. Choose your CSV, Excel or Parquet file. MetricGuard reads it and shows **Columns found**, with a sample value for each column.
3. Answer the questions:
   - **Which column identifies each row?** Pick your ID column (e.g. `order_id`).
   - **What do you want to total?** Choose **A total of one amount column (most common)** and pick the amount (e.g. `revenue`).
     - Other choices, if you need them: *a total of one column minus another* (e.g. revenue − cost), *a conditional total*, or *several amount columns*. If your file has more amount columns, you'll be asked whether to **Keep them available too**.
   - **Unit:** type what the amount is measured in, e.g. `INR`, `USD`, `units`, `count`. MetricGuard never converts units.
   - **Filter columns:** tick the columns you'll want to filter or group by (status, region, date…).
4. Tick **I confirm the whole file contains the rows to compare.**
5. Click **Check dataset**. MetricGuard checks every row, column type and amount. When it's done you'll see **Dataset ready**.

If something is wrong, for example a duplicate ID or text in an amount column, you'll see **Dataset checks failed** with a **Recommended fix**. Correct the file and upload it again.

**Advanced details** (precision, how blanks are treated and so on) are optional. The defaults suit most files.

### 6b. Add a newer version of the same data

Did the same report get re-exported today? Open the dataset and click **Add newer load**, then upload the new file. It needs the same columns with the same meaning. Each version is called a **load**, and MetricGuard can compare any two loads (section 7B).

### 6c. Two connected tables

If your amounts are in one table and details are in another (e.g. `orders` plus a `customers` lookup), use **New comparison → Upload two connected tables**:

- **Main table:** the one with the amounts.
- **Lookup table:** the one with extra details, linked by a key column.

Records whose link has no match are **kept and reported**, never dropped silently.

### 6d. Connect a database (PostgreSQL, Snowflake, BigQuery)

Use **New comparison → Warehouse table**:

1. Choose the source (**PostgreSQL**, **Snowflake** or **BigQuery**) and enter the connection details:
   - **PostgreSQL:** a connection string.
   - **Snowflake:** account identifier, database and access token.
   - **BigQuery:** project ID, dataset and access token.
2. Click **Test connection & load columns**. MetricGuard lists your tables. When you pick a table, it suggests the ID, amount and date columns. You can change any of them.
3. Optional: fill in **Save as** to keep the connection for next time. Only workspace owners can save connections, and saved credentials are stored encrypted.
4. Optional: set a **Cutoff column** and **Cutoff value** to take a fixed point in time (e.g. all orders up to 31 October).
5. Import. The table becomes a dataset just like an uploaded file.

> **Good practice:** use a **read-only** database user. MetricGuard only reads. The database must be reachable from the internet; private office networks can't be reached.

---

## 7. Run a comparison

Go to **New comparison**. The first question is **What do you want to compare?**

### 7A. Two calculations on the same data

Use this when two teams calculate "the same" number differently.

*Example: Finance counts only paid orders; the dashboard counts all orders. Why are they different?*

1. **Choose data:** pick a dataset.
2. **Define calculations:** you describe two calculations side by side.
   - **Current calculation:** e.g. the dashboard's way.
   - **Proposed calculation:** e.g. Finance's way.

   For each one, choose:
   - **What do you want to compare?** Options: **Total *amount* (SUM)**, **Number of records (COUNT)**, a **Rate** (share of records meeting a condition, e.g. conversion), or **one amount divided by another** (e.g. cost ÷ revenue).
   - **Include only records where…** add conditions, e.g. `status` *is* `paid`, or `order_date` *between* two dates. Choose **All conditions (AND)** or **Any condition (OR)**.

   Prefer typing SQL? Switch from **Guided form** to **SQL editor**. It understands simple totals and counts, such as `SELECT SUM(revenue) FROM orders_october WHERE status = 'paid'`. Click a column chip to insert its name. The form can also show the **SQL this form produces**.
3. Click **Check and compare**.

### 7B. Same calculation on two data loads, or two sources

Use this when the **data** changed, not the formula.

*Example: Yesterday's export said 4,812 orders, today's says 4,790. Which orders changed?*
*Or: Our billing system and our CRM disagree. Which records differ?*

1. Choose **Same calculation on two data loads**.
2. Under **What are the two files?**, choose:
   - **Two loads of the same dataset:** pick the **Earlier load** and the **Later load**.
   - **Two different sources:** pick **Source A** and **Source B**. They need compatible columns: the same ID, amount and filter columns.
3. Describe the calculation once (or **Start from a saved calculation**): what to total or count, and which records must match.
4. Click **Compare the two loads** (or **Compare the two sources**).

---

## 8. Read the result

The result page answers in plain language first, then shows the proof.

### The answer

At the top you'll see the difference, e.g. *"Proposed is 15,000.00 INR lower than current"*, and what explains it.

### Reconciliation: it always adds up

The difference is split into categories, and the categories add up **exactly** to the total difference. When they do, you'll see **Balanced ✓**. Typical categories:

| Category | Meaning |
|---|---|
| **Only in one calculation** / filtered in or out | The record counts in one calculation but not the other (e.g. refunded orders are excluded by Finance). |
| **New in the later load** / **Only in source B** | The record exists only in the newer file, or only in source B. |
| **Removed** / **Only in source A** | The record exists only in the older file, or only in source A. |
| **Value changed** / **Value differs** | Same record, different amount (e.g. a price was corrected). |

### The tabs

- **A · Records:** every record behind the difference. The **largest change comes first** by default.
  - **Search** for an ID or value, or **filter** by category or group.
  - **Show columns** adds more fields to the table.
- **B · Where it sits:** splits the difference by a column (region, status, date…). It opens on the column where the difference is most concentrated.
  - The **Concentration** section names the column or pair of columns that best explains it, e.g. "62% of the difference is in `region = South` with `status = refunded`".
  - If the difference is spread evenly, it says so.
- **C · Definitions:** the exact calculations that were compared.
- **D · Limits:** what this result does and doesn't prove.

> Detailed records are kept for a limited time. If you see **Detailed records expired — rerun to see them**, run the comparison again from the case.

---

## 9. Save your answer as a case

Under the result, in **Save as a case**:

1. **Case name:** e.g. "October revenue: Finance vs Dashboard".
2. **Your conclusion:** one or two sentences in your own words, e.g. *"The ₹15,000 gap is 12 refunded orders that the dashboard still counts."*
3. **Recorded by:** your name.
4. Click **Save case**.

The case is now in **Cases**. Each case keeps the exact data fingerprint and calculations, so anyone can open it and use **Replay & verify** to recompute it and confirm the answer is still exact.

**Managing cases**

- **Cases → Needs a conclusion** lists comparisons you ran but haven't concluded yet. Use **Write conclusion** to finish them.
- Use the row's actions menu to **rename** or **delete** a case or comparison. MetricGuard won't delete something a saved case or metric still depends on.
- **Edit & re-run** on a case opens its calculations again so you can change them and compare again.
- Comparisons you ran but didn't save (drafts) can be hidden or deleted.

**Keeping a copy:** open the case and click **Records (CSV)** to download every record behind the difference. Guest accounts in particular should do this before the 8-hour erase.

---

## 10. Share a case

Open a case and click **Share**. There are two ways:

- **Workspace members:** anyone in your workspace can already open the case.
- **Public read-only link:** anyone with the link can view the case without signing in. They can't change anything.
  - Choose when the link expires: **After 7 days** or **After 30 days**.
  - **Copy the link immediately.** It's shown only once.
  - **Revoke link** turns it off at any time.

---

## 11. Metrics: name the numbers you care about

A **metric** is a business number with a name, like *Net revenue* or *Paid orders*, plus the calculation your team has approved for it.

- **Create one from a result:** click **Save as metric**, choose **New metric…** or an existing one, and pick **Which calculation?**
- **Or go to Metrics → Create metric:** give it a name and an importance (**Critical**, **Important** or **Standard**), then link a calculation to a dataset.
- Each metric shows its **Approved value** and the **Difference from approved** when new data arrives.
- When you add a newer load of a dataset, use **Check your metrics on the new load** to see which metrics moved and why.

---

## 12. Changes

**Changes** lists comparisons over time: new data loads, and edits to metric definitions. You can **compare two versions of a saved metric** to see how a definition change moved the number, checked on frozen data.

Teams using dbt or Power BI can also check those changes. That's an advanced setup; see [CLI and CI usage](cli-usage.md).

---

## 13. Work with your team

### Invite people

1. Go to **Settings → Members**. Only workspace owners can invite.
2. Under **Invite by email**, enter the person's email and choose a **Role**:
   - **Owner:** everything, including inviting people and saving database connections.
   - **Analyst — compare and save:** add data, run comparisons, save cases.
   - **Reader — view only:** open and read cases.
3. **Tell them yourself** (chat, email, Slack). MetricGuard doesn't send emails. Ask them to sign in at **https://metricguard.vercel.app** with Google or GitHub, using **that same email address**. They join your workspace automatically. Until they do, they show as **Waiting to sign in**.
4. To take access away, use **Remove** (members) or **Revoke** (invites not yet used).

### Switch workspaces

If you belong to more than one workspace, use the workspace switcher in the sidebar.

---

## 14. Settings, usage and feedback

- **Settings → Workspace information:** the workspace name and ID.
- **Settings → Usage:** your current limits and how much you've used: rows per dataset, upload size, comparisons in the last 24 hours, and storage used.
- **Settings → Account:** sign out, or permanently **delete your account**. You must type `DELETE` to confirm. Deleting erases your personal workspace and everything in it. Shared workspaces stay, minus you.
- **Settings → Appearance:** light or dark mode.
- **Feedback** (sidebar): tell the team what's confusing or broken. Mention the page you were on.

---

## 15. Free beta limits

| Limit | Value |
|---|---|
| Rows per dataset | 50,000 |
| Size per uploaded file | 25 MB |
| Comparisons running at once | 1 per workspace |
| Comparisons per day | 30 per workspace |
| Guest accounts: total storage | 25 MB |
| Guest accounts: lifetime | Erased 8 hours after sign-up |

**Settings → Usage** shows the exact limits for your workspace.

---

## 16. Troubleshooting

| What you see | What to do |
|---|---|
| **Free beta limit: … rows / MB** | Your file is too big. Filter it to a smaller date range or fewer columns, or split it. |
| **Guest accounts can store up to 25 MB in total** | Delete a dataset you no longer need (Datasets → actions menu), then try again. |
| **Dataset checks failed** | Read the **Recommended fix**. Common causes are duplicate IDs, text such as `₹` or `N/A` in the amount column, or mixed date formats. |
| **No records match** | Your conditions exclude everything. Check spelling and capitals (`Paid` ≠ `paid`) and date ranges. |
| **The two calculations are identical** | Change one of them. There's nothing to compare. |
| **This calculation is outside the supported grammar** | The SQL is too complex. Use the **Guided form**, or keep the SQL to one `SUM(...)` or `COUNT(*)` with an optional `WHERE`. |
| **Choose two different datasets / loads** | You picked the same thing twice. |
| **Detailed records expired** | Rerun the comparison from the case. |
| A comparison stays "waiting" | Only 1 comparison runs at a time per workspace. Wait for the current one to finish. |
| Can't connect to a database | Check the details and that the database accepts connections from the internet. Use **Test connection & load columns** to see the error. |
| An invited person can't see the workspace | They must sign in with **exactly the invited email address**. |
| Sign-in fails | Your Google or GitHub email must be **verified**. Try the other provider, or check the email in that account's settings. |

Still stuck? Use **Feedback** and describe what you clicked and what you saw.

---

## 17. Words you'll see

| Word | Meaning |
|---|---|
| **Dataset** | A file or table you've added, frozen and checked. |
| **Load** | One version of a dataset (e.g. Monday's export and Tuesday's export). |
| **Row ID** | The column that identifies each record uniquely. |
| **Amount / measure** | The number being totalled. |
| **Calculation** | A rule like "total revenue where status is paid". |
| **Current / Proposed** | The two calculations being compared. |
| **Source A / Source B** | Two different systems' data being compared. |
| **Reconciliation** | The breakdown of the difference into categories that add up exactly. |
| **Balanced ✓** | The categories add up to the full difference: nothing is unexplained. |
| **Concentration** | Where most of the difference sits (which column values). |
| **Case** | A saved result with your conclusion. |
| **Replay & verify** | Recompute a saved case to prove it still holds. |
| **Metric** | A named business number with an approved calculation. |
| **Workspace** | A shared space for a team's data and cases. |

---

## 18. Your data and privacy

- Your workspace is private to you and the people you invite.
- Public links show only that one case, and only until they expire or you revoke them.
- Database passwords and tokens you save are stored encrypted, and MetricGuard only reads from your database.
- Deleting your account erases your personal workspace. Guest workspaces are erased automatically after 8 hours.
- See the Privacy and Terms pages on the website for details.
