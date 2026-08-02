/*
 * sounds.js — tiny WebAudio sound-cue module for Second Brain (Mission Control).
 * Exposes window.SB_SOUND = { enabled(), setEnabled(bool), play(name) }.
 * Names: 'approve', 'chain', 'reply'.
 * - No autoplay: the AudioContext is only created lazily inside play(),
 *   which itself only runs after a user gesture has called it.
 * - Toggle persists in localStorage under 'sb_sound' ('1' = on). Default OFF.
 * - play() is a silent no-op when disabled, or when WebAudio is unavailable,
 *   or if anything goes wrong building/starting the sound graph.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "sb_sound";
  var GAIN_LEVEL = 0.12; // ~ -18dB
  var ctx = null;

  function readEnabled() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  var enabledFlag = readEnabled();

  function enabled() {
    return enabledFlag;
  }

  function setEnabled(val) {
    enabledFlag = !!val;
    try {
      window.localStorage.setItem(STORAGE_KEY, enabledFlag ? "1" : "0");
    } catch (e) {
      /* ignore persistence failures (private mode, quota, etc.) */
    }
    return enabledFlag;
  }

  function getContext() {
    if (ctx) return ctx;
    var Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    try {
      ctx = new Ctor();
    } catch (e) {
      ctx = null;
    }
    return ctx;
  }

  /**
   * Schedule a single tone: sine oscillator -> gain envelope -> destination.
   * startAt / durationSec are relative to the passed AudioContext's current time.
   */
  function tone(ac, freq, startAt, durationSec, peakGain) {
    var osc = ac.createOscillator();
    var gain = ac.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, ac.currentTime + startAt);

    var t0 = ac.currentTime + startAt;
    var t1 = t0 + durationSec;

    // Gentle attack, then decay to (near) silence by the end of the note.
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(peakGain, t0 + Math.min(0.012, durationSec / 4));
    gain.gain.exponentialRampToValueAtTime(0.0001, t1);

    osc.connect(gain);
    gain.connect(ac.destination);

    osc.start(t0);
    osc.stop(t1 + 0.02);
  }

  var PATCHES = {
    // Quick two-note up-chirp 880 -> 1320 Hz, ~60ms total.
    approve: function (ac) {
      tone(ac, 880, 0, 0.03, GAIN_LEVEL);
      tone(ac, 1320, 0.03, 0.03, GAIN_LEVEL);
    },
    // Double tick: two short 660Hz blips, 90ms apart.
    chain: function (ac) {
      tone(ac, 660, 0, 0.03, GAIN_LEVEL);
      tone(ac, 660, 0.09, 0.03, GAIN_LEVEL);
    },
    // Soft single 520Hz ping, 120ms, gentle decay.
    reply: function (ac) {
      tone(ac, 520, 0, 0.12, GAIN_LEVEL);
    },
  };

  function play(name) {
    if (!enabledFlag) return;
    var patch = PATCHES[name];
    if (!patch) return;

    var ac = getContext();
    if (!ac) return;

    try {
      // Some browsers create contexts in a "suspended" state until a user
      // gesture resumes them; play() is only ever called from a gesture-
      // triggered handler, so this resume is safe and not an autoplay.
      if (ac.state === "suspended" && typeof ac.resume === "function") {
        ac.resume();
      }
      patch(ac);
    } catch (e) {
      /* swallow any WebAudio errors — sound is best-effort, never fatal */
    }
  }

  window.SB_SOUND = {
    enabled: enabled,
    setEnabled: setEnabled,
    play: play,
  };
})();
