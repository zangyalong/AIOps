**Comparison target**

- Source visual truth: `/var/folders/6l/2x93jbfx5910gg3hkd__k3fc0000gn/T/codex-clipboard-a4c4a3c5-0f6d-4cc1-81bb-15886df1fd03.png`, `/var/folders/6l/2x93jbfx5910gg3hkd__k3fc0000gn/T/codex-clipboard-afee842a-22ba-4755-8f74-ecbec11986ed.png`, and the user-supplied Pi Agent source at `/Users/leishaohui/Desktop/zyl/pi-main`.
- Implementation: the existing AIOMS `/chat` screen rendered in the Codex in-app browser.
- Implementation screenshot path: unavailable. The pre-polish implementation was captured and inspected in the in-app browser, but the final post-fix reload was denied by the browser's local-page security review.
- Viewport: 1280 × 720 CSS pixels, device scale factor 1.
- Source pixels: 544 × 125 and 594 × 204; source images are focused diff references rather than a full-page frame.
- State: a historical production-like conversation containing read investigations, failed/successful write operations, and a dotenv patch preview.
- Density normalization: none; the focused diff region was judged at its rendered CSS size because the references use different crops and dimensions.

**Full-view comparison evidence**

- The conversation now reads as a continuous execution rail: short narration, concrete tool call, expandable details, then the final answer. It no longer repeats one generic “已调查运维证据” line for every call.
- User messages remain visually distinct while assistant text stays full-width, preserving the existing AIOMS visual system instead of copying Claude's colors.
- Write operations show title, technical tool name, risk, execution state, target, visible diff, and optional parameters/output in one hierarchy.

**Focused region comparison evidence**

- The dotenv preview is rendered as an editor-style hunk with separate deletion/addition rows, old/new line-number gutters, semantic red/green backgrounds, and intraline emphasis. Older persisted previews without numeric hunk metadata remain readable; new previews include actual source line numbers.
- The textarea was filled with a long Chinese sentence and grew from 42 px to 133 px; Shift+Enter inserted a newline and clearing the input returned it to 42 px.
- A historical `investigate` card was expanded: “调用参数” contained `{ "area": "servers" }` and “工具输出” contained the persisted evidence. Historical write failures were correlated with operation audit records and rendered as failed rather than completed.

**Findings and comparison history**

- [P2, fixed] The first implementation expanded the entire write-operation detail panel whenever a diff existed, causing large argument JSON to dominate the timeline. The diff now stays visible while parameters and execution output remain collapsed behind “详情”.
- [P2, fixed] The old preview flattened the hunk, risk, and path into one inline code row. The replacement separates action metadata from the editor diff and preserves semantic line structure.
- [P2, fixed] The old single-line input scrolled horizontally. The replacement wraps naturally, auto-grows to 144 px, supports Shift+Enter, and remains keyboard accessible.
- Post-fix evidence: the production build passes after separating the always-visible diff from the collapsed parameter/output detail. A final rendered recapture is still required because the browser denied the post-fix local-page reload.

**Required fidelity surfaces**

- Fonts and typography: existing AIOMS display/body/data font tokens are preserved; code, args, tool names, and diff content consistently use the data font with readable line height and wrapping.
- Spacing and layout rhythm: compact 44–48 px headers and a one-pixel execution rail replace disconnected generic rows; expanded content has bounded scroll regions.
- Colors and visual tokens: existing teal/neutral AIOMS palette is retained; status colors are limited to success, risk, failure, and diff semantics.
- Image quality and asset fidelity: no new raster or decorative assets were needed; existing product marks are unchanged. The target is a code-native diff, so editor rows are implemented as semantic HTML/CSS rather than image placeholders.
- Copy and content: user-facing labels describe the actual investigation domain and action; technical tool names remain visible for auditability.
- Responsiveness/accessibility: expand controls are real buttons with `aria-expanded` and focus outlines; mobile layouts hide secondary metadata and stack operation actions; textarea has an accessible label.

**Residual test gaps**

- The currently running backend process must reload before newly generated previews return numeric hunk lines; old history correctly uses the backward-compatible semantic hunk header.
- No automated component-test framework exists in the frontend, so the expand/collapse and textarea behavior were verified in the running browser plus production build/type checking.
- The final density adjustment was not recaptured after browser security review denied the local-page reload; user-side visual confirmation remains required.

**Final result**

blocked
