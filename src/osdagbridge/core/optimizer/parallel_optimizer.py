"""
parallel_optimizer.py
----------------------
Generic *synchronous* parallel Differential Evolution.

This mirrors the sequential engine in ``optimizer.py`` but evaluates the whole
trial population of each generation across a process pool, so the expensive
per-candidate work (e.g. a full grillage + IRC 22 design pipeline) runs on every
CPU core at once.

Why multiprocessing and not threads
------------------------------------
The per-candidate evaluation is CPU-bound, pure-Python work.  CPython's GIL
serialises that, so ``threading`` gives almost no speed-up.  Real parallelism
needs separate processes (``concurrent.futures.ProcessPoolExecutor``), one per
core.

Synchronous DE
--------------
Classic DE evaluates one trial and immediately does greedy selection.  To
parallelise without changing the algorithm's behaviour we use the *synchronous*
(generational) variant:

  1. Build **all** ``pop_size`` trial vectors first (mutation DE/rand/1 +
     binomial crossover).  This only *reads* the current population, so doing the
     whole batch at once is safe.
  2. Evaluate **all** trials in parallel across the pool.
  3. Do greedy selection (trial vs. parent) sequentially - it is cheap.

Determinism
-----------
The RNG that drives mutation/crossover lives entirely in the parent process, so
a given ``seed`` reproduces the same search regardless of worker count.  Only the
(pure) fitness evaluation is farmed out.

Picklability contract
---------------------
``fitness_func`` and ``bounds_func`` are sent to worker processes, so they must
be **module-level** callables (not lambdas / closures).  Per-run configuration
that the fitness function needs (large/un-picklable context) should be installed
into each worker **once** via ``worker_init`` / ``worker_initargs`` rather than
captured in a closure or shipped with every task.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


# Progress callback signature:
#   progress_cb(done, total, gen, vector, fitness)
#     done    : candidates evaluated so far in the current batch (1-based)
#     total   : batch size (== pop_size); the "x/50" denominator
#     gen     : generation index (0 = initial population, then 1, 2, ...)
#     vector  : the design vector just evaluated
#     fitness : its fitness (np.inf if infeasible)
ProgressCallback = Callable[[int, int, int, np.ndarray, float], None]


@dataclass
class OptimisationResult:
    """
    Returned by ``ParallelOptimizer.run()`` - field-compatible with the
    sequential engine's ``OptimisationResult``.

    Attributes
    ----------
    best_vector   : the optimal raw np.ndarray
    best_fitness  : objective value at best_vector (np.inf if no feasible
                    candidate was ever found)
    history       : np.ndarray of shape (generations, n_dims + 1); each row is
                    ``[*best_vector, best_fitness]`` for that generation.
                    ``history[:, :-1]`` recovers the best vectors,
                    ``history[:, -1]`` is the fitness curve.
    generations   : number of generations actually run
    converged     : True if improvement fell below ``tol`` before max generations
    feasible      : True if at least one feasible candidate was found
    """
    best_vector  : np.ndarray
    best_fitness : float
    history      : np.ndarray
    generations  : int
    converged    : bool = False
    feasible     : bool  = True


# ------------------------------------------------------------------------------
#  Core synchronous parallel DE
# ------------------------------------------------------------------------------

def parallel_differential_evolution(
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
    worker_init     : Optional[Callable]     = None,
    worker_initargs : Sequence[Any]          = (),
    progress_cb     : Optional[ProgressCallback] = None,
    start_method    : Optional[str]          = None,
    max_tasks_per_child : Optional[int]      = 1,
) -> OptimisationResult:
    """
    Synchronous (generational) parallel Differential Evolution.

    ``max_tasks_per_child`` recycles a pool worker after it has evaluated this
    many designs (default 1 = a fresh process per design). Recycling is what
    actually returns a design's memory to the OS: ``gc`` / ``ops.wipe()`` free
    Python and the FE domain, but C-level allocations inside OpenSees / ospgrillage
    are not handed back while the worker lives, so over a long run they pile up
    and only drop when the pool finally shuts down. Tearing the worker down after
    each design reclaims everything immediately. ``None`` keeps workers for the
    whole run (fastest, but memory grows with the number of designs).

    ``start_method`` selects the multiprocessing start method for the pool
    ("spawn", "fork", "forkserver"). Use "spawn" when the parent process has a
    live GUI / non-fork-safe native state (Qt, OpenSees): forked children would
    inherit that state and crash. ``None`` uses the platform default.

    Constraint handling
    -------------------
    There is no separate constraint callback: ``fitness_func`` must return a
    finite objective value for feasible vectors and ``+inf`` (``math.inf`` /
    ``np.inf``) for infeasible ones.  Folding the feasibility check and the
    objective into a single call keeps each candidate to **one** cross-process
    round-trip.  ``+inf`` candidates always lose greedy selection, so an entirely
    infeasible start population is fine - the search converges once mutations
    land in the feasible region.

    Boundary handling
    -----------------
    Mutant vectors are clipped to their own ``bounds_func(mutant)`` envelope
    after mutation (bounds may depend on the vector itself).
    """
    def _evaluate_batch(executor, pop, gen):
        """
        Evaluate a whole population in parallel, returning fitness ordered to
        match ``pop``. Results are placed back by index (so selection stays
        deterministic) while progress fires per candidate as each completes.
        """
        n   = len(pop)
        out = np.empty(n, dtype=float)
        futures = {executor.submit(fitness_func, pop[i]): i for i in range(n)}
        done = 0
        for fut in as_completed(futures):
            i        = futures[fut]
            out[i]   = fut.result()
            done    += 1
            if progress_cb is not None:
                progress_cb(done, n, gen, pop[i], float(out[i]))
        return out

    rng    = np.random.default_rng(seed)
    n_dims = len(initial_guess)

    # Outer envelope for the initial scatter comes from the warm-start point.
    init_bounds = bounds_func(initial_guess)
    lo          = init_bounds[:, 0]
    hi          = init_bounds[:, 1]

    population    = rng.uniform(lo, hi, size=(pop_size, n_dims))
    population[0] = np.clip(initial_guess, lo, hi)   # warm-start slot 0

    history    = np.empty((generations, n_dims + 1))
    converged  = False
    actual_gen = generations

    import multiprocessing as _mp
    mp_context = _mp.get_context(start_method) if start_method else None

    # max_tasks_per_child requires Python 3.11+; only pass it when set so the
    # engine still imports on older runtimes.
    pool_kwargs = {}
    if max_tasks_per_child is not None:
        pool_kwargs["max_tasks_per_child"] = max_tasks_per_child

    with ProcessPoolExecutor(
        max_workers = max_workers,
        initializer = worker_init,
        initargs    = tuple(worker_initargs),
        mp_context  = mp_context,
        **pool_kwargs,
    ) as executor:

        # --- Evaluate the initial population in parallel ----------------------
        fitness = _evaluate_batch(executor, population, gen=0)

        best_idx     = int(np.argmin(fitness))
        best_fitness = float(fitness[best_idx])
        best_vector  = population[best_idx].copy()

        # --- Main generational loop ------------------------------------------
        for gen in range(generations):

            # 1) Build the entire trial population (reads current pop only).
            trials = np.empty_like(population)
            for i in range(pop_size):
                candidates = [j for j in range(pop_size) if j != i]
                a, b, c    = rng.choice(candidates, size=3, replace=False)

                mutant     = population[a] + F * (population[b] - population[c])
                dep_bounds = bounds_func(mutant)
                mutant     = np.clip(mutant, dep_bounds[:, 0], dep_bounds[:, 1])

                cross_mask = rng.random(n_dims) < CR
                cross_mask[rng.integers(0, n_dims)] = True   # force >=1 dim
                trials[i]  = np.where(cross_mask, mutant, population[i])

            # 2) Evaluate every trial in parallel (the expensive step).
            trial_fitness = _evaluate_batch(executor, trials, gen=gen + 1)

            # 3) Greedy selection (cheap, sequential).
            improved = trial_fitness <= fitness
            population[improved] = trials[improved]
            fitness[improved]    = trial_fitness[improved]

            gen_best_idx = int(np.argmin(fitness))
            if fitness[gen_best_idx] < best_fitness:
                best_fitness = float(fitness[gen_best_idx])
                best_vector  = population[gen_best_idx].copy()

            history[gen, :-1] = best_vector
            history[gen,  -1] = best_fitness

            # Convergence: best improved by less than tol over one generation.
            if gen > 1 and abs(history[gen - 1, -1] - best_fitness) < tol:
                converged  = True
                actual_gen = gen + 1
                break

    history = history[:actual_gen]   # trim unused rows on early exit

    return OptimisationResult(
        best_vector  = best_vector,
        best_fitness = best_fitness,
        history      = history,
        generations  = actual_gen,
        converged    = converged,
        feasible     = np.isfinite(best_fitness),
    )


# ------------------------------------------------------------------------------
#  Public wrapper (mirrors Optimizer.run from optimizer.py)
# ------------------------------------------------------------------------------

class ParallelOptimizer:
    """Reusable parallel optimiser. Drop-in parallel counterpart of ``Optimizer``."""

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
        worker_init     : Optional[Callable]     = None,
        worker_initargs : Sequence[Any]          = (),
        progress_cb     : Optional[ProgressCallback] = None,
        start_method    : Optional[str]          = None,
        max_tasks_per_child : Optional[int]      = 1,
    ) -> OptimisationResult:

        return parallel_differential_evolution(
            fitness_func    = fitness_func,
            bounds_func     = bounds_func,
            initial_guess   = initial_guess,
            pop_size        = pop_size,
            generations     = generations,
            F               = F,
            CR              = CR,
            tol             = tol,
            seed            = seed,
            max_workers     = max_workers,
            worker_init     = worker_init,
            worker_initargs = worker_initargs,
            progress_cb     = progress_cb,
            start_method    = start_method,
            max_tasks_per_child = max_tasks_per_child,
        )
