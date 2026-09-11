from forex_strength.strength import calculate_currency_strength, rank_pairs


def test_strength_assigns_positive_move_to_base_and_negative_to_quote() -> None:
    score = calculate_currency_strength({"EURUSD": 1.10}, {"EURUSD": 1.00})
    assert score["EUR"] > 0
    assert score["USD"] < 0
    assert abs(score["EUR"] + score["USD"]) < 1e-12


def test_rank_pairs_puts_strong_base_before_weak_quote() -> None:
    ranking = rank_pairs({"USD": 1.0, "EUR": 0.5, "JPY": -1.0})
    assert ranking[0] == ("USDJPY", 2.0)
