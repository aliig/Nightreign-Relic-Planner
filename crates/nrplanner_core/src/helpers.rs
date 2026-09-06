//! Per-vessel context helpers and the leaf orphan check.

use std::collections::HashMap;

use crate::greedy::Assignment;
use crate::inventory::Inventory;
use crate::state::VesselState;

/// Why a context score <= 0 placement may still pay off later, per profile.
///
/// The per-relic half (which compats a relic unlocks, which negative keys it
/// carries) is precomputed into the inventory; only the ">= 2 candidates share
/// this key" filter is per vessel, because it depends on which relics are
/// eligible for THIS vessel's slots.  Port of `_ctx_helper_map`.
pub struct CtxHelpers {
    /// profile -> (unlock compat ids, shared negative keys). Profiles with
    /// neither are absent, exactly as the Python map omits them.
    map: HashMap<usize, (Vec<u32>, Vec<u32>)>,
}

impl CtxHelpers {
    pub fn build(inv: &Inventory, candidates: &[Vec<usize>]) -> Self {
        // Distinct candidate profiles across all free slots, first-seen order.
        let mut distinct: Vec<usize> = Vec::new();
        let mut seen: Vec<bool> = vec![false; inv.len()];
        for slot in candidates {
            for &p in slot {
                if !seen[p] {
                    seen[p] = true;
                    distinct.push(p);
                }
            }
        }

        let mut neg_counts: HashMap<u32, u32> = HashMap::new();
        for &p in &distinct {
            for &k in inv.neg_keys.get(p) {
                *neg_counts.entry(k).or_insert(0) += 1;
            }
        }

        let mut map = HashMap::new();
        for &p in &distinct {
            let unlocks: Vec<u32> = inv.unlocks.get(p).to_vec();
            let shared: Vec<u32> = inv
                .neg_keys
                .get(p)
                .iter()
                .copied()
                .filter(|k| neg_counts.get(k).copied().unwrap_or(0) >= 2)
                .collect();
            if !unlocks.is_empty() || !shared.is_empty() {
                map.insert(p, (unlocks, shared));
            }
        }
        CtxHelpers { map }
    }

    /// True when this profile is worth placing at ctx <= 0 in the current
    /// state — i.e. it still has an unlock to contribute or a shared negative
    /// effect not yet paid for.  A profile with no helper entry is never
    /// worth it, which is why "no helper" and "empty map" behave identically
    /// in Python (both `continue`).
    #[inline]
    pub fn still_useful(&self, p: usize, state: &VesselState) -> bool {
        match self.map.get(&p) {
            None => false,
            Some((unlocks, dedups)) => {
                !(state.desired_compat_placed.contains_all(unlocks)
                    && state.effect_ids.contains_all(dedups))
            }
        }
    }
}

/// The leaf-level excluded-stacking-category check.
///
/// Rejects a layout when, for any checked compat, an undesired competitor is
/// placed and the desired effect is either absent or sits to its right — the
/// leftmost effect in a no_stack compat is the one that wins in-game.
///
/// Slot order here is absolute: the caller only enables this when the vessel
/// has no pinned slots, so free-slot indices and real slot indices agree.
/// Port of `has_orphaned_excl_category_effects` over the precomputed
/// per-profile desired/undesired bitmasks.
pub fn has_orphaned_excl_category(inv: &Inventory, assignment: &Assignment) -> bool {
    let mut any_undesired = 0u64;
    for &(p, _) in assignment {
        if let Some(p) = p {
            any_undesired |= inv.leaf_undesired[p];
        }
    }
    if any_undesired == 0 {
        return false; // no undesired competitor anywhere — nothing to check
    }

    let mut bits = any_undesired;
    while bits != 0 {
        let bit = bits & bits.wrapping_neg();
        bits ^= bit;

        let mut leftmost_desired: Option<usize> = None;
        let mut leftmost_undesired: Option<usize> = None;
        for (slot, &(p, _)) in assignment.iter().enumerate() {
            let Some(p) = p else { continue };
            if leftmost_desired.is_none() && inv.leaf_desired[p] & bit != 0 {
                leftmost_desired = Some(slot);
            }
            if leftmost_undesired.is_none() && inv.leaf_undesired[p] & bit != 0 {
                leftmost_undesired = Some(slot);
            }
            if leftmost_desired.is_some() && leftmost_undesired.is_some() {
                break;
            }
        }

        match (leftmost_desired, leftmost_undesired) {
            (_, None) => continue,          // no undesired competitor — OK
            (None, Some(_)) => return true, // orphaned
            (Some(d), Some(u)) if u < d => return true, // priority-blocked
            _ => continue,
        }
    }
    false
}
