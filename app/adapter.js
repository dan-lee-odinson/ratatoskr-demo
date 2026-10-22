/* Copyright 2026 Dan Lee-Odinson. Licensed under the Apache License, Version 2.0; see LICENSE.

   Where the companion app's items come from and where the owner's actions go (Dan's ruling 61). One contract, so a
   view never knows which it has:

     load()          -> Promise of {data: {projects, items}, actions: [...]}, the actions in the order recorded
     record(action)  -> Promise of the action as recorded: with its seq, its time and its status
                        ("saved": prototype state; "pending", "acknowledged" or "failed": an external update)
     reset()         -> Promise; clears what this adapter recorded
     name, describe  -> what the app tells the owner about its data and its writes

   FixtureAdapter (P0): the fixtures drawn from the committed exports, and the owner's actions in the browser's local
   storage, so they persist across navigation, a refresh and a restart. Every action it records is "saved": prototype
   state, never an external write. */
(function (root) {
  "use strict";

  var KEY = "ratatoskr-companion/v1";

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

  function FixtureAdapter(fixtures, storage, now) {
    storage = storage || memoryStorage();
    now = now || function () { return new Date().toISOString(); };

    function read() {
      try {
        var raw = storage.getItem(KEY);
        var parsed = raw ? JSON.parse(raw) : null;
        return parsed && Array.isArray(parsed.actions) ? parsed.actions : [];
      } catch (e) {
        return [];
      }
    }

    function write(actions) {
      storage.setItem(KEY, JSON.stringify({ version: 1, actions: actions }));
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
        storage.removeItem(KEY);
        return Promise.resolve();
      }
    };
  }

  var api = { KEY: KEY, FixtureAdapter: FixtureAdapter, memoryStorage: memoryStorage, browserStorage: browserStorage };
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RatatoskrAdapter = api;
  }
})(this);
