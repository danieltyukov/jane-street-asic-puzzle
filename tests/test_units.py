"""Tests that need no puzzle files: parsers, the solver, the LFSR model."""

from asicre import lfsr, liberty, starbattle


def test_expression_parser_precedence():
    tree = liberty.parse_expr("(!A1&!B1) | (!A2&!B1) | (!A3&!B1)")
    for a1 in (0, 1):
        for a2 in (0, 1):
            for a3 in (0, 1):
                for b1 in (0, 1):
                    expected = int(not ((a1 and a2 and a3) or b1))  # a31oi
                    got = liberty.evaluate(tree, {"A1": a1, "A2": a2, "A3": a3, "B1": b1})
                    assert got == expected


def test_expression_juxtaposition_and_postfix_not():
    tree = liberty.parse_expr("A B' + C")
    assert liberty.evaluate(tree, {"A": 1, "B": 0, "C": 0}) == 1
    assert liberty.evaluate(tree, {"A": 1, "B": 1, "C": 0}) == 0
    assert liberty.evaluate(tree, {"A": 0, "B": 1, "C": 1}) == 1


def test_constants_parse():
    assert liberty.parse_expr("1") == ("const", 1)
    assert liberty.parse_expr("0") == ("const", 0)


def test_bit_parallel_rendering_matches_evaluate():
    tree = liberty.parse_expr("(A0&!S) | (A1&S)")  # mux2
    src = liberty.to_python(tree, {"A0": "a", "A1": "b", "S": "s"})
    # lanes: every combination of the three inputs, one per bit
    a, b, s = 0b11110000, 0b11001100, 0b10101010
    # src is our own rendering of a fixed expression, not external input
    got = eval(src) & 0xFF
    for lane in range(8):
        bits = {"A0": (a >> lane) & 1, "A1": (b >> lane) & 1, "S": (s >> lane) & 1}
        assert (got >> lane) & 1 == liberty.evaluate(tree, bits)


REGIONS = [
    "aaaaabbcdde", "aafaabccdde", "aafbbbbccde", "aafbgggecce", "fafbgeeeeee", "fffbgggehhh",
    "bbbbbbgehii", "bjjjgggehii", "bjjkeeeehii", "bbjkkeeehhh", "bjjkeeeeeee",
]


def test_star_battle_unique_solution():
    sols = starbattle.solve(REGIONS, limit=3)
    assert len(sols) == 1
    assert all(starbattle.check(REGIONS, sols[0]).values())


def test_star_battle_touching_variant():
    board = starbattle.solve(REGIONS, adjacency=False, require_touching=True, limit=1)[0]
    result = starbattle.check(REGIONS, board)
    assert result["rows"] and result["columns"] and result["regions"]
    assert not result["not_touching"]


def test_lfsr_is_maximal_length():
    reg = lfsr.LFSR(8, [3, 4, 5, 7])
    assert reg.period() == 255
    assert reg.polynomial() == "x^8 + x^6 + x^5 + x^4 + 1"


def test_lfsr_crack_finds_planted_message():
    reg = lfsr.LFSR(8, [3, 4, 5, 7])
    plain = b"HELLO WORLD"
    stream = reg.keystream(0x5A, len(plain))
    rom = [p ^ k for p, k in zip(plain, stream)]
    hits = lfsr.crack(rom, reg)
    assert (0x5A, "HELLO WORLD") in hits
