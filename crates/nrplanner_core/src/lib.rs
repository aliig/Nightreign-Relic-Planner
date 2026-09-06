//! PyO3 glue for the Nightreign vessel-slot solver.
//!
//! The Python side (`nrplanner.solver_bridge`) owns everything that touches
//! game data or Pydantic: it compiles relic profiles, interns their ids into
//! two dense namespaces, and hands the result here once per (build,
//! inventory).  Each vessel then passes only its candidate index lists.
//!
//! Nothing here holds the GIL during a solve: `solve_vessel` copies its
//! arguments into Rust-owned data, clones the `Arc<Inventory>`, and runs
//! inside `Python::allow_threads`, so the vessels of one build can be solved
//! on a plain thread pool.

use std::sync::Arc;

use pyo3::prelude::*;
use pyo3::types::PyDict;

mod backtrack;
mod bitset;
mod convert;
mod greedy;
mod helpers;
mod inventory;
mod score;
mod solve;
mod state;

use crate::inventory::Inventory;
use crate::solve::{solve_free_slots, SolveParams};

/// A compiled inventory, shared by every vessel of one build.
///
/// Frozen and immutable: the only thing a solve does with it is read, which is
/// what makes sharing one across solver threads sound.
#[pyclass(frozen, name = "CompiledInventory")]
pub struct CompiledInventory {
    inner: Arc<Inventory>,
}

#[pymethods]
impl CompiledInventory {
    /// Number of compiled relic profiles.
    fn __len__(&self) -> usize {
        self.inner.len()
    }

    fn __repr__(&self) -> String {
        format!(
            "<CompiledInventory profiles={} universe={} limits={}>",
            self.inner.len(),
            self.inner.universe_size,
            self.inner.limit_namespace_size
        )
    }
}

/// Build a `CompiledInventory` from the bridge's dict of `list[int]` columns.
#[pyfunction]
fn compile_inventory(
    columns: &Bound<'_, PyDict>,
    universe_size: usize,
    limit_namespace_size: usize,
) -> PyResult<CompiledInventory> {
    let inv = convert::inventory_from_columns(columns, universe_size, limit_namespace_size)?;
    Ok(CompiledInventory {
        inner: Arc::new(inv),
    })
}

/// Solve one vessel's free slots.
///
/// `candidates` is one list of profile indices per free slot, already in the
/// stable net-descending order the Python side established and already
/// filtered of this vessel's pinned/excluded relics.
///
/// Returns `(assignments, truncated, nodes)`, where each assignment is a list
/// of `(profile_index or -1, context_score)` in free-slot order.
#[pyfunction]
#[pyo3(signature = (inv, candidates, top_n, curse_max, deadline_secs, validate_leaves,
                    req_full_mask, req_initial_mask))]
#[allow(clippy::too_many_arguments)]
fn solve_vessel(
    py: Python<'_>,
    inv: &CompiledInventory,
    candidates: Vec<Vec<usize>>,
    top_n: usize,
    curse_max: i64,
    deadline_secs: f64,
    validate_leaves: bool,
    req_full_mask: u64,
    req_initial_mask: u64,
) -> PyResult<(Vec<Vec<(isize, i64)>>, bool, u64)> {
    let n_profiles = inv.inner.len();
    for (slot, ids) in candidates.iter().enumerate() {
        if let Some(&bad) = ids.iter().find(|&&p| p >= n_profiles) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "candidate profile index {bad} in slot {slot} is out of range \
                 (inventory has {n_profiles} profiles)"
            )));
        }
    }
    let shared = Arc::clone(&inv.inner);

    let (layouts, truncated, nodes) = py.allow_threads(move || {
        solve_free_slots(SolveParams {
            inv: &shared,
            candidates: &candidates,
            top_n,
            curse_max,
            deadline_secs,
            validate_leaves,
            req_full_mask,
            req_initial_mask,
        })
    });

    let out = layouts
        .into_iter()
        .map(|a| {
            a.into_iter()
                .map(|(p, s)| (p.map(|p| p as isize).unwrap_or(-1), s))
                .collect()
        })
        .collect();
    Ok((out, truncated, nodes))
}

/// Test hook: score one profile after placing `placed` (profile indices) in
/// order.  Keeps `test_profile_equivalence` able to pin the compiled profile
/// to the legacy `score_relic_in_context` / `place` once the Python hot path
/// is gone.
#[pyfunction]
fn score_profile_debug(
    inv: &CompiledInventory,
    profile_idx: usize,
    placed: Vec<usize>,
    curse_max: i64,
) -> PyResult<i64> {
    let inventory = &inv.inner;
    let n = inventory.len();
    for &p in placed.iter().chain(std::iter::once(&profile_idx)) {
        if p >= n {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "profile index {p} out of range (inventory has {n} profiles)"
            )));
        }
    }
    let mut state = state::VesselState::new(inventory);
    let mut delta = state::Delta::default();
    for p in placed {
        state.place(inventory, p, &mut delta);
    }
    Ok(score::score_profile(inventory, profile_idx, &state, curse_max))
}

/// Test hook: the full stacking state after placing `placed` (profile indices)
/// in order, as dense ids.
///
/// Lets `test_profile_equivalence` pin the Rust `VesselState` against the
/// legacy `VesselState.place` over real game data — the placement half of the
/// compiled-vs-legacy equivalence property.  Returns
/// `(effect_ids, exclusivity_ids, no_stack_exclusivity_ids,
///   no_stack_compat_ids, desired_compat_placed, curse_counts,
///   limited_counts)`, the counters as `(id, count)` pairs for nonzero
/// entries only (Python's dicts delete a key when it reaches zero).
#[pyfunction]
#[allow(clippy::type_complexity)]
fn state_debug(
    inv: &CompiledInventory,
    placed: Vec<usize>,
) -> PyResult<(
    Vec<u32>,
    Vec<u32>,
    Vec<u32>,
    Vec<u32>,
    Vec<u32>,
    Vec<(u32, u32)>,
    Vec<(u32, u32)>,
)> {
    let inventory = &inv.inner;
    let n = inventory.len();
    for &p in &placed {
        if p >= n {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "profile index {p} out of range (inventory has {n} profiles)"
            )));
        }
    }
    let mut st = state::VesselState::new(inventory);
    let mut delta = state::Delta::default();
    for p in placed {
        st.place(inventory, p, &mut delta);
    }
    let nonzero = |counts: &[u32]| -> Vec<(u32, u32)> {
        counts
            .iter()
            .enumerate()
            .filter(|(_, &c)| c > 0)
            .map(|(i, &c)| (i as u32, c))
            .collect()
    };
    Ok((
        st.effect_ids.iter().collect(),
        st.exclusivity_ids.iter().collect(),
        st.no_stack_exclusivity_ids.iter().collect(),
        st.no_stack_compat_ids.iter().collect(),
        st.desired_compat_placed.iter().collect(),
        nonzero(&st.curse_counts),
        nonzero(&st.limited_counts),
    ))
}

/// Build provenance, for the startup log and the parity test.
#[pyfunction]
fn engine_info(py: Python<'_>) -> PyResult<Py<PyDict>> {
    let d = PyDict::new(py);
    d.set_item("version", env!("CARGO_PKG_VERSION"))?;
    d.set_item("debug_assertions", cfg!(debug_assertions))?;
    d.set_item("abi3", true)?;
    Ok(d.into())
}

#[pymodule]
fn nrplanner_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CompiledInventory>()?;
    m.add_function(wrap_pyfunction!(compile_inventory, m)?)?;
    m.add_function(wrap_pyfunction!(solve_vessel, m)?)?;
    m.add_function(wrap_pyfunction!(score_profile_debug, m)?)?;
    m.add_function(wrap_pyfunction!(state_debug, m)?)?;
    m.add_function(wrap_pyfunction!(engine_info, m)?)?;
    Ok(())
}
