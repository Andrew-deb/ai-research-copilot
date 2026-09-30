/*
 * shortcuts.js — one place that knows what every key does.
 *
 * Bindings used to be hardcoded across nine files, each one binding its own
 * document listener and none of them able to see the others. That is how Ctrl+K
 * ended up claimed twice: main.js bound it for chat-history search and won by
 * registering first, so the command palette was unreachable by the shortcut
 * people arrive already expecting. Both files still carry a comment explaining
 * the collision from their own end, which is the clearest possible sign that no
 * single place knew the answer.
 *
 * So: the registry below is the answer, and it is used three ways.
 *
 *   1. It DISPATCHES global chords. One listener, first match wins, and a
 *      duplicate is a test failure rather than a race between script tags.
 *   2. It GENERATES the reference sheet, so the documentation cannot drift from
 *      the behaviour — there is nothing to keep in step.
 *   3. It is CHECKED for collisions by the test suite, including between a
 *      local binding and a global one, since a global fires everywhere.
 *
 * Not rebindable, deliberately. Rebinding needs persistence, conflict
 * resolution and a migration for saved values; the registry is the half that
 * pays for itself immediately, and rebinding becomes small once it exists.
 *
 * Entries whose scope is not "global" are declared but NOT dispatched here.
 * They are contextual — Escape closes whatever is open, Enter sends the message
 * you are typing — and moving them to a global listener would mean each one
 * re-deriving the context it already sits inside. They are listed so the sheet
 * is complete and so the collision check can see them.
 */
(function () {
  "use strict";

  const APPLE = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");

  function entry(id, keys, scope, group, description) {
    return { id: id, keys: keys, scope: scope, group: group, description: description };
  }

  /* "Mod" is Cmd on Apple hardware and Ctrl everywhere else. Written once here
     rather than as `metaKey || ctrlKey` at nine call sites. */
  const REGISTRY = [
    entry("palette.open", "Mod+K", "global", "Navigate", "Open the command palette"),
    entry("chat.search", "Mod+Shift+F", "global", "Navigate", "Search your chat history"),
    entry("help.shortcuts", "Mod+/", "global", "Navigate", "Show keyboard shortcuts"),

    entry("chat.send", "Enter", "composer", "Writing", "Send the message"),
    entry("chat.newline", "Shift+Enter", "composer", "Writing", "Start a new line instead of sending"),
    entry("chat.saveEdit", "Mod+Enter", "composer", "Writing", "Save an edited message"),

    entry("note.bold", "Mod+B", "editor", "Notes", "Bold the selection"),
    entry("note.italic", "Mod+I", "editor", "Notes", "Italicise the selection"),
    entry("note.rename", "Enter", "heading", "Notes", "Rename from the title, then Enter to confirm"),

    entry("list.down", "ArrowDown", "palette", "Lists", "Move down the results"),
    entry("list.up", "ArrowUp", "palette", "Lists", "Move up the results"),
    entry("list.choose", "Enter", "palette", "Lists", "Open the highlighted result"),

    entry("rail.narrow", "ArrowLeft", "rail", "Panels", "Narrow the panel"),
    entry("rail.widen", "ArrowRight", "rail", "Panels", "Widen the panel"),
    entry("rail.min", "Home", "rail", "Panels", "Snap the panel to its narrowest"),
    entry("rail.max", "End", "rail", "Panels", "Snap the panel to its widest"),

    entry("ui.dismiss", "Escape", "overlay", "Everywhere", "Close the panel, dialog or menu in front of you")
  ];

  /* ------------------------------------------------------------ matching -- */

  function parse(keys) {
    const parts = keys.split("+");
    const key = parts[parts.length - 1];
    return {
      mod: parts.indexOf("Mod") !== -1,
      shift: parts.indexOf("Shift") !== -1,
      alt: parts.indexOf("Alt") !== -1,
      key: key.toLowerCase()
    };
  }

  function matches(event, keys) {
    const want = parse(keys);
    const mod = event.metaKey || event.ctrlKey;

    // Every modifier is checked, including the ones the chord does NOT want.
    // Without that, Mod+Shift+F would also fire Mod+F — which is how two
    // shortcuts answer one keystroke.
    return mod === want.mod
      && event.shiftKey === want.shift
      && event.altKey === want.alt
      && (event.key || "").toLowerCase() === want.key;
  }

  function format(keys) {
    return keys
      .replace("Mod", APPLE ? "⌘" : "Ctrl")
      .replace("Shift", APPLE ? "⇧" : "Shift")
      .replace("ArrowDown", "↓").replace("ArrowUp", "↑")
      .replace("ArrowLeft", "←").replace("ArrowRight", "→")
      .replace("Enter", "↵")
      .split("+");
  }

  /* ----------------------------------------------------------- dispatch -- */

  const handlers = Object.create(null);

  function register(id, handler) {
    const known = REGISTRY.some(function (e) { return e.id === id; });
    if (!known) {
      // Loud, because a typo here produces a shortcut that silently never
      // fires and a sheet that never mentions it.
      console.warn("shortcuts: no registry entry for " + id);
      return;
    }
    handlers[id] = handler;
  }

  function isTyping(target) {
    if (!target) { return false; }
    const tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || target.isContentEditable;
  }

  document.addEventListener("keydown", function (event) {
    for (let i = 0; i < REGISTRY.length; i += 1) {
      const item = REGISTRY[i];
      if (item.scope !== "global" || !handlers[item.id]) { continue; }
      if (!matches(event, item.keys)) { continue; }

      // A chord with no modifier must not fire while somebody is typing it into
      // a field. Every global chord currently has one, so this is a guard for
      // the next entry rather than for any of these.
      if (item.keys.indexOf("Mod") === -1 && isTyping(event.target)) { return; }

      event.preventDefault();
      handlers[item.id](event);
      return;                     // first match wins, and there is only ever one
    }
  });

  window.RCShortcuts = {
    REGISTRY: REGISTRY,
    register: register,
    matches: matches,
    format: format
  };
})();
