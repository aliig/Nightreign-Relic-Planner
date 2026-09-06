//! `score_profile` — the single hottest function in the solver.
//!
//! A line-for-line port of `nrplanner.scoring.score_profile`.  The branch
//! order inside each kind is load-bearing (it mirrors the legacy
//! `score_relic_in_context` the compiled profile is pinned to), so it must not
//! be "simplified" — the differential parity test compares scores exactly.

use crate::inventory::{Inventory, K_EXCL_CAT, K_NO_STACK, K_STACK, K_UNIQUE};
use crate::state::VesselState;

/// Disqualifying penalty for exceeding curse_max or a negative-weight limit.
/// Mirrors `nrplanner.models.CURSE_EXCESS_PENALTY`.
pub const CURSE_EXCESS_PENALTY: i64 = -200;

pub fn score_profile(inv: &Inventory, p: usize, state: &VesselState, curse_max: i64) -> i64 {
    let mut score = inv.static_score[p];

    for e in inv.dyn_entries_of(p) {
        if e.lname >= 0 || e.lfam >= 0 {
            let capped = (e.lname >= 0 && state.limited_count(e.lname) as i64 >= e.lname_max)
                || (e.lfam >= 0 && state.limited_count(e.lfam) as i64 >= e.lfam_max);
            if capped {
                // Sign fork: a limit on a desired effect is a score cap (extra
                // copies are neutral); on an undesired one it is a tolerance —
                // copies beyond it disqualify like excess curses.
                if e.weight < 0 {
                    score += CURSE_EXCESS_PENALTY;
                }
                continue;
            }
        }
        match e.kind {
            K_STACK => score += e.weight,
            K_UNIQUE => {
                if state.effect_ids.contains(e.eff) {
                    continue;
                }
                if e.text >= 0 && state.effect_ids.contains(e.text as u32) {
                    continue;
                }
                if e.excl >= 0 && state.no_stack_exclusivity_ids.contains(e.excl as u32) {
                    score += e.penalty;
                    continue;
                }
                if e.compat >= 0 && state.no_stack_compat_ids.contains(e.compat as u32) {
                    score += e.penalty;
                    continue;
                }
                score += e.weight;
            }
            K_NO_STACK => {
                if e.excl >= 0 && state.exclusivity_ids.contains(e.excl as u32) {
                    score += e.penalty;
                    continue;
                }
                if e.text >= 0 && state.effect_ids.contains(e.text as u32) {
                    continue;
                }
                if state.effect_ids.contains(e.eff) {
                    continue;
                }
                score += e.weight;
            }
            // K_EXCL_CAT — blocking penalty until the desired effect is placed.
            _ => {
                debug_assert_eq!(e.kind, K_EXCL_CAT);
                if !state.desired_compat_placed.contains(e.compat as u32) {
                    score += e.penalty;
                }
            }
        }
    }

    for &c in inv.penalized_curses.get(p) {
        if state.curse_count(c) as i64 >= curse_max {
            score += CURSE_EXCESS_PENALTY;
        }
    }
    score
}
