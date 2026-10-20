# Evidence UI: the reconciliation file

**Status:** approved 2026-10-20. Sections 1–9 are built (bridge, ledger, share bars, custody log, hash prints, stamps, copy summary, homepage). Still to do after launch: exact multi-column drivers and review/sign-off.
**Why:** the redesign looks clean but reads like every SaaS dashboard (icon cards, rounded panels, pills). MetricGuard is an accountant's reconciliation and an evidence file, so the UI should look like one. These rules extend DESIGN.md (Chain of Custody); every existing rule still applies: tokens only, seal blue only for the seal/primary action/active marker, ink deltas (no red/green for numbers), mono figures, no side stripes, no eyebrow labels.

---

## 1. Vocabulary (use consistently, sparingly)
- A saved result is a **case** (MG-XXXX). Its sections are **exhibits**: **A Records**, **B Where it sits**, **C Definitions**, **D Limits** (was "What is uncertain"). The tab shows the exhibit letter in kraft mono before the plain name ("A  Records").
- **Balanced**: listed changes + unexplained = the total change (unexplained is 0 or the stated residual).
- **Custody log**: the timeline of what happened to the evidence.
- Stamps say only: **SEALED**, **BALANCED**, **REPLAYED ✓**, **DIFFERS**. Nothing else is stamped.

## 2. The reconciliation bridge (signature visual)
Where: the case and result page, directly under the answer figure, full content width, in the first viewport on desktop.

```
 Current total        Excluded by     Added by      Value         Unexplained    Proposed total
                      proposed        proposed      changed
 ┃██████████┃  ┄┄┄  ┃▒▒▒▒▒▒┃     ┄┄┄                                        ┄┄┄ ┃█████┃
 ┃██████████┃       ┃▒▒▒▒▒▒┃                                    0.00  [BALANCED] ┃█████┃
 750.75 INR          −340.00                                                    410.75 INR
                     3 records
```
- Bars: **Current** and **Proposed** totals are solid `--ink`; each step is a bar from the running level to the next, filled with a 45° hatch of `--text-2` for decreases and solid `--text-2` for increases (the sign and the label carry direction; no red or green). Connectors are 1px dashed `--line-strong`. The final "Unexplained" step is a hairline at the closing level with the value in mono and a **BALANCED** stamp (ok colour). If the residual isn't 0, it shows the residual and no stamp.
- Steps are the **change categories** (only non-zero ones, in this order). Single-table and relational runs: *Excluded by proposed* (records counted now, not under the proposed rule), *Added by proposed*, *Value changed* (counted by both, different contribution). Two-load runs: *Removed from the later load*, *New in the later load*, *Value changed*, *Now matches the filter*, *No longer matches the filter*. COUNT DISTINCT: *No longer counted*, *Newly counted*. Ratios, rates, AVG: steps are the summed record **effects** per category, in the result unit (pts for shares), and the bars run from the current value to the proposed value.
- Each step label: name, change (mono, U+2212 minus), and "N records" in muted mono. Clicking a step opens Exhibit A filtered to that category.
- Scale: a linear axis from the smallest to the largest running level, with 8% padding. When totals dwarf the steps (a step under 2% of the axis), the axis breaks (a zig-zag on the total bars) so the steps stay readable; the totals keep their exact labels.
- Accessibility: an SVG with `role="img"` and an `aria-label` sentence ("Current 750.75 INR, minus 340.00 INR excluded by the proposed calculation, equals proposed 410.75 INR; unexplained 0.00; balanced"), plus a visually hidden table of the same numbers. It works at 360px (the labels stack under the bars; the bars keep their order).
- Data: the API returns exact per-category sums (section 7), and the bridge never re-adds numbers in the browser.

## 3. Exhibit A: the records ledger
Replaces the current "Records that explain the difference" table and card layout.

```
A  Records                                   [ All changes 3 | Excluded by proposed 3 | Added 0 | Value changed 0 ]
────────────────────────────────────────────────────────────────────────────────────────────── (2px ink rule)
 Record      Current → Proposed                         Change        Running total
 R003        Included 200.00 → Excluded                 −200.00          −200.00
 R007        Included 90.00  → Excluded                  −90.00          −290.00
 R004        Included 50.00  → Excluded                  −50.00          −340.00
──────────────────────────────────────────────────────────────────────────────────────────────
 Total of listed changes                                −340.00
                                                        ═══════ (double rule)
 Listed changes −340.00 + unexplained 0.00 = −340.00 ✓ Balanced
 ✓ 5 records unchanged (included by both or neither) · Show them
```
- **No card around it**: it's a ruled ledger on the page ground: a 2px `--ink` top rule, 1px `--line` row rules, no zebra stripes, no rounded corners.
- **Category tabs** use the same categories as the bridge, each with a mono count, as a segmented underline control (not pills). The default is "All changes", largest first.
- **Current → Proposed** is one cell: the current state muted (`--muted`, and struck through when the record leaves the calculation), a thin arrow, then the proposed state in ink. Contributions are in mono.
- **Running total**: the cumulative sum down the visible order (largest first by default), in muted mono, right-aligned. It reaches the total at the end of the list when every changed record is shown.
- **Total row**: the label in Archivo Narrow caps, the figure in mono, and an accountant's **double rule** under the figure (`border-bottom: 3px double var(--ink)`).
- **Balanced line**: one sentence; ✓ and "Balanced" in ok colour when the residual is 0.
- **Unchanged records are hidden behind one line** ("✓ 49,912 records unchanged · Show them"), which expands the full register (today's All-records explorer: search, order, columns, CSV) inside a disclosure.
- Phone (under 760px): each record is a ruled block: key and change on the first line, "current → proposed" on the second, running total on the third. No horizontal scroll.

## 4. Exhibit B: where it sits (breakdown)
```
B  Where it sits      by [ region ▾ ]
 Value     Change          Share of the difference ▮▮▮▮▮▮▮▮▮░   Share of records ▏      Changed records
 north     −47,761,032.86  ████████████████████ 100%            ▏ 20%   Concentrated    4,866 of 19,858
 central   0.00                                                 ▏ 20%                   0 of 20,111
 …
 Other (14 values)  −1,203.40   ▏…
```
- Two measures per row: **share of the difference** (a solid ink bar, 0–100%, using absolute changes when signs are mixed, with the sign shown in the number) and **share of all records** (a thin `--line-strong` tick on the same scale). A row whose difference share is at least twice its record share, and at least 10% of the difference, gets a small **Concentrated** label (kraft text, not a stamp).
- One-line explanation above the rows: "Dark bar: share of the difference. Tick: share of all records. A value that takes far more of the difference than of the records is where the change sits."
- **Top 10 + Other**: when there are more than 12 values, show the 10 largest by absolute change and one exact "Other (N values)" row; "Show all" expands.
- Rows with change 0 are listed after the others, muted.
- Two-load runs: the existing message "Breakdowns aren't available for comparisons between two data loads yet."

## 5. Custody log, hash prints and replay
- **Hash print**: a deterministic 6×6 mirror-symmetric glyph from the first 18 bytes of a SHA-256 (cell on/off from the bits, two tones: `--ink` and `--tint-strong`), 24px, beside the short hash in mono (`761d…90a7`). The same hash always gives the same glyph, so matching evidence *looks* identical. Component: `HashPrint`.
- **Custody log** (in the seal card: "Custody log ›" opens a right-side drawer on desktop and a full-screen sheet on phones): a vertical ruled timeline with one entry per event, each with time (en-GB, mono), actor, what happened and its hash print:
  1. Dataset frozen (name, rows, dataset hash)
  2. Calculations pinned (current and proposed names; definition hashes)
  3. Compared (engine "isolated runner", duration, evidence hash)
  4. Sealed as case MG-XXXX (who, when)
  5. Each replay: who, when, outcome; **REPLAYED ✓** with the replay's evidence hash print next to the original's, or **DIFFERS** with both prints.
- **Replay & verify** (in the drawer and the seal card): it runs the existing replay, then shows three short steps as they complete ("Fetching the frozen dataset", "Running both calculations", "Comparing evidence hashes") and ends with the stamp. With reduced motion: no step animation, just the final state.
- Data: everything already exists (snapshot, versions, run timings, investigation actor/time, replay runs). Add an API field or endpoint only if the replay history for a case isn't already listable.

## 6. Stamps
- A component `Stamp kind="sealed|balanced|replayed|differs"`: Archivo Narrow 700, uppercase, letter-spacing 0.14em, 2px solid border in the stamp colour, 3px radius, padding 2px 8px, `transform: rotate(-2deg)`, and a short ink-down animation once (scale 1.15 → 1, 180 ms), which reduced motion skips.
- Colours: sealed is seal blue; balanced and replayed are ok; differs is ink on danger-tint. **At most one stamp per region** of the screen.

## 7. Backend additions
- **Evidence summary categories**: `GET /api/v1/comparisons/{id}/evidence-summary` adds `categories: [{key, label, records, change}]`, exact Decimal sums over the published evidence (the same verified file the summary reads), plus `unchanged_records`. The sum of `change` over the categories plus the residual equals the total change exactly; a test asserts it for SUM, COUNT, COUNT DISTINCT, AVG, ratio and two-load runs. Category keys: `excluded`, `added`, `value_changed`; two-loads: `removed`, `added`, `value_changed`, `entered_filter`, `left_filter`; distinct: `no_longer_counted`, `newly_counted`.
- **Records page** filter: `category=<key>` on the evidence endpoint, so Exhibit A tabs page server-side.
- **Breakdown** adds per group: `record_share` (records in the group ÷ all records) and `difference_share`, plus an exact `other` group when `top=10` is asked for.

## 8. Copy summary
- "Copy summary" button in the case header actions, with keyboard shortcut **C** (ignored while typing). It copies Markdown that also reads well as plain text:
```
**Demo · Completed orders only** (MG-F3A3)
−340.00 INR: the proposed calculation is lower than the current one.
• 3 records explain it, unexplained 0.00 (balanced).
• Largest: R003 −200.00, R007 −90.00, R004 −50.00.
• Where it sits: status = pending −200.00 (59%).
Evidence sealed 17 Oct 2026 · replay verified 20 Oct 2026
https://<app>/w/<workspace>/investigations/<id>
```
  …then a quiet confirmation ("Summary copied") that disappears after 2 s. A second action, "Copy link", copies only the URL.

## 9. Homepage: remove the generic patterns
- Hero: replace the animated card with an animated **bridge** (section 2) using the demo numbers, which closes with the BALANCED stamp. The hero must show the product's own picture.
- "Security & trust": replace the 2×2 icon tiles with a real **custody log excerpt** (section 5) of the demo case, with hash prints.
- "The problem": keep the two disagreeing tiles, but drop the icon list; use one plain sentence under them.
- "Works with your data": replace the pills with a two-column ruled list (Sources | Calculations).
- Remove remaining icon-title-text card grids and identical rounded panels; use ruled sections, numbered steps and real data.

## 10. Do not
- No new colours beyond DESIGN.md tokens; no red/green numbers; no gradients; no glassmorphism; no AI chat; no confidence scores; no charts other than the bridge and the share bars.
- Don't hide exactness behind visuals: every bar has its exact figure next to it.
