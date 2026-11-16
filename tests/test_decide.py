from check_stock import IN_STOCK, OUT_OF_STOCK, UNKNOWN, Evidence, decide, transition


def btn(text, disabled=False):
    return {"text": text, "disabled": disabled}


def test_target_api_in_stock():
    assert decide(Evidence(http_status=200, target_api_status=["IN_STOCK"]))[0] == IN_STOCK


def test_target_api_out_beats_buttons():
    ev = Evidence(http_status=200, target_api_status=["OUT_OF_STOCK"], main_buttons=[btn("Notify me")])
    assert decide(ev)[0] == OUT_OF_STOCK


def test_nintendo_add_to_cart():
    assert decide(Evidence(http_status=200, main_buttons=[btn("Add to cart")]))[0] == IN_STOCK


def test_disabled_add_to_cart_is_not_in_stock():
    ev = Evidence(http_status=200, main_buttons=[btn("Add to cart", disabled=True)])
    assert decide(ev)[0] == UNKNOWN


def test_sold_out_button():
    assert decide(Evidence(http_status=200, main_buttons=[btn("Sold out")]))[0] == OUT_OF_STOCK


def test_mixed_buttons_fall_back_to_jsonld():
    ev = Evidence(
        http_status=200,
        main_buttons=[btn("Add to cart"), btn("Sold out")],
        jsonld_availability=["https://schema.org/OutOfStock"],
    )
    assert decide(ev)[0] == OUT_OF_STOCK


def test_jsonld_in_stock():
    ev = Evidence(http_status=200, jsonld_availability=["http://schema.org/InStock"])
    assert decide(ev)[0] == IN_STOCK


def test_blocked_http():
    assert decide(Evidence(http_status=403, main_buttons=[btn("Add to cart")]))[0] == UNKNOWN


def test_block_page_text():
    ev = Evidence(http_status=200, page_text_head="Pardon Our Interruption...", main_buttons=[btn("Add to cart")])
    assert decide(ev)[0] == UNKNOWN


def test_no_signal():
    assert decide(Evidence(http_status=200))[0] == UNKNOWN


def run_sequence(statuses, notify_unknown=False):
    state, alerts = {}, []
    for s in statuses:
        alert, state = transition(state, s, notify_unknown)
        alerts.append(alert)
    return alerts


def test_push_only_on_change_to_in_stock():
    seq = [OUT_OF_STOCK, IN_STOCK, IN_STOCK, IN_STOCK]
    assert run_sequence(seq) == [None, "in_stock", None, None]


def test_no_push_while_out_of_stock():
    assert run_sequence([OUT_OF_STOCK, OUT_OF_STOCK, OUT_OF_STOCK]) == [None, None, None]


def test_sold_out_again_and_next_drop():
    seq = [OUT_OF_STOCK, IN_STOCK, OUT_OF_STOCK, OUT_OF_STOCK, IN_STOCK]
    assert run_sequence(seq) == [None, "in_stock", "sold_out", None, "in_stock"]


def test_unknown_blip_mid_drop_does_not_repush():
    assert run_sequence([IN_STOCK, UNKNOWN, IN_STOCK]) == ["in_stock", None, None]


def test_unknown_pushes_once_when_enabled():
    seq = [OUT_OF_STOCK, UNKNOWN, UNKNOWN, OUT_OF_STOCK, UNKNOWN]
    assert run_sequence(seq, notify_unknown=True) == [None, "unknown", None, None, "unknown"]


def test_no_previous_state_in_stock_alerts():
    assert transition({}, IN_STOCK)[0] == "in_stock"


def test_target_api_refused_is_unknown():
    status, reason = decide(Evidence(http_status=200, api_http_errors=[435, 435]))
    assert status == UNKNOWN and "435" in reason

