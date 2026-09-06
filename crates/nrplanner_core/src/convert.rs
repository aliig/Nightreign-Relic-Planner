//! dict-of-columns -> `Inventory`, with every column validated.
//!
//! The bridge ships plain `list[int]` columns (no numpy in this repo, and at
//! ~80k ints per build the encoding is not the bottleneck).  Everything is
//! checked here rather than trusted: a malformed column would otherwise show
//! up as an out-of-bounds panic deep in the solver, and the column name in the
//! error is what makes a bridge bug findable.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::inventory::{Csr, DynEntry, Inventory};

fn column(columns: &Bound<'_, PyDict>, name: &str) -> PyResult<Vec<i64>> {
    match columns.get_item(name)? {
        Some(v) => v
            .extract::<Vec<i64>>()
            .map_err(|e| PyValueError::new_err(format!("column {name:?}: {e}"))),
        None => Err(PyValueError::new_err(format!("missing column {name:?}"))),
    }
}

fn expect_len(name: &str, got: usize, want: usize) -> PyResult<()> {
    if got != want {
        return Err(PyValueError::new_err(format!(
            "column {name:?} has {got} entries, expected {want}"
        )));
    }
    Ok(())
}

/// Build a CSR block from an offsets column and an ids column, checking that
/// the offsets are monotone, start at 0, end at the id count, and that every
/// id is inside `universe`.
fn csr(
    columns: &Bound<'_, PyDict>,
    off_name: &str,
    ids_name: &str,
    n_profiles: usize,
    universe: usize,
) -> PyResult<Csr> {
    let off = column(columns, off_name)?;
    let ids = column(columns, ids_name)?;
    expect_len(off_name, off.len(), n_profiles + 1)?;
    if off.first().copied() != Some(0) {
        return Err(PyValueError::new_err(format!("column {off_name:?} must start at 0")));
    }
    if off.last().copied() != Some(ids.len() as i64) {
        return Err(PyValueError::new_err(format!(
            "column {off_name:?} ends at {:?}, but {ids_name:?} has {} entries",
            off.last(),
            ids.len()
        )));
    }
    for w in off.windows(2) {
        if w[1] < w[0] {
            return Err(PyValueError::new_err(format!(
                "column {off_name:?} is not monotonically increasing"
            )));
        }
    }
    for &id in &ids {
        if id < 0 || id as usize >= universe {
            return Err(PyValueError::new_err(format!(
                "column {ids_name:?} holds id {id} outside the 0..{universe} namespace"
            )));
        }
    }
    Ok(Csr {
        off: off.into_iter().map(|v| v as u32).collect(),
        ids: ids.into_iter().map(|v| v as u32).collect(),
    })
}

fn as_u64_masks(name: &str, vals: Vec<i64>) -> PyResult<Vec<u64>> {
    vals.into_iter()
        .map(|v| {
            u64::try_from(v).map_err(|_| {
                PyValueError::new_err(format!("column {name:?} holds a negative mask {v}"))
            })
        })
        .collect()
}

pub fn inventory_from_columns(
    columns: &Bound<'_, PyDict>,
    universe_size: usize,
    limit_namespace_size: usize,
) -> PyResult<Inventory> {
    let handle = column(columns, "handle")?;
    let n = handle.len();

    let static_score = column(columns, "static_score")?;
    expect_len("static_score", static_score.len(), n)?;
    let pos_bound = column(columns, "pos_bound")?;
    expect_len("pos_bound", pos_bound.len(), n)?;
    let net = column(columns, "net")?;
    expect_len("net", net.len(), n)?;

    let req_mask = column(columns, "req_mask")?;
    expect_len("req_mask", req_mask.len(), n)?;
    let leaf_desired = column(columns, "leaf_desired_mask")?;
    expect_len("leaf_desired_mask", leaf_desired.len(), n)?;
    let leaf_undesired = column(columns, "leaf_undesired_mask")?;
    expect_len("leaf_undesired_mask", leaf_undesired.len(), n)?;

    // --- dynamic effect entries (CSR over dyn_off) ---
    let dyn_off = column(columns, "dyn_off")?;
    expect_len("dyn_off", dyn_off.len(), n + 1)?;
    let n_dyn = *dyn_off.last().unwrap_or(&0) as usize;
    let kind = column(columns, "dyn_kind")?;
    let weight = column(columns, "dyn_weight")?;
    let eff = column(columns, "dyn_eff")?;
    let text = column(columns, "dyn_text")?;
    let excl = column(columns, "dyn_excl")?;
    let compat = column(columns, "dyn_compat")?;
    let penalty = column(columns, "dyn_penalty")?;
    let lname = column(columns, "dyn_lname")?;
    let lname_max = column(columns, "dyn_lname_max")?;
    let lfam = column(columns, "dyn_lfam")?;
    let lfam_max = column(columns, "dyn_lfam_max")?;
    for (name, got) in [
        ("dyn_kind", kind.len()),
        ("dyn_weight", weight.len()),
        ("dyn_eff", eff.len()),
        ("dyn_text", text.len()),
        ("dyn_excl", excl.len()),
        ("dyn_compat", compat.len()),
        ("dyn_penalty", penalty.len()),
        ("dyn_lname", lname.len()),
        ("dyn_lname_max", lname_max.len()),
        ("dyn_lfam", lfam.len()),
        ("dyn_lfam_max", lfam_max.len()),
    ] {
        expect_len(name, got, n_dyn)?;
    }

    let mut dyn_entries = Vec::with_capacity(n_dyn);
    for i in 0..n_dyn {
        let check = |name: &str, v: i64, universe: usize| -> PyResult<i32> {
            // -1 is the "absent" sentinel; only real ids are range-checked
            // (casting -1 to usize would wrap to a huge value).
            if v < -1 || (v >= 0 && v as usize >= universe) {
                return Err(PyValueError::new_err(format!(
                    "column {name:?}[{i}] = {v} is outside -1..{universe}"
                )));
            }
            Ok(v as i32)
        };
        let e = check("dyn_eff", eff[i], universe_size)?;
        if e < 0 {
            return Err(PyValueError::new_err(format!("column 'dyn_eff'[{i}] must be a real id")));
        }
        dyn_entries.push(DynEntry {
            kind: kind[i] as u8,
            weight: weight[i],
            eff: e as u32,
            text: check("dyn_text", text[i], universe_size)?,
            excl: check("dyn_excl", excl[i], universe_size)?,
            compat: check("dyn_compat", compat[i], universe_size)?,
            penalty: penalty[i],
            lname: check("dyn_lname", lname[i], limit_namespace_size)?,
            lname_max: lname_max[i],
            lfam: check("dyn_lfam", lfam[i], limit_namespace_size)?,
            lfam_max: lfam_max[i],
        });
    }

    Ok(Inventory {
        universe_size,
        limit_namespace_size,
        handle,
        static_score,
        pos_bound,
        net,
        req_mask: as_u64_masks("req_mask", req_mask)?,
        leaf_desired: as_u64_masks("leaf_desired_mask", leaf_desired)?,
        leaf_undesired: as_u64_masks("leaf_undesired_mask", leaf_undesired)?,
        dyn_off: dyn_off.into_iter().map(|v| v as u32).collect(),
        dyn_entries,
        curses: csr(columns, "curse_off", "curse_ids", n, universe_size)?,
        penalized_curses: csr(columns, "pcurse_off", "pcurse_ids", n, universe_size)?,
        effs: csr(columns, "eff_off", "eff_ids", n, universe_size)?,
        excls: csr(columns, "excl_off", "excl_ids", n, universe_size)?,
        ns_excls: csr(columns, "nsexcl_off", "nsexcl_ids", n, universe_size)?,
        ns_compats: csr(columns, "nscompat_off", "nscompat_ids", n, universe_size)?,
        dcps: csr(columns, "dcp_off", "dcp_ids", n, universe_size)?,
        limit_keys: csr(columns, "limit_off", "limit_keys", n, limit_namespace_size)?,
        unlocks: csr(columns, "unlock_off", "unlock_ids", n, universe_size)?,
        neg_keys: csr(columns, "neg_off", "neg_keys", n, universe_size)?,
    })
}
