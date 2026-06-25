"""
run_optimization.py — isolated optimisation runner (cross-platform)
===================================================================

Launched by the GUI as a **separate process**:

    python -m osdagbridge.core.optimizer.run_optimization <in_pickle> <out_pickle>

Why a separate process
----------------------
The GUI process has Qt (and a live OpenSees domain) loaded, which is NOT safe to
fork and, on Windows, would force ``spawn`` to re-import the GUI's ``__main__``
(re-running ``create_sqlite`` and the whole UI in every worker). Running the
optimisation here instead means:

  * This process is **clean** (no Qt) — so its internal pool can use ``fork`` /
    ``forkserver`` on Linux/macOS and ``spawn`` on Windows safely.
  * On Windows, ``spawn`` re-imports only THIS module (guarded, side-effect free),
    never the GUI app — so the app's ``__main__`` is left completely untouched.

Protocol
--------
Input  : a pickle file holding ``{"base_input_dict": dict, "params": dict}``.
Output : progress is streamed to stdout, one JSON object per line, each prefixed
         with the sentinel ``@@OPT@@`` so the parent can pick them out of any
         other noise. The final result (winning ``input_dict`` + records +
         summary) is pickled to ``<out_pickle>``.

This module must stay import-light and free of GUI imports.
"""

from __future__ import annotations

import json
import pickle
import sys
import threading

SENTINEL = "@@OPT@@"

# Progress lines (main thread) and core-monitor lines (drainer thread) both write
# to stdout, so serialise whole-line writes to keep them from interleaving.
_EMIT_LOCK = threading.Lock()


def _emit(obj: dict) -> None:
    """Write one sentinel-prefixed JSON line to stdout and flush immediately."""
    try:
        line = SENTINEL + json.dumps(obj) + "\n"
        with _EMIT_LOCK:
            sys.__stdout__.write(line)
            sys.__stdout__.flush()
    except Exception:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        _emit({"type": "error", "message": "usage: run_optimization <in> <out>"})
        return 2
    in_path, out_path = argv[0], argv[1]

    try:
        with open(in_path, "rb") as fh:
            payload = pickle.load(fh)
        base_input_dict = payload["base_input_dict"]
        params = dict(payload.get("params") or {})

        from osdagbridge.core.optimizer.parallel_bridge_optimizer import optimize_parallel

        _emit({"type": "start", "pop_size": params.get("pop_size"),
               "generations": params.get("generations")})

        # --- live per-core monitor ------------------------------------------
        # Workers are recycled per design (new PID each time), so map each PID to
        # a stable "core" slot (reusing slots as workers finish) and tag each
        # started design with a running number — that's the "Core C → Design #k"
        # the GUI popup shows.
        _core_state = {
            "slot_of": {},   # pid -> core slot
            "free":    [],   # freed slots ready to reuse
            "count":   0,    # designs started so far
        }
        _core_lock = threading.Lock()

        def _on_core_event(ev: dict) -> None:
            pid = ev.get("pid")
            kind = ev.get("ev")
            if kind == "start":
                with _core_lock:
                    slot = _core_state["slot_of"].get(pid)
                    if slot is None:
                        slot = (_core_state["free"].pop()
                                if _core_state["free"]
                                else len(_core_state["slot_of"]))
                        _core_state["slot_of"][pid] = slot
                    _core_state["count"] += 1
                    design_no = _core_state["count"]
                _emit({
                    "type": "core", "ev": "start",
                    "core": slot, "pid": pid, "design_no": design_no,
                    "n": ev.get("n"), "t_slab": ev.get("t_slab"),
                    "D": ev.get("D"), "bf": ev.get("bf"),
                    "tf": ev.get("tf"), "tw": ev.get("tw"),
                })
            elif kind == "done":
                with _core_lock:
                    slot = _core_state["slot_of"].pop(pid, None)
                    if slot is not None:
                        _core_state["free"].append(slot)
                _emit({
                    "type": "core", "ev": "done",
                    "core": slot, "pid": pid,
                    "feasible": ev.get("feasible"),
                    "weight": ev.get("weight"), "secs": ev.get("secs"),
                })

        def _on_candidate(info: dict) -> None:
            # Forward only JSON-serialisable fields (drop numpy vector etc.).
            comp = info.get("components") or {}
            _emit({
                "type":          "progress",
                "done":          int(info["done"]),
                "total":         int(info["total"]),
                "gen":           int(info["gen"]),
                "overall_done":  int(info["overall_done"]),
                "overall_total": int(info["overall_total"]),
                "feasible":      bool(info["feasible"]),
                "weight_kN":     info.get("weight_kN"),
                "best_kN":       info.get("best_kN"),
                "steel_kN":      comp.get("steel_girders_kN"),
                "deck_kN":       comp.get("deck_concrete_kN"),
            })

        # build_cad stays False here: CAD is built once, in the GUI process, on
        # the winning design (pythonocc + rendering belong with the GUI).
        params.pop("build_cad", None)
        params.pop("on_candidate", None)
        bridge = optimize_parallel(
            base_input_dict,
            on_candidate=_on_candidate,
            on_core_event=_on_core_event,
            build_cad=False,
            **params,
        )

        result = {
            "input_dict": bridge.input_dict,
            "records":    getattr(bridge, "optimization_records", []),
            "summary":    getattr(bridge, "optimization_summary", {}),
        }
        with open(out_path, "wb") as fh:
            pickle.dump(result, fh)

        _emit({"type": "done"})
        return 0

    except Exception as exc:
        import traceback
        _emit({"type": "error", "message": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
