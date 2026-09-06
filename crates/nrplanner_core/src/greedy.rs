//! Greedy solvers — ports of `_greedy_solve`, `_greedy_solve_once` and
//! `_greedy_cover_once`.
//!
//! Greedy is not just a fallback: its best score seeds the backtracker's
//! pruning threshold, so any divergence here silently changes how much of the
//! search space the exhaustive solver explores (and therefore the node count
//! the parity test compares).  Every tie-break below is deliberate.

use std::collections::HashSet;

use crate::inventory::Inventory;
use crate::score::score_profile;
use crate::state::{Delta, VesselState};

/// One layout over the free slots: per slot, the profile index or None, and
/// the context score it scored when placed.
pub type Assignment = Vec<(Option<usize>, i64)>;

pub struct GreedyCtx<'a> {
    pub inv: &'a Inventory,
    /// Per free slot, candidate profile indices in stable net-desc order.
    pub candidates: &'a [Vec<usize>],
    pub curse_max: i64,
}

/// Handles of the relics a layout places.
fn placed_key(inv: &Inventory, a: &Assignment) -> Vec<i64> {
    let mut k: Vec<i64> = a
        .iter()
        .filter_map(|&(p, _)| p.map(|p| inv.handle[p]))
        .collect();
    k.sort_unstable();
    k
}

/// `_greedy_solve_once`: fill each slot with the first strict maximum, in
/// candidate order, skipping already-used relics.
pub fn greedy_solve_once(
    ctx: &GreedyCtx,
    state: &mut VesselState,
    delta: &mut Delta,
    excluded: &HashSet<usize>,
) -> Assignment {
    let n = ctx.candidates.len();
    let mut assigned: Assignment = vec![(None, 0); n];
    let mut used: HashSet<usize> = excluded.clone();
    state.reset();

    for slot in 0..n {
        let mut best: Option<(i64, usize)> = None;
        for &p in &ctx.candidates[slot] {
            if used.contains(&p) {
                continue;
            }
            let score = score_profile(ctx.inv, p, state, ctx.curse_max);
            // Strict `>`: the FIRST maximum in candidate order wins.
            if best.is_none() || score > best.unwrap().0 {
                best = Some((score, p));
            }
        }
        match best {
            Some((score, p)) if score > 0 => {
                assigned[slot] = (Some(p), score);
                used.insert(p);
                state.place(ctx.inv, p, delta);
            }
            _ => assigned[slot] = (None, 0),
        }
    }
    assigned
}

/// `_greedy_solve`: up to `top_n` diverse greedy passes.
pub fn greedy_solve(ctx: &GreedyCtx, top_n: usize) -> Vec<Assignment> {
    let mut results: Vec<Assignment> = Vec::new();
    let mut excluded: HashSet<usize> = HashSet::new();
    let mut seen: HashSet<Vec<i64>> = HashSet::new();
    let mut state = VesselState::new(ctx.inv);
    let mut delta = Delta::default();

    for _ in 0..top_n {
        let assignment = greedy_solve_once(ctx, &mut state, &mut delta, &excluded);
        let key = placed_key(ctx.inv, &assignment);
        if key.is_empty() || seen.contains(&key) {
            break;
        }
        seen.insert(key);

        // Force diversity: drop the layout's best relic from the next pass.
        let mut best_handle: Option<i64> = None;
        let mut best_score = -1i64;
        let mut best_profile = 0usize;
        for &(p, score) in &assignment {
            if let Some(p) = p {
                if score > best_score {
                    best_score = score;
                    best_handle = Some(ctx.inv.handle[p]);
                    best_profile = p;
                }
            }
        }
        results.push(assignment);
        // Python guards on `if best_handle:` — a ga_handle of 0 is falsy and
        // is therefore never excluded.  Reproduced exactly.
        if let Some(h) = best_handle {
            if h != 0 {
                excluded.insert(best_profile);
            }
        }
    }
    results
}

/// `_greedy_cover_once`: a greedy fill that guarantees Required coverage when
/// it succeeds, using suffix-union feasibility to keep later slots able to
/// cover what is still missing.  `None` when some slot has no feasible option.
#[allow(clippy::too_many_arguments)]
pub fn greedy_cover_once(
    ctx: &GreedyCtx,
    req_suffix_union: &[u64],
    req_full_mask: u64,
    req_initial_mask: u64,
) -> Option<Assignment> {
    let n = ctx.candidates.len();
    let mut assigned: Assignment = vec![(None, 0); n];
    let mut used: HashSet<usize> = HashSet::new();
    let mut state = VesselState::new(ctx.inv);
    let mut delta = Delta::default();
    let mut mask = req_initial_mask;

    for slot in 0..n {
        let rest = req_suffix_union[slot + 1];
        let empty_ok = (mask | rest) == req_full_mask;
        // (score, new_bits, profile) — strict lexicographic, first wins ties.
        let mut best: Option<(i64, u32, usize)> = None;
        for &p in &ctx.candidates[slot] {
            if used.contains(&p) {
                continue;
            }
            let pmask = ctx.inv.req_mask[p];
            if (mask | pmask | rest) != req_full_mask {
                continue;
            }
            let score = score_profile(ctx.inv, p, &state, ctx.curse_max);
            let new_bits = (pmask & !mask).count_ones();
            if best.is_none() || (score, new_bits) > (best.unwrap().0, best.unwrap().1) {
                best = Some((score, new_bits, p));
            }
        }

        let Some((score, _new_bits, p)) = best else {
            if !empty_ok {
                return None; // no single candidate keeps this slot feasible
            }
            assigned[slot] = (None, 0);
            continue;
        };
        if empty_ok && score <= 0 {
            assigned[slot] = (None, 0);
            continue;
        }
        assigned[slot] = (Some(p), score);
        used.insert(p);
        state.place(ctx.inv, p, &mut delta);
        mask |= ctx.inv.req_mask[p];
    }

    if mask == req_full_mask {
        Some(assigned)
    } else {
        None
    }
}
