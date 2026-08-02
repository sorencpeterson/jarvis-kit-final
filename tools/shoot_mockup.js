#!/usr/bin/env node
/**
 * shoot_mockup.js -- render a LOCAL mockup HTML file to an og:image PNG.
 *
 * Part of the B22 mockup screenshot pipeline (proposal system). Playwright itself lives
 * under [HOME]/Claude/playwright-project, not here, so `require("@playwright/test")`
 * needs help resolving. Node's CommonJS require() walks up node_modules from the SCRIPT
 * FILE's own directory, not the shell's cwd -- so plain `cd playwright-project && node
 * <this file>` does NOT work despite looking like it should (verified by hand; it throws
 * "Cannot find module '@playwright/test'"). NODE_PATH is the one that actually works:
 *
 *   NODE_PATH=[HOME]/Claude/playwright-project/node_modules \
 *     node [APP_ROOT]/tools/shoot_mockup.js <in.html> <out.png>
 *
 * (og_shots.py, the caller, sets this env var itself -- see its shoot_one().)
 *
 * Output: a 1200x630 PNG (the og:image / Twitter-card / iMessage-preview standard size),
 * deviceScaleFactor 1 (no retina 2x -- that would roughly quadruple bytes for a size the
 * preview surface displays small anyway), under 300KB.
 *
 * What this does NOT do: it does not talk to the live server, GHL, or any queue file --
 * it is a pure "local html file in, png out" renderer. og_shots.py is the caller that
 * knows about pids, tokens, and the proposals queue.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const WIDTH = 1200;
const HEIGHT = 630;
const MAX_BYTES = 300 * 1024; // 300KB budget from the brief
const SETTLE_MS = 800; // let webfonts/CSS transitions/entrance-anims settle before the shot

// Injected into <head> as the LAST style tag so it wins the cascade without touching the
// template files themselves. Two jobs:
//   1. hide the sticky "CONCEPT PREVIEW" ribbon (and reclaim the header's top offset that
//      the ribbon's height normally pushes it down by -- see body.ribbon-mini in the
//      mockup*.html packs, same top:0 rule, applied here directly instead of waiting on
//      the packs' own 5s setTimeout so the shot never races that timer).
//   2. force prefers-reduced-motion behavior via a real media-feature override AND collapse
//      transition/animation durations to ~0, so entrance transitions (card lift-ins, hero
//      parallax, etc.) can't be mid-flight when the screenshot fires.
// Raw CSS only -- page.addStyleTag() below already wraps this in its own <style> element,
// so wrapping it again here would make the browser see a literal "<style>" string as
// unparseable rule text (silently ignored, ribbon stays visible). Learned the hard way:
// first version double-wrapped this and the ribbon didn't hide; verified fixed via a real
// screenshot, see status-ogshots.md.
const SHOT_CSS = `
  .ribbon{display:none!important}
  header{top:0!important}
  *,*::before,*::after{
    animation-duration:0.001ms!important;
    animation-delay:0s!important;
    transition-duration:0.001ms!important;
    transition-delay:0s!important;
    scroll-behavior:auto!important;
  }
`;

function die(msg) {
  console.error(`shoot_mockup: ${msg}`);
  process.exit(1);
}

async function main() {
  const [, , inArg, outArg] = process.argv;
  if (!inArg || !outArg) {
    die("usage: node shoot_mockup.js <in.html> <out.png>");
  }

  const inPath = path.resolve(inArg);
  const outPath = path.resolve(outArg);

  if (!fs.existsSync(inPath)) {
    die(`input html not found: ${inPath}`);
  }

  let chromium;
  try {
    ({ chromium } = require("@playwright/test"));
  } catch (e) {
    die(
      "could not require('@playwright/test') -- set " +
        "NODE_PATH=[HOME]/Claude/playwright-project/node_modules " +
        "(cwd/cd alone does not fix this: Node resolves require() relative to this " +
        "script's own path, not the shell's cwd). " +
        `(${e.message})`
    );
  }

  fs.mkdirSync(path.dirname(outPath), { recursive: true });

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: WIDTH, height: HEIGHT },
      deviceScaleFactor: 1,
      reducedMotion: "reduce", // Playwright-level emulation of prefers-reduced-motion:reduce
    });
    const page = await context.newPage();

    // file:// URL so relative asset paths inside the mockup (data-uri favicons, inline
    // SVGs, etc.) resolve exactly as they do in the real .mock.html on disk -- no server
    // needed, matching "renders a LOCAL mockup file" from the brief.
    const fileUrl = "file://" + inPath;
    await page.goto(fileUrl, { waitUntil: "networkidle" });

    // Inject the hide-ribbon / kill-motion override AFTER load so it always wins the
    // cascade (it's appended last), then give webfonts and any late layout/JS (the ribbon
    // pack's own minimize timer, card hover-init, etc.) a fixed window to settle.
    await page.addStyleTag({ content: SHOT_CSS.trim() });
    if (page.evaluateHandle) {
      try {
        await page.evaluate(() => document.fonts && document.fonts.ready);
      } catch (_) {
        /* document.fonts unsupported or already settled -- non-fatal */
      }
    }
    await page.waitForTimeout(SETTLE_MS);

    const pngBuffer = await page.screenshot({ type: "png" });
    fs.writeFileSync(outPath, pngBuffer);

    const bytes = pngBuffer.length;
    console.log(
      `shoot_mockup: wrote ${outPath} (${WIDTH}x${HEIGHT}, ${(bytes / 1024).toFixed(1)}KB)`
    );
    if (bytes > MAX_BYTES) {
      // Not fatal -- og:image still works over budget, but flag it loudly since the
      // brief's target is <300KB (email/iMessage preview fetchers can be stingy).
      console.warn(
        `shoot_mockup: WARNING output is ${(bytes / 1024).toFixed(1)}KB, over the ${(
          MAX_BYTES / 1024
        ).toFixed(0)}KB budget`
      );
    }
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  die(e && e.stack ? e.stack : String(e));
});
