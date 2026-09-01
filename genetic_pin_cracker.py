"""
Genetic Algorithm — Guessing a 4-Digit Numeric Combination
=============================================================
Same evolving principle as genetic_string_evolution_fast.py, applied
to a 4-digit PIN instead of a text phrase.

Feedback model
--------------
A real lock only tells you "correct" or "incorrect" for the whole
combination — that gives a GA nothing to select on, since every wrong
guess looks equally wrong. To let evolution actually work, the fitness
signal here is Mastermind-style: for each guess, how many digit
POSITIONS are correct (digit 7 in slot 2 counts, even if slots 1/3/4
are wrong). This is the same kind of partial-credit feedback a
physical device *could* expose (e.g. a keypad that flashes per correct
digit), and it's what makes evolutionary search actually outperform
blind random guessing.

If you only have pure pass/fail feedback (no partial credit), a GA has
no gradient to climb and brute-force / random guessing is provably as
good as anything else — that comparison is included below so you can
see the difference directly.

Two strategies are run back-to-back on the SAME hidden target so you
can compare guesses-to-solve directly:
    1. Pure random guessing   (baseline)
    2. Genetic algorithm      (evolving guesses using positional feedback)
"""

from __future__ import annotations

import time

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    "digits":          6,       # length of the combination
    "digit_range":     10,      # 0-9
    "population_size": 60,
    "mutation_rate":   0.15,    # higher than text version — tiny search space
    "max_generations": 2000,
    "seed":            None,    # None = different target/run every time
    "target":          "161321",    # None = generate random target
}


# ─────────────────────────────────────────────────────────────────────────────
# Hidden target — the thing being "cracked"
# ─────────────────────────────────────────────────────────────────────────────

def make_target(
        digits: int, digit_range: int, rng: np.random.Generator, override: str | None = None
) -> np.ndarray:
    if override is not None:
        if len(override) != digits:
            raise ValueError(f"target {override!r} must be exactly {digits} digits long")
        if not override.isdigit():
            raise ValueError(f"target {override!r} must contain only digits")
        return np.array([int(ch) for ch in override], dtype=np.int32)
    return rng.integers(0, digit_range, size=digits, dtype=np.int32)



def fmt(code: np.ndarray) -> str:
    return "".join(str(d) for d in code)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — pure random guessing (baseline)
# ─────────────────────────────────────────────────────────────────────────────

def random_guess_search(
    target: np.ndarray,
    digits: int,
    digit_range: int,
    rng: np.random.Generator,
    max_attempts: int,
) -> tuple[int, float]:
    start = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        guess = rng.integers(0, digit_range, size=digits, dtype=np.int32)
        if np.array_equal(guess, target):
            return attempt, time.monotonic() - start
    return -1, time.monotonic() - start


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — genetic algorithm using positional-match feedback
# ─────────────────────────────────────────────────────────────────────────────

class PinPopulation:
    """
    population : int32 matrix, shape (pop_size, digits) — each row is one guess
    fitness    : float32 vector, shape (pop_size,) — fraction of digits correct
    """

    def __init__(
        self,
        target: np.ndarray,
        population_size: int,
        digits: int,
        digit_range: int,
        mutation_rate: float,
        rng: np.random.Generator,
    ) -> None:
        self.target = target
        self.pop_size = population_size
        self.digits = digits
        self.digit_range = digit_range
        self.mutation_rate = mutation_rate
        self.rng = rng

        self.population = self.rng.integers(
            0, digit_range, size=(population_size, digits), dtype=np.int32
        )
        self.fitness = np.zeros(population_size, dtype=np.float32)
        self.generations = 0
        self.attempts_used = 0          # total guesses actually "submitted" to the lock
        self.best_guess = self.population[0]
        self.finished = False

        self._evaluate()

    # ── Fitness: count of digits in the correct position ───────────────────
    def _evaluate(self) -> None:
        matches = self.population == self.target                # (pop, digits)
        scores = matches.sum(axis=1) / self.digits               # (pop,)
        self.fitness = (scores ** 2).astype(np.float32)

        self.attempts_used += self.pop_size   # every individual = one guess submitted

        best_idx = int(np.argmax(scores))
        self.best_guess = self.population[best_idx]
        self.finished = bool(matches[best_idx].all())

    def reproduce(self) -> None:
        pop, n = self.pop_size, self.digits

        probs = self.fitness / self.fitness.sum() if self.fitness.sum() > 0 else None
        parent_a_idx = self.rng.choice(pop, size=pop, p=probs)
        parent_b_idx = self.rng.choice(pop, size=pop, p=probs)
        parents_a = self.population[parent_a_idx]
        parents_b = self.population[parent_b_idx]

        # Crossover — random midpoint per child (digits is short, so this
        # is coarse; mutation carries most of the search at this scale)
        midpoints = self.rng.integers(0, n + 1, size=pop)
        col_idx = np.arange(n)[None, :]
        take_from_a = col_idx < midpoints[:, None]
        children = np.where(take_from_a, parents_a, parents_b)

        # Mutation — replace individual digits at random
        mutate_mask = self.rng.random(size=(pop, n)) < self.mutation_rate
        random_digits = self.rng.integers(0, self.digit_range, size=(pop, n), dtype=np.int32)
        children = np.where(mutate_mask, random_digits, children)

        self.population = children.astype(np.int32)
        self.generations += 1
        self._evaluate()


def genetic_search(
    target: np.ndarray,
    digits: int,
    digit_range: int,
    population_size: int,
    mutation_rate: float,
    rng: np.random.Generator,
    max_generations: int,
) -> tuple[int, int, float]:
    """Returns (generations_used, total_attempts_used, elapsed_seconds), or (-1, attempts, elapsed) on failure."""
    start = time.monotonic()
    pop = PinPopulation(
        target=target,
        population_size=population_size,
        digits=digits,
        digit_range=digit_range,
        mutation_rate=mutation_rate,
        rng=rng,
    )
    while not pop.finished and pop.generations < max_generations:
        pop.reproduce()

    elapsed = time.monotonic() - start
    if pop.finished:
        return pop.generations, pop.attempts_used, elapsed
    return -1, pop.attempts_used, elapsed


# ─────────────────────────────────────────────────────────────────────────────
# Runner — head-to-head comparison
# ─────────────────────────────────────────────────────────────────────────────

def run(cfg: dict) -> None:
    digits = cfg["digits"]
    digit_range = cfg["digit_range"]
    rng_master = np.random.default_rng(cfg["seed"])

    target = make_target(digits, digit_range, rng_master, override=cfg.get("target"))
    total_combinations = digit_range ** digits

    print(f"Hidden target        : {fmt(target)}  (not shown to either strategy)")
    print(f"Search space         : {total_combinations:,} possible combinations")
    print("=" * 60)

    # ── Strategy 1: pure random guessing ────────────────────────────────────
    rng_random = np.random.default_rng(rng_master.integers(0, 2**31))
    max_random_attempts = total_combinations * 20  # generous cap
    attempt, elapsed = random_guess_search(
        target, digits, digit_range, rng_random, max_random_attempts
    )
    print("[Random guessing]")
    if attempt == -1:
        print(f"  ✘ Failed to find target within {max_random_attempts:,} attempts")
    else:
        print(f"  ✔ Found in {attempt:,} attempts ({elapsed:.4f}s)")

    print("-" * 60)

    # ── Strategy 2: genetic algorithm ───────────────────────────────────────
    rng_ga = np.random.default_rng(rng_master.integers(0, 2**31))
    generations, attempts_used, elapsed = genetic_search(
        target,
        digits,
        digit_range,
        population_size=cfg["population_size"],
        mutation_rate=cfg["mutation_rate"],
        rng=rng_ga,
        max_generations=cfg["max_generations"],
    )
    print("[Genetic algorithm]")
    if generations == -1:
        print(f"  ✘ Failed to converge within {cfg['max_generations']} generations "
              f"({attempts_used:,} guesses submitted)")
    else:
        print(f"  ✔ Found in {generations} generations "
              f"({attempts_used:,} total guesses submitted, {elapsed:.4f}s)")

    print("=" * 60)
    if attempt != -1 and generations != -1:
        # ratio > 1  → random needed more guesses than GA (GA more efficient)
        # ratio < 1  → GA needed more guesses than random (GA less efficient)
        ratio = attempt / attempts_used if attempts_used else float("inf")
        if ratio >= 1:
            print(f"GA was {ratio:.2f}x more guess-efficient than random search "
                  f"({attempts_used:,} vs {attempt:,} guesses)")
        else:
            print(f"GA was {1 / ratio:.2f}x LESS guess-efficient than random search "
                  f"({attempts_used:,} vs {attempt:,} guesses)")


if __name__ == "__main__":
    run(CONFIG)