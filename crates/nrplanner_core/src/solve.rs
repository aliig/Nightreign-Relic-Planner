//! The solver seam: candidates in, ranked free-slot layouts out.
//!
//! Port of `VesselOptimizer._solve_free_slots_python` from the greedy pass
//! onward (candidate selection stays in Python — it needs game data and
//! Pydantic).  Greedy first for a pruning floor, then the exhaustive
//! backtracker, then merge and the requirement-covering filter.

use std::collections::HashSet;

use crate::backtrack::{backtrack_solve, BacktrackParams};
use crate::greedy::{greedy_cover_once, greedy_solve, Assignment, GreedyCtx};
use crate::inventory::Inventory;

/// Sentinel for "the search is unseeded": a constrained solve with no covering
/// layout yet must not prune anything, and its optimum may be negative.
const UNSEEDED_THRESHOLD: i64 = -(1i64 << 60);

pub struct SolveParams<'a> {
    pub inv: &'a Inventory,
    /// Per free slot, candidate profile indices in stable net-desc order.
    pub candidates: &'a [Vec<usize>],
    pub top_n: usize,
    pub curse_max: i64,
    pub deadline_secs: f64,
    pub validate_leaves: bool,
    pub req_full_mask: u64,
    pub req_initial_mask: u64,
}

fn total_score(a: &Assignment) -> i64 {
    a.iter().map(|&(_, s)| s).sum()
}

fn covers(inv: &Inventory, a: &Assignment, initial: u64, full: u64) -> bool {
    let mut m = initial;
    for &(p, _) in a {
        if let Some(p) = p {
            m |= inv.req_mask[p];
        }
    }
    m == full
}

/// The handles a layout places, sorted — the merge/dedup key.
fn layout_handles(inv: &Inventory, a: &Assignment) -> Vec<i64> {
    let mut k: Vec<i64> = a
        .iter()
        .filter_map(|&(p, _)| p.map(|p| inv.handle[p]))
        .collect();
    k.sort_unstable();
    k
}

/// Returns `(layouts, truncated, nodes)`.
pub fn solve_free_slots(p: SolveParams) -> (Vec<Assignment>, bool, u64) {
    let num_free = p.candidates.len();
    if num_free == 0 {
        return (vec![Vec::new()], false, 0);
    }

    // Per-free-slot suffix unions of requirement coverage: what the slots from
    // index j onward can still contribute.  `[num_free] == 0` doubles as leaf
    // rejection in the backtracker.  The union is optimistic (it ignores
    // one-relic-per-slot and handle reuse), so it is a sound prune but
    // "feasible" here does not guarantee coverable.
    let mut req_suffix_union = vec![0u64; num_free + 1];
    let mut constrained = false;
    if p.req_full_mask != 0 {
        for j in (0..num_free).rev() {
            let slot_union = p.candidates[j]
                .iter()
                .fold(0u64, |acc, &c| acc | p.inv.req_mask[c]);
            req_suffix_union[j] = req_suffix_union[j + 1] | slot_union;
        }
        // When the vessel can't possibly cover, fall back to the ordinary
        // unconstrained solve — the caller flags those results
        // meets_requirements=False.
        constrained = (p.req_initial_mask | req_suffix_union[0]) == p.req_full_mask;
    }

    let ctx = GreedyCtx {
        inv: p.inv,
        candidates: p.candidates,
        curse_max: p.curse_max,
    };

    // Always run greedy first — it is fast O(n*k) and its best score seeds the
    // backtracker for aggressive pruning.
    let mut raw_free = greedy_solve(&ctx, p.top_n);

    let initial_threshold = if constrained {
        // Only a requirement-COVERING assignment may seed the threshold: the
        // covering optimum can score below the best unconstrained greedy, so a
        // non-covering seed could prune every valid optimum away.
        let covering_best = raw_free
            .iter()
            .filter(|a| covers(p.inv, a, p.req_initial_mask, p.req_full_mask))
            .map(total_score)
            .max();
        match covering_best {
            Some(best) => best - 1,
            None => match greedy_cover_once(
                &ctx,
                &req_suffix_union,
                p.req_full_mask,
                p.req_initial_mask,
            ) {
                Some(cover) => {
                    let seed = total_score(&cover) - 1;
                    raw_free.push(cover);
                    seed
                }
                None => UNSEEDED_THRESHOLD,
            },
        }
    } else {
        raw_free.iter().map(total_score).max().unwrap_or(0) - 1
    };

    let (bt_results, truncated, nodes) = backtrack_solve(BacktrackParams {
        inv: p.inv,
        candidates: p.candidates,
        top_n: p.top_n,
        curse_max: p.curse_max,
        initial_threshold,
        deadline_secs: p.deadline_secs,
        validate_leaves: p.validate_leaves,
        constrained,
        req_suffix_union: &req_suffix_union,
        req_full_mask: p.req_full_mask,
        req_initial_mask: p.req_initial_mask,
    });

    if !bt_results.is_empty() {
        // Merge and deduplicate by relic set, backtrack results first.  Only
        // when the backtracker returned something: otherwise greedy duplicates
        // are left as they are, exactly as in Python.
        let mut seen: HashSet<Vec<i64>> = HashSet::new();
        let mut merged: Vec<Assignment> = Vec::with_capacity(bt_results.len() + raw_free.len());
        for a in bt_results.into_iter().chain(raw_free.into_iter()) {
            let key = layout_handles(p.inv, &a);
            if seen.insert(key) {
                merged.push(a);
            }
        }
        raw_free = merged;
    }

    if constrained {
        // Constrained backtrack results always cover; greedy layouts may not.
        // When any covering layout exists, non-covering ones are dropped; when
        // none does (an infeasible slot packing, or a truncated search),
        // greedy stays as the flagged fallback.
        let covering: Vec<Assignment> = raw_free
            .iter()
            .filter(|a| covers(p.inv, a, p.req_initial_mask, p.req_full_mask))
            .cloned()
            .collect();
        if !covering.is_empty() {
            raw_free = covering;
        }
    }

    (raw_free, truncated, nodes)
}
