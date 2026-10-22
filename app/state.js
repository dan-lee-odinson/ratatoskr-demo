/* Copyright 2026 Dan Lee-Odinson. Licensed under the Apache License, Version 2.0; see LICENSE.

   The companion app prototype's state (Dan's ruling 61): the items (fixtures, or the owner service's live list) and
   the owner's recorded actions, folded into what each view shows. No DOM here, so the tests drive it directly.

   The courier's rules, kept: a sensitive hold has no release; an unsure hold may be released by the owner, which is
   an owner authorization, not a model clear, and resumes the remaining checks; an unsure scene is answered
   Commitment or Reference; a category is a `proj:` label on the existing task, and the owner's first answer stands,
   as with the Telegram buttons. Privacy, scene and category uncertainty are three states with different actions. */
(function (root) {
  "use strict";

  var LEAVE = "unsorted";

  function clone(x) {
    return JSON.parse(JSON.stringify(x));
  }

  function pct(p) {
    return typeof p === "number" ? p.toFixed(2) : "-";
  }

  // An item with the owner's actions applied: what it is now, and by whom.
  function fresh(item) {
    var view = clone(item);
    view.resolution = null; // a hold kept or released, a scene answered
    view.ownerCategory = null; // the owner's category answer: {value, status, at}
    view.tags = []; // the owner's tags (prototype state unless a service writes them; none does)
    view.actions = [];
    return view;
  }

  function apply(view, action) {
    view.actions.push(action);
    if (action.status === "failed") {
      return; // recorded, shown, never applied
    }
    switch (action.type) {
      case "keep-held":
        if (!view.resolution) {
          view.resolution = { type: "kept-held", at: action.at };
        }
        break;
      case "release":
        view.resolution = { type: "released", at: action.at };
        break;
      case "scene":
        if (!view.resolution) {
          view.resolution = { type: "scene", value: action.value, at: action.at };
        }
        break;
      case "categorize":
        if (!view.ownerCategory) {
          view.ownerCategory = { value: action.value, status: action.status, at: action.at };
        }
        break;
      case "tag-add":
        if (view.tags.indexOf(action.value) < 0) {
          view.tags.push(action.value);
        }
        break;
      case "tag-remove":
        view.tags = view.tags.filter(function (t) { return t !== action.value; });
        break;
    }
  }

  // The state every view reads: items in their fixture order, each with the owner's actions applied.
  function build(data, actions) {
    var views = {};
    var order = [];
    (data.items || []).forEach(function (item) {
      views[item.id] = fresh(item);
      order.push(item.id);
    });
    (actions || []).forEach(function (a) {
      if (views[a.item]) {
        apply(views[a.item], a);
      }
    });
    return { projects: data.projects || [], views: views, order: order, actions: (actions || []).slice() };
  }

  function category(view) {
    if (view.kind !== "task") {
      return null;
    }
    if (view.ownerCategory) {
      return { value: view.ownerCategory.value, by: "owner", status: view.ownerCategory.status };
    }
    if (view.project.sure) {
      return { value: view.project.choice, by: "decider" };
    }
    return { value: LEAVE, by: null }; // Decider unsure and no answer yet: unsorted, as the courier writes it
  }

  // What the owner can do with an item now. A sensitive hold and an unsure hold differ: only the unsure one has
  // a Release, and a sensitive one never does.
  function availableActions(view) {
    if (view.kind === "hold") {
      var out = ["review"];
      if (!view.resolution) {
        out.push("keep-held");
      }
      if (view.verdict === "unsure" && !(view.resolution && view.resolution.type === "released")) {
        out.push("release");
      }
      return out;
    }
    if (view.kind === "scene") {
      return view.resolution ? ["review"] : ["review", "commitment", "reference"];
    }
    if (view.kind === "task") {
      return view.ownerCategory ? ["open", "tag"] : ["open", "tag", "categorize", "leave-unsorted"];
    }
    return [];
  }

  function inNeedsReview(view) {
    if (view.kind === "hold" || view.kind === "scene") {
      return !view.resolution;
    }
    return view.kind === "task" && !view.project.sure && !view.ownerCategory;
  }

  function needsReview(state) {
    return state.order.map(function (id) { return state.views[id]; }).filter(inNeedsReview);
  }

  // Items the owner has resolved, newest first: a review kept or released, a scene answered, a category chosen.
  function reviewed(state) {
    return state.order.map(function (id) { return state.views[id]; })
      .filter(function (v) { return v.resolution || (v.kind === "task" && v.ownerCategory); })
      .sort(function (a, b) { return last(b) - last(a); });
  }

  function last(view) {
    return view.actions.length ? view.actions[view.actions.length - 1].seq : 0;
  }

  function normaliseTag(tag) {
    return String(tag || "").trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "").slice(0, 32);
  }

  function tasks(state, filter) {
    filter = filter || {};
    return state.order.map(function (id) { return state.views[id]; }).filter(function (v) {
      if (v.kind !== "task") {
        return false;
      }
      if (filter.category && category(v).value !== filter.category) {
        return false;
      }
      return !(filter.tag && v.tags.indexOf(filter.tag) < 0);
    });
  }

  function tagsInUse(state) {
    var seen = [];
    tasks(state).forEach(function (v) {
      v.tags.forEach(function (t) {
        if (seen.indexOf(t) < 0) {
          seen.push(t);
        }
      });
    });
    return seen.sort();
  }

  // What a Save, a tap or a review action would record: nothing when it would change nothing, so a repeated Save
  // records no second action. `change` holds any of: category (a project or "unsorted"), tags (the wanted list),
  // action ("keep-held", "release", "commitment", "reference").
  function propose(state, itemId, change) {
    var view = state.views[itemId];
    var out = [];
    if (!view) {
      return out;
    }
    var allowed = availableActions(view);
    if (change.action) {
      if (change.action === "commitment" || change.action === "reference") {
        if (allowed.indexOf(change.action) >= 0) {
          out.push({ type: "scene", value: change.action });
        }
      } else if (allowed.indexOf(change.action) >= 0) {
        out.push({ type: change.action });
      }
    }
    if (view.kind === "task" && change.category) {
      var current = category(view);
      var wanted = change.category === LEAVE || state.projects.indexOf(change.category) >= 0;
      // the first owner answer stands; for a task Decider was sure of, only a different category is an answer
      if (wanted && !view.ownerCategory && (!view.project.sure || change.category !== current.value)) {
        out.push({ type: "categorize", value: change.category });
      }
    }
    if (view.kind === "task" && change.tags) {
      var want = [];
      change.tags.map(normaliseTag).forEach(function (t) {
        if (t && want.indexOf(t) < 0) {
          want.push(t);
        }
      });
      want.forEach(function (t) {
        if (view.tags.indexOf(t) < 0) {
          out.push({ type: "tag-add", value: t });
        }
      });
      view.tags.forEach(function (t) {
        if (want.indexOf(t) < 0) {
          out.push({ type: "tag-remove", value: t });
        }
      });
    }
    return out.map(function (a) {
      a.item = itemId;
      a.by = "owner";
      a.via = "app";
      return a;
    });
  }

  var STATUS = {
    saved: "Saved in the prototype; not sent to Todoist",
    pending: "Pending: waiting for the courier's acknowledgment",
    acknowledged: "Acknowledged: the courier updated the Todoist task",
    failed: "Failed: not applied",
    local: "Saved in app, not in Todoist",
    prototype: "Prototype only: the courier was not told"
  };

  // How an action's outcome reads on a card and in Activity. A saved prototype result and an acknowledged Todoist
  // update are different statuses.
  function statusText(action) {
    if (action.status === "saved" && (action.type === "tag-add" || action.type === "tag-remove")) {
      return STATUS.local;
    }
    if (action.status === "saved" && action.type !== "categorize") {
      return STATUS.prototype;
    }
    return (STATUS[action.status] || action.status) + (action.status === "failed" && action.error ? " (" + action.error + ")" : "");
  }

  // What the model said about an item, as recorded: kept apart from what the owner chose.
  function modelLine(view) {
    if (view.kind === "hold") {
      return "Decider (" + view.gate + "): " + view.verdict + ", p " + pct(view.p) + " (floor " + pct(view.floor) + ")";
    }
    if (view.kind === "scene") {
      return "Decider (scene): unsure, top " + view.scene.choice + ", confidence " + pct(view.scene.confidence) +
        " (floor " + pct(view.scene.floor) + ")";
    }
    if (view.kind === "task") {
      return "Decider (project): " + (view.project.sure ? "" : "unsure, top ") + view.project.choice +
        ", confidence " + pct(view.project.confidence) + " (floor " + pct(view.project.floor) + ")";
    }
    return "";
  }

  function ownerLine(action) {
    switch (action.type) {
      case "keep-held": return "Owner kept it held after private review (simulated viewer)";
      case "release": return "Owner released the unsure hold (owner authorization; the remaining checks resume)";
      case "scene": return "Owner chose " + (action.value === "commitment" ? "Commitment" : "Reference");
      case "categorize": return action.value === LEAVE ? "Owner left it unsorted" : "Owner chose " + action.value;
      case "tag-add": return "Owner added tag \"" + action.value + "\"";
      case "tag-remove": return "Owner removed tag \"" + action.value + "\"";
    }
    return "Owner: " + action.type;
  }

  // Activity, newest first: each entry keeps Decider's recorded answer apart from the owner's choice.
  function activity(state) {
    return state.actions.slice().reverse().map(function (a) {
      var view = state.views[a.item];
      return {
        seq: a.seq, at: a.at, item: a.item, ref: view ? view.ref : a.item, input: view ? view.input : "",
        model: view && a.type.indexOf("tag-") !== 0 ? modelLine(view) : null,
        owner: ownerLine(a), status: a.status, statusText: statusText(a)
      };
    });
  }

  var api = {
    LEAVE: LEAVE, STATUS: STATUS, build: build, availableActions: availableActions, needsReview: needsReview,
    reviewed: reviewed, tasks: tasks, tagsInUse: tagsInUse, category: category, propose: propose,
    activity: activity, modelLine: modelLine, ownerLine: ownerLine, statusText: statusText,
    normaliseTag: normaliseTag, pct: pct
  };
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RatatoskrState = api;
  }
})(this);
