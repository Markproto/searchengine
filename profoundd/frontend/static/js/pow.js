// Profoundd proof-of-work solver.
//
// Receives {challenge, difficulty, target} from server. Iterates a nonce
// through SHA-256 until the digest has `difficulty` leading zero bits.
// Posts the nonce back; on success the server marks the session verified
// and returns the redirect URL.
//
// Difficulty default 18 → ~250 ms median on a modern desktop. Pure JS,
// no dependencies. Uses WebCrypto SubtleCrypto for SHA-256.

(function () {
  "use strict";

  function leadingZeroBits(bytes) {
    var count = 0;
    for (var i = 0; i < bytes.length; i++) {
      if (bytes[i] === 0) { count += 8; continue; }
      // count leading zeros in this byte
      var b = bytes[i];
      while ((b & 0x80) === 0 && b !== 0) { /* shouldn't happen for nonzero, but guard */ break; }
      // Manual count:
      for (var bit = 7; bit >= 0; bit--) {
        if ((bytes[i] >> bit) & 1) {
          break;
        }
        count += 1;
      }
      break;
    }
    return count;
  }

  async function sha256(str) {
    var enc = new TextEncoder().encode(str);
    var buf = await crypto.subtle.digest("SHA-256", enc);
    return new Uint8Array(buf);
  }

  async function solve(challenge, difficulty, onProgress) {
    var nonce = 0;
    var t0 = performance.now();
    var lastTick = t0;
    while (true) {
      var nonceStr = nonce.toString();
      var digest = await sha256(challenge + nonceStr);
      if (leadingZeroBits(digest) >= difficulty) {
        return { nonce: nonceStr, ms: Math.round(performance.now() - t0) };
      }
      nonce += 1;
      // progress tick every 250ms so the UI can update
      var now = performance.now();
      if (now - lastTick > 250) {
        lastTick = now;
        if (onProgress) onProgress(nonce, Math.round(now - t0));
      }
    }
  }

  async function run() {
    var holder = document.getElementById("pow-data");
    if (!holder) {
      console.error("pow.js: no #pow-data element");
      return;
    }
    var challenge = holder.getAttribute("data-challenge");
    var difficulty = parseInt(holder.getAttribute("data-difficulty"), 10);
    var target = holder.getAttribute("data-target");

    var statusEl = document.getElementById("pow-status");
    var spinnerEl = document.getElementById("pow-spinner");
    var progressEl = document.getElementById("pow-progress");

    if (statusEl) statusEl.textContent = "Verifying you are human…";

    var result;
    try {
      result = await solve(challenge, difficulty, function (n, ms) {
        if (progressEl) {
          progressEl.textContent = "(" + ms + " ms, " + n.toLocaleString() + " hashes)";
        }
      });
    } catch (e) {
      if (statusEl) statusEl.textContent = "Verification failed: " + e;
      return;
    }

    if (statusEl) statusEl.textContent = "Solved in " + result.ms + " ms. Submitting…";

    try {
      var resp = await fetch("/api/verify-human", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          challenge: challenge,
          nonce: result.nonce,
          target: target,
          difficulty: difficulty,
          solve_ms: result.ms
        })
      });
      var data = await resp.json();
      if (data.ok && data.redirect) {
        if (statusEl) statusEl.textContent = "Verified. Redirecting…";
        // Brief delay so user sees the success state, then navigate.
        setTimeout(function () { window.location.href = data.redirect; }, 200);
      } else {
        if (statusEl) statusEl.textContent = "Server rejected verification: " + (data.reason || "unknown");
        if (spinnerEl) spinnerEl.style.display = "none";
      }
    } catch (e) {
      if (statusEl) statusEl.textContent = "Network error during verification: " + e;
      if (spinnerEl) spinnerEl.style.display = "none";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
