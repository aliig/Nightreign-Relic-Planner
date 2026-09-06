//! The exhaustive backtracking solver — port of `_backtrack_solve`.
//!
//! Roughly 83% of solve CPU lived in the Python original.  Everything here is
//! a deliberate transcription: the prune order, the node counting, the clock
//! sampling cadence, the threshold floor and the tie order all affect which
//! optima come back and in what order, and the differential parity test
//! compares the node count as well as the results.

use std::collections::HashSet;
use std::time::Instant;

use crate::greedy::Assignment;
use crate::helpers::{has_orphaned_excl_category, CtxHelpers};
use crate::inventory::Inventory;
use crate::score::score_profile;
use crate::state::{Delta, VesselState};

/// Vessels have at most 6 slots (3 standard + 3 deep).
const MAX_SLOTS: usize = 6;

/// A layout's identity for dedup: its placed profile indices, sorted, padded
/// with `u32::MAX`.  Profile index and ga_handle are in bijection within one
/// inventory (the profile memo is handle-keyed), so this is the same key as
/// Python's `frozenset(used)`.
type LayoutKey = [u32; MAX_SLOTS];

/// Monotonic deadline.
///
/// `time.time()` in Python; `Instant` here, which differs only across wall
/// clock jumps.  A negative budget trips on the very first node, which is what
/// the truncation tests rely on — so the budget is never turned into a
/// `Duration` (that would panic on a negative float).
pub struct Deadline {
    start: Instant,
    budget_secs: f64,
}

impl Deadline {
    pub fn new(budget_secs: f64) -> Self {
        Deadline {
            start: Instant::now(),
            budget_secs,
        }
    }

    #[inline]
    pub fn expired(&self) -> bool {
        self.start.elapsed().as_secs_f64() > self.budget_secs
    }
}

/// The best `top_n` layouts, score-descending.
///
/// Python appends then stable-sorts descending, which lands a new entry after
/// every entry with a score >= its own — exactly `partition_point`.
struct TopList {
    entries: Vec<(i64, Assignment)>,
    top_n: usize,
}

impl TopList {
    fn new(top_n: usize) -> Self {
        TopList {
            entries: Vec::with_capacity(top_n + 1),
            top_n,
        }
    }

    fn insert(&mut self, score: i64, assignment: Assignment) -> Option<Assignment> {
        let at = self.entries.partition_point(|e| e.0 >= score);
        self.entries.insert(at, (score, assignment));
        if self.entries.len() > self.top_n {
            return Some(self.entries.pop().unwrap().1);
        }
        None
    }

    fn min_score(&self) -> i64 {
        self.entries.last().map(|e| e.0).unwrap_or(0)
    }

    fn len(&self) -> usize {
        self.entries.len()
    }
}

pub struct BacktrackParams<'a> {
    pub inv: &'a Inventory,
    pub candidates: &'a [Vec<usize>],
    pub top_n: usize,
    pub curse_max: i64,
    pub initial_threshold: i64,
    pub deadline_secs: f64,
    pub validate_leaves: bool,
    pub constrained: bool,
    pub req_suffix_union: &'a [u64],
    pub req_full_mask: u64,
    pub req_initial_mask: u64,
}

struct Search<'a> {
    inv: &'a Inventory,
    candidates: &'a [Vec<usize>],
    num_slots: usize,
    top_n: usize,
    curse_max: i64,
    initial_threshold: i64,
    validate_leaves: bool,
    constrained: bool,
    req_suffix_union: &'a [u64],
    req_full_mask: u64,

    helpers: CtxHelpers,
    deadline: Deadline,

    /// Suffix sums of the best positive pre-score available per slot.
    suffix_pos: Vec<i64>,
    /// Per slot, suffix maxima of the pos bounds over the net-ordered list.
    suffix_max: Vec<Vec<i64>>,

    state: VesselState,
    deltas: Vec<Delta>,
    used: Vec<bool>,
    placed: Vec<u32>,
    current: Assignment,

    top: TopList,
    seen: HashSet<LayoutKey>,
    min_threshold: i64,
    truncated: bool,
    nodes: u64,
}

impl<'a> Search<'a> {
    fn layout_key(&self) -> LayoutKey {
        let mut key = [u32::MAX; MAX_SLOTS];
        key[..self.placed.len()].copy_from_slice(&self.placed);
        key[..self.placed.len()].sort_unstable();
        key
    }

    fn backtrack(&mut self, slot_idx: usize, score: i64, req_mask: u64) {
        self.nodes += 1;
        // Sample the clock on the first node and every 1024 thereafter — the
        // per-node call cost was measurable on its own.  Checking the first
        // node keeps an already-expired deadline truncating immediately
        // (callers pass one deliberately).  `truncated` is still read every
        // node so the unwind stays prompt once tripped.
        if (self.nodes - 1) & 1023 == 0 && self.deadline.expired() {
            self.truncated = true;
        }
        if self.truncated {
            return;
        }

        if self.constrained
            && (req_mask | self.req_suffix_union[slot_idx]) != self.req_full_mask
        {
            return; // remaining slots can't cover the missing requirements
        }

        if slot_idx == self.num_slots {
            self.record_leaf(score);
            return;
        }

        let remaining_max = self.suffix_pos[slot_idx + 1];
        let n_cands = self.candidates[slot_idx].len();
        for ci in 0..n_cands {
            if score + self.suffix_max[slot_idx][ci] + remaining_max <= self.min_threshold {
                break; // no remaining candidate's pos bound can clear it
            }
            let p = self.candidates[slot_idx][ci];
            if self.used[p] {
                continue;
            }
            if score + self.inv.pos_bound[p] + remaining_max <= self.min_threshold {
                continue; // admissible upper-bound prune
            }

            let ctx_score = score_profile(self.inv, p, &self.state, self.curse_max);
            if ctx_score <= 0 {
                // An empty slot is at least as good UNLESS this relic provides
                // a still-missing requirement (it can be mandatory at a net
                // loss), or can still raise later relics' scores.
                let carries_requirement =
                    self.constrained && (self.inv.req_mask[p] & !req_mask) != 0;
                if !carries_requirement && !self.helpers.still_useful(p, &self.state) {
                    continue;
                }
            }
            if score + ctx_score + remaining_max <= self.min_threshold {
                continue; // actual-score prune
            }

            self.current[slot_idx] = (Some(p), ctx_score);
            self.used[p] = true;
            self.placed.push(p as u32);
            let mut delta = std::mem::take(&mut self.deltas[slot_idx]);
            self.state.place(self.inv, p, &mut delta);

            let next_mask = if self.constrained {
                req_mask | self.inv.req_mask[p]
            } else {
                0
            };
            self.backtrack(slot_idx + 1, score + ctx_score, next_mask);

            self.state.unplace(self.inv, p, &delta);
            self.deltas[slot_idx] = delta;
            self.placed.pop();
            self.used[p] = false;
        }

        // Try the empty slot last (score 0 — worst case), unconditionally:
        // no bound check here, which affects both the node count and tie order.
        self.current[slot_idx] = (None, 0);
        self.backtrack(slot_idx + 1, score, req_mask);
    }

    fn record_leaf(&mut self, score: i64) {
        if !(score > self.min_threshold || self.top.len() < self.top_n) {
            return;
        }
        let key = self.layout_key();
        if self.seen.contains(&key) {
            return;
        }
        // Reject layouts that orphan or priority-block a desired
        // excluded-category effect — keeping them out of `top` stops invalid
        // layouts from inflating the pruning threshold past valid optima.
        // Rejected leaves are deliberately NOT added to `seen`.
        if self.validate_leaves && has_orphaned_excl_category(self.inv, &self.current) {
            return;
        }
        self.seen.insert(key);
        if let Some(dropped) = self.top.insert(score, self.current.clone()) {
            let mut removed = [u32::MAX; MAX_SLOTS];
            let mut n = 0;
            for &(p, _) in &dropped {
                if let Some(p) = p {
                    removed[n] = p as u32;
                    n += 1;
                }
            }
            removed[..n].sort_unstable();
            self.seen.remove(&removed);
        }
        // While `top` isn't full, fall back to -1 — but never above
        // initial_threshold: an unseeded constrained search may have a
        // NEGATIVE covering optimum, and a -1 threshold would prune the path
        // to it.
        self.min_threshold = if self.top.len() == self.top_n {
            self.top.min_score()
        } else {
            (-1).min(self.initial_threshold)
        };
    }
}

/// Returns `(layouts, truncated, nodes)`.
pub fn backtrack_solve(p: BacktrackParams) -> (Vec<Assignment>, bool, u64) {
    let num_slots = p.candidates.len();

    // Admissible remaining-score bound: best POSITIVE pre-score per slot, as
    // suffix sums.  Candidates are net-ordered, so the per-slot max must be
    // computed explicitly rather than taken from the list head.
    let mut suffix_pos = vec![0i64; num_slots + 1];
    for s in (0..num_slots).rev() {
        let slot_max = p.candidates[s]
            .iter()
            .map(|&c| p.inv.pos_bound[c])
            .max()
            .unwrap_or(0);
        suffix_pos[s] = suffix_pos[s + 1] + slot_max;
    }

    // Per-slot suffix max of pos bounds over the net-ordered list: once even
    // the best remaining candidate can't clear the threshold, the whole tail
    // is prunable with a break instead of per-candidate continues.
    let suffix_max: Vec<Vec<i64>> = p
        .candidates
        .iter()
        .map(|c| {
            let mut smax = vec![0i64; c.len() + 1];
            for j in (0..c.len()).rev() {
                smax[j] = smax[j + 1].max(p.inv.pos_bound[c[j]]);
            }
            smax
        })
        .collect();

    let mut search = Search {
        inv: p.inv,
        candidates: p.candidates,
        num_slots,
        top_n: p.top_n,
        curse_max: p.curse_max,
        initial_threshold: p.initial_threshold,
        validate_leaves: p.validate_leaves,
        constrained: p.constrained,
        req_suffix_union: p.req_suffix_union,
        req_full_mask: p.req_full_mask,
        helpers: CtxHelpers::build(p.inv, p.candidates),
        deadline: Deadline::new(p.deadline_secs),
        suffix_pos,
        suffix_max,
        state: VesselState::new(p.inv),
        deltas: (0..num_slots).map(|_| Delta::default()).collect(),
        used: vec![false; p.inv.len()],
        placed: Vec::with_capacity(num_slots),
        current: vec![(None, 0); num_slots],
        top: TopList::new(p.top_n),
        seen: HashSet::new(),
        min_threshold: p.initial_threshold,
        truncated: false,
        nodes: 0,
    };

    search.backtrack(0, 0, p.req_initial_mask);

    // The all-empty leaf enters top/seen like any other and is dropped only
    // here, at the end.
    let layouts = search
        .top
        .entries
        .into_iter()
        .filter(|(_, a)| a.iter().any(|&(r, _)| r.is_some()))
        .map(|(_, a)| a)
        .collect();
    (layouts, search.truncated, search.nodes)
}
