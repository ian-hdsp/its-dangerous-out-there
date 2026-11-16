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



def test_desktop_alert_only_acts_on_fresh_in_stock_pushes():
    import time
    import desktop_alert as d
    opened = []
    d.webbrowser.open = lambda url, new=0: opened.append(url)
    d.alarm = lambda: None
    d.handle({"event": "message", "tags": ["rotating_light"], "title": "sold out again", "click": "u0"})
    d.handle({"event": "keepalive"})
    d.handle({"event": "message", "tags": ["in_stock"], "time": time.time() - 3600, "click": "stale"})
    d.handle({"event": "message", "tags": ["rotating_light", "in_stock"], "time": time.time(), "click": "u1"})
    assert opened == ["u1"]


def test_test_run_pushes_are_labelled(monkeypatch):
    import check_stock as c
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(c.requests, "post", lambda url, data, headers, timeout: sent.update(headers) or Resp())
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("TEST_RUN", "1")
    c.notify("Zelda Switch 2 IN STOCK at Nintendo Store", "b", "u", tag="in_stock")
    assert sent["Title"].startswith("[TEST] ") and "test_tube" in sent["Tags"].split(",")
    monkeypatch.delenv("TEST_RUN")
    c.notify("Zelda Switch 2 IN STOCK at Nintendo Store", "b", "u", tag="in_stock")
    assert not sent["Title"].startswith("[TEST]") and "test_tube" not in sent["Tags"]


def test_desktop_alert_ignores_test_pushes():
    import time
    import desktop_alert as d
    opened = []
    d.webbrowser.open = lambda url, new=0: opened.append(url)
    d.alarm = lambda: None
    d.handle({"event": "message", "tags": ["test_tube", "rotating_light", "in_stock"],
              "title": "[TEST] Zelda", "time": time.time(), "click": "test"})
    assert opened == []


def test_scan_stock_fields_plain_and_escaped_json():
    from check_stock import scan_stock_fields
    plain = '{"sku":"121642","stock_status":"IN_STOCK","only_x_left_in_stock":null,"salable_qty":0}'
    escaped = '<script>self.__next_f.push("{\\"stockStatus\\":\\"OUT_OF_STOCK\\",\\"isSalable\\":false}")</script>'
    got = scan_stock_fields(plain, "api") + scan_stock_fields(escaped, "page")
    assert ("api", "stock_status", "IN_STOCK") in got
    assert ("api", "salable_qty", "0") in got
    assert ("page", "stockStatus", "OUT_OF_STOCK") in got
    assert ("page", "isSalable", "false") in got


def test_scan_stock_fields_broad_names_and_url_values():
    from check_stock import scan_stock_fields
    text = ('{"offers":{"availability":"https://schema.org/OutOfStock"},'
            '"isSoldOut":true,"maxOrderQuantity":2,"inventoryLevel":"NONE","name":"x"}')
    got = {(k, v) for _, k, v in scan_stock_fields(text, "page")}
    assert ("availability", "https://schema.org/OutOfStock") in got
    assert ("isSoldOut", "true") in got
    assert ("maxOrderQuantity", "2") in got
    assert ("inventoryLevel", "NONE") in got
    assert not any(k == "name" for k, _ in got)


def _btn_ev(salable):
    return Evidence(http_status=200, main_buttons=[btn("Add to cart")], salable=salable)


def test_add_to_cart_but_not_salable_is_out_of_stock():
    status, reason = decide(_btn_ev(False))
    assert status == OUT_OF_STOCK and "isSalableQty=false" in reason


def test_add_to_cart_and_salable_is_in_stock():
    status, reason = decide(_btn_ev(True))
    assert status == IN_STOCK and "isSalableQty=true" in reason


def test_add_to_cart_without_live_data_still_in_stock():
    # if Nintendo's live data isn't seen at all, fall back to the button (don't miss a drop)
    assert decide(_btn_ev(None))[0] == IN_STOCK


def test_find_salable_matches_this_product_not_related_items():
    from check_stock import find_salable
    title = "Nintendo Switch™ 2 – The Legend of Zelda™ – 40th Anniversary Edition"
    payload = {"data": {"product": {"name": title, "isSalableQty": False,
                                    "related": [{"name": "Pro Controller", "isSalableQty": True}]}}}
    assert find_salable([payload], title) is False
    payload["data"]["product"]["isSalableQty"] = True
    assert find_salable([payload], "  " + title.upper() + " ") is True
    assert find_salable([{"name": "Other", "isSalableQty": True}], title) is None
