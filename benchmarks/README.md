# NexusAndroid bench (real phone)

`Banc` drives the connected phone through the engine itself (no MCP client, no model) and compares each tuning value
of `native/Tuning.cs` with and without. Every result is judged on the phone, not on what the tool reports: a ✓ for a
tap that did nothing and a ~ for a tap that did act are both counted.

Harmless apps only: Clock tabs and its overflow menu, scrolling Settings, `look`, and read-only tools (messages,
notifications, apps info, checkup, calendar). The phone is left on its home screen. The engine's own user-presence
check still applies: a phone in the user's hands stops the run.

```powershell
cd benchmarks/Banc
dotnet run -c Release -- 8                # 8 repetitions, every section
dotnet run -c Release -- 10 scroll        # one section: settle, scroll, look or read
```

Results land in `benchmarks/results/<date>-fold.md` (table) and `.json`.

Offline, no phone: `dotnet run -c Release -- sizes <screenshot.png>` prints the size, time and legibility (words Windows OCR still reads, against the original) of each image encoding.

2026-11-06, two real Fold screens (an SMS with a photo, the wallpaper home screen), legibility by OCR:

| Encoding | Size | Words read |
| --- | --- | --- |
| WebP lossless | 883-983 KB (over the 500 KB cap) | 100 % |
| WebP, 3 low bits dropped | 488-489 KB | 100 % |
| WebP, 4 low bits dropped | 342-387 KB | 86-100 % |
| JPEG quality 82 | 183-219 KB | 100 % |

So the chain is lossless WebP, then WebP with 2 then 3 bits dropped, then JPEG from quality 82 (no lossy WebP encoder on Windows).

## Scenarios

| Section | What is measured |
| --- | --- |
| settle | Tap a Clock tab: reported ✓, tab really active 1.2 s later, false ✓, missed effects, median time; then open and close the overflow menu |
| scroll | One notch down and up in Settings: distance moved (median title shift), spread, return to start, time |
| look | Text only and with image: time, image size, text size. The image cap is not a bench knob: it follows the shared rule (`NexusShared/images.md`: lossless up to 500 KB), which protects how well the model reads small text, something this bench does not measure. 0.23.18 lowered it to 200 KB (lossy JPEG) by mistake; 0.23.19 restored it. |
| read | Median time of the read-only tools |

## Choices it settled (2026-11-06, Galaxy Fold, Android 17, Wi-Fi)

| Setting | Before | Now | Why |
| --- | --- | --- | --- |
| Settle after a gesture | two equal reads | first change | 632 ms vs 1,056 ms per tap, 8/8 taps and 4/4 menus really done and reported ✓ in both |
| Scroll shape | 4 moves, 120 ms hold | 2 moves, 60 ms hold | 633 px for 657 px of travel in 10/10 notches in both, 802 ms vs 929 ms; `input swipe` flung on to ~1,177 px |
| `look` lossless format | PNG (378 KB) | WebP (172 KB) | the shared rule's format; same pixels, so same legibility; +95 ms to encode; busy screens stay lossless longer under the 500 KB cap |
