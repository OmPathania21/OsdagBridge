"""
threaded_optimizer.py
----------------------
Generic *synchronous* parallel Differential Evolution using **threads**
(``concurrent.futures.ThreadPoolExecutor``) instead of processes.

How this differs from ``parallel_optimizer.py``
-----------------------------------------------
  * Workers are threads in the *same* process, so there is **no pickling, no
    forkserver/spawn, no separate interpreters**. ``fitness_func`` may be a plain
    closure that captures per-run state directly (no worker_init / global config).
  * Because threads share memory, this sidesteps every multiprocessing-startup
    problem (Qt fork-safety, import re-runs, pickle errors).

Important caveats (threads, not processes)
------------------------------------------
  * **GIL:** pure-Python CPU work is serialised by the GIL, so threads only give
    real speed-up while the heavy work sits in C extensions that *release* the
    GIL. Whether that holds here is something to measure, not assume.
  * **Shared global state:** all threads share the one process. If ``fitness_func``
    touches process-global state (e.g. a single global OpenSees domain), running
    several at once can corrupt results. Use ``max_workers`` / an external lock to
    control concurrency if so.

Synchronous DE, determinism and the progress callback all behave exactly as in
``parallel_optimizer.py``. The progress callback and greedy selection run on the
calling thread (the one iterating ``as_completed``), which is convenient for GUIs
— only ``fitness_func`` itself runs on worker threads.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import numpy as np


# progress_cb(done, total, gen, vector, fitness) — see parallel_optimizer for semantics.
ProgressCallback = Callable[[int, int, int, np.ndarray, float], None]


@dataclass
class OptimisationResult:
    """Field-compatible with parallel_optimizer.OptimisationResult."""
    best_vector  : np.ndarray
    best_fitness : float
    history      : np.ndarray
    generations  : int
    converged    : bool = False
    feasible     : bool  = True


def threaded_differential_evolution(
    fitness_func    : Callable[[np.ndarray], float],
    bounds_func     : Callable[[np.ndarray], np.ndarray],
    initial_guess   : np.ndarray,
    pop_size        : int,
    generations     : int,
    F               : float,
    CR              : float,
    tol             : float,
    seed            : int,
    max_workers     : Optional[int]          = None,
    progress_cb     : Optional[ProgressCallback] = None,
) -> OptimisationResult:
    """
    Synchronous (generational) Differential Evolution evaluated on a thread pool.

    Constraint handling, boundary clipping, determinism and history are identical
    to the process-based engine: ``fitness_func`` returns a finite objective for
    feasible vectors and ``+inf`` for infeasible ones.
    """
    def _evaluate_batch(executor, pop, gen):
        n   = len(pop)
        out = np.empty(n, dtype=float)
        futures = {executor.submit(fitness_func, pop[i]): i for i in range(n)}
        done = 0
        for fut in as_completed(futures):
            i      = futures[fut]
            out[i] = fut.result()
            done  += 1
            if progress_cb is not None:
                progress_cb(done, n, gen, pop[i], float(out[i]))
        return out

    rng    = np.random.default_rng(seed)
    n_dims = len(initial_guess)

    init_bounds = bounds_func(initial_guess)
    lo          = init_bounds[:, 0]
    hi          = init_bounds[:, 1]

    population    = rng.uniform(lo, hi, size=(pop_size, n_dims))
    population[0] = np.clip(initial_guess, lo, hi)

    history    = np.empty((generations, n_dims + 1))
    converged  = False
    actual_gen = generations

    with ThreadPoolExecutor(max_workers=max_workers) as executor:

        fitness = _evaluate_batch(executor, population, gen=0)

        best_idx     = int(np.argmin(fitness))
        best_fitness = float(fitness[best_idx])
        best_vector  = population[best_idx].copy()

        for gen in range(generations):
            trials = np.empty_like(population)
            for i in range(pop_size):
                candidates = [j for j in range(pop_size) if j != i]
                a, b, c    = rng.choice(candidates, size=3, replace=False)

                mutant     = population[a] + F * (population[b] - population[c])
                dep_bounds = bounds_func(mutant)
                mutant     = np.clip(mutant, dep_bounds[:, 0], dep_bounds[:, 1])

                cross_mask = rng.random(n_dims) < CR
                cross_mask[rng.integers(0, n_dims)] = True
                trials[i]  = np.where(cross_mask, mutant, population[i])

            trial_fitness = _evaluate_batch(executor, trials, gen=gen + 1)

            improved = trial_fitness <= fitness
            population[improved] = trials[improved]
            fitness[improved]    = trial_fitness[improved]

            gen_best_idx = int(np.argmin(fitness))
            if fitness[gen_best_idx] < best_fitness:
                best_fitness = float(fitness[gen_best_idx])
                best_vector  = population[gen_best_idx].copy()

            history[gen, :-1] = best_vector
            history[gen,  -1] = best_fitness

            if gen > 1 and abs(history[gen - 1, -1] - best_fitness) < tol:
                converged  = True
                actual_gen = gen + 1
                break

    history = history[:actual_gen]

    return OptimisationResult(
        best_vector  = best_vector,
        best_fitness = best_fitness,
        history      = history,
        generations  = actual_gen,
        converged    = converged,
        feasible     = np.isfinite(best_fitness),
    )


class ThreadedOptimizer:
    """Reusable thread-based optimiser. Drop-in counterpart of ParallelOptimizer."""

    @staticmethod
    def run(
        fitness_func    : Callable[[np.ndarray], float],
        bounds_func     : Callable[[np.ndarray], np.ndarray],
        initial_guess   : np.ndarray,
        tol             : float                  = 1e-3,
        pop_size        : int                    = 60,
        generations     : int                    = 300,
        F               : float                  = 0.8,
        CR              : float                  = 0.9,
        seed            : int                    = 42,
        max_workers     : Optional[int]          = None,
        progress_cb     : Optional[ProgressCallback] = None,
    ) -> OptimisationResult:
        return threaded_differential_evolution(
            fitness_func  = fitness_func,
            bounds_func   = bounds_func,
            initial_guess = initial_guess,
            pop_size      = pop_size,
            generations   = generations,
            F             = F,
            CR            = CR,
            tol           = tol,
            seed          = seed,
            max_workers   = max_workers,
            progress_cb   = progress_cb,
        )
