/* Copyright 2026 Dan Lee-Odinson. Licensed under the Apache License, Version 2.0; see LICENSE.

   The companion app prototype's views (Dan's ruling 61): Needs review, Tasks, Activity and About, the task sheet,
   and the simulated private viewer. Plain DOM; every rule lives in state.js and every read and write in adapter.js. */
(function (root) {
  "use strict";

  var S = root.RatatoskrState;
  var REVIEW_LINK = "ratatoskr-review://local/"; // the mocked secure link: intercepted in this page, never followed

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
    });
  }

  function when(at) {
    var d = new Date(at);
    return isNaN(d) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  var INPUT = { photo: "Photo", recording: "Recording" };

  // The sandbox (Dan's ruling 65): its label, on screen in every view, sheet and viewer, and the fixtures its guided
  // scenario uses: an ordinary task Decider was unsure of, an unsure photo hold, a sensitive transcript hold.
  var SANDBOX_LABEL = "Interactive sandbox: recorded Decider results, simulated integrations.";
  var SCENARIO = { task: "task-rec_009-3", unsure: "hold-s6", sensitive: "hold-rec_014" };

  function App(el, adapter, options) {
    this.el = el;
    this.adapter = adapter;
    this.sandbox = !!adapter.sandbox;
    this.scenario = { step: 0, card: null, arriving: false }; // the guided scenario's progress, never stored
    this.timers = []; // its pending arrivals, cleared by a reset
    this.delay = options && typeof options.delay === "number" ? options.delay : 700; // a notification's arrival, ms
    this.tab = "review";
    this.filter = { category: "", tag: "" };
    this.sheet = null; // the task open in its sheet
    this.draft = null; // that sheet's edits before Save: its tags, and the category chosen (null until one is)
    this.viewer = null; // the item in the simulated private viewer
    this.notice = "";
    this.busy = false;
  }

  App.prototype.start = function () {
    var self = this;
    this.el.addEventListener("click", function (e) { self.onClick(e); });
    this.el.addEventListener("submit", function (e) { e.preventDefault(); self.onSave(e.target); });
    this.el.addEventListener("change", function (e) { self.onChange(e); });
    if (this.sandbox) {
      this.el.classList.add("sandboxed");
      if (this.el.id === "app") {
        root.document.title = "Ratatoskr · interactive sandbox";
      }
    }
    return this.reload();
  };

  App.prototype.reload = function () {
    var self = this;
    return this.adapter.load().then(function (loaded) {
      self.data = loaded.data;
      self.actions = loaded.actions.slice();
      self.state = S.build(self.data, self.actions);
      self.render();
      return self;
    }, function (e) { // the owner service did not answer: say so, and offer to disconnect
      self.el.innerHTML = "<header class=\"top\"><div class=\"brand\">Ratatoskr</div><div class=\"sub\">" +
        "Companion · mobile web prototype</div></header><main class=\"main\"><p class=\"notice\">" +
        esc(String(e && e.message || e)) + "</p><button class=\"btn\" data-disconnect>Disconnect</button></main>";
      return self;
    });
  };

  // Records each proposed action through the adapter. An external update shows as pending until the adapter returns
  // its acknowledgment (or its failure); a prototype result is saved at once.
  App.prototype.record = function (proposals, done) {
    var self = this;
    if (!proposals.length) {
      this.notice = "Nothing to save: no change since the last save.";
      this.render();
      return Promise.resolve([]);
    }
    this.busy = true;
    var recorded = [];
    var chain = Promise.resolve();
    proposals.forEach(function (p) {
      chain = chain.then(function () {
        var pending = null;
        if (self.adapter.external && p.type === "categorize") {
          pending = JSON.parse(JSON.stringify(p));
          pending.seq = "pending-" + recorded.length;
          pending.status = "pending";
          pending.at = new Date().toISOString();
          self.actions.push(pending);
          self.state = S.build(self.data, self.actions);
          self.render();
        }
        return self.adapter.record(p).then(function (a) {
          if (pending) {
            self.actions.splice(self.actions.indexOf(pending), 1);
          }
          self.actions.push(a);
          recorded.push(a);
          self.state = S.build(self.data, self.actions);
        });
      });
    });
    return chain.then(function () {
      self.busy = false;
      var failed = recorded.filter(function (a) { return a.status === "failed"; })[0];
      self.notice = failed ? "Not applied: " + (failed.error || "the update failed") + "." : done || "Saved.";
      self.render();
      return recorded;
    });
  };

  App.prototype.onClick = function (e) {
    var t = e.target.closest("[data-tab],[data-review],[data-act],[data-open],[data-close],[data-close-viewer]," +
      "[data-add-tag],[data-remove-tag],[data-reset],[data-connect],[data-disconnect],[data-scenario],[data-file],a[href]");
    if (!t || this.busy) {
      if (t && t.matches("a[href]")) {
        e.preventDefault();
      }
      return;
    }
    if (t.matches("a[href]") && t.getAttribute("href").indexOf(REVIEW_LINK) === 0) {
      e.preventDefault(); // the mocked secure link: intercepted here, never navigated to
      this.openViewer(t.getAttribute("data-review"));
      return;
    }
    if (t.hasAttribute("data-tab")) {
      this.tab = t.getAttribute("data-tab");
      this.sheet = this.viewer = null;
      this.notice = "";
      this.render();
    } else if (t.hasAttribute("data-act")) {
      var item = t.getAttribute("data-item");
      var act = t.getAttribute("data-act");
      var self = this;
      var proposals = S.propose(this.state, item, { action: act });
      var words = { "keep-held": "Kept held. Recorded in Activity.", release: "Released by owner (owner authorization)." +
        (this.sandbox ? " In the courier, the remaining checks would resume from the check after the one that held " +
        "it; the sandbox shows no later result." : ""),
        commitment: "Scene answered: Commitment.", reference: "Scene answered: Reference." }[act];
      this.record(proposals, words).then(function () {
        if (self.sandbox && item === SCENARIO.unsure) { // the scenario's private example, answered
          self.scenario.step = 4;
          self.scenario.card = null;
          self.render();
        }
        if (act === "keep-held") { // back to the inbox, as the brief's scenario A asks
          self.viewer = null;
          self.tab = "review";
          self.render();
        }
      });
    } else if (t.hasAttribute("data-open")) {
      this.openSheet(t.getAttribute("data-open"));
    } else if (t.hasAttribute("data-close")) {
      this.sheet = null;
      this.notice = "";
      this.render();
    } else if (t.hasAttribute("data-close-viewer")) {
      this.viewer = null;
      this.tab = "review";
      this.render();
    } else if (t.hasAttribute("data-add-tag")) {
      var input = this.el.querySelector("[data-tag-input]");
      var tag = S.normaliseTag(input ? input.value : "");
      if (tag && this.draft.tags.indexOf(tag) < 0) {
        this.draft.tags.push(tag);
      }
      this.render();
    } else if (t.hasAttribute("data-remove-tag")) {
      var gone = t.getAttribute("data-remove-tag");
      this.draft.tags = this.draft.tags.filter(function (x) { return x !== gone; });
      this.render();
    } else if (t.hasAttribute("data-scenario")) {
      this.onScenario(t.getAttribute("data-scenario"));
    } else if (t.hasAttribute("data-file")) {
      this.fileScenarioTask(t.getAttribute("data-file"));
    } else if (this.sandbox && (t.hasAttribute("data-connect") || t.hasAttribute("data-disconnect"))) {
      return; // the sandbox has no live connection, and no control reaches one
    } else if (t.hasAttribute("data-connect")) { // the owner service's token, kept in this browser
      var field = this.el.querySelector("[data-token]");
      if (field && field.value.trim()) {
        root.RatatoskrAdapter.browserStorage().setItem(root.RatatoskrAdapter.TOKEN_KEY, field.value.trim());
        root.location.reload();
      }
    } else if (t.hasAttribute("data-disconnect")) {
      root.RatatoskrAdapter.browserStorage().removeItem(root.RatatoskrAdapter.TOKEN_KEY);
      root.location.reload();
    } else if (t.hasAttribute("data-reset")) {
      if (this.sandbox) {
        this.resetSandbox();
        return;
      }
      var app = this;
      this.adapter.reset().then(function () {
        app.notice = "Prototype data cleared.";
        return app.reload();
      });
    }
  };

  // --- the sandbox's guided scenario (ruling 65) ---

  App.prototype.after = function (fn) { // a notification's arrival, cancelled by a reset
    var self = this;
    var id = root.setTimeout(function () {
      self.timers = self.timers.filter(function (x) { return x !== id; });
      fn();
    }, this.delay);
    this.timers.push(id);
  };

  App.prototype.onScenario = function (what) {
    var self = this;
    var sc = this.scenario;
    if (what === "start") {
      sc.step = 1;
      this.render();
    } else if ((what === "send-task" || what === "send-private") && !sc.arriving) {
      sc.arriving = true;
      sc.card = null;
      this.render();
      this.after(function () {
        sc.arriving = false;
        sc.card = what === "send-task" ? { type: "task", id: SCENARIO.task, arrived: true } :
          { type: "hold", id: SCENARIO.unsure, arrived: true };
        sc.step = what === "send-task" ? 2 : 3;
        self.render();
      });
    } else if (what === "open-sensitive") {
      sc.card = null;
      this.openViewer(SCENARIO.sensitive);
    } else if (what === "dismiss") {
      sc.card = null;
      this.render();
    } else if (what === "reset") {
      this.resetSandbox();
    }
  };

  // The owner's tap on the notification: the same proposal and record as the task's sheet, so the first answer stands.
  App.prototype.fileScenarioTask = function (value) {
    var self = this;
    var proposals = S.propose(this.state, SCENARIO.task, { category: value });
    return this.record(proposals, value === S.LEAVE ? "Left unsorted by owner." : "Filed by owner: " + value + ".")
      .then(function () {
        self.scenario.card = { type: "task", id: SCENARIO.task };
        self.scenario.step = 3;
        self.render();
      });
  };

  // Everything the sandbox holds: its actions, the scenario's progress, the open sheet or viewer, pending arrivals.
  App.prototype.resetSandbox = function () {
    var self = this;
    this.timers.forEach(function (id) { root.clearTimeout(id); });
    this.timers = [];
    this.scenario = { step: 0, card: null, arriving: false };
    this.sheet = this.viewer = this.draft = null;
    this.tab = "review";
    this.filter = { category: "", tag: "" };
    return this.adapter.reset().then(function () {
      self.notice = "Sandbox reset: every action and the scenario's progress cleared.";
      return self.reload();
    });
  };

  App.prototype.onChange = function (e) {
    if (e.target.name === "category" && this.draft && !e.target.disabled) { // kept until Save: a re-render shows it
      this.draft.category = e.target.value;
      return;
    }
    var key = e.target.getAttribute("data-filter");
    if (key) {
      this.filter[key] = e.target.value;
      this.render();
    }
  };

  App.prototype.onSave = function (form) {
    var id = form.getAttribute("data-save");
    var chosen = form.querySelector("input[name=category]:checked");
    var category = this.draft.category || (chosen && !chosen.disabled ? chosen.value : null);
    var proposals = S.propose(this.state, id, { category: category, tags: this.draft.tags });
    var view = this.state.views[id];
    var categorized = proposals.some(function (p) { return p.type === "categorize"; });
    return this.record(proposals, categorized ? "Categorized by owner." : "Saved.").then(function () { return view; });
  };

  App.prototype.openSheet = function (id) {
    this.sheet = id;
    this.draft = { tags: this.state.views[id].tags.slice(), category: null };
    this.notice = "";
    this.render();
  };

  App.prototype.openViewer = function (id) {
    this.viewer = id;
    this.notice = "";
    this.render();
  };

  // --- rendering ---

  App.prototype.render = function () {
    var body = { review: this.renderReview, tasks: this.renderTasks, activity: this.renderActivity,
      about: this.renderAbout }[this.tab].call(this);
    var count = S.needsReview(this.state).length;
    this.el.innerHTML =
      "<header class=\"top\">" + this.labelHtml() + "<div class=\"brand\">Ratatoskr</div><div class=\"sub\">" +
      "Companion · mobile web prototype</div><div class=\"source\">" + esc(this.sandbox ?
        "Recorded results · no live connection" : this.adapter.name === "fixtures" ? "Fixture data · prototype writes" :
        "Owner service · live") + "</div></header>" +
      "<main class=\"main\">" + (this.notice && !this.sheet && !this.viewer ? "<p class=\"notice\" role=\"status\">" +
        esc(this.notice) + "</p>" : "") + this.renderScenario() + body + "</main>" + this.renderNotification() +
      "<nav class=\"tabs\">" + [["review", "Needs review" + (count ? " (" + count + ")" : "")], ["tasks", "Tasks"],
        ["activity", "Activity"], ["about", "About"]].map(function (x) {
          return "<button data-tab=\"" + x[0] + "\" class=\"" + (this.tab === x[0] ? "on" : "") + "\">" + esc(x[1]) +
            "</button>";
        }, this).join("") + "</nav>" +
      (this.sheet ? this.renderSheet(this.state.views[this.sheet]) : "") +
      (this.viewer ? this.renderViewer(this.state.views[this.viewer]) : "");
  };

  function badge(view) {
    return view.illustrative ? "<span class=\"badge illus\">Illustrative data, not a recorded model answer</span>" : "";
  }

  App.prototype.labelHtml = function (where) { // the sandbox's label: in the header, every sheet and every viewer
    return this.sandbox ? "<div class=\"sandbox-label" + (where ? " " + where : "") + "\" role=\"note\">" +
      esc(SANDBOX_LABEL) + "</div>" : "";
  };

  App.prototype.renderScenario = function () {
    if (!this.sandbox) {
      return "";
    }
    var sc = this.scenario;
    var holdArrived = sc.card && sc.card.type === "hold";
    function button(label, what, waiting) {
      return "<button class=\"btn primary\" data-scenario=\"" + what + "\"" + (waiting ? " disabled" : "") + ">" +
        esc(waiting ? "Arriving…" : label) + "</button>";
    }
    var step = [
      ["A one-minute guided scenario", "An ordinary task Decider was unsure of, filed by the owner in one tap; then " +
        "an item held for privacy, reviewed in the simulated viewer.", button("Start the scenario", "start")],
      ["Step 1 of 4 · Send a sample commitment", "A task from a recorded conversation that Decider was unsure where " +
        "to file.", button("Send a sample commitment", "send-task", sc.arriving)],
      ["Step 2 of 4 · Choose a project", "On the notification: Decider's recorded probabilities, in its order, one " +
        "button per project, and Leave unsorted.", ""],
      ["Step 3 of 4 · Try a private example", holdArrived ? "Press Review privately on the notification." :
        "An item held for privacy arrives as an ID-only notification.",
        holdArrived ? "" : button("Send a private example", "send-private", sc.arriving)],
      ["Step 4 of 4 · Compare, or reset and try again", "A sensitive hold offers no release; an unsure one offers the " +
        "owner's Release.", button("Open a sensitive hold", "open-sensitive")]
    ][sc.step];
    return "<section class=\"scenario\" aria-label=\"Guided scenario\"><div class=\"stitle\">" + esc(step[0]) +
      "</div><p class=\"fine\">" + esc(step[1]) + "</p><div class=\"actions\">" + step[2] +
      "<button class=\"btn\" data-scenario=\"reset\">Reset the sandbox</button></div></section>";
  };

  // A phone-style notification inside the page (no browser notification API): the scenario's task, or an ID-only hold.
  App.prototype.renderNotification = function () {
    var card = this.sandbox && this.scenario.card;
    var view = card && this.state.views[card.id];
    if (!view || this.sheet || this.viewer) {
      return "";
    }
    var inner;
    if (card.type === "task") {
      var probs = view.project.probabilities || {};
      var order = Object.keys(probs).sort(function (a, b) { return probs[b] - probs[a]; });
      var chosen = view.ownerCategory;
      var filed = view.actions.filter(function (a) { return a.type === "categorize"; })[0];
      inner = "<div class=\"text\">" + esc(taskText(view)) + "</div><div class=\"model\">" + esc(S.modelLine(view)) +
        "</div>" + (chosen ? "<div class=\"state done\">" + esc(chosen.value === S.LEAVE ? "Left unsorted by owner" :
        "Filed by owner: " + chosen.value) + "</div><p class=\"fine\">" + esc(filed ? S.statusText(filed) : "") +
        ". Decider's answer stays recorded apart from the owner's.</p><div class=\"actions\"><button class=\"btn\" " +
        "data-tab=\"tasks\">See it in Tasks</button><button class=\"btn\" data-tab=\"activity\">See Activity</button>" +
        "</div>" : "<p class=\"fine\">Decider's recorded probabilities, in its order:</p><div class=\"actions picks\">" +
        order.map(function (p) {
          return "<button class=\"btn\" data-file=\"" + esc(p) + "\">" + esc(p) + " <span class=\"num\">" +
            S.pct(probs[p]) + "</span></button>";
        }).join("") + "<button class=\"btn\" data-file=\"" + S.LEAVE + "\">Leave unsorted</button></div>");
    } else {
      inner = "<div class=\"kind\">" + esc(INPUT[view.input]) + " · held for privacy</div><div class=\"ref mono\">" +
        esc(view.ref) + "</div><p class=\"fine\">ID only: no excerpt, title or thumbnail in the notification.</p>" +
        "<div class=\"actions\">" + reviewLink(view) + "</div>";
    }
    var arriving = card.arrived; // the slide-in plays once, when it arrives, not on every redraw
    card.arrived = false;
    return "<aside class=\"notif " + esc(card.type) + (arriving ? " arrive" : "") + "\" role=\"status\" " +
      "aria-live=\"polite\" data-notif=\"" + esc(view.id) + "\"><div class=\"nhead\"><span>Ratatoskr · now</span>" +
      "<button class=\"x\" data-scenario=" +
      "\"dismiss\" aria-label=\"Dismiss\">×</button></div>" + inner + "</aside>";
  };

  function reviewLink(view, label) {
    return "<a class=\"btn primary\" href=\"" + REVIEW_LINK + esc(view.ref) + "\" data-review=\"" + esc(view.id) +
      "\">" + esc(label || "Review privately") + "</a>";
  }

  App.prototype.card = function (view) {
    if (view.kind === "hold") {
      return "<article class=\"card hold\" data-card=\"" + esc(view.id) + "\"><div class=\"kind\">" +
        esc(INPUT[view.input]) + " · held for privacy</div><div class=\"ref mono\">" + esc(view.ref) + "</div>" +
        "<div class=\"state\">Held by the " + esc(view.gate) + " · " + esc(view.verdict) + " · p " + S.pct(view.p) +
        "</div><div class=\"fine\">ID only: no excerpt, title or thumbnail on this card.</div>" + badge(view) +
        "<div class=\"actions\">" + reviewLink(view) + "</div></article>";
    }
    if (view.kind === "scene") {
      return "<article class=\"card scene\" data-card=\"" + esc(view.id) + "\"><div class=\"kind\">" +
        esc(INPUT[view.input]) + " · scene unsure</div><div class=\"ref mono\">" + esc(view.ref) + "</div>" +
        "<div class=\"state\">" + esc(S.modelLine(view)) + "</div>" + badge(view) + "<div class=\"actions\">" +
        "<button class=\"btn\" data-act=\"commitment\" data-item=\"" + esc(view.id) + "\">Commitment</button>" +
        "<button class=\"btn\" data-act=\"reference\" data-item=\"" + esc(view.id) + "\">Reference</button>" +
        reviewLink(view, "View privately") + "</div></article>";
    }
    return "<article class=\"card task\" data-card=\"" + esc(view.id) + "\"><div class=\"kind\">" +
      esc(INPUT[view.input]) + " · task · category uncertain</div><div class=\"text\">" + esc(taskText(view)) + "</div>" +
      "<div class=\"state\">" + esc(S.modelLine(view)) + "</div>" + badge(view) + "<div class=\"actions\">" +
      "<button class=\"btn primary\" data-open=\"" + esc(view.id) + "\">Categorize</button></div></article>";
  };

  function taskText(view) { // a live task's text only where this courier sent it to Telegram (ruling 61 P1)
    return view.text || "Task text: on Telegram and in Todoist, not kept on the laptop";
  }

  function outcome(view) {
    if (view.kind === "task") {
      var c = S.category(view);
      var a = view.actions.filter(function (x) { return x.type === "categorize"; })[0];
      return (c.value === S.LEAVE ? "Left unsorted by owner" : "Categorized by owner: " + c.value) +
        (a ? " · " + S.statusText(a) : "");
    }
    var r = view.resolution;
    var last = view.actions[view.actions.length - 1];
    var what = r.type === "kept-held" ? "Kept held after review" : r.type === "released" ?
      "Released by owner (owner authorization)" : "Scene: " + (r.value === "commitment" ? "Commitment" : "Reference") +
      ", chosen by owner";
    return what + (last ? " · " + S.statusText(last) : "");
  }

  App.prototype.renderReview = function () {
    var open = S.needsReview(this.state);
    var done = S.reviewed(this.state);
    return "<h2>Needs review</h2>" + (open.length ? open.map(this.card, this).join("") :
      "<p class=\"empty\">Nothing waiting.</p>") +
      (done.length ? "<h3>Reviewed</h3><ul class=\"done\">" + done.map(function (v) {
        var reopen = v.kind === "task" ? "data-open=\"" + esc(v.id) + "\"" : "";
        return "<li>" + (v.kind === "task" ? "<button class=\"link\" " + reopen + ">" :
          "<a class=\"link\" href=\"" + REVIEW_LINK + esc(v.ref) + "\" data-review=\"" + esc(v.id) + "\">") +
          "<span class=\"mono\">" + esc(v.ref) + "</span> · " + esc(outcome(v)) + (v.kind === "task" ? "</button>" :
          "</a>") + "</li>";
      }).join("") + "</ul>" : "");
  };

  App.prototype.taskCard = function (view) {
    var c = S.category(view);
    var cat = c.value === S.LEAVE ? (c.by === "owner" ? "unsorted · left by owner" : "unsorted · Decider unsure") :
      c.value + (c.by === "owner" ? " · by owner" : " · by Decider");
    var status = c.by === "owner" ? outcome(view) : "";
    return "<article class=\"card task\" data-card=\"" + esc(view.id) + "\"><button class=\"cardlink\" data-open=\"" +
      esc(view.id) + "\"><div class=\"text\">" + esc(taskText(view)) + "</div><div class=\"meta\">" +
      (view.due ? "Due: " + esc(view.due) + " · " : "") + esc(INPUT[view.input]) + " " + esc(view.ref) +
      (view.from ? " · " + esc(view.from) : "") + "</div><div class=\"chips\"><span class=\"chip cat\">" + esc(cat) +
      "</span>" + view.tags.map(function (t) { return "<span class=\"chip tag\">#" + esc(t) + "</span>"; }).join("") +
      "</div>" + (status ? "<div class=\"fine\">" + esc(status) + "</div>" : "") +
      (view.tags.length ? "<div class=\"fine\">Tags: " + esc(S.STATUS.local) + "</div>" : "") + "</button></article>";
  };

  App.prototype.renderTasks = function () {
    var state = this.state;
    var shown = S.tasks(state, this.filter);
    var groups = state.projects.concat([S.LEAVE]).map(function (p) {
      return [p, shown.filter(function (v) { return S.category(v).value === p; })];
    }).filter(function (g) { return g[1].length; });
    var opts = function (values, chosen, all) {
      return "<option value=\"\">" + all + "</option>" + values.map(function (v) {
        return "<option value=\"" + esc(v) + "\"" + (v === chosen ? " selected" : "") + ">" + esc(v) + "</option>";
      }).join("");
    };
    return "<h2>Tasks</h2><div class=\"filters\"><label>Category <select data-filter=\"category\">" +
      opts(state.projects.concat([S.LEAVE]), this.filter.category, "All") + "</select></label><label>Tag " +
      "<select data-filter=\"tag\">" + opts(S.tagsInUse(state), this.filter.tag, "Any") + "</select></label></div>" +
      (groups.length ? groups.map(function (g) {
        return "<section class=\"group\" data-group=\"" + esc(g[0]) + "\"><h3>" + esc(g[0]) + " <span class=\"count\">" +
          g[1].length + "</span></h3>" + g[1].map(this.taskCard, this).join("") + "</section>";
      }, this).join("") : "<p class=\"empty\">No task matches the filter.</p>");
  };

  function entryHtml(e) {
    return "<li class=\"entry status-" + esc(e.status) + "\"><div class=\"when\">" + esc(when(e.at)) + " · " +
      esc(INPUT[e.input] || "") + " <span class=\"mono\">" + esc(e.ref) + "</span></div>" +
      (e.model ? "<div class=\"model\">" + esc(e.model) + "</div>" : "") + "<div class=\"owner\">" + esc(e.owner) +
      "</div><div class=\"status\">" + esc(e.statusText) + "</div></li>";
  }

  App.prototype.renderActivity = function () {
    var entries = S.activity(this.state);
    return "<h2>Activity</h2><p class=\"fine\">Each entry keeps Decider's recorded answer apart from the owner's " +
      "choice.</p>" + (entries.length ? "<ol class=\"activity\">" + entries.map(entryHtml).join("") + "</ol>" :
      "<p class=\"empty\">No owner action yet.</p>");
  };

  App.prototype.renderAbout = function () {
    var a = this.adapter;
    return "<h2>About this " + (this.sandbox ? "sandbox" : "prototype") + "</h2><p>A <strong>mobile web prototype" +
      "</strong> of the owner's interface to Ratatoskr: one page of plain HTML, CSS and JavaScript. It is not a native " +
      "app.</p>" + (this.sandbox ? "<p>This is its <strong>interactive sandbox</strong>: the same page, served " +
      "publicly, playing Decider's recorded answers from the committed exports. It has no live connection: no " +
      "model, courier, Todoist or Telegram request is made.</p>" : "") +
      "<p class=\"fine\">Data: " + esc(a.describe) + ". Writes: " + esc(a.writes) + ".</p>" +
      "<h3>Real and simulated</h3><table class=\"inventory\"><tr><th>Part</th><th>Here</th></tr>" +
      [["Decider's answers (probabilities, confidences, floors)", "Real: as the committed exports recorded them; " +
        "each item names its export"],
       ["Photographs and transcript in the viewer", "Real fixtures: the repository's fictional test content"],
       ["The courier's rules shown (no release for sensitive, Release for unsure, first answer stands)",
        "Real: the same rules as the courier"],
       ["Secure link and private viewer", "Simulated: the link is intercepted in this page; no connection is made"],
       ["Keep held, Release, scene answers", "Prototype only: recorded here; the courier is not told"],
       ["Category changes", a.name === "fixtures" ? "Prototype only: saved in this browser, not sent to Todoist" :
        "Live: sent to the courier's owner service; acknowledged only when the courier updated the Todoist task"],
       ["Tags", "Saved in app, not in Todoist"],
       ["Notifications", this.sandbox ? "Simulated: drawn in this page by the guided scenario; no Telegram, no " +
        "browser notification" : "None: this prototype needs no Telegram"]].concat(this.sandbox ?
       [["Live connection", "None: the sandbox has no live connection and no connection controls"]] : [])
      .map(function (r) {
        return "<tr><td>" + esc(r[0]) + "</td><td>" + esc(r[1]) + "</td></tr>";
      }).join("") + "</table>" + (this.sandbox ? "<p><button class=\"btn\" data-scenario=\"reset\">Reset the " +
      "sandbox</button></p>" : this.renderConnection() +
      "<p><button class=\"btn\" data-reset>Clear prototype data</button></p>");
  };

  // The owner service (P1): connected, connectable (the page came from a server), or not from here (a file).
  App.prototype.renderConnection = function () {
    if (this.adapter.name === "service") {
      return "<h3>Owner service</h3><p class=\"fine\">Connected to the courier on this machine. A category change " +
        "is acknowledged only when the courier has updated the Todoist task.</p><button class=\"btn\" " +
        "data-disconnect>Disconnect</button>";
    }
    if (!/^https?:$/.test(root.location.protocol)) {
      return "<h3>Owner service</h3><p class=\"fine\">To connect, open this page from the courier's owner service " +
        "(<code>ratatoskr-demo --loop --owner-service</code>).</p>";
    }
    return "<h3>Owner service</h3><p class=\"fine\">The token from the courier's secrets file " +
      "(OWNER_SERVICE_TOKEN):</p><div class=\"addtag\"><input data-token type=\"password\" autocomplete=\"off\">" +
      "<button class=\"btn\" data-connect>Connect</button></div>";
  };

  App.prototype.renderSheet = function (view) {
    var self = this;
    var chosen = view.ownerCategory;
    var pending = chosen ? null : this.draft.category; // chosen in this sheet, not saved yet
    var probs = view.project.probabilities || {};
    var order = Object.keys(probs).length ? Object.keys(probs).sort(function (a, b) { return probs[b] - probs[a]; }) :
      this.state.projects.slice(); // no probabilities sent with a live task: the courier's order
    var current = S.category(view).value;
    var radios = order.map(function (p) {
      var prob = probs[p];
      var checked = chosen ? chosen.value === p : pending ? pending === p : (view.project.sure && current === p);
      return "<label class=\"opt\"><input type=\"radio\" name=\"category\" value=\"" + esc(p) + "\"" +
        (checked ? " checked" : "") + (chosen ? " disabled" : "") + "> <span class=\"name\">" + esc(p) + "</span>" +
        "<span class=\"bar\"><span style=\"width:" + Math.round((prob || 0) * 100) + "%\"></span></span>" +
        "<span class=\"num\">" + S.pct(prob) + "</span></label>";
    }).join("") + "<label class=\"opt leave\"><input type=\"radio\" name=\"category\" value=\"" + S.LEAVE + "\"" +
      ((chosen ? chosen.value : pending) === S.LEAVE ? " checked" : "") + (chosen ? " disabled" : "") +
      "> <span class=\"name\">Leave unsorted</span></label>";
    var history = S.activity(this.state).filter(function (e) { return e.item === view.id; });
    return "<div class=\"sheet\" role=\"dialog\" aria-label=\"Task\">" + this.labelHtml("stick") +
      "<div class=\"sheethead\"><button class=\"btn\" " +
      "data-close>Back</button><h2>Task</h2></div><div class=\"text\">" + esc(taskText(view)) + "</div><div class=\"meta\">" +
      (view.due ? "Due: " + esc(view.due) + " · " : "") + esc(INPUT[view.input]) + " " + esc(view.ref) +
      (view.from ? " · " + esc(view.from) : "") + "</div>" + badge(view) +
      "<div class=\"model\">" + esc(S.modelLine(view)) + "</div><p class=\"fine\">Decider's answer as recorded in " +
      esc(view.source.export) + ". The category is the task's <code>proj:</code> label in Todoist.</p>" +
      "<form data-save=\"" + esc(view.id) + "\"><fieldset><legend>Category, with Decider's probability for each" +
        (chosen ? ": " +
        (chosen.value === S.LEAVE ? "left unsorted by owner" : "categorized by owner") +
        " (the first answer stands, as with the Telegram buttons)" : "") + "</legend>" + radios + "</fieldset>" +
      "<fieldset><legend>Tags <span class=\"fine\">(" + esc(S.STATUS.local) + ")</span></legend><div class=\"chips\">" +
      this.draft.tags.map(function (t) {
        return "<span class=\"chip tag\">#" + esc(t) + " <button type=\"button\" class=\"x\" data-remove-tag=\"" +
          esc(t) + "\" aria-label=\"Remove " + esc(t) + "\">×</button></span>";
      }).join("") + "</div><div class=\"addtag\"><input data-tag-input placeholder=\"add a tag\" maxlength=\"32\">" +
      "<button type=\"button\" class=\"btn\" data-add-tag>Add</button></div></fieldset>" +
      "<button class=\"btn primary\" type=\"submit\"" + (self.busy ? " disabled" : "") + ">Save</button>" +
      (this.notice ? "<p class=\"notice\" role=\"status\">" + esc(this.notice) + "</p>" : "") + "</form>" +
      "<h3>History</h3>" + (history.length ? "<ol class=\"activity\">" + history.map(entryHtml).join("") + "</ol>" :
        "<p class=\"empty\">No owner action yet.</p>") + "</div>";
  };

  App.prototype.renderViewer = function (view) {
    var actions = S.availableActions(view);
    var content = !view.viewer ? "<p class=\"fine\">The held content stays on the laptop: a private review " +
      "connection is not built in this prototype, so nothing of it is shown here.</p>" :
      view.viewer.type === "photo" ? "<img class=\"photo\" src=\"" + esc(view.viewer.src) +
      "\" alt=\"Fictional picture held for review\">" : "<div class=\"transcript\"><div class=\"ttitle\">" +
      esc(view.viewer.title) + "</div><p>" + esc(view.viewer.text) + "</p></div>";
    var r = view.resolution;
    var status = S.modelLine(view);
    var buttons = "";
    if (view.kind === "hold") {
      if (actions.indexOf("keep-held") >= 0) {
        buttons += "<button class=\"btn primary\" data-act=\"keep-held\" data-item=\"" + esc(view.id) + "\">Keep held" +
          "</button><p class=\"fine\">Keep held: the item stays on the laptop and nothing is sent anywhere.</p>";
      }
      if (actions.indexOf("release") >= 0) {
        buttons += "<button class=\"btn warn\" data-act=\"release\" data-item=\"" + esc(view.id) + "\">Release " +
          "(owner authorization)</button><p class=\"fine\">An owner release resumes the remaining checks for this " +
          "photo; it is not a model clear and not permission to upload. Prototype only: the courier is not told" +
          (this.adapter.name === "service" ? ", even with the owner service connected" : "") + ".</p>";
      }
      if (view.verdict === "sensitive") {
        buttons += "<p class=\"fine\">A sensitive verdict has no release, as in the courier.</p>";
      }
    } else if (!r) {
      buttons = "<button class=\"btn\" data-act=\"commitment\" data-item=\"" + esc(view.id) + "\">Commitment</button>" +
        "<button class=\"btn\" data-act=\"reference\" data-item=\"" + esc(view.id) + "\">Reference</button>";
    }
    return "<div class=\"viewer\" role=\"dialog\" aria-label=\"Simulated private review\"><div class=\"simbanner\">" +
      this.labelHtml("inline") + "Simulated private review</div><p class=\"fine\">Opened from an intercepted link " +
      "(<span class=\"mono\">" +
      esc(REVIEW_LINK + view.ref) + "</span>): it never left this page. No connection to the laptop is made" +
      (view.viewer ? "; this is the repository's fictional fixture, and it" : ", and this page") + " does not show a " +
      "secure connection.</p><div class=\"kind\">" +
      esc(INPUT[view.input]) + " <span class=\"mono\">" + esc(view.ref) + "</span></div>" + badge(view) +
      "<div class=\"content\">" + content + "</div><div class=\"state\">" + esc(status) + "</div>" +
      (r ? "<div class=\"state done\">" + esc(outcome(view)) + "</div>" : "") +
      (view.run_note ? "<p class=\"fine\">" + esc(view.run_note) + "</p>" : "") +
      "<div class=\"actions\">" + buttons + "</div>" + (this.notice ? "<p class=\"notice\" role=\"status\">" +
        esc(this.notice) + "</p>" : "") + "<button class=\"btn back\" data-close-viewer>Return to inbox</button></div>";
  };

  root.RatatoskrApp = { App: App, REVIEW_LINK: REVIEW_LINK, esc: esc };
})(this);
