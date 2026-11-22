"use strict";
(() => {
  // One conversation built from the windows a page shows while it scrolls: the recording (createRecorder
  // below) and the background site sync (captureConversation in content.js) both add each visible window.
  // Identity, order and continuity are judged separately:
  // - a row with a source key (a site message ID) is that message wherever it shows; its text may grow;
  // - a source order (a site turn number) identifies and orders a row the same way;
  // - a row with neither is matched by role and text, and only through the window's overlap with what is
  //   already recorded: the window joins where that overlap lines up in exactly one place. Text alone, a
  //   long common prefix or a repeated short reply never proves that two rows are one message.
  // A window that lines up nowhere starts a separate fragment (a gap); a later window that overlaps two
  // fragments joins them. A window that lines up in more than one place is skipped: the next window, with
  // more context, usually settles it.
  const same = (recorded, seen, growing) => {
    if (recorded.role !== seen.role) return false;
    if (recorded.source_key || seen.source_key) return recorded.source_key === seen.source_key;
    if (Number.isInteger(recorded.source_order) && Number.isInteger(seen.source_order)) {
      return recorded.source_order === seen.source_order;
    }
    if (recorded.text === seen.text) return true;
    // A reply still being written grows at the very end: the last row of both the record and the window.
    return growing && recorded.role === "assistant"
      && (recorded.text.startsWith(seen.text) || seen.text.startsWith(recorded.text));
  };
  const identity = row => row.source_key ? "id:" + row.source_key
    : Number.isInteger(row.source_order) ? "order:" + row.role + ":" + row.source_order : "";
  // Offsets at which window lines up with record: every overlapping pair is the same message, the overlap is
  // not empty, and no identified row of the window sits anywhere else in the record.
  const placements = (record, window) => {
    const known = new Map();
    record.forEach((row, index) => { const id = identity(row); if (id) known.set(id, index); });
    const found = [];
    for (let offset = 1 - window.length; offset < record.length; offset += 1) {
      let overlap = 0, valid = true;
      for (let index = 0; index < window.length && valid; index += 1) {
        const at = offset + index, id = identity(window[index]);
        if (id && known.has(id) && known.get(id) !== at) valid = false;
        else if (at >= 0 && at < record.length) {
          const growing = at === record.length - 1 && index === window.length - 1;
          if (same(record[at], window[index], growing)) overlap += 1;
          else valid = false;
        }
      }
      if (valid && overlap) found.push(offset);
    }
    return found;
  };
  const combine = (record, window, offset) => {
    const result = [];
    for (let at = Math.min(0, offset); at < Math.max(record.length, offset + window.length); at += 1) {
      const recorded = at >= 0 && at < record.length ? record[at] : null;
      const seen = at - offset >= 0 && at - offset < window.length ? window[at - offset] : null;
      if (recorded && seen) {
        // Keep the longer text: the newer one while a reply grows, the fuller one if a window cut it.
        result.push(seen.text.length >= recorded.text.length ? {...recorded, ...seen} : {...seen, ...recorded});
      } else result.push({...(recorded || seen)});
    }
    return result;
  };
  const sequenceKey = rows => rows.map(row => identity(row) || row.role + "\0" + row.text).join("\u0001");
  // The one way window joins record, "AMBIGUOUS" if it lines up in places that give different results, or
  // null if it lines up nowhere.
  const join = (record, window) => {
    const offsets = placements(record, window);
    if (!offsets.length) return null;
    const results = offsets.map(offset => combine(record, window, offset));
    const first = sequenceKey(results[0]);
    return results.every(rows => sequenceKey(rows) === first) ? results[0] : "AMBIGUOUS";
  };
  const createMerger = ({maxRows = 10000, maxCharacters = 10000000} = {}) => {
    let fragments = [], ambiguous = 0;
    const checkSize = (candidate) => {
      const rows = candidate.flat();
      if (rows.length > maxRows || rows.reduce((total, row) => total + row.text.length, 0) > maxCharacters) {
        throw Error("RECORDING_SIZE_LIMIT");
      }
    };
    return {
      // Returns "JOINED", "GAP" (kept as a new fragment unless keepGaps is false), "AMBIGUOUS" or "EMPTY".
      add(rows, {keepGaps = true} = {}) {
        const window = rows.filter(row => row.text && ["user", "assistant"].includes(row.role))
          .map(({role, text, source_key, source_order}) => ({role, text,
            ...(source_key ? {source_key} : {}), ...(Number.isInteger(source_order) ? {source_order} : {})}));
        if (!window.length) return "EMPTY";
        if (!fragments.length) { checkSize([window]); fragments = [window]; return "JOINED"; }
        const joined = fragments.map(fragment => join(fragment, window));
        if (joined.includes("AMBIGUOUS")) { ambiguous += 1; return "AMBIGUOUS"; }
        const target = joined.findIndex(Boolean);
        if (target < 0) {
          if (!keepGaps) return "GAP";
          checkSize([...fragments, window]);
          fragments = [...fragments, window];
          return "GAP";
        }
        // The window may also bridge to other fragments: join every one that now lines up with it.
        let merged = joined[target], rest = fragments.filter((_, index) => index !== target), changed = true;
        while (changed) {
          changed = false;
          for (const fragment of rest) {
            const bridged = join(merged, fragment);
            if (bridged && bridged !== "AMBIGUOUS") {
              merged = bridged; rest = rest.filter(other => other !== fragment); changed = true; break;
            }
          }
        }
        checkSize([merged, ...rest]);
        fragments = [merged, ...rest];
        return "JOINED";
      },
      // The messages to save, with how much was left out. Fragments are put in order only when every row has
      // a site turn number; otherwise the largest fragment that holds both questions and answers is kept (one that
      // holds a single role cannot be imported) and the others are counted as unplaced.
      result() {
        if (!fragments.length) return {messages: [], unplaced_count: 0, gap_count: 0, ambiguous_count: ambiguous};
        const numbered = fragments.every(fragment => fragment.every(row => Number.isInteger(row.source_order)));
        if (numbered) {
          const ordered = [...fragments].sort((left, right) => left[0].source_order - right[0].source_order);
          return {messages: ordered.flat().map(row => ({...row})), unplaced_count: 0,
            gap_count: fragments.length - 1, ambiguous_count: ambiguous};
        }
        const importable = fragments.filter(fragment => fragment.some(row => row.role === "user") &&
          fragment.some(row => row.role === "assistant"));
        const kept = (importable.length ? importable : fragments)
          .reduce((best, fragment) => fragment.length > best.length ? fragment : best);
        return {messages: kept.map(row => ({...row})),
          unplaced_count: fragments.flat().length - kept.length,
          gap_count: fragments.length - 1, ambiguous_count: ambiguous};
      },
      fragmentCount: () => fragments.length
    };
  };

  // A window cut where the page has not drawn a message yet (content.js marks it unread): the parts on either
  // side go to the merger on their own, so a missing answer never sits as a join between its neighbours and a
  // later window can still put it in its place.
  const windowParts = (rows) => {
    const parts = [[]];
    for (const row of rows) {
      if (row.unread) parts.push([]);
      else parts[parts.length - 1].push(row);
    }
    return parts.filter(part => part.length);
  };

  // Scroll events can fire dozens of times a second, and each sample walks the
  // whole conversation DOM. Unthrottled, long conversations stuttered while
  // recording. The 250 ms timer still samples whatever the scroll skipped.
  const MIN_SCROLL_SAMPLE_GAP_MS = 100;
  const createRecorder = ({capture,validate,finish,now=()=>Date.now(),schedule=setInterval,cancel=clearInterval}) => {
    let state = null, timer = null, merger = null, base = null, lastSampleAt = 0;
    const status = () => {
      if (!state) return {status:"IDLE",message_count:0,unplaced_count:0};
      const merged = merger.result();
      return {...state, message_count:merged.messages.length, unplaced_count:merged.unplaced_count,
        remaining_seconds:Math.max(0,Math.ceil((state.deadline-now())/1000))};
    };
    const stop = async (reason="USER_STOP") => {
      if (!state || state.status !== "RECORDING") return status();
      state.status="SAVING";state.stop_reason=reason;
      if (timer!==null) cancel(timer);timer=null;
      try {
        // The import needs both roles; a record of one role (or none) is not a conversation yet.
        const messages = merger.result().messages.map((r,index)=>({role:r.role,text:r.text,
          message_id:r.source_key ? "source-"+r.source_key : "scroll-"+index+"-"+r.role}));
        const users = messages.filter(r=>r.role==="user").length, answers = messages.length-users;
        if (!messages.length) throw Error("CURRENT_PAGE_CONVERSATION_NOT_FOUND");
        if (!users || !answers) {
          // Read, but the page's questions and answers could not be told apart (a site whose markup the
          // reader does not know): report the counts rather than "no conversation".
          state.user_count=users;state.assistant_count=answers;
          throw Error("CURRENT_PAGE_ROLES_UNCLEAR");
        }
        const reply=await finish({ ...base, messages,
          error_code:"SCROLL_RECORDING_VISIBLE_WINDOWS_ONLY",recording_stop_reason:reason},state.token);
        if (!reply?.ok) throw Error(reply?.error_code || "RECORDING_SAVE_FAILED");
        state.status="SAVED";
      } catch(e) { state.status="ERROR";state.error_code=String(e?.message || "RECORDING_SAVE_FAILED"); }
      return status();
    };
    const sample = (force=false) => {
      if (state?.status !== "RECORDING") return;
      if (now()>=state.deadline) { void stop("TIME_LIMIT");return; }
      if (force!==true && now()-lastSampleAt<MIN_SCROLL_SAMPLE_GAP_MS) return;
      lastSampleAt=now();
      try {
        validate(base.source_url);
        const page=capture();
        if (page.source_url!==base.source_url) { void stop("PAGE_CHANGED");return; }
        windowParts(page.messages).forEach(part => merger.add(part));
      } catch(e) {
        const code=String(e?.message || "");
        if (code==="CURRENT_PAGE_CONVERSATION_NOT_FOUND") return;
        void stop(code.includes("SIZE_LIMIT")?"SIZE_LIMIT":"PAGE_CHANGED_OR_UNAVAILABLE");
      }
    };
    return {
      start({token,deadline}) {
        if (state?.status==="RECORDING" || state?.status==="SAVING") return status();
        validate();
        base=capture();merger=createMerger();windowParts(base.messages).forEach(part => merger.add(part));
        state={status:"RECORDING",token,deadline:Math.min(Number(deadline),now()+600000),started_at:now()};
        timer=schedule(()=>sample(true),250);
        return status();
      },sample,stop,status
    };
  };
  globalThis.MEMORIVE_SCROLL_RECORDING = {createMerger,createRecorder};
})();
