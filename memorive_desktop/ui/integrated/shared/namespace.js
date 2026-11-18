/* The page's namespace. Every script publishes what other scripts or Python use as a member of window.Memorive
   (Memorive.app, Memorive.service, Memorive.inboxBridge, Memorive.frames.<component>, ...); ui/README.md lists
   the members Python uses. This script runs first, so every later script can add to it. Memorive.frames is
   here from the start because shared/app.js looks a route's frame up before shared/window-frame.js fills it. */
window.Memorive = { frames: {} };
