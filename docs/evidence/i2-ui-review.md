# I2 UI verification - 2026-11-01

Verified in Chromium 156.0.8078.4 against Vite on port 5174 and the local API on port 8001. The isolated database is `.test-tmp/i2-ui/app.db`. Ran `.venv\Scripts\python -m scripts.seed_demo` with that data directory, then uploaded the repository's 5,000-row `sample_orders.csv` through the UI and saved a real comparison case. No customer data or deployed service was used.

## 38. Phone results

Bridge steps and ledger/Where it sits rows become labelled cards below 640px. The full Records register now labels its record-ID cell. Existing labels in Ledger needed no source changes.

| Viewport | Page width | Ledger / Where it sits | Full register | Overflowing cells |
| --- | --- | --- | --- | --- |
| 375px | 375px | 343px | 313px | 0 |
| 420px | 420px | 388px | 358px | 0 |

Where it sits also has zero unlabelled cells at both widths. A browser-only fixture with a 200-digit record ID and a 150-digit amount still produced zero page/cell overflow at 375px; the page was reloaded afterwards. The real no-matching-records search state also fits 375px. Guide images contain actual data, not the long-value fixture.

Before/after screenshots are under `output/i2/before/` and `output/i2/after/`: `375-records.png`, `375-where.png`, `375-full-register.png`, and the corresponding three `420-*.png` files. After full-register captures show its first cards in the phone viewport; the before captures include the full table.

## 39. Keyboard and accessibility

- Used Tab, Shift+Tab, Enter and Escape to open the guest Feedback dialog, cycle within it and close it. Repaired focus return to the guest button; its focus outline is visible.
- Started a real replay using only the keyboard. Its checks table has the accessible caption "Saved case and replay checks" and labelled cells. The completion status is polite.
- Used only keys to choose the date column and cycle Week, Month and the default grouping. The select has the accessible name "Date grouping".
- Entered unsupported SQL by keyboard, triggered the actual refusal and returned to the proposed SQL field. It is named, marked invalid, and described by the refusal message. Repaired the missing focus outline using `--link`.
- The actual delayed-request waking note exposes `role="status"` and `aria-live="polite"`; the guest notice also explicitly announces changes politely.

Guest expiry was supplied through a browser response fixture, since local access is not a real guest account. Waking was produced by delaying a real bootstrap response. No feedback was sent. Chromium accessibility semantics were inspected; an NVDA/JAWS auditory test was not performed.

## 40. Dark contrast

Measured computed sRGB colors using the WCAG relative-luminance formula. Existing tokens already pass, so no text colors were changed. Ratios are unchanged before and after.

| Element | Text/background ratio | Underline/background |
| --- | --- | --- |
| Urgent guest notice | 7.05:1 | - |
| Guest permanent-account button | 13.62:1 | - |
| Refused SQL token | 13.62:1 | 6.53:1 |
| SQL keyword | 7.47:1 | - |
| SQL function | 7.47:1 | - |
| Waking notice | 13.62:1 | - |
| Concentrated tag | 7.88:1 | - |

Text exceeds 4.5:1; the refusal underline exceeds 3:1. Raw measurements are in `output/i2/{before,after}/contrast.json`.

## 24. Chromium print

Printed the actual workspace case and its local public share to A4 PDFs with print backgrounds enabled, starting from dark-mode screens. Before PDFs retained dark backgrounds. The final print stylesheet forces light paper and dark ink through existing tokens, keeps table rows together, wraps long values, widens the current/proposed column and avoids a footer-only page.

- `output/pdf/i2-case-before.pdf`
- `output/pdf/i2-case-after.pdf`
- `output/pdf/i2-public-case-before.pdf`
- `output/pdf/i2-public-case-after.pdf`

All four PDFs have three pages. The final case and public PDFs were rendered to PNG and visually inspected: no empty first page, clipped columns, split record rows or dark page backgrounds. Multi-page tables repeat their headings and totals. Final PNG renders are in `output/i2/after/`.

## 35. Guide images

All six images are actual UI captures in light mode at 1280x800. They are linked in the matching guide sections and were decoded successfully on the real `/guide` page.

| Image under `apps/web/public/guide/` | Bytes |
| --- | --- |
| `add-dataset.png` | 89,883 |
| `column-questions.png` | 113,925 |
| `two-calculations.png` | 81,555 |
| `reconciliation.png` | 32,271 |
| `where-it-sits.png` | 38,881 |
| `share-case.png` | 92,400 |

All are below 250,000 bytes. Markdown image tests cover escaped alt text, lazy loading, inline code, external paths and directory traversal. The npm test command now includes the Markdown tests rather than passing their filename as an unused argument to another test.

J1 refreshed the reconciliation and Where it sits images on 2026-11-01 after adding Indian digit grouping for INR. These retain the same actual isolated case, totals, light mode and 1280×800 viewport; the table above gives their current sizes. Original I2 evidence captures remain under `output/i2/`.

## Automated checks and scope

- `npm test --prefix apps/web`: passed.
- `npm run build --prefix apps/web`: passed; Vite reports its large-chunk warning.
- No Python source, API client or calculation behavior was changed in I2, so the Python suite was not rerun.
- Protected report edits are limited to attributes plus the explicitly approved missing data-label/caption additions. Claude's concurrent logic changes were preserved.
- No commit, push, deployment or generated graph refresh was performed.
