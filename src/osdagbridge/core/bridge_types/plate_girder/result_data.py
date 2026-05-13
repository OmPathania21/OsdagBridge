"""
result_data.py
--------------
Extracts analysis results from a solved PlateGirderBridge into the flat dict
format consumed by plot_generator functions.
"""

from __future__ import annotations
import json
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[5] / "tools"


def restructure_data(bridge, dev: bool = False) -> dict:
    """
    Restructure xarray analysis results into a flat dict for plot_generator and
    any other consumer.  Optionally dumps to tools/bridge_plot_data.json.

    Parameters
    ----------
    bridge : PlateGirderBridge
        Fully solved bridge (design() already called).
    dev : bool
        If True, also write the result to tools/bridge_plot_data.json.

    Returns
    -------
    dict — structure::

        {
            "nodes": {
                "1": [x, y, z],          # node tag → [X, Y, Z] coords (m)
                "2": [x, y, z],
                ...
            },
            "members": {
                "1": [n1, n2],            # element tag → [i-node, j-node]
                ...
            },
            "longitudinal_members": [1, 2, 5, ...],   # tags where both nodes share same Z and Y (girder elements)
            "transverse_members":   [3, 4, 6, ...],   # tags where nodes differ in Z (deck/diaphragm elements)
            "edge_dist": 0.75,            # deck overhang (m); 0.0 if none
            "loadcases": [
                "girder self weight",
                "Deck slab load",
                ...
            ],
            "force_components": [         # all force component names present in dataset
                "Mx_i", "Mx_j",
                "My_i", "My_j",
                "Mz_i", "Mz_j",
                "Vx_i", "Vx_j",
                "Vy_i", "Vy_j",
                "Vz_i", "Vz_j",
            ],
            "disp_components": [          # all displacement component names
                "x", "y", "z",
                "theta_x", "theta_y", "theta_z",
            ],
            "forces": {
                "girder self weight": {
                    "1": {                # element tag (str)
                        "Mx_i": -0.71,   # N·m — raw SI from opensees
                        "Mx_j": -4478.2,
                        "Vy_i":  5971.9, # N
                        "Vy_j": -5971.9,
                        ...
                    },
                    ...
                },
                ...
            },
            "displacements": {
                "girder self weight": {
                    "1": {               # node tag (str)
                        "x":       0.0,
                        "y":       1.4e-05,  # m — raw SI
                        "z":       0.0,
                        "theta_x": 2.2e-05,  # rad
                        "theta_y": 0.0,
                        "theta_z": 3.8e-04,
                    },
                    ...
                },
                ...
            },
        }
    """
    ds_all     = bridge.get_results_dataset()
    loadcases  = bridge.get_available_loadcases()
    nodes, members = bridge.get_nodes_members()
    edge_dist  = bridge.get_edge_dist()

    ds0 = ds_all.sel(Loadcase=loadcases[0])
    force_components = [str(c) for c in ds0["forces"].coords["Component"].values]
    disp_components  = [str(c) for c in ds0["displacements"].coords["Component"].values]

    # Classify members: longitudinal = both nodes share same Z and same Y
    _z_tol = 1e-3
    longitudinal_members = []
    transverse_members   = []
    for tag, (n1, n2) in members.items():
        if (abs(nodes[n1][2] - nodes[n2][2]) < _z_tol and
                abs(nodes[n1][1] - nodes[n2][1]) < _z_tol):
            longitudinal_members.append(tag)
        else:
            transverse_members.append(tag)

    data: dict = {
        "nodes":                {str(k): list(v) for k, v in nodes.items()},
        "members":              {str(k): list(v) for k, v in members.items()},
        "longitudinal_members": longitudinal_members,   # both nodes same Z and Y
        "transverse_members":   transverse_members,     # nodes differ in Z
        "edge_dist":            float(edge_dist),
        "loadcases":            list(loadcases),
        "force_components":     force_components,
        "disp_components":      disp_components,
        "forces":               {},
        "displacements":        {},
    }

    for lc in loadcases:
        ds = ds_all.sel(Loadcase=lc)
        data["forces"][str(lc)] = {
            str(int(elem)): {
                comp: float(ds["forces"].sel(Element=elem, Component=comp).values)
                for comp in force_components
            }
            for elem in ds["forces"].coords["Element"].values
        }
        data["displacements"][str(lc)] = {
            str(int(node)): {
                comp: float(ds["displacements"].sel(Node=node, Component=comp).values)
                for comp in disp_components
            }
            for node in ds["displacements"].coords["Node"].values
        }

    if dev:
        _dump(data)

    return data


def _dump(data: dict) -> None:
    """Write data to tools/bridge_plot_data.json."""
    _TOOLS_DIR.mkdir(exist_ok=True)
    out_path = _TOOLS_DIR / "bridge_plot_data.json"
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print("=" * 60)
    print(f"[plot data] saved → {out_path.resolve()}")
    print("=" * 60)
