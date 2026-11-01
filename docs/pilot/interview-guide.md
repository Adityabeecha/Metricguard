# Is the problem real? An interview guide for 5–8 analysts

**Goal:** find out, before building anything else, whether people who live with mismatched numbers would actually use MetricGuard, and for which job. Talk first, demo second, watch third.

## What we are testing

| | Hypothesis | Who would feel it |
|---|---|---|
| H1 | **Proof matters.** When two numbers disagree, people must *show* why (to a manager, auditor or client), and a spreadsheet isn't good enough proof. | Finance analysts at month-end, client reporting, audits |
| H2 | **Averages and ratios are the hard part.** "Why did AOV / conversion / margin move?" is painful by hand because every record shifts both the top and the bottom. | Product and growth analysts |
| H3 | **Repeated checks.** The same comparison is needed every load or every definition change, so a tool beats re-doing it by hand. | Analytics engineers (dbt), BI owners |
| H0 | **It's fine by hand.** A `FULL OUTER JOIN` or VLOOKUP in 10–30 minutes is good enough, and nobody would switch. | Everyone, if the others fail |

The biggest known risk (from the Argon review): **two exports from different systems rarely have the same column names**, and MetricGuard needs them to match. Watch for this in every session.

## Who to talk to (5–8 people)

Look for people who answered *yes* to at least one of these in the last month:
- "Two reports or systems gave different numbers for the same thing, and I had to find out why."
- "I changed a metric's definition (SQL, dbt model, dashboard filter) and had to explain how it moved."
- "I reconcile two exports (e.g. billing vs CRM, Shopify/Stripe vs the ledger) regularly."

Aim for a mix: 2–3 finance/ops analysts, 2–3 product/data analysts, 1–2 analytics engineers. Places to find them: former colleagues and classmates, LinkedIn (search "financial analyst reconciliation", "analytics engineer dbt"), the dbt Community Slack, r/analytics and r/dataengineering, local data meetups. Offer 45 minutes and a thank-you (a coffee voucher is plenty).

Before the call, send: *"Please bring one real case where two numbers didn't match. Anonymise it if you need to — we only need the shape of the data."*

## Part 1 — Their world, no demo (15–20 min)

Ask about **the last real time**, never "would you…". Don't mention MetricGuard yet.

1. Tell me about the last time two numbers that should have matched didn't. What were the two numbers?
2. Who noticed, and who needed the answer? How urgent was it?
3. Walk me through what you did, step by step. *(Listen for: where the data came from, renaming/cleaning, joins, spreadsheets, how long each step took.)*
4. How long did it take in total? How often does this happen — weekly, monthly, at close?
5. How did you show the answer to whoever asked? Did anyone question it?
6. Was it a total, or an average/ratio (AOV, conversion, margin)? *(H2)*
7. Did you have to do the same check again later? *(H3)*
8. What's the most annoying part of the whole thing?
9. Have you tried any tool for this? Why did you stop, or why not?

**Probe, don't pitch:** "Why was that hard?", "What happened next?", "Can you show me the spreadsheet you used?"

## Part 2 — Try it on their own case (20 min)

Share https://metricguard.vercel.app. If you want their workspace to survive the session, give them full access first (**Settings → Beta admin → Give full access**; guests are erased after 8 hours).

Say only: *"Try to get your case into it and find the answer. Think aloud. I'll stay quiet unless you're stuck for two minutes."*

Watch and note the time at each point:
- [ ] Gets their files in (time taken; did they have to **rename columns or clean in Excel first**?)
- [ ] Chooses the right comparison (two calculations / two loads / two sources)
- [ ] Gets a result, and understands **Balanced ✓** and the categories
- [ ] Uses **Where it sits** or the record list to find the cause
- [ ] Reaches an answer they believe
- [ ] Saves or shares the case

Where they hesitate or say "hmm" matters more than anything they say afterwards.

## Part 3 — Wrap-up (5–10 min)

1. Compared to how you did it last time, was this faster, slower or about the same?
2. Would you have trusted this answer enough to send it to your manager or auditor? Why or why not?
3. What would have to be different for you to use this next time it happens?
4. Who else on your team deals with this? *(Ask for an introduction: a real signal if they agree.)*
5. If this cost money, who would pay, and roughly what would feel reasonable? *(Only ask after everything else; note the reaction, not the number.)*

## Reading the signals

| Strong (counts) | Weak (doesn't count) | Negative |
|---|---|---|
| Uses it again on their own within a week | "Looks great", "nice UI" | Gives up getting the data in |
| Sends a case link to a colleague | "I'd use this if…" (hypothetical) | "I'd just do this in SQL/Excel" after trying it |
| Introduces you to a teammate | Asks for many new features | Can't name a recent real case |
| Asks about price or team access | Polite interest in the demo | The answer didn't match what they believed, and they trusted their spreadsheet instead |

## After 5–8 sessions: decide

Count, per hypothesis, how many people showed a **strong** signal.

- **3 or more on one hypothesis** → narrow the product to that job:
  - H1 → "reconcile two exports with proof" for finance: build **column mapping between two different files** first.
  - H2 → "explain why an average or ratio moved": lead with ratios, simplify everything else.
  - H3 → "metric change review on every dbt pull request": invest in the CI check.
- **Fewer than 3 anywhere, and most said "fine by hand"** → H0 wins. Stop building; keep it as a portfolio project.
- **Most got stuck before a result** → the blocker is getting data in. Fix that (column mapping, paste from clipboard) and re-test with 3 new people before judging the idea.

## Notes template (one per session)

```
Person / role / company size:
Last real mismatch (what, how often, how long it took):
Total or ratio? Repeated?
How they proved the answer:
Time to get data in:   Renamed/cleaned first? (y/n)
Reached an answer? (y/n)  Trusted it? (y/n)  Shared it? (y/n)
Strongest quote:
Signal per hypothesis (strong / weak / negative): H1 __  H2 __  H3 __
Follow-up: used again? (check after one week)
```

## Avoid

- Pitching before Part 2. Their words about the past are the data; your demo isn't.
- Counting compliments or "I would" as evidence.
- Helping too early in Part 2. Every rescue hides a real problem.
- Building anything between sessions, except fixing a bug that stops every session.
