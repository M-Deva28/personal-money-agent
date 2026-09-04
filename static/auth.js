/* Shared interactivity for the sign-in / create-account pages:
   password visibility toggles, error shake, busy button state,
   ambient 3D card tilt, and password-strength scoring. Pure vanilla,
   no dependencies — matches the dashboard's hand-rolled JS style. */

(function () {
  function $(s, c) { return (c || document).querySelector(s); }
  function $$(s, c) { return Array.from((c || document).querySelectorAll(s)); }

  /* ---------- password show/hide ---------- */
  $$("[data-pw-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var inp = $("#" + btn.dataset.pwToggle);
      if (!inp) return;
      var show = inp.type === "password";
      inp.type = show ? "text" : "password";
      btn.classList.toggle("on", show);
      var eye = btn.querySelector(".eye");
      var eyeOff = btn.querySelector(".eye-off");
      if (eye) eye.style.display = show ? "none" : "";
      if (eyeOff) eyeOff.style.display = show ? "" : "none";
      inp.focus();
    });
  });

  /* ---------- helpers exposed for the page scripts ---------- */
  window.PMA = window.PMA || {};
  PMA.$ = $;
  PMA.$$ = $$;

  PMA.shake = function (el) {
    if (!el) return;
    el.classList.remove("shake");
    void el.offsetWidth; /* restart the animation */
    el.classList.add("shake");
    setTimeout(function () { el.classList.remove("shake"); }, 500);
  };

  PMA.busy = function (btn, on) {
    btn.classList.toggle("loading", on);
    btn.disabled = on;
  };

  PMA.showErr = function (msg) {
    var e = $("#err");
    if (!e) return;
    var tx = e.querySelector(".tx");
    if (tx) tx.textContent = msg || "Something went wrong.";
    e.classList.add("show");
    PMA.shake(e);
  };
  PMA.clearErr = function () {
    var e = $("#err");
    if (e) e.classList.remove("show");
  };
  PMA.clearErr();

  /* ---------- ambient 3D tilt (desktop pointer only) ---------- */
  PMA.tilt = function () {
    var card = $(".a-card");
    var stage = $(".a-stage");
    if (!card || !stage) return;
    if (window.matchMedia("(pointer: coarse)").matches) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    var raf = null;
    stage.addEventListener("mousemove", function (ev) {
      if (raf) return;
      raf = requestAnimationFrame(function () {
        raf = null;
        var r = card.getBoundingClientRect();
        var px = (ev.clientX - r.left) / r.width - 0.5;
        var py = (ev.clientY - r.top) / r.height - 0.5;
        card.style.transform =
          "rotateX(" + (-py * 3.5).toFixed(2) + "deg) rotateY(" + (px * 5).toFixed(2) + "deg)";
      });
    });
    stage.addEventListener("mouseleave", function () {
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      card.style.transform = "rotateX(0deg) rotateY(0deg)";
    });
  };

  /* ---------- password strength ---------- */
  PMA.strength = function (pw) {
    if (!pw) return { score: 0, label: "" };
    var score = 0;
    if (pw.length >= 8) score++;
    if (pw.length >= 12) score++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
    if (/\d/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw)) score++;
    score = Math.min(4, score);
    var label = score <= 1 ? "Too weak" : score === 2 ? "Weak" : score === 3 ? "Okay" : "Strong";
    return { score: score, label: label };
  };
})();
