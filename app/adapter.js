/* Copyright 2026 Dan Lee-Odinson. Licensed under the Apache License, Version 2.0; see LICENSE.

   Where the companion app's items come from and where the owner's actions go (Dan's ruling 61). One contract, so a
   view never knows which it has:

     load()          -> Promise of {data: {projects, items}, actions: [...]}, the actions in the order recorded
     record(action)  -> Promise of the action as recorded: with its seq, its time and its status
                        ("saved": prototype state; "pending", "acknowledged" or "failed": an external update)
     reset()         -> Promise; clears what this adapter recorded
     name, describe  -> what the app tells the owner about its data and its writes
     external        true when a record() may be an external update, shown pending until it is answered

   FixtureAdapter (P0): the fixtures drawn from the committed exports, and the owner's actions in the browser's local
   storage, so they persist across navigation, a refresh and a restart. Every action it records is "saved": prototype
   state, never an external write.

   ServiceAdapter (P1): the courier's owner service (ratatoskr-demo --loop --owner-service), chosen when the page is
   served by it and holds its token.

   The sandbox (Dan's ruling 65): on the public host, or with ?sandbox=1 for local testing, the fixtures under the
   sandbox's own storage key, chosen before anything else; no address or query switches it off. */
(function (root) {
  "use strict";

  var KEY = "ratatoskr-companion/v1";
  var SERVICE_KEY = "ratatoskr-companion/service/v1"; // the owner's local actions on live items
  var TOKEN_KEY = "ratatoskr-companion/token"; // the owner service's token, as the owner entered it
  var SANDBOX_KEY = "ratatoskr-companion/sandbox/v1"; // the sandbox's actions, apart from the prototype's
  var SANDBOX_HOSTS = ["dan-lee-odinson.github.io"]; // where the page is the public sandbox

  function memoryStorage() {
    var data = {};
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
      setItem: function (k, v) { data[k] = String(v); },
      removeItem: function (k) { delete data[k]; }
    };
  }

  // The browser's local storage, or memory where it cannot be used (a private window, a blocked file origin).
  function browserStorage() {
    try {
      var s = root.localStorage;
      s.setItem(KEY + "/probe", "1");
      s.removeItem(KEY + "/probe");
      return s;
    } catch (e) {
      return memoryStorage();
    }
  }

  function FixtureAdapter(fixtures, storage, now, key) {
    storage = storage || memoryStorage();
    key = key || KEY;
    now = now || function () { return new Date().toISOString(); };

    function read() {
      try {
        var raw = storage.getItem(key);
        var parsed = raw ? JSON.parse(raw) : null;
        return parsed && Array.isArray(parsed.actions) ? parsed.actions : [];
      } catch (e) {
        return [];
      }
    }

    function write(actions) {
      storage.setItem(key, JSON.stringify({ version: 1, actions: actions }));
    }

    return {
      name: "fixtures",
      describe: "Fixtures: fictional content with Decider's recorded answers from the committed exports",
      writes: "Saved in this browser only; nothing is sent to the courier or to Todoist",
      load: function () {
        return Promise.resolve({ data: fixtures, actions: read() });
      },
      record: function (action) {
        var actions = read();
        var recorded = JSON.parse(JSON.stringify(action));
        recorded.seq = actions.length ? actions[actions.length - 1].seq + 1 : 1;
        recorded.at = recorded.at || now();
        recorded.status = "saved";
        recorded.target = "prototype";
        actions.push(recorded);
        write(actions);
        return Promise.resolve(recorded);
      },
      reset: function () {
        storage.removeItem(key);
        return Promise.resolve();
      }
    };
  }

  // ServiceAdapter (P1): the courier's owner service on this machine. Items from GET /owner/pending (the live ledger:
  // unsorted tasks, with the owner's answer once given; holds ID only). A category goes to POST /owner/pick and is
  // "acknowledged" only when the courier answers that it updated the Todoist task; until then the page shows it
  // pending, and a refusal is "failed". Tags and review actions stay in this browser, as in P0.
  function ServiceAdapter(base, token, storage, fetchImpl, now) {
    var local = FixtureAdapter({ projects: [], items: [] }, storage, now, SERVICE_KEY);
    fetchImpl = fetchImpl || function (url, init) { return root.fetch(url, init); };
    now = now || function () { return new Date().toISOString(); };
    var items = {};

    function call(path, init) {
      init = init || {};
      init.headers = Object.assign({ Authorization: "Bearer " + token }, init.headers || {});
      return fetchImpl(base + path, init).then(function (response) {
        return response.json().then(function (body) { return { ok: response.ok, code: response.status, body: body }; },
          function () { return { ok: false, code: response.status, body: {} }; });
      });
    }

    return {
      name: "service",
      external: true,
      describe: "Owner service: the courier's live ledger on this machine (unsorted tasks with their text where " +
        "Telegram had it; holds ID only)",
      writes: "A category change goes to the courier, which updates the Todoist task; tags and review actions stay " +
        "in this browser",
      load: function () {
        return Promise.all([call("/owner/pending"), local.load()]).then(function (got) {
          var answer = got[0];
          if (!answer.ok) {
            throw new Error("the owner service answered " + answer.code);
          }
          var data = answer.body;
          items = {};
          var synthetic = [];
          data.items.forEach(function (item, i) {
            items[item.id] = item;
            if (item.resolved) { // the ledger's record of the owner's answer: acknowledged, by whichever channel
              synthetic.push({ seq: "ledger-" + i, item: item.id, type: "categorize", value: item.resolved.project,
                at: item.resolved.at, by: item.resolved.by, via: item.resolved.via || "telegram",
                status: "acknowledged", target: "todoist" });
            }
          });
          var kept = got[1].actions.filter(function (a) { return a.type !== "categorize"; });
          return { data: data, actions: synthetic.concat(kept) };
        });
      },
      record: function (action) {
        var item = items[action.item];
        if (action.type !== "categorize" || !item || !item.pick) {
          return local.record(action);
        }
        var out = JSON.parse(JSON.stringify(action));
        out.seq = "app-" + item.pick + "-" + action.value;
        out.at = now();
        out.target = "todoist";
        return call("/owner/pick", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sid: item.pick, project: action.value }) }).then(function (answer) {
          if (answer.ok && answer.body.status === "acknowledged") {
            out.status = "acknowledged";
          } else {
            out.status = "failed";
            out.error = answer.body.status === "already answered" ? "already " +
              (answer.body.project === "unsorted" ? "left unsorted" : "filed as " + answer.body.project) +
              (answer.body.via ? " via " + answer.body.via : "") : (answer.body.error || "the service answered " +
              answer.code);
          }
          return out;
        }, function (e) {
          out.status = "failed";
          out.error = "the owner service could not be reached";
          return out;
        });
      },
      reset: function () { return local.reset(); }
    };
  }

  function isSandbox(location) { // ?sandbox=1 only switches it on; on the public host nothing switches it off
    return SANDBOX_HOSTS.indexOf(location.hostname) >= 0 || /(?:^\?|&)sandbox=1(?:&|$)/.test(location.search || "");
  }

  function SandboxAdapter(fixtures, storage) {
    var adapter = FixtureAdapter(fixtures, storage, null, SANDBOX_KEY);
    adapter.sandbox = true;
    adapter.describe = "Interactive sandbox: fictional content with Decider's recorded answers from the committed " +
      "exports";
    adapter.writes = "Saved in this browser only, under the sandbox's own key; no model, courier, Todoist or " +
      "Telegram request is made";
    return adapter;
  }

  // The sandbox first, before any fragment is parsed or credential read. Then the service when the page came from it
  // (http) and holds its token; the fixtures otherwise. A token in the address's fragment (#token=...) is kept and
  // taken out of the address; a fragment never reaches a server.
  function choose(fixtures, storage, location, fetchImpl) {
    if (isSandbox(location)) {
      return SandboxAdapter(fixtures, storage);
    }
    var match = /(?:^#|&)token=([^&]+)/.exec(location.hash || "");
    if (match) {
      storage.setItem(TOKEN_KEY, decodeURIComponent(match[1]));
      try { // the token out of the address bar; where the browser refuses (a file's page), it is kept all the same
        root.history.replaceState(null, "", location.pathname + location.search);
      } catch (e) { /* nothing to undo */ }
    }
    var token = storage.getItem(TOKEN_KEY);
    if (token && /^https?:$/.test(location.protocol)) {
      return ServiceAdapter(location.origin, token, storage, fetchImpl);
    }
    return FixtureAdapter(fixtures, storage);
  }

  var api = { KEY: KEY, SERVICE_KEY: SERVICE_KEY, TOKEN_KEY: TOKEN_KEY, SANDBOX_KEY: SANDBOX_KEY,
    FixtureAdapter: FixtureAdapter, ServiceAdapter: ServiceAdapter, SandboxAdapter: SandboxAdapter, choose: choose,
    isSandbox: isSandbox, memoryStorage: memoryStorage, browserStorage: browserStorage };
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RatatoskrAdapter = api;
  }
})(this);
