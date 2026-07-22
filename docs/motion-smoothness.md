# Reach motion — toward buttery-smooth UI

Research notes (2026-07-21). Complements `widgets/transitions.py`.

## Why it still isn’t “butter”

| Factor | Effect |
|--------|--------|
| **Gtk.Stack CROSSFADE** | Both children stay mapped and are painted every frame for the whole duration. Heavy pages (maps, plugin desks, long lists) double the paint cost. |
| **Duration vs weight** | Long fades (200 ms+) make double-paint *noticeable*; very long feels laggy; **too short** feels harsh. Sweet spot for app shells is usually **~100–140 ms**. |
| **Work during transition** | Sync reloads / network / list rebuild on navigate fight the compositor (we defer those after `PAGE_MS`). |
| **CSS + stack at once** | Rail width + button color + stack fade stacking eases can feel mushy if each is 160–180 ms. |
| **Scroll custom tick** | Soft wheel ease is intentional; over-easing feels sluggish if `_EASE` is low. |
| **No custom stack easing** | Built-in Stack transitions have **fixed** GTK timing curves — we only control duration/type. |

GNOME discussions (GtkStack “not smooth” with many widgets) agree: **reduce simultaneous paint**, not “add more animation.”

## What “buttery” usually means

1. **60 fps** frame clock with no multi-frame stalls  
2. **Short, decisive** transitions (not slow dissolves)  
3. **Single primary motion** per gesture (don’t rail-expand + full-page crossfade + list rebuild together)  
4. **Content ready** before the transition ends (or clearly after, never during)

## Techniques (ordered by ROI for Reach)

### A. Keep transitions short + single-purpose (done / iterate)

- Page ~150 ms → try **120 ms** if still soft  
- Panel/subpage ≤ **110–130 ms**  
- CSS hover **~90 ms ease-out** only on color (not layout)

### B. Prefer slide for drill-in, crossfade for peer pages

- **Slide** (settings/tools subpages): only one edge reveals; often cheaper than full opacity blend  
- **Crossfade** (sidebar peers): keep, but short  
- Avoid animating **width** of main content while crossfading

### C. Don’t paint two heavy trees

Harder in GTK without caching:

- Leave pages in the stack (already) so first switch isn’t cold construct  
- **Never** rebuild widget trees on every nav click  
- Defer `reload()` until after transition (done for china/tools/marketplace)  
- Future: `Gtk.Snapshot` / texture cache of inactive page (high effort)

### D. Frame-clock-aligned work

- All post-nav work via `GLib.idle_add` / `timeout_add(PAGE_MS+16)`  
- Avoid sync disk/network on the UI thread during gestures

### E. Honor reduced motion

- `gtk-enable-animations=false` → duration 0 (done via `effective_duration_ms`)

### F. Optional “instant nav” for known-heavy pages

- Plugin C2 desks / maps: duration 0 or 80 ms on first show, then normal  
- Trade polish for responsiveness when the child is inherently heavy

### G. Not recommended

- Lengthening fades “to feel smoother” (worsens double-paint)  
- Animating many CSS properties (margin, shadow, filter) on large regions  
- Custom Python-driven opacity of entire pages every tick (worse than Stack)

## Open experiments

1. Page stack at **120 ms** crossfade vs **130 ms** slide (A/B feel)  
2. Skip transition when `page_id` is a plugin (`plugin:…`)  
3. Profile with Sysprof while switching Home ↔ Paths ↔ plugin  
4. Adw.NavigationView migration (longer project; better platform patterns)

## Success criteria

- Sidebar click: no visible stutter; transition finishes before content “pops”  
- Settings drill-in: slide settles in one beat  
- No multi-second freezes (those are logic bugs, not motion)
