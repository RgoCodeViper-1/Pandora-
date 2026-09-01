"""
Genetic Algorithm — Evolving a Target String (NumPy-vectorized)
=================================================================
Same algorithm as genetic_string_evolution.py (Nature of Code ch. 9),
rewritten so the whole population lives in NumPy arrays instead of a
list of DNA objects. Fitness, crossover, and mutation all run as
array operations across the entire population at once, instead of
per-character Python loops.

Configuration is done via the CONFIG arrays/matrices below instead of
argparse — edit the values directly and run the file.

Why this is faster
-------------------
Old version: population_size × target_length individual Python-level
operations per generation (object attribute access, list indexing,
string joins).

This version: population_size × target_length operations run as a
handful of vectorized NumPy calls per generation — the same math,
executed in C instead of the Python interpreter loop.
"""

from __future__ import annotations

import string
import time

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edit these directly, no CLI args
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    "target":          "I am Pandora, your lovely assistant",
    "population_size": 600,
    "mutation_rate":   0.02,
    "max_generations": 20000,
    "print_every":     21,
    "seed":            42,          # None for non-deterministic runs
}

# Alphabet the GA is allowed to guess with — stored as a NumPy array of
# single characters so it can be indexed directly by integer codes.
ALPHABET: np.ndarray = np.array(
    list(string.ascii_letters + string.digits + string.punctuation + " ")
)
ALPHABET_SIZE = len(ALPHABET)


# ─────────────────────────────────────────────────────────────────────────────
# Encoding helpers — string <-> integer index matrix
# ─────────────────────────────────────────────────────────────────────────────

def encode(s: str) -> np.ndarray:
    """Map each character to its index in ALPHABET. Shape: (len(s),)."""
    lookup = {ch: i for i, ch in enumerate(ALPHABET)}
    return np.array([lookup[ch] for ch in s], dtype=np.int32)


def decode_row(row: np.ndarray) -> str:
    """Turn one row of integer indices back into a string."""
    return "".join(ALPHABET[row])


# ─────────────────────────────────────────────────────────────────────────────
# Population — entirely array-based, no per-individual objects
# ─────────────────────────────────────────────────────────────────────────────

class Population:
    """
    population : int32 matrix, shape (pop_size, gene_length)
                 each row is one candidate string, encoded as alphabet indices
    fitness    : float32 vector, shape (pop_size,)
    """

    def __init__(
        self,
        target: str,
        population_size: int,
        mutation_rate: float,
        rng: np.random.Generator,
    ) -> None:
        self.target_codes = encode(target)          # shape (L,)
        self.gene_length = len(target)
        self.pop_size = population_size
        self.mutation_rate = mutation_rate
        self.rng = rng

        # Random initial population: pop_size rows x gene_length columns
        self.population = self.rng.integers(
            0, ALPHABET_SIZE, size=(self.pop_size, self.gene_length), dtype=np.int32
        )
        self.fitness = np.zeros(self.pop_size, dtype=np.float32)
        self.generations = 0
        self.best_phrase = ""
        self.finished = False

        self._calc_fitness()

    # ── Fitness — vectorized across the whole population at once ───────────
    def _calc_fitness(self) -> None:
        # matches: boolean matrix, True where population[i, j] == target[j]
        matches = self.population == self.target_codes  # broadcast, shape (pop, L)
        scores = matches.sum(axis=1) / self.gene_length  # shape (pop,)
        self.fitness = (scores ** 2).astype(np.float32)  # square, same as book

        best_idx = int(np.argmax(self.fitness))
        self.best_phrase = decode_row(self.population[best_idx])
        self.finished = self.best_phrase == "".join(ALPHABET[self.target_codes])

    def average_fitness(self) -> float:
        return float(self.fitness.mean())

    # ── One full generation: select -> crossover -> mutate ─────────────────
    def reproduce(self) -> None:
        pop, L = self.pop_size, self.gene_length

        # 1. Selection — fitness-weighted sampling of parent indices.
        #    Two full index arrays, one for each parent slot, drawn in one
        #    vectorized call instead of pop*2 individual random.choices().
        probs = self.fitness / self.fitness.sum() if self.fitness.sum() > 0 else None
        parent_a_idx = self.rng.choice(pop, size=pop, p=probs)
        parent_b_idx = self.rng.choice(pop, size=pop, p=probs)

        parents_a = self.population[parent_a_idx]  # shape (pop, L)
        parents_b = self.population[parent_b_idx]  # shape (pop, L)

        # 2. Crossover — one random midpoint per child, vectorized with a
        #    boolean mask: for each row, columns < midpoint come from
        #    parent_a, columns >= midpoint come from parent_b.
        midpoints = self.rng.integers(0, L + 1, size=pop)              # shape (pop,)
        col_idx = np.arange(L)[None, :]                                # shape (1, L)
        take_from_a = col_idx < midpoints[:, None]                     # shape (pop, L)
        children = np.where(take_from_a, parents_a, parents_b)

        # 3. Mutation — per-gene probability mask, replace masked genes
        #    with fresh random alphabet indices in one shot.
        mutate_mask = self.rng.random(size=(pop, L)) < self.mutation_rate
        random_genes = self.rng.integers(0, ALPHABET_SIZE, size=(pop, L), dtype=np.int32)
        children = np.where(mutate_mask, random_genes, children)

        self.population = children.astype(np.int32)
        self.generations += 1
        self._calc_fitness()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def run(cfg: dict) -> None:
    target          = cfg["target"]
    population_size = cfg["population_size"]
    mutation_rate    = cfg["mutation_rate"]
    max_generations  = cfg["max_generations"]
    print_every      = cfg["print_every"]
    seed             = cfg["seed"]

    rng = np.random.default_rng(seed)

    print(f"Target      : {target!r}")
    print(f"Population  : {population_size}")
    print(f"Mutation    : {mutation_rate:.3f}")
    print("-" * 60)

    pop = Population(
        target=target,
        population_size=population_size,
        mutation_rate=mutation_rate,
        rng=rng,
    )

    start = time.monotonic()
    while not pop.finished and pop.generations < max_generations:
        pop.reproduce()
        if pop.generations % print_every == 0 or pop.finished:
            print(
                f"gen {pop.generations:>5} | "
                f"avg fitness {pop.average_fitness():.4f} | "
                f"best: {pop.best_phrase!r}"
            )

    elapsed = time.monotonic() - start
    print("-" * 60)
    if pop.finished:
        print(f"✔ Converged in {pop.generations} generations ({elapsed:.2f}s)")
    else:
        print(f"✘ Did not converge within {max_generations} generations")
    print(f"Final guess : {pop.best_phrase!r}")


if __name__ == "__main__":
    run(CONFIG)