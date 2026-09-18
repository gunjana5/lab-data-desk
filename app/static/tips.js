// tip pop - same habit as the other desks, ? not i

(function () {
  var pop = document.getElementById("tip-pop");
  if (!pop) return;
  var openBtn = null;
  var PAD = 10;
  var GAP = 8;

  function place(btn) {
    var br = btn.getBoundingClientRect();
    pop.hidden = false;
    // measure after show so width/height are real
    var pr = pop.getBoundingClientRect();
    var top = br.bottom + GAP;
    var left = br.left;
    var placeName = "below";
    if (top + pr.height > window.innerHeight - PAD) {
      top = br.top - pr.height - GAP;
      placeName = "above";
    }
    if (top < PAD) top = PAD;
    if (left + pr.width > window.innerWidth - PAD) {
      left = window.innerWidth - pr.width - PAD;
    }
    if (left < PAD) left = PAD;
    pop.style.top = top + "px";
    pop.style.left = left + "px";
    pop.className = "tip-pop place-" + placeName;
  }

  function close() {
    pop.hidden = true;
    pop.textContent = "";
    if (openBtn) openBtn.classList.remove("is-open");
    openBtn = null;
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".tip-btn");
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      var text = btn.getAttribute("data-tip") || "";
      if (openBtn === btn) {
        close();
        return;
      }
      if (openBtn) openBtn.classList.remove("is-open");
      openBtn = btn;
      btn.classList.add("is-open");
      pop.textContent = text;
      place(btn);
      return;
    }
    if (!pop.contains(e.target)) close();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });

  window.addEventListener(
    "scroll",
    function () {
      if (openBtn) place(openBtn);
    },
    true
  );
  window.addEventListener("resize", function () {
    if (openBtn) place(openBtn);
  });
})();
