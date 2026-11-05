// PCSS 한/영 전환. 서버는 한국어 화면만 만들고, 영문 모드면 여기서 화면 글자를 사전(i18n-en.js)으로 바꾼다.
// - 텍스트 노드, placeholder · title · aria-label · 버튼 value, document.title, alert/confirm/prompt
// - 나중에 JS 가 그린 내용은 MutationObserver 로, Chart.js 캔버스는 전역 플러그인으로 바꾼다
// - 원문을 기억해 두었다가 한국어로 돌아갈 때 그대로 되돌린다 (POST 결과 화면도 새로고침 없이)
// - 사용자가 쓴 글처럼 번역하면 안 되는 곳은 translate="no" 또는 data-i18n-skip 을 붙인다
(function () {
  "use strict";
  var KEY = "pcss_lang";
  var KO = /[가-힣]/;
  var ATTRS = ["placeholder", "title", "aria-label", "alt", "data-tip"];
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };

  function readLang() {
    try { var v = localStorage.getItem(KEY); if (v) return v; } catch (e) {}
    var m = document.cookie.match(/(?:^|;\s*)pcss_lang=(\w+)/);
    return m ? m[1] : "ko";
  }
  var lang = readLang() === "en" ? "en" : "ko";
  var root = document.documentElement;

  // 영문 모드에서 한국어가 잠깐 보였다가 바뀌는 깜빡임을 막는다 (번역이 끝나면 풀고, 늦어도 1.5초 뒤엔 보인다)
  var hide = document.createElement("style");
  hide.textContent = "html.i18n-wait body{visibility:hidden!important}";
  document.head.appendChild(hide);
  if (lang === "en") {
    root.classList.add("i18n-wait", "lang-en");
    root.lang = "en";
    setTimeout(function () { root.classList.remove("i18n-wait"); }, 1500);
  }

  // ---------- 사전 ----------
  var exact = null, patterns = null, fallbackRe = null, fallbackMap = null;

  function norm(s) { return s.replace(/\s+/g, " ").trim(); }
  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function build() {
    if (exact) return;
    exact = Object.create(null); patterns = []; fallbackMap = Object.create(null);
    var dict = window.PCSS_EN || {}, plain = [];
    Object.keys(dict).forEach(function (k) {
      var v = dict[k];
      if (k.indexOf("{}") >= 0 || /\{\d\}/.test(k)) {
        var parts = k.split("{}");
        patterns.push({ re: new RegExp("^" + parts.map(escRe).join("(.+?)") + "$"), v: v, lit: parts.join("").length });
      } else {
        exact[k] = v;
        if (k.length >= 2 && v) { plain.push(k); fallbackMap[k] = v; }
      }
    });
    // 고정 글자가 많은(더 구체적인) 패턴부터 맞춰 본다: "관리자 {}명" 이 "{}명" 보다 먼저
    patterns.sort(function (a, b) { return b.lit - a.lit; });
    plain.sort(function (a, b) { return b.length - a.length; });
    fallbackRe = plain.length ? new RegExp(plain.map(escRe).join("|"), "g") : null;
  }

  function singular(s) {
    return s.replace(/(^|[^\d.,])1 (paper|conference|view|author|admin|field|param|researcher|member)s\b/g, "$11 $2");
  }

  function fill(tpl, groups) {
    var i = 0;
    return tpl.replace(/\{(\d?)\}/g, function (_, n) {
      var g = n === "" ? groups[i++] : groups[+n];
      return g == null ? "" : translate(g);
    });
  }

  // 한 덩어리 문장을 번역. 모르는 문장(사용자 글 등)은 원문 그대로 돌려준다
  function translate(s) {
    if (!s || !KO.test(s)) return s;
    build();
    var lead = s.match(/^\s*/)[0], tail = s.match(/\s*$/)[0], n = norm(s);
    if (n in exact) return lead + exact[n] + tail;
    for (var i = 0; i < patterns.length; i++) {
      var m = patterns[i].re.exec(n);
      if (m) return lead + singular(fill(patterns[i].v, m.slice(1))) + tail;
    }
    // 아는 문구가 섞여 있는 경우: 전부 바꿔서 한국어가 하나도 안 남을 때만 쓴다
    if (fallbackRe) {
      var out = n.replace(fallbackRe, function (k) { return fallbackMap[k]; });
      if (!KO.test(out)) return lead + out + tail;
    }
    return s;
  }

  // ---------- DOM ----------
  var origText = new Map();   // Text 노드 -> 원문
  var origAttr = new Map();   // Element -> {attr: 원문}
  var origHtml = new Map();   // data-i18n-en 요소 -> 원래 innerHTML
  var origTitle = null;
  var busy = false;

  function skipped(el) {
    for (; el && el.nodeType === 1; el = el.parentNode) {
      if (SKIP_TAGS[el.tagName]) return true;
      if (el.getAttribute("translate") === "no" || el.hasAttribute("data-i18n-skip")) return true;
    }
    return false;
  }

  function doText(node) {
    var v = node.nodeValue;
    if (!KO.test(v) || skipped(node.parentNode)) return;
    var t = translate(v);
    if (t !== v) { origText.set(node, v); node.nodeValue = t; }
  }

  function doAttrs(el) {
    // textarea 는 안쪽 글(사용자 입력)만 건너뛰고 placeholder 같은 속성은 번역한다
    if (skipped(el.tagName === "TEXTAREA" ? el.parentNode : el)) return;
    var list = ATTRS;
    if (el.tagName === "INPUT" && /^(button|submit|reset)$/i.test(el.type)) list = ATTRS.concat("value");
    for (var i = 0; i < list.length; i++) {
      var a = list[i], v = el.getAttribute(a);
      if (!v || !KO.test(v)) continue;
      var t = translate(v);
      if (t === v) continue;
      var o = origAttr.get(el) || {};
      if (!(a in o)) o[a] = v;
      origAttr.set(el, o);
      el.setAttribute(a, t);
    }
  }

  function walk(rootNode) {
    if (rootNode.nodeType === 3) { doText(rootNode); return; }
    if (rootNode.nodeType !== 1 && rootNode.nodeType !== 9 && rootNode.nodeType !== 11) return;
    if (rootNode.nodeType === 1) { if (skipped(rootNode)) return; doAttrs(rootNode); }
    // 굵은 글씨 등으로 쪼개진 문장은 템플릿이 data-i18n-en 에 영문 HTML 을 통째로 준다
    var whole = rootNode.querySelectorAll ? rootNode.querySelectorAll("[data-i18n-en]") : [];
    if (rootNode.nodeType === 1 && rootNode.hasAttribute("data-i18n-en")) whole = [rootNode].concat(Array.prototype.slice.call(whole));
    Array.prototype.forEach.call(whole, function (el) {
      if (origHtml.has(el) || skipped(el)) return;
      origHtml.set(el, el.innerHTML);
      el.innerHTML = el.getAttribute("data-i18n-en");
    });
    var tw = document.createTreeWalker(rootNode, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (n) {
        if (n.nodeType === 1) {
          if (n.getAttribute("translate") === "no" || n.hasAttribute("data-i18n-skip")) return NodeFilter.FILTER_REJECT;
          if (n.tagName === "TEXTAREA") { doAttrs(n); return NodeFilter.FILTER_REJECT; }
          return SKIP_TAGS[n.tagName] ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        }
        return KO.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });
    var n, texts = [], els = [];
    while ((n = tw.nextNode())) (n.nodeType === 3 ? texts : els).push(n);
    texts.forEach(doText);
    els.forEach(doAttrs);
  }

  function doTitle() {
    if (KO.test(document.title)) { if (origTitle == null) origTitle = document.title; document.title = translate(document.title); }
  }

  var observer = new MutationObserver(function (muts) {
    if (lang !== "en" || busy) return;
    busy = true;
    for (var i = 0; i < muts.length; i++) {
      var m = muts[i];
      if (m.type === "childList") m.addedNodes.forEach(walk);
      else if (m.type === "characterData") doText(m.target);
      else if (m.type === "attributes" && m.target.nodeType === 1) doAttrs(m.target);
    }
    doTitle();
    busy = false;
  });

  function applyEn() {
    busy = true;
    walk(document.body || document.documentElement);
    doTitle();
    busy = false;
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS.concat("value") });
    root.classList.remove("i18n-wait");
  }

  function revertKo() {
    observer.disconnect();
    origText.forEach(function (v, node) { if (node.isConnected) node.nodeValue = v; });
    origAttr.forEach(function (o, el) { if (el.isConnected) for (var a in o) el.setAttribute(a, o[a]); });
    origHtml.forEach(function (h, el) { if (el.isConnected) el.innerHTML = h; });
    origText.clear(); origAttr.clear(); origHtml.clear();
    if (origTitle != null) { document.title = origTitle; origTitle = null; }
  }

  // ---------- alert / confirm / prompt ----------
  ["alert", "confirm", "prompt"].forEach(function (fn) {
    var orig = window[fn];
    if (!orig) return;
    window[fn] = function (msg) {
      var args = Array.prototype.slice.call(arguments);
      if (lang === "en" && typeof msg === "string") args[0] = translate(msg);
      return orig.apply(window, args);
    };
  });

  // ---------- Chart.js: 범례 · 축 제목 · 라벨 · 툴팁 ----------
  function trDeep(obj, depth) {
    if (!obj || depth > 6) return;
    if (Array.isArray(obj)) {
      for (var i = 0; i < obj.length; i++) {
        if (typeof obj[i] === "string") obj[i] = translate(obj[i]);
        else if (obj[i] && typeof obj[i] === "object") trDeep(obj[i], depth + 1);
      }
      return;
    }
    ["label", "text", "title"].forEach(function (k) {
      if (typeof obj[k] === "string") obj[k] = translate(obj[k]);
      else if (Array.isArray(obj[k])) trDeep(obj[k], depth + 1);
    });
  }
  var chartPlugin = {
    id: "pcssI18n",
    beforeUpdate: function (chart) {
      if (lang !== "en") return;
      var d = chart.config.data || {}, o = chart.config.options || {};
      if (Array.isArray(d.labels)) trDeep(d.labels, 0);
      (d.datasets || []).forEach(function (ds) { if (typeof ds.label === "string") ds.label = translate(ds.label); });
      var sc = o.scales || {};
      Object.keys(sc).forEach(function (k) { if (sc[k] && sc[k].title) trDeep(sc[k].title, 0); });
      if (o.plugins && o.plugins.title) trDeep(o.plugins.title, 0);
    },
    beforeTooltipDraw: function (chart, args) {
      if (lang !== "en") return;
      var tt = args.tooltip;
      if (tt.title) tt.title = tt.title.map(translate);
      (tt.body || []).forEach(function (b) { b.lines = b.lines.map(translate); });
      if (tt.footer) tt.footer = tt.footer.map(translate);
    },
  };
  function hookChart(C) { try { if (C && C.register) C.register(chartPlugin); } catch (e) {} }
  if (window.Chart) hookChart(window.Chart);
  else {
    // Chart.js UMD 가 window.Chart 를 만드는 순간 플러그인을 건다 (차트가 그려지기 전)
    var _C;
    try {
      Object.defineProperty(window, "Chart", {
        configurable: true,
        get: function () { return _C; },
        set: function (v) { _C = v; hookChart(v); },
      });
    } catch (e) {}
  }
  function hasCharts() {
    var C = window.Chart;
    return !!(C && C.instances && Object.keys(C.instances).length);
  }

  // ---------- 전환 버튼 ----------
  var css = document.createElement("style");
  css.textContent =
    ".pcss-lang{position:fixed;top:14px;right:16px;z-index:65;display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;flex:none;" +
    "border:1px solid #dcdfe4;border-radius:16px;background:rgba(255,255,255,.96);color:#34495e;font:600 13px/1 'Noto Sans KR',Arial,sans-serif;" +
    "cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,.06);-webkit-tap-highlight-color:transparent;touch-action:manipulation}" +
    ".pcss-lang:hover{border-color:#c80150;color:#c80150}" +
    ".pcss-lang:focus-visible{outline:2px solid #c80150;outline-offset:2px}" +
    ".pcss-lang svg{width:15px;height:15px;flex:none;fill:none;stroke:currentColor;stroke-width:2}" +
    ".pcss-lang b{font-weight:800}.pcss-lang span{opacity:.45;font-weight:600}.pcss-lang .l-code{display:none}" +
    ".pcss-lang.is-inline{position:static;box-shadow:none;background:#fff}" +
    "@media (min-width:1024px){.pcss-ph{padding-right:104px}}" +
    /* 좁은 화면: 상단 바 안에 들어간 버튼만 쓰고, 언어는 짧은 코드로 */
    "@media (max-width:1023.98px){.has-lang-slot .pcss-lang.is-fixed{display:none}" +
    ".pcss-lang{top:10px;right:max(12px,env(safe-area-inset-right));height:34px;padding:0 11px}" +
    ".pcss-lang span,.pcss-lang .l-full{display:none}.pcss-lang .l-code{display:inline}}" +
    "@media print{.pcss-lang{display:none!important}}";
  document.head.appendChild(css);

  function paintBtn(btn) {
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/></svg>' +
      (lang === "en" ? '<b class="l-full">EN</b><b class="l-code">EN</b><span>/ 한국어</span>' : '<b class="l-full">한국어</b><b class="l-code">KO</b><span>/ EN</span>');
    btn.setAttribute("translate", "no");
    btn.title = lang === "en" ? "한국어로 보기" : "View in English";
    btn.setAttribute("aria-label", btn.title);
  }

  function setLang(next) {
    lang = next;
    try { localStorage.setItem(KEY, next); } catch (e) {}
    document.cookie = "pcss_lang=" + next + ";path=/;max-age=31536000;samesite=lax";
    root.lang = next;
    root.classList.toggle("lang-en", next === "en");
    if (hasCharts()) { location.reload(); return; }  // 차트는 새로 그려야 해서 GET 화면만 다시 연다
    if (next === "en") applyEn(); else revertKo();
    document.querySelectorAll(".pcss-lang").forEach(paintBtn);
    window.dispatchEvent(new CustomEvent("pcss:lang", { detail: next }));
  }

  // 헤더 안에 [data-lang-slot] 자리가 있으면 거기에 넣는다 (모바일 상단 바 · 관리자 헤더).
  // 사이드바 페이지의 데스크톱 화면은 상단 바가 없어서 오른쪽 위 고정 버튼을 따로 둔다.
  function makeBtn(cls) {
    var btn = document.createElement("button");
    btn.type = "button"; btn.className = "pcss-lang " + cls;
    paintBtn(btn);
    btn.addEventListener("click", function () { setLang(lang === "en" ? "ko" : "en"); });
    return btn;
  }
  function mountBtn() {
    if (document.querySelector(".pcss-lang") || window.PCSS_NO_LANG_BTN) return;
    var slots = document.querySelectorAll("[data-lang-slot]"), fixed = true;
    for (var i = 0; i < slots.length; i++) {
      slots[i].appendChild(makeBtn("is-inline"));
      if (slots[i].getAttribute("data-lang-slot") === "all") fixed = false;
    }
    if (slots.length) root.classList.add("has-lang-slot");
    if (fixed) {
      var f = makeBtn("is-fixed"); f.id = "pcss-lang";
      document.body.appendChild(f);
    }
  }

  window.PCSS_T = function (s) { return lang === "en" ? translate(s) : s; };
  window.PCSS_LANG = function () { return lang; };

  function onReady() {
    mountBtn();
    if (lang === "en") applyEn();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", onReady);
  else onReady();
})();
