---
name: MetricGuard
description: Chain of Custody. Every comparison result is sealed evidence with a case number, and the answer comes first.
colors:
  seal: "#2557D6"
  seal-strong: "#1C45B0"
  seal-tint: "#EAF0FC"
  rail-accent: "#AFC4F7"
  kraft: "#8A6A3F"
  kraft-tint: "#F0E6D6"
  link: "#2F4BA0"
  link-strong: "#233B80"
  rail: "#22262A"
  rail-2: "#2F343A"
  rail-text: "#E9EBED"
  rail-muted: "#AEB4BA"
  ground: "#F2F1EC"
  panel: "#FFFFFF"
  panel-2: "#F8F7F3"
  tint: "#ECEAE3"
  tint-strong: "#E2DED3"
  ink: "#1D2023"
  text-2: "#3A3E42"
  muted: "#5B6065"
  faint: "#8A8F94"
  line: "#E1DDD3"
  line-strong: "#CEC9BD"
  ok: "#2E7D4F"
  ok-tint: "#E8F3EC"
  ok-line: "#B9DCC6"
  warn: "#9A5B0B"
  warn-tint: "#FBF3E3"
  warn-line: "#EBCF9C"
  danger: "#A8261D"
  danger-tint: "#FBEEEC"
  danger-line: "#EFC2BD"
typography:
  display:
    fontFamily: "'JetBrains Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace"
    fontSize: "clamp(40px, 5vw, 64px)"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.03em"
    fontFeature: "'tnum' on"
  headline:
    fontFamily: "'Archivo', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  title:
    fontFamily: "'Archivo', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "16px"
    fontWeight: 700
    letterSpacing: "-0.01em"
  body-lead:
    fontFamily: "'Archivo', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.45
  body:
    fontFamily: "'Archivo', system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "'ss01' on"
  label:
    fontFamily: "'Archivo Narrow', 'Archivo', system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "0.12em"
  figure:
    fontFamily: "'JetBrains Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace"
    fontSize: "14px"
    fontWeight: 600
    fontFeature: "'tnum' on"
rounded:
  stamp: "3px"
  base: "6px"
  card: "8px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "22px"
  xl: "28px"
  gutter: "40px"
components:
  button-primary:
    backgroundColor: "{colors.seal}"
    textColor: "{colors.panel}"
    rounded: "{rounded.base}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.seal-strong}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0 14px"
    height: "36px"
  button-secondary-hover:
    backgroundColor: "{colors.panel-2}"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0 12px"
    height: "36px"
  chip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.pill}"
    padding: "6px 10px"
  chip-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.panel}"
  stamp-sealed:
    backgroundColor: "{colors.ok-tint}"
    textColor: "{colors.ok}"
    typography: "{typography.label}"
    rounded: "{rounded.stamp}"
    padding: "3px 7px"
  stamp-draft:
    backgroundColor: "{colors.kraft-tint}"
    textColor: "{colors.kraft}"
    rounded: "{rounded.stamp}"
    padding: "3px 7px"
  stamp-open:
    backgroundColor: "{colors.warn-tint}"
    textColor: "{colors.warn}"
    rounded: "{rounded.stamp}"
    padding: "3px 7px"
  stamp-failed:
    backgroundColor: "{colors.danger-tint}"
    textColor: "{colors.danger}"
    rounded: "{rounded.stamp}"
    padding: "3px 7px"
  seal-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "18px 18px 14px"
  seal-strip:
    backgroundColor: "{colors.seal}"
    textColor: "{colors.panel}"
    padding: "9px 18px 11px"
  seal-strip-intact:
    backgroundColor: "{colors.ok}"
  seal-strip-broken:
    backgroundColor: "{colors.ink}"
  nav-rail:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.rail-muted}"
    width: "232px"
  nav-item-active:
    backgroundColor: "{colors.rail-2}"
    textColor: "{colors.panel}"
    padding: "9px 10px"
  register-row:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "12px 16px"
  tab-count:
    backgroundColor: "{colors.tint}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.pill}"
    padding: "1px 6px"
---

# Design System: MetricGuard

## Overview

**Creative North Star: "Chain of Custody"**

MetricGuard is a case file, not a dashboard. Every comparison result is handled like forensic evidence: it carries a case number (MG-XXXX), it is sealed with the hashes of the frozen inputs, and a replay either keeps the seal intact or breaks it. The screen leads with the answer, a large mono figure and one plain sentence, and puts the record-level evidence directly beneath it in tabbed registers.

The material world is a charcoal locker rail on a warm label-stock ground with white panels. Colour is scarce and committed: seal blue marks the seal, the primary action and where you are; kraft brown marks case numbers and draft tags; ballpoint blue marks links and focus; green, amber and brick are state only. Every figure, hash and case number is set in JetBrains Mono with tabular numerals, so numbers read as recorded facts rather than decoration. Density is that of a working register: 36px controls, 8px row gaps, hairline warm borders.

The system refuses the category default for analytics tools: navy sidebar, blue buttons, small caps labels stacked over paragraphs of explanation over cards. Headings speak for themselves.

**Key Characteristics:**
- The answer owns the first viewport: 64px mono figure, one sentence, then the current and proposed pair.
- The evidence seal is the signature: a perforated tape strip with the case number, turning green when a replay matches.
- Seal blue is rare. If more than three things on a screen are seal blue, something is wrong.
- Every number is mono, tabular, and carries its unit and exact value.
- Registers (tables) become stacked cards on phones; nothing scrolls sideways.

## Colors

A warm neutral paper world with one rare stamp-blue accent, a kraft secondary for custody marks, a ballpoint link blue, and three state colours that never decorate.

### Primary
- **Seal Blue** (seal): the evidence seal strip, the primary action button, and the active marker (current nav item, current step, active tab underline, selected dataset row, text caret). Never used for state, for negative numbers, or for errors.
- **Pressed Seal** (seal-strong): hover and pressed state of the primary button only.
- **Seal Wash** (seal-tint): reserved pale wash of the seal; not currently a component fill.
- **Rail Sky** (rail-accent): the icon of the active nav item on the charcoal rail, where full seal blue would sink into the charcoal.

### Secondary
- **Kraft** (kraft): case numbers (MG-XXXX) in lists and headers, the dataset "added" date, and the Draft stamp. The custody label colour.
- **Kraft Tag** (kraft-tint): background of the Draft stamp.

### Tertiary
- **Ballpoint Blue** (link): links, link hover on register names, the focus ring, input focus border, and the Running stamp text.
- **Pressed Ballpoint** (link-strong): link hover depth.

### Neutral
- **Locker Charcoal** (rail): the left navigation rail and the avatar.
- **Locker Shelf** (rail-2): the active nav item background and the rail footer divider.
- **Rail Text / Rail Muted** (rail-text, rail-muted): text on the rail; muted for inactive items, counts and engine status.
- **Label Stock** (ground): the page ground and top bar.
- **White Panel** (panel): cards, register rows, inputs, the seal card.
- **Off Panel** (panel-2): hover fill for register rows and secondary buttons.
- **Tint / Tint Strong** (tint, tint-strong): tab counts, the Running stamp, quiet fills.
- **Ink** (ink): headings, figures, active labels, the active chip fill, and the broken-seal strip.
- **Text 2 / Muted / Faint** (text-2, muted, faint): secondary text, metadata and column labels, disabled-adjacent hints.
- **Hairline / Hairline Strong** (line, line-strong): panel borders and table rules; control borders and the dashed seal-status divider.

### State (never decorative)
- **Verified Green** (ok, ok-tint, ok-line): Sealed stamp, intact seal strip, done step numbers.
- **Amber** (warn, warn-tint, warn-line): Open stamp, refusals, warnings.
- **Brick** (danger, danger-tint, danger-line): Failed stamp, broken-seal status text, error alerts. Distinct from seal blue by hue and use.

### Named Rules
**The Seal Is Rare Rule.** Seal blue is reserved for the seal, the primary action and the active marker. It never signals state, never colours a number, and never fills a decorative surface.

**The Ink Deltas Rule.** Signed deltas are ink, JetBrains Mono, tabular and right-aligned, with U+2212 (−) for negatives. Direction is carried by the sign, never by red or green.

**The State Only Rule.** Green, amber and brick appear only on stamps, seal status, alerts and refusals. A positive number is not "good" and is never green.

## Typography

**Display Font:** JetBrains Mono (with ui-monospace, Cascadia Mono, Consolas)
**Body Font:** Archivo (with system-ui, Segoe UI)
**Label Font:** Archivo Narrow (with Archivo)

**Character:** A sturdy grotesque for prose and controls, a narrow tracked-caps face for column and field labels, and a mono for every recorded fact. The scale contrast between a 64px mono figure and 11px narrow caps is the type specimen of the system.

### Hierarchy
- **Display** (JetBrains Mono 700, clamp(40px, 5vw, 64px), line-height 1, -0.03em): the answer figure only; its unit sits beside it in 18px mono, muted.
- **Headline** (Archivo 700, 28px, 1.15, -0.02em): page and case titles.
- **Title** (Archivo 700, 16px): section headings (Needs a conclusion, Conclusion, Records that explain the difference).
- **Body Lead** (Archivo 400, 18px, 1.45, max 64ch): the answer sentence.
- **Body** (Archivo 400, 14px, max 72ch): page descriptions, conclusions (15px, 1.5), table cells.
- **Label** (Archivo Narrow 600, 11px, 0.12em, uppercase): register column headers and the inline labels of stacked record cards. Stamps use the same face at 700, 10.5px; the seal strip at 700, 12px, 0.14em.
- **Figure** (JetBrains Mono 500 to 700, 11 to 15px, tabular): case numbers, hashes, counts, current/proposed values, deltas.

### Named Rules
**The Mono Facts Rule.** Every figure, hash, timestamp in the seal and case number is JetBrains Mono with tabular numerals. Prose never is.

**The Heading Speaks Rule.** No eyebrow or kicker labels above headings. A heading stands alone; context goes in the one line of muted metadata beneath it (case number, kind, author, time, unit).

## Layout

A fixed left rail (232px, charcoal) and a content column on the label-stock ground with a 52px top bar carrying breadcrumbs and account actions. Content padding is 28px 40px 64px, capped at 1480px.

The result screen is a two-column case header: the answer column (figure, sentence, current/proposed pair, conclusion) and a 320px seal card on the right, 24px apart. Below 1100px the seal card drops under the answer. Tabs (Records, Breakdown, Definitions, What is uncertain) sit beneath with the register.

Lists are stacks of white rows with 8px gaps (attention rows, dataset rows, side-question rows), each 10 to 16px padded. Section headings sit 10px above their list with a mono count on the right. The guided flow uses a compact step rail: numbered mono circles in a horizontal underline row.

Responsive behaviour, observed at 375px:
- Below 900px the rail becomes an off-canvas drawer that casts its shadow only while open.
- Below 760px content padding drops to 18px 16px 48px, breadcrumbs hide, page-heading actions stretch full width.
- **Registers stack into cards below 760px.** Each table row becomes a bordered white card; the header row is visually hidden and each cell shows its column name inline in tracked caps. The Cases register uses a grid card: case number and stamp, then the name, then the change at 18px, then dataset and updated time.
- There is no horizontal page scroll at 375px.

**The Answer First Rule.** On a result, the figure and its sentence are the first content in the viewport. Evidence, definitions and actions come after.

## Elevation & Depth

Flat, paper-on-paper. Depth comes from the white panel against the warm ground and from hairline warm borders, not from shadow. Two quiet shadows exist, plus a drawer shadow.

### Shadow Vocabulary
- **Rest** (`box-shadow: 0 1px 2px rgba(29, 32, 35, 0.06)`): the seal card and any new resting panel.
- **Lift** (`box-shadow: 0 6px 18px -6px rgba(29, 32, 35, 0.18)`): reserved for raised overlays.
- **Primary press** (`box-shadow: 0 1px 2px rgba(28, 69, 176, 0.25)`): the seal-blue button only.
- **Drawer** (`box-shadow: 10px 0 30px rgba(29, 32, 35, 0.3)`): the mobile rail, only while open.
- **Selected ring** (`box-shadow: 0 0 0 1px var(--seal)`): a selected dataset row doubles its seal border.

### Named Rules
**The Flat Paper Rule.** Surfaces are flat at rest. Hierarchy comes from white on label stock and hairlines; never add a shadow to make a card "pop".

## Shapes

Gently squared. Controls, register rows and list rows share one 6px radius; the seal card and panels use 8px; stamps are nearly square at 3px with a 1px currentColor border, like an ink stamp; chips and counts are full pills. Borders are 1px warm hairlines. The one recurring silhouette is the seal strip's perforated bottom edge: a row of 2px punched holes every 10px. The seal-status divider is dashed, like a tear line.

**The No Side Stripe Rule.** No coloured side stripes on cards, rows, alerts or callouts. Emphasis comes from a stamp, a figure or a full border, never from a coloured left edge. The rail's 3px inset seal mark on the current nav item is a position marker, not a decorative stripe, and is the only edge mark in the system.

## Components

### Buttons
Plain and decisive; one seal-blue button per area.
- **Shape:** gently squared (6px), 36px tall (30px small), 0 14px padding, Archivo 600 13px.
- **Primary:** seal blue fill, white text, faint blue press shadow. Used for the area's one primary action (Save conclusion, Replay saved inputs, Run).
- **Hover / Focus:** primary deepens to seal-strong; focus shows a 2px ballpoint-blue outline at 2px offset. Transitions 0.15s on the expo-out ease.
- **Secondary:** white fill, ink text, strong hairline border; hover to off-panel with a faint border. Disabled at 0.55 opacity.

### Chips
- **Style:** white pill, strong hairline, Archivo 600 12px, text-2 colour, with a mono count at 0.8 opacity.
- **State:** pressed chips fill ink with white text. Filters only, never actions.

### Stamps
Status as an inked custody stamp: Archivo Narrow 700 10.5px, 0.12em tracked caps, 3px radius, 1px currentColor border on a tint. Sealed (green), Draft (kraft), Running (ballpoint on tint), Failed (brick), Open (amber).

### Cards / Containers
- **Corner Style:** 8px for panels and the seal card; 6px for list rows.
- **Background:** white on the label-stock ground.
- **Shadow Strategy:** rest shadow at most (see Elevation & Depth).
- **Border:** 1px hairline.
- **Internal Padding:** 10 to 18px; panel headers 16px 20px.

### Inputs / Fields
- **Style:** white, 1px strong hairline, 6px radius, 36px tall, 0 12px padding, 13px text, seal-blue caret.
- **Focus:** border turns ballpoint blue with a 3px blue halo at 12% opacity.
- **Disabled:** 0.5 to 0.55 opacity with a not-allowed cursor; reasons appear as a 12px line beneath.

### Navigation
- **Rail:** 232px charcoal, the MetricGuard logo (a seal-blue guard shield carrying a rising metric line over a perforated seal edge, `Logo.tsx`, also `public/favicon.svg`) beside the wordmark. Items are Archivo 500 14px in rail-muted with mono counts at the right.
- **Hover:** 6% white wash, white text.
- **Active:** locker-shelf fill, white text, rail-blush icon, and a 3px inset seal mark on the left edge.
- **Mobile:** off-canvas drawer below 900px.

### Tabs and Step Rail
- **Tabs:** Archivo 600 14px, muted; active is ink with a 2px seal underline; counts are mono pills on tint. The row scrolls horizontally with snap, **hides its scrollbar**, and below 760px shows a 28px edge fade to the ground colour so cut-off tabs read as more.
- **Step rail:** the same underline grammar; step numbers are 20px mono circles, ink-filled when current, green-tinted when done.

### Registers
Evidence tables. Column headers are 11px tracked Archivo Narrow caps in muted; figure columns are mono, tabular and right-aligned; rows hover to off-panel. Below 760px they stack into cards (see Layout), with the change figure at 16 to 18px.

### The Evidence Seal (signature)
A white 8px card with a seal-blue tape strip across its top: "SEALED" in tracked narrow caps on the left, the MG case number in mono on the right, and a perforated bottom edge. Beneath, a definition list of muted labels and mono values (dataset and hash, evidence hash, sealed time, engine). A dashed tear line separates the status sentence. While replaying, a green fill sweeps left to right across the strip (1.2s, expo-out; instant under reduced motion). When the replay matches the strip turns verified green and the status reads intact; when it does not, the strip turns ink and the status turns brick.

## Do's and Don'ts

### Do:
- **Do** reserve seal blue (#2557D6) for the seal, the primary action and the active marker.
- **Do** set signed deltas in ink, JetBrains Mono, tabular and right-aligned, with U+2212 (−) for negatives.
- **Do** put the case number (MG-XXXX, kraft mono) in the metadata line under a case title and on every case row.
- **Do** stack registers into labelled cards below 760px so no key or figure column is clipped.
- **Do** hide the scrollbar on tab rows and add the edge fade where they overflow.
- **Do** use stamps for state: Sealed, Draft, Running, Failed, Open.
- **Do** reference the :root custom properties; a new colour is a new token, not a raw hex.

### Don't:
- **Don't** use seal blue for state, errors, warnings or negative numbers.
- **Don't** colour deltas red or green.
- **Don't** add coloured side stripes to cards, rows, alerts or callouts.
- **Don't** put eyebrow or kicker labels above headings.
- **Don't** add shadows to make panels pop; the system is flat paper.
- **Don't** set figures, hashes or case numbers in the UI face.
- **Don't** let a table force horizontal page scroll on a phone.

## Themes

Light, Dark and System (the default, which follows the operating system). The choice is a per-browser convenience saved in localStorage under `mg-theme`, set from a compact icon switch in the top bar (hidden below 760px) and from Settings › Appearance. `applyTheme` runs before the first render and sets `data-theme` and `color-scheme` on `<html>`, so the page never flashes; while System is chosen the page follows OS changes live.

Dark keeps the same world: a near-black ground (#131619), graphite panels (#1B1F23, #22272C), warm-neutral ink (#E7E9EB), a darker charcoal rail (#0E1012), seal blue #3466E0 (white text stays above 4.5:1), kraft #C9A574, link #8FADF5, and lifted state colours (ok #5DBE88, warn #E0A64C, danger #EE857B) on dark tints. Every component colour comes from tokens, so no component carries its own light or dark value; inverted chips and step numbers use `--panel` for their text so they flip correctly.

**The Token Only Rule.** Components never use raw hex. White surfaces are `var(--panel)`, text on the seal or primary button is `var(--on-accent)`, and the selection highlight is `var(--selection)`. A raw colour in a component breaks dark mode.
