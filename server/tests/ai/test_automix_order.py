"""Automix ordering: tempo matching incl. half/double, key + energy costs, window (intent) and first-track rules."""
import itertools
import random

import pytest

from nasmusic.ai.automix import Item, arc_target, order, pair_cost, tempo_match, transition_info


def test_tempo_match_half_double():
    m, s = tempo_match(128, 126)
    assert m == 1 and s == pytest.approx(128 / 126)
    m, s = tempo_match(140, 70.5)
    assert m == 2 and s == pytest.approx(140 / 141)
    m, s = tempo_match(87, 172)
    assert m == 0.5 and s == pytest.approx(87 / 86)
    assert tempo_match(None, 120) is None and tempo_match(120, 0) is None


def test_pair_cost_prefers_compatible():
    a = Item("a", 124, "8A", 0.5, True)
    good = Item("g", 125, "9A", 0.55, True)
    clash = Item("c", 124, "2B", 0.5, True)
    far = Item("f", 100, "8A", 0.5, True)
    assert pair_cost(a, good) < pair_cost(a, clash)
    assert pair_cost(a, good) < pair_cost(a, far)
    assert pair_cost(a, Item("u")) < pair_cost(a, far)   # unknown is neutral, a 20 % jump is not


def test_order_keeps_first_and_multiset_and_window():
    random.seed(3)
    items = [Item(f"t{i}", random.choice([90, 124, 128, 140, 174]), f"{random.randint(1, 12)}{random.choice('AB')}",
                  random.random(), True) for i in range(40)]
    for w in (1, 3, 6):
        out = order(items, window=w)
        assert out[0].id == "t0"
        assert sorted(x.id for x in out) == sorted(x.id for x in items)
        pos = {x.id: k for k, x in enumerate(out)}
        assert all(abs(pos[f"t{i}"] - i) <= w for i in range(40))


def test_order_improves_flow():
    items = [Item("a", 124, "8A", 0.5, True), Item("b", 90, "3B", 0.5, True), Item("c", 125, "9A", 0.5, True),
             Item("d", 91, "4B", 0.5, True), Item("e", 126, "9A", 0.5, True), Item("f", 92, "4B", 0.5, True)]

    def cost(seq):
        return sum(pair_cost(x, y) for x, y in zip(seq, seq[1:]))

    out = order(items, window=5, arc=False)
    assert cost(out) < cost(items)
    # best possible with 'a' first, by brute force
    best = min(cost([items[0], *p]) for p in itertools.permutations(items[1:]))
    assert cost(out) == pytest.approx(best)


def test_order_without_data_keeps_order():
    items = [Item(f"t{i}") for i in range(10)]
    assert [x.id for x in order(items)] == [f"t{i}" for i in range(10)]


def test_order_small_lists_untouched():
    items = [Item("a", 120), Item("b", 90)]
    assert order(items) == items


def test_energy_arc_shape():
    assert arc_target(0, 0, 1) == pytest.approx(0.3)
    assert arc_target(0.7, 0, 1) == pytest.approx(1.0)
    assert arc_target(1.0, 0, 1) == pytest.approx(0.72)


def test_transition_info():
    t = transition_info(Item("a", 128, "8A", 0.5, True), Item("b", 64.5, "8B", 0.5, True))
    assert t["tempoMultiplier"] == 2 and t["beatmatch"] and t["keyDistance"] == 1
    t = transition_info(Item("a", 128, None, None, True), Item("b", 100, None, None, True))
    assert not t["beatmatch"] and t["keyDistance"] is None
    t = transition_info(Item("a", 128, stable=True), Item("b", 128, stable=False))
    assert not t["beatmatch"]
