/**
 * Entry point of the runtime bundle injected into every dashboard frame (dist/bundle/frame.js).
 * It installs the default styles, defines the <tf-*> elements and connects to the host.
 * Custom dashboard code can then use `await window.tabfreeReady` or `window.tabfree`.
 */

import { defineElements } from "./components.js";
import { client } from "./sdk.js";
import { STYLES } from "./styles.js";

const style = document.createElement("style");
style.textContent = STYLES;
document.head.prepend(style);

window.tabfreeReady = client();
window.tabfreeReady.then(
  (c) => {
    window.tabfree = c;
    document.documentElement.dataset.theme = c.theme.mode;
  },
  () => undefined, // components show the connection error themselves
);
defineElements();
