# UI Plan

Researched 2026-08-08 against Microsoft's published Fluent 2 and Windows 11
guidance, then measured against the current implementation. Every item below is
a specific defect with a number attached, not a preference.

---

## Findings

### 1. The type scale is below Microsoft's stated legibility floor

Windows 11 sets hard minimums: **14px Regular, 12px Semibold**. Below those,
"text smaller than these sizes and weights are illegible in some languages."

| Token | Current | Windows 11 ramp | Verdict |
|---|---:|---|---|
| `CAPTION` | 11px | Caption 12/16 | **below floor** |
| `BODY` | 13px | Body 14/20 | **below floor** |
| `SUBTITLE` | 14px | Body Strong 14/20 | matches, misnamed |
| `TITLE` | 17px | Subtitle 20/28 | too small |
| `DISPLAY` | 22px | Title 28/36 | too small |

Everything is roughly one step small. That reads as "dense" on a 27-inch
monitor and as "unreadable" on a laptop.

### 2. `text_subtle` fails WCAG AA for normal text, in both themes

Measured against the composited background:

| Theme | Colour | Ratio | Required |
|---|---|---:|---|
| Dark | `#7A737E` | **3.85:1** | 4.5:1 |
| Light | `#8F878F` | **3.29:1** | 4.5:1 |

It is currently used for column headers, the section label, and the status bar —
all normal-size text, all failing. This is the measurable cause of the "light
theme looks washed out" note from the last review.

### 3. No line heights are defined at all

Windows specifies typography as size/line-height pairs (12/16, 14/20, 20/28,
28/36) because vertical rhythm comes from baseline alignment. The current tokens
define sizes only, so every vertical measurement is ad hoc.

### 4. `PLACES` violates the casing rule

Windows 11: **sentence case for all UI text, including titles.** The sidebar
header is uppercase with letter-spacing, which is a web convention, not a
Windows one.

### 5. The spacing scale is missing Fluent's icon-alignment steps

Fluent's ramp includes 2, 6 and 10 specifically because they "account for extra
padding in the Fluent icons and help align icons to the four pixel grid," plus
20 and 28. The current scale jumps 16 → 24, which is why icon rows needed manual
nudging.

### 6. Bold is not part of the Windows ramp

Confirmed correct in the current code: emphasis uses Semibold (600). Italic is
deliberately excluded from the ramp because it reduces readability, "particularly
for people with dyslexia" — worth keeping out.

---

## Plan

Ordered by impact per unit of risk.

| # | Change | Why |
|---|---|---|
| 1 | Adopt the Windows 11 type ramp with line heights | Fixes the legibility floor and gives real vertical rhythm |
| 2 | Raise `text_subtle` to pass AA | Fixes measured contrast failures in both themes |
| 3 | Extend spacing to the full Fluent ramp | Removes manual icon nudging |
| 4 | Sentence-case the section label | Matches the platform |
| 5 | Re-space rows and chrome against the new ramp | Density follows type, not the reverse |

Deliberately **not** doing:

- **A custom typeface.** Segoe UI Variable is the system font and its optical
  size axis is what keeps small text legible. Substituting it would look
  imported rather than native.
- **Italic anywhere.** Excluded from the ramp for accessibility reasons.
- **More accent colours.** One accent plus a neutral ramp is the whole point of
  the restraint; a second hue competes with the file-type icon tints that
  already carry meaning.

---

## Sources

- [Typography in Windows — Microsoft Learn](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography)
- [Layout — Fluent 2 Design System](https://fluent2.microsoft.design/layout)
- [Typography — Fluent 2 Design System](https://fluent2.microsoft.design/typography)
- [Design guidelines — Microsoft Learn](https://learn.microsoft.com/en-us/windows/apps/design/guidelines-overview)
- [Breadcrumbs UI design — Setproduct](https://www.setproduct.com/blog/breadcrumbs-ui-design)
