//! Mutable stacking state for one vessel — the Rust twin of `VesselState`.
//!
//! Placement is reversible: `place` records exactly the ids it newly set, so
//! `remove` clears those and nothing more.  That is the same
//! difference-then-union contract `VesselState.place_profile` /
//! `VesselState.remove` implement in Python, and it is what makes the
//! backtracker's undo correct when two relics share an id.
//!
//! Counters (`curse_counts`, `limited_counts`) are dense vectors instead of
//! dicts.  Python deletes a key when it hits zero, which is unobservable:
//! every read goes through `.get(k, 0)`.

use crate::bitset::BitSet;
use crate::inventory::Inventory;

/// The ids one placement newly contributed, per collection.
///
/// Profiles carry 3-12 ids, so these stay tiny; the backtracker keeps one
/// Delta per slot depth and reuses it rather than allocating per node.
#[derive(Clone, Debug, Default)]
pub struct Delta {
    pub effs: Vec<u32>,
    pub excls: Vec<u32>,
    pub ns_excls: Vec<u32>,
    pub ns_compats: Vec<u32>,
    pub dcps: Vec<u32>,
}

impl Delta {
    fn clear(&mut self) {
        self.effs.clear();
        self.excls.clear();
        self.ns_excls.clear();
        self.ns_compats.clear();
        self.dcps.clear();
    }
}

#[derive(Clone, Debug)]
pub struct VesselState {
    pub effect_ids: BitSet,
    pub exclusivity_ids: BitSet,
    pub no_stack_exclusivity_ids: BitSet,
    pub no_stack_compat_ids: BitSet,
    pub desired_compat_placed: BitSet,
    pub curse_counts: Vec<u32>,
    pub limited_counts: Vec<u32>,
}

impl VesselState {
    pub fn new(inv: &Inventory) -> Self {
        VesselState {
            effect_ids: BitSet::new(inv.universe_size),
            exclusivity_ids: BitSet::new(inv.universe_size),
            no_stack_exclusivity_ids: BitSet::new(inv.universe_size),
            no_stack_compat_ids: BitSet::new(inv.universe_size),
            desired_compat_placed: BitSet::new(inv.universe_size),
            curse_counts: vec![0; inv.universe_size],
            limited_counts: vec![0; inv.limit_namespace_size],
        }
    }

    pub fn reset(&mut self) {
        self.effect_ids.clear();
        self.exclusivity_ids.clear();
        self.no_stack_exclusivity_ids.clear();
        self.no_stack_compat_ids.clear();
        self.desired_compat_placed.clear();
        self.curse_counts.iter_mut().for_each(|c| *c = 0);
        self.limited_counts.iter_mut().for_each(|c| *c = 0);
    }

    #[inline]
    pub fn curse_count(&self, id: u32) -> u32 {
        self.curse_counts[id as usize]
    }

    #[inline]
    pub fn limited_count(&self, key: i32) -> u32 {
        self.limited_counts[key as usize]
    }

    /// Place profile `p`, recording the undo information in `delta`.
    pub fn place(&mut self, inv: &Inventory, p: usize, delta: &mut Delta) {
        delta.clear();
        for &id in inv.effs.get(p) {
            if self.effect_ids.insert(id) {
                delta.effs.push(id);
            }
        }
        for &id in inv.excls.get(p) {
            if self.exclusivity_ids.insert(id) {
                delta.excls.push(id);
            }
        }
        for &id in inv.ns_excls.get(p) {
            if self.no_stack_exclusivity_ids.insert(id) {
                delta.ns_excls.push(id);
            }
        }
        for &id in inv.ns_compats.get(p) {
            if self.no_stack_compat_ids.insert(id) {
                delta.ns_compats.push(id);
            }
        }
        for &id in inv.dcps.get(p) {
            if self.desired_compat_placed.insert(id) {
                delta.dcps.push(id);
            }
        }
        for &id in inv.curses.get(p) {
            self.curse_counts[id as usize] += 1;
        }
        for &k in inv.limit_keys.get(p) {
            self.limited_counts[k as usize] += 1;
        }
    }

    /// Undo the placement of profile `p` recorded in `delta`.
    pub fn unplace(&mut self, inv: &Inventory, p: usize, delta: &Delta) {
        for &id in &delta.effs {
            self.effect_ids.remove(id);
        }
        for &id in &delta.excls {
            self.exclusivity_ids.remove(id);
        }
        for &id in &delta.ns_excls {
            self.no_stack_exclusivity_ids.remove(id);
        }
        for &id in &delta.ns_compats {
            self.no_stack_compat_ids.remove(id);
        }
        for &id in &delta.dcps {
            self.desired_compat_placed.remove(id);
        }
        // Counters take the profile's FULL id lists, not the delta: every
        // copy was counted, so every copy must be uncounted.
        for &id in inv.curses.get(p) {
            self.curse_counts[id as usize] -= 1;
        }
        for &k in inv.limit_keys.get(p) {
            self.limited_counts[k as usize] -= 1;
        }
    }
}
