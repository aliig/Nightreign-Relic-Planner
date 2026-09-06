//! Fixed-universe bitset.
//!
//! The solver's inner loop is membership tests (`score_profile` does about six
//! per candidate per node) against sets that never hold more than a few dozen
//! ids out of a universe of a few thousand — so a word-array bitset beats a
//! hash set on both the test and the clear.

#[derive(Clone, Debug)]
pub struct BitSet {
    words: Vec<u64>,
}

impl BitSet {
    pub fn new(universe: usize) -> Self {
        BitSet {
            words: vec![0u64; universe.div_ceil(64)],
        }
    }

    #[inline]
    pub fn contains(&self, id: u32) -> bool {
        let i = id as usize;
        (self.words[i >> 6] >> (i & 63)) & 1 != 0
    }

    /// Sets the bit; returns true when it was NOT already set.
    ///
    /// The return value is what makes a placement reversible: the delta records
    /// exactly the ids this relic newly contributed, mirroring the
    /// difference-then-union of `VesselState.place_profile`.
    #[inline]
    pub fn insert(&mut self, id: u32) -> bool {
        let i = id as usize;
        let mask = 1u64 << (i & 63);
        let w = &mut self.words[i >> 6];
        let was = *w & mask != 0;
        *w |= mask;
        !was
    }

    #[inline]
    pub fn remove(&mut self, id: u32) {
        let i = id as usize;
        self.words[i >> 6] &= !(1u64 << (i & 63));
    }

    /// True when every id in `ids` is present.
    #[inline]
    pub fn contains_all(&self, ids: &[u32]) -> bool {
        ids.iter().all(|&id| self.contains(id))
    }

    /// The ids present, ascending.  Diagnostics only (the `state_debug` hook);
    /// the solver never enumerates a set.
    pub fn iter(&self) -> impl Iterator<Item = u32> + '_ {
        self.words.iter().enumerate().flat_map(|(w, &word)| {
            (0..64).filter_map(move |b| {
                if word >> b & 1 != 0 {
                    Some((w * 64 + b) as u32)
                } else {
                    None
                }
            })
        })
    }

    pub fn clear(&mut self) {
        self.words.iter_mut().for_each(|w| *w = 0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn insert_reports_novelty_once() {
        let mut b = BitSet::new(200);
        assert!(b.insert(3));
        assert!(!b.insert(3));
        assert!(b.contains(3));
        b.remove(3);
        assert!(!b.contains(3));
    }

    #[test]
    fn iter_yields_present_ids_ascending() {
        let mut b = BitSet::new(200);
        for id in [130u32, 3, 64, 3] {
            b.insert(id);
        }
        assert_eq!(b.iter().collect::<Vec<_>>(), vec![3, 64, 130]);
    }

    #[test]
    fn spans_multiple_words() {
        let mut b = BitSet::new(200);
        for id in [0u32, 63, 64, 127, 128, 199] {
            assert!(b.insert(id));
        }
        assert!(b.contains_all(&[0, 63, 64, 127, 128, 199]));
        assert!(!b.contains(65));
        b.clear();
        assert!(!b.contains(199));
    }
}
