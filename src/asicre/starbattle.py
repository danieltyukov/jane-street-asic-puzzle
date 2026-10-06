"""Star Battle ("Two Not Touch") helpers: solve, check, and draw boards.

A board is a list of 121 bits in the order the chip consumes them: row-major,
top-left first. ``regions`` is an 11x11 grid of region labels.
"""

from __future__ import annotations

import z3

N = 11


def neighbours(r: int, c: int):
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if (dr or dc) and 0 <= r + dr < N and 0 <= c + dc < N:
                yield r + dr, c + dc


def solve(regions: list[str], stars: int = 2, adjacency: bool = True,
          limit: int = 2, require_touching: bool = False) -> list[list[int]]:
    """Return up to ``limit`` boards that satisfy the rules.

    ``adjacency=False`` drops the no-touching rule; ``require_touching`` asks
    for boards that pass every count but do have two touching stars, which is
    what the chip's "TWO NOT TOUCH" message is for.
    """
    x = [[z3.Bool(f"s_{r}_{c}") for c in range(N)] for r in range(N)]
    s = z3.Solver()
    for r in range(N):
        s.add(z3.PbEq([(x[r][c], 1) for c in range(N)], stars))
    for c in range(N):
        s.add(z3.PbEq([(x[r][c], 1) for r in range(N)], stars))
    labels = sorted({ch for row in regions for ch in row})
    for lab in labels:
        cells = [x[r][c] for r in range(N) for c in range(N) if regions[r][c] == lab]
        s.add(z3.PbEq([(v, 1) for v in cells], stars))
    touching = []
    for r in range(N):
        for c in range(N):
            for rr, cc in neighbours(r, c):
                if (rr, cc) > (r, c):
                    touching.append(z3.And(x[r][c], x[rr][cc]))
    if adjacency:
        s.add(z3.Not(z3.Or(touching)))
    elif require_touching:
        s.add(z3.Or(touching))

    out = []
    while len(out) < limit and s.check() == z3.sat:
        m = s.model()
        board = [1 if z3.is_true(m.eval(x[r][c])) else 0 for r in range(N) for c in range(N)]
        out.append(board)
        s.add(z3.Or([x[r][c] != bool(board[r * N + c]) for r in range(N) for c in range(N)]))
    return out


def check(regions: list[str], board: list[int], stars: int = 2) -> dict[str, bool]:
    rows = all(sum(board[r * N:(r + 1) * N]) == stars for r in range(N))
    cols = all(sum(board[r * N + c] for r in range(N)) == stars for c in range(N))
    labels = {ch for row in regions for ch in row}
    regs = all(sum(board[r * N + c] for r in range(N) for c in range(N) if regions[r][c] == lab) == stars
               for lab in labels)
    apart = not any(board[r * N + c] and board[rr * N + cc]
                    for r in range(N) for c in range(N) for rr, cc in neighbours(r, c))
    return {"rows": rows, "columns": cols, "regions": regs, "not_touching": apart}


def render(regions: list[str], board: list[int] | None = None) -> str:
    lines = []
    for r in range(N):
        cells = []
        for c in range(N):
            star = board is not None and board[r * N + c]
            cells.append("*" if star else regions[r][c])
        lines.append(" ".join(cells))
    return "\n".join(lines)


def from_rows(rows: list[tuple[int, int]]) -> list[int]:
    board = [0] * (N * N)
    for r, cols in enumerate(rows):
        for c in cols:
            board[r * N + c] = 1
    return board
