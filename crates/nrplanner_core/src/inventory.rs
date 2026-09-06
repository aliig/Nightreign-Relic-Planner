//! The compiled inventory: one struct-of-arrays per (build, relic set).
//!
//! Mirrors `BuildScorer.compile_profile` output for every relic that survives
//! the build's pre-filters.  All raw game ids (effect, text, exclusivity,
//! compat, curse) are interned by the Python bridge into one dense namespace
//! of size `universe_size`; limit keys (effect NAMES and family names, sharing
//! one counter namespace exactly as `VesselState.limited_counts` does) into a
//! second of size `limit_namespace_size`.
//!
//! Plain data, no PyO3: `cargo test` exercises it without libpython.

/// Dynamic-effect kinds — must match `scoring.K_STACK`/`K_UNIQUE`/etc.
pub const K_STACK: u8 = 0;
pub const K_UNIQUE: u8 = 1;
pub const K_NO_STACK: u8 = 2;
pub const K_EXCL_CAT: u8 = 3;

/// One entry of a profile's `dyn` tuple.
///
/// `lname_max` / `lfam_max` carry the limit thresholds inline: an effect name
/// that is also a family name shares one counter but has two thresholds, and
/// carrying them here resolves each from the right map without shipping both
/// maps into Rust.
#[derive(Clone, Copy, Debug)]
pub struct DynEntry {
    pub kind: u8,
    pub weight: i64,
    pub eff: u32,
    pub text: i32,
    pub excl: i32,
    pub compat: i32,
    pub penalty: i64,
    pub lname: i32,
    pub lname_max: i64,
    pub lfam: i32,
    pub lfam_max: i64,
}

/// A CSR-encoded list-of-lists: `off` has len+1 entries, `ids` is the payload.
#[derive(Clone, Debug, Default)]
pub struct Csr {
    pub off: Vec<u32>,
    pub ids: Vec<u32>,
}

impl Csr {
    #[inline]
    pub fn get(&self, i: usize) -> &[u32] {
        &self.ids[self.off[i] as usize..self.off[i + 1] as usize]
    }
}

#[derive(Debug)]
pub struct Inventory {
    pub universe_size: usize,
    pub limit_namespace_size: usize,

    /// Raw ga_handle. Only the solver's `handle != 0` greedy quirk needs it.
    pub handle: Vec<i64>,
    pub static_score: Vec<i64>,
    pub pos_bound: Vec<i64>,
    /// Net pre-score.  Read on the Python side to order each colour pool; the
    /// solver receives candidates already sorted, so it never needs it.
    #[allow(dead_code)]
    pub net: Vec<i64>,
    /// Bitmask of Required specs this relic satisfies.
    pub req_mask: Vec<u64>,
    /// Per checked excluded-stacking compat: does the relic carry the desired
    /// effect / an undesired competitor.  Drives the leaf orphan check.
    pub leaf_desired: Vec<u64>,
    pub leaf_undesired: Vec<u64>,

    pub dyn_off: Vec<u32>,
    pub dyn_entries: Vec<DynEntry>,

    pub curses: Csr,
    /// Curses subject to the build-wide curse_max check (see RelicProfile).
    pub penalized_curses: Csr,

    // The five placement sets, in the order place_profile applies them.
    pub effs: Csr,
    pub excls: Csr,
    pub ns_excls: Csr,
    pub ns_compats: Csr,
    pub dcps: Csr,

    pub limit_keys: Csr,
    /// Excluded-category compats whose DESIRED effect this relic carries.
    pub unlocks: Csr,
    /// Canonical ids of negatively-weighted no_stack/unique effects, BEFORE
    /// the "shared by >= 2 candidates" filter (which is per vessel).
    pub neg_keys: Csr,
}

impl Inventory {
    pub fn len(&self) -> usize {
        self.handle.len()
    }

    #[inline]
    pub fn dyn_entries_of(&self, p: usize) -> &[DynEntry] {
        &self.dyn_entries[self.dyn_off[p] as usize..self.dyn_off[p + 1] as usize]
    }
}
