"""
results_data.py
---------------
Extracts analysis results from an ospgrillage OspGrillage model directly into
the flat dict format consumed by plot_generator functions.

Accepts the OspGrillage model object and its xarray Dataset directly, with no
bridge-wrapper dependency.
"""

from __future__ import annotations
import json
from pathlib import Path

import openseespy.opensees as ops

from .results_data_post_processing import post_process

_TOOLS_DIR = Path(__file__).resolve().parents[5] / "tools"


def _build_nodes_members() -> tuple[dict, dict]:
    """Read node coords and element connectivity from the live openseespy model."""
    nodes = {
        int(n): list(map(float, ops.nodeCoord(n)))
        for n in ops.getNodeTags()
    }
    members = {
        int(e): list(map(int, ops.eleNodes(e)))
        for e in ops.getEleTags()
    }
    return nodes, members


def restructure_data(
    model,
    edge_dist: float = 0.0,
    dev: bool = False,
) -> dict:
    """
    Restructure xarray analysis results into a flat dict for plot_generator and
    any other consumer.  Optionally dumps to tools/bridge_plot_data.json.

    Uses all available ospgrillage APIs to extract metadata (nodes, groups) 
    and results (forces, displacements, reactions, shell results).

    Parameters
    ----------
    model : OspGrillage
        Fully analysed ospgrillage model (analyze() already called).
    dataset : xarray.Dataset, optional
        Pre-computed results dataset.  When omitted, ``model.get_results()``
        is called to obtain it.
    edge_dist : float
        Deck overhang distance in metres (0.0 when no overhang).
    dev : bool
        If True, also write the result to tools/bridge_plot_data.json.

    Returns
    -------
    dict — structure::

    {
        "nodes": {
            "1": [0.0, 0.0, 0.0],
            "2": [0.0, 0.0, 0.75],
            "...": "..."
        },

        "members": {
            "1": [1, 2],
            "2": [2, 3],
            "...": "..."
        },

        "groups": {
            "edge_beam": [
                11,
                22,
                33,
                "..."
            ],

            "exterior_main_beam_1": [
                12,
                23,
                34,
                "..."
            ],

            "exterior_main_beam_2": [
                22,
                33,
                44,
                "..."
            ],

            "interior_main_beam": [
                13,
                24,
                35,
                "..."
            ],

            "start_edge": [
                1,
                2,
                3,
                "..."
            ],
            "end_edge": [
                138,
                139,
                140,
                141,
                ...
            ],
            "transverse_slab": [
                6,
                7,
                8,
                9,
                ...,

            "shell_slab": []
        },

        "longitudinal_members": [
            11,
            12,
            13,
            "..."
        ],

        "transverse_members": [
            1,
            2,
            3,
            "..."
        ],

        "edge_dist": 0.75,

        "loadcases": [
            "girder self weight",
            "superimposed dead load",
            "live load",
            "..."
        ],

        "force_components": [
            "Mx_i",
            "Mx_j",
            "My_i",
            "My_j",
            "Mz_i",
            "Mz_j",
            "Vx_i",
            "Vx_j",
            "Vy_i",
            "Vy_j",
            "Vz_i",
            "Vz_j"
        ],

        "disp_components": [
            "theta_x",
            "theta_y",
            "theta_z",
            "x",
            "y",
            "z"
        ],

        "forces": {
            "girder self weight": {
            "1": {
                "Mx_i": -0.7137482121047469,
                "Mx_j": -4478.21603280773,
                "My_i": 0.0,
                "My_j": 0.0,
                "...": "..."
            },

            "2": {
                "...": "..."
            },

            "...": "..."
            },

            "live load": {
            "...": "..."
            }
        },

        "displacements": {
            "girder self weight": {
            "1": {
                "theta_x": 2.1983232320953836e-05,
                "theta_y": 0.0,
                "theta_z": 0.00038309565871718596,
                "x": 0.0,
                "y": 1.3989811021922501e-05,
                "z": 0.0
            },

            "2": {
                "...": "..."
            },

            "...": "..."
            }
        },

        "reactions": {
            "girder self weight": {
             "...": "..."
            }
        },

        "forces_shell": {
            "...": "..."
        },

        "stresses_shell": {
            "...": "..."
        }
    }
    """
    ds_all = model.get_results()
    # Deduplicate: ospgrillage appends results on every analyze() call, so
    # a second analyze() (e.g. after adding the governing LL case) produces
    # duplicate Loadcase entries.  Keep first occurrence of each name and
    # drop the duplicates from ds_all so that .sel(Loadcase=lc) always
    # returns a 0-d slice (not a multi-row array that breaks float()).
    _seen_lcs: set = set()
    _unique_idx: list = []
    for _i, _lc in enumerate(ds_all.coords["Loadcase"].values):
        _s = str(_lc)
        if _s not in _seen_lcs:
            _seen_lcs.add(_s)
            _unique_idx.append(_i)
    if len(_unique_idx) < len(ds_all.coords["Loadcase"].values):
        ds_all = ds_all.isel(Loadcase=_unique_idx)
    loadcases = [str(lc) for lc in ds_all.coords["Loadcase"].values]

    # ── 1. Metadata from live model / ospgrillage APIs ────────────────
    nodes, members = _build_nodes_members()

    # Extract standard member groups via ospgrillage get_element API
    standard_groups = [
        "edge_beam", "exterior_main_beam_1", "exterior_main_beam_2",
        "interior_main_beam", "start_edge", "end_edge", "transverse_slab",
        "shell_slab"
    ]
    groups = {}
    for group_name in standard_groups:
        try:
            # get_element returns list of tags
            groups[group_name] = [int(e) for e in model.get_element(
                member=group_name, options="elements")]
        except Exception:
            groups[group_name] = []

    # Coordinate components
    ds0 = ds_all.isel(Loadcase=0)
    force_components = [str(
        c) for c in ds0["forces"].coords["Component"].values] if "forces" in ds0.data_vars else []
    disp_components = [str(
        c) for c in ds0["displacements"].coords["Component"].values] if "displacements" in ds0.data_vars else []

    # Classify members: longitudinal = both nodes share same Z and same Y
    _z_tol = 1e-3
    longitudinal_members = []
    transverse_members = []
    for tag, (n1, n2) in members.items():
        if n1 not in nodes or n2 not in nodes:
            continue
        if (abs(nodes[n1][2] - nodes[n2][2]) < _z_tol and
                abs(nodes[n1][1] - nodes[n2][1]) < _z_tol):
            longitudinal_members.append(tag)
        else:
            transverse_members.append(tag)

    data: dict = {
        "nodes": {str(k): list(v) for k, v in nodes.items()},
        "members": {str(k): list(v) for k, v in members.items()},
        "groups": groups,
        "longitudinal_members": longitudinal_members,
        "transverse_members": transverse_members,
        "edge_dist": float(edge_dist),
        "loadcases": list(loadcases),
        "force_components": force_components,
        "disp_components": disp_components,
        "forces": {},
        "displacements": {},
        "reactions": {},
        "forces_shell": {},
        "stresses_shell": {},
    }

    # ── 2. Results from xarray Dataset ────────────────────────────────
    # Extract full numpy arrays once per load case instead of one .sel()
    # call per (element/node × component) — avoids tens of thousands of
    # individual xarray label lookups.
    for lc in loadcases:
        ds = ds_all.sel(Loadcase=lc)
        str_lc = str(lc)

        # Displacements — one .values call → (n_nodes, n_comps) numpy array
        if "displacements" in ds.data_vars:
            arr  = ds["displacements"].values
            nids = ds["displacements"].coords["Node"].values
            cids = [str(c) for c in ds["displacements"].coords["Component"].values]
            data["displacements"][str_lc] = {
                str(int(n)): dict(zip(cids, row.tolist()))
                for n, row in zip(nids, arr)
            }

        # Forces — one .values call → (n_elems, n_comps) numpy array
        if "forces" in ds.data_vars:
            arr  = ds["forces"].values
            eids = ds["forces"].coords["Element"].values
            cids = [str(c) for c in ds["forces"].coords["Component"].values]
            data["forces"][str_lc] = {
                str(int(e)): dict(zip(cids, row.tolist()))
                for e, row in zip(eids, arr)
            }

        # Reactions — one .values call → (n_nodes, n_comps) numpy array
        if "reactions" in ds.data_vars:
            arr  = ds["reactions"].values
            nids = ds["reactions"].coords["Node"].values
            cids = [str(c) for c in ds["reactions"].coords["Component"].values]
            data["reactions"][str_lc] = {
                str(int(n)): dict(zip(cids, row.tolist()))
                for n, row in zip(nids, arr)
            }

        # Shell Forces — one .values call → (n_elems, n_comps) numpy array
        if "forces_shell" in ds.data_vars:
            arr  = ds["forces_shell"].values
            eids = ds["forces_shell"].coords["Element"].values
            cids = [str(c) for c in ds["forces_shell"].coords["Component"].values]
            data["forces_shell"][str_lc] = {
                str(int(e)): dict(zip(cids, row.tolist()))
                for e, row in zip(eids, arr)
            }

        # Shell Stresses — one .values call → (n_elems, n_stress_comps) numpy array
        if "stresses_shell" in ds.data_vars:
            arr  = ds["stresses_shell"].values
            eids = ds["stresses_shell"].coords["Element"].values
            cids = [str(c) for c in ds["stresses_shell"].coords["Stress"].values]
            data["stresses_shell"][str_lc] = {
                str(int(e)): dict(zip(cids, row.tolist()))
                for e, row in zip(eids, arr)
            }

    if dev:
        _dump(data, "bridge_plot_data_raw.json")

    data = post_process(data)

    if dev:
        _dump(data)
        _dump_crossbracing(data)
        _plot_dev(model, ds_all)

    return data


def _dump_crossbracing(data: dict) -> None:
    """Write raw crossbracing Vz forces to tools/crossbracing_results.json."""
    import math
    import warnings as _warnings

    EQ_TOL = 1e-3  # kN

    cb_chains  = data.get("crossbracings", [])
    forces     = data.get("forces", {})
    load_cases = data.get("loadcases", list(forces.keys()))

    results = {}

    for cb_num, chain in enumerate(cb_chains, start=1):
        mems = chain.get("members", [])
        if not mems:
            continue

        m_id = str(mems[0])

        start = chain.get("start")
        end   = chain.get("end")
        length_m = None
        if start and end:
            sc = start["coords"]
            ec = end["coords"]
            length_m = round(math.sqrt(sum((ec[i] - sc[i]) ** 2 for i in range(3))), 4)

        lc_results = []

        for lc in load_cases:
            lc_str = str(lc)
            elem_f = forces.get(lc_str, {}).get(m_id, {})

            vz_i = elem_f.get("Vz_i")
            vz_j = elem_f.get("Vz_j")

            if vz_i is None or vz_j is None:
                continue

            vz_i_kn = vz_i / 1e3
            vz_j_kn = vz_j / 1e3

            eq_ok   = abs(vz_i_kn + vz_j_kn) <= EQ_TOL
            warning = None

            if not eq_ok:
                warning = (
                    f"Vz_i={vz_i_kn:.4f} kN, Vz_j={vz_j_kn:.4f} kN — "
                    f"expected Vz_i = -Vz_j (diff={vz_i_kn + vz_j_kn:.4f} kN)"
                )
                _warnings.warn(
                    f"[CB {cb_num}] LC '{lc_str}': equilibrium violated — {warning}",
                    stacklevel=2,
                )

            lc_results.append({
                "load_case":      lc_str,
                "Vz_i_kN":        round(vz_i_kn, 4),
                "Vz_j_kN":        round(vz_j_kn, 4),
                "type":           "tension" if vz_i_kn > 0 else "compression",
                "equilibrium_ok": eq_ok,
                "warning":        warning,
            })

        # Forces are in global axis. Cross-bracings run along global Z, so Vz is their axial force.
        # Vz_i > 0 → tension, Vz_i < 0 → compression.
        tension_rows     = [r for r in lc_results if r["Vz_i_kN"] >  0]
        compression_rows = [r for r in lc_results if r["Vz_i_kN"] < 0]

        critical_tension = None
        if tension_rows:
            row = max(tension_rows, key=lambda r: r["Vz_i_kN"])  # largest positive = worst tension
            critical_tension = {
                "load_case": row["load_case"],
                "Vz_i_kN":  row["Vz_i_kN"],
                "Vz_j_kN":  row["Vz_j_kN"],
            }

        critical_compression = None
        if compression_rows:
            row = min(compression_rows, key=lambda r: r["Vz_i_kN"])  # most negative = worst compression
            critical_compression = {
                "load_case": row["load_case"],
                "Vz_i_kN":  row["Vz_i_kN"],
                "Vz_j_kN":  row["Vz_j_kN"],
            }

        left_girder  = chain.get("left_girder", "")
        right_girder = chain.get("right_girder", "")
        girder_pair  = f"{left_girder}-{right_girder}" if left_girder and right_girder else None

        results[str(cb_num)] = {
            "cb_number":            cb_num,
            "member_id":            m_id,
            "girder_pair":          girder_pair,
            "length_m":             length_m,
            "critical_tension":     critical_tension,
            "critical_compression": critical_compression,
            "results":              lc_results,
        }

    _TOOLS_DIR.mkdir(exist_ok=True)
    out_path = _TOOLS_DIR / "crossbracing_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print("=" * 60)
    print(f"[crossbracing] saved → {out_path.resolve()}")
    print("=" * 60)


def _extract_osdag_summary(result: dict) -> dict:
    if not result:
        return {}
    def _first(*keys):
        for k in keys:
            v = result.get(k)
            if v is not None:
                return v
        return None

    return {
        "section":     _first("section_size.designation", "Optimum.Designation"),
        "capacity_kN": _first("Member.tension_capacity",  "Design.Strength"),
        "efficiency":  _first("Member.efficiency",        "Optimum.UR"),
        "slenderness": result.get("Member.Slenderness"),
        "connection":  "Welded" if "Weld.Type" in result else "Bolted",
    }


def enrich_crossbracing_dump(pair_designs: dict) -> None:
    """
    Update tools/crossbracing_results.json with Osdag design results.

    Parameters
    ----------
    pair_designs : dict
        {
            "G1-G2": {
                "diagonal": {"tension": result_or_None, "compression": result_or_None},
                "chord":    {"tension": result_or_None, "compression": result_or_None},
            }, ...
        }
    """
    out_path = _TOOLS_DIR / "crossbracing_results.json"
    if not out_path.exists():
        return

    with open(out_path) as f:
        data = json.load(f)

    for entry in data.values():
        pair    = entry.get("girder_pair")
        designs = pair_designs.get(pair, {})

        diag_designs  = designs.get("diagonal", {})
        chord_designs = designs.get("chord",    {})

        if entry.get("critical_tension"):
            entry["critical_tension"]["osdag_diagonal"] = _extract_osdag_summary(
                diag_designs.get("tension") or {}
            )
            entry["critical_tension"]["osdag_chord"] = _extract_osdag_summary(
                chord_designs.get("tension") or {}
            )

        if entry.get("critical_compression"):
            entry["critical_compression"]["osdag_diagonal"] = _extract_osdag_summary(
                diag_designs.get("compression") or {}
            )
            entry["critical_compression"]["osdag_chord"] = _extract_osdag_summary(
                chord_designs.get("compression") or {}
            )

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

    print("=" * 60)
    print(f"[crossbracing] enriched with Osdag designs → {out_path.resolve()}")
    print("=" * 60)


def _dump(data: dict, filename: str = "bridge_plot_data.json") -> None:
    """Write data to tools/<filename>."""
    _TOOLS_DIR.mkdir(exist_ok=True)
    out_path = _TOOLS_DIR / filename
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print("=" * 60)
    print(f"[plot data] saved → {out_path.resolve()}")
    print("=" * 60)


def _plot_dev(model, ds_all) -> None:
    """
    Generate interactive plotly HTML files in tools/plots/ using the
    ospgrillage plotting API.  Called only when dev=True.

    Files produced
    --------------
    model.html  — 3-D mesh with node/element labels
    Mz.html     — Bending moment diagram      (plot_bmd,   all members)
    Fy.html     — Shear force diagram         (plot_sfd,   all members)
    Mx.html     — Torsion moment diagram      (plot_tmd,   all members)
    y.html      — Vertical deflection         (plot_def,   all members)
    Fx.html     — Axial force                 (plot_force, all members)
    Fz.html     — Transverse shear            (plot_force, all members)
    My.html     — Minor-axis bending moment   (plot_force, all members)
    """
    import ospgrillage as og

    plots_dir = _TOOLS_DIR / "plots"
    plots_dir.mkdir(exist_ok=True)

    # ── 1. Model visualisation ────────────────────────────────────────────
    try:
        fig = og.plot_model(
            model,
            backend="plotly",
            show_node_labels=True,
            show_element_labels=True,
            show=False,
        )
        out = plots_dir / "model.html"
        fig.write_html(str(out))
        print(f"[dev plot] model.html → {out}")
    except Exception as exc:
        print(f"[dev plot] model failed: {exc}")

    # ── 2. Convenience wrappers (Mz, Fy, Mx, y) ──────────────────────────
    convenience = [
        ("Mz.html", og.plot_bmd),
        ("Fy.html", og.plot_sfd),
        ("Mx.html", og.plot_tmd),
        ("y.html",  og.plot_def),
    ]
    for fname, fn in convenience:
        try:
            fig = fn(model, ds_all, backend="plotly", show=False)
            out = plots_dir / fname
            fig.write_html(str(out))
            print(f"[dev plot] {fname} → {out}")
        except Exception as exc:
            print(f"[dev plot] {fname} failed: {exc}")

    # ── 3. Generic plot_force for remaining components ────────────────────
    generic = ["Vx", "Vz", "My"]
    for comp in generic:
        fname = f"{comp}.html"
        try:
            fig = og.plot_force(
                model, ds_all,
                member=og.Members.ALL,
                component=comp,
                show=False,
            )
            out = plots_dir / fname
            fig.write_html(str(out))
            print(f"[dev plot] {fname} → {out}")
        except Exception as exc:
            print(f"[dev plot] {fname} failed: {exc}")
