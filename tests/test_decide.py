import os
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


def test_send_test_push_is_always_labelled(monkeypatch):
    import check_stock as c
    sent = []
    monkeypatch.setattr(c, "notify", lambda title, body, url, priority="urgent", tag="":
                        sent.append((os.environ.get("TEST_RUN"), title, tag)))
    monkeypatch.setenv("SEND_TEST_PUSH", "1")
    monkeypatch.delenv("TEST_RUN", raising=False)
    monkeypatch.setenv("LOOP_MINUTES", "0")

    class Stop(Exception):
        pass

    # stand-in for Playwright (not installed in the unit-test CI job); stop before any browser
    import sys
    import types
    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: (_ for _ in ()).throw(Stop())
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    try:
        c.main()
    except Stop:
        pass
    assert sent and sent[0][0] == "1" and sent[0][2] == "in_stock"


def _seq_with_time(steps, reminder_secs=600):
    """steps: [(seconds, status)] -> alerts, with reminders after reminder_secs."""
    state, alerts = {}, []
    for t, s in steps:
        alert, state = transition(state, s, False, now=t, reminder_secs=reminder_secs)
        alerts.append(alert)
    return alerts


def test_one_reminder_per_drop_after_ten_minutes():
    steps = [(0, OUT_OF_STOCK)] + [(60 * m, IN_STOCK) for m in range(1, 21)] + [(1260, OUT_OF_STOCK)]
    alerts = _seq_with_time(steps)
    assert alerts[1] == "in_stock"                       # minute 1: drop starts
    assert [i for i, a in enumerate(alerts) if a == "reminder"] == [11]  # minute 11 = 10 min later
    assert alerts.count("reminder") == 1 and alerts[-1] == "sold_out"


def test_next_drop_gets_its_own_reminder():
    steps = ([(0, IN_STOCK), (600, IN_STOCK), (660, OUT_OF_STOCK),
              (3600, IN_STOCK), (4200, IN_STOCK)])
    assert _seq_with_time(steps) == ["in_stock", "reminder", "sold_out", "in_stock", "reminder"]


def test_short_drop_gets_no_reminder_and_unknown_blip_keeps_clock():
    assert _seq_with_time([(0, IN_STOCK), (300, OUT_OF_STOCK)]) == ["in_stock", "sold_out"]
    # a failed page load mid-drop neither re-alerts nor resets the 10-minute clock
    assert _seq_with_time([(0, IN_STOCK), (300, UNKNOWN), (600, IN_STOCK)]) == ["in_stock", None, "reminder"]


def test_reminders_off_when_zero():
    assert _seq_with_time([(0, IN_STOCK), (3600, IN_STOCK)], reminder_secs=0) == ["in_stock", None]


def test_reminder_push_is_labelled_as_same_drop(monkeypatch):
    import check_stock as c
    sent = []
    monkeypatch.setattr(c, "notify", lambda title, body, url, priority="urgent", tag="":
                        sent.append((title, body, priority, tag)))
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state = {"Nintendo Store": {"status": IN_STOCK, "known": IN_STOCK,
                                "since": 1794856586.0, "reminded": False}}  # 2026-11-16 19:16:26 UTC
    monkeypatch.setattr(c.time, "time", lambda: 1794856586.0 + 600)
    c.send_alerts([(prod, IN_STOCK, "buy button")], state, False)
    title, body, priority, tag = sent[0]
    assert title.startswith("Reminder (same drop)") and "IN STOCK" not in title
    assert "11:16 AM PST / 2:16 PM EST" in title and "Not a new drop" in body
    assert priority == "urgent" and tag == "repeat"


def test_reminder_not_lost_to_check_jitter():
    # the check 10 intervals later finishes 0.1 s "early": still gets the reminder
    steps = [(100.0, IN_STOCK), (100.0 + 599.9, IN_STOCK)]
    assert _seq_with_time(steps) == ["in_stock", "reminder"]


def test_drop_log_records_one_row_per_drop(monkeypatch, tmp_path):
    import csv
    import check_stock as c
    monkeypatch.setattr(c, "notify", lambda *a, **k: None)
    log = tmp_path / "drops.csv"
    monkeypatch.setenv("DROP_LOG", str(log))
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state, clock = {}, [0.0]
    monkeypatch.setattr(c.time, "time", lambda: clock[0])
    base = 1794856527.0  # 2026-11-16 19:15:27 UTC (drop 3's last sold-out check)
    timeline = [(0, OUT_OF_STOCK)] + [(59 + 60 * i, IN_STOCK) for i in range(19)] + [(1200, OUT_OF_STOCK)]
    for t, s in timeline:
        clock[0] = base + t
        c.send_alerts([(prod, s, "r")], state, False)
    rows = list(csv.DictReader(open(log)))
    assert len(rows) == 1
    r = rows[0]
    assert r["start_after_utc"] == "2026-11-16T19:15:27Z" and r["start_by_utc"] == "2026-11-16T19:16:26Z"
    assert r["end_after_utc"] == "2026-11-16T19:34:26Z" and r["end_by_utc"] == "2026-11-16T19:35:27Z"
    assert r["min_minutes"] == "18.0" and r["max_minutes"] == "20.0"
    assert r["start_pst"] == "11:16 AM PST" and r["start_est"] == "2:16 PM EST"
    # a second drop appends a second row under the same header
    for t, s in [(3600, IN_STOCK), (3660, OUT_OF_STOCK)]:
        clock[0] = base + t
        c.send_alerts([(prod, s, "r")], state, False)
    assert len(list(csv.DictReader(open(log)))) == 2


def test_no_drop_log_without_env(monkeypatch, tmp_path):
    import check_stock as c
    monkeypatch.setattr(c, "notify", lambda *a, **k: None)
    monkeypatch.delenv("DROP_LOG", raising=False)
    monkeypatch.chdir(tmp_path)
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state = {}
    for s in (OUT_OF_STOCK, IN_STOCK, OUT_OF_STOCK):
        c.send_alerts([(prod, s, "r")], state, False)
    assert list(tmp_path.iterdir()) == []


class _FakeResp:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail = payload or {}, fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("boom")

    def json(self):
        return self.payload


def _capture_posts(monkeypatch, fail_hosts=()):
    import check_stock as c
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers})
        return _FakeResp({"status": 1, "receipt": "R123"}, fail=any(h in url for h in fail_hosts))
    monkeypatch.setattr(c.requests, "post", fake_post)
    return calls


def _pushover_env(monkeypatch, ntfy=True):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("TEST_RUN", raising=False)
    if ntfy:
        monkeypatch.setenv("NTFY_TOPIC", "topic")
    else:
        monkeypatch.delenv("NTFY_TOPIC", raising=False)


def test_pushover_priorities():
    from check_stock import pushover_priority
    assert pushover_priority("urgent", "in_stock") == 2   # IN STOCK: emergency
    assert pushover_priority("urgent", "repeat") == 1     # reminder: high
    assert pushover_priority("low", "") == -1             # sold out: quiet


def test_in_stock_goes_to_both_channels_as_emergency(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch)
    calls = _capture_posts(monkeypatch)
    receipt = c.notify("Zelda Switch 2 IN STOCK at Nintendo Store", "Tap", "https://p", tag="in_stock")
    assert receipt == ["R123"]
    po = [x for x in calls if "pushover" in x["url"]][0]["data"]
    assert po["priority"] == 2 and po["retry"] == 180 and po["expire"] == 360
    assert po["url"] == "https://p" and po["title"] == "Zelda Switch 2 IN STOCK at Nintendo Store"
    assert any("ntfy" in x["url"] for x in calls)


def test_test_runs_are_labelled_on_pushover_and_expire_sooner(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    monkeypatch.setenv("TEST_RUN", "1")
    calls = _capture_posts(monkeypatch)
    c.notify("Zelda Switch 2 IN STOCK at Nintendo Store", "b", "u", tag="in_stock")
    po = calls[0]["data"]
    assert po["title"].startswith("[TEST] ") and po["expire"] == 180


def test_channels_are_independent(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch)
    calls = _capture_posts(monkeypatch, fail_hosts=("ntfy",))
    assert c.notify("t", "b", "u", tag="in_stock") == ["R123"]  # ntfy down: Pushover still sent
    calls = _capture_posts(monkeypatch, fail_hosts=("pushover",))
    assert c.notify("t", "b", "u", tag="in_stock") == []       # Pushover down: no exception
    assert any("ntfy" in x["url"] for x in calls)


def test_sold_out_cancels_unacknowledged_emergency(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    calls = _capture_posts(monkeypatch)
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state = {}
    for s in (OUT_OF_STOCK, IN_STOCK):
        c.send_alerts([(prod, s, "r")], state, False)
    assert state["Nintendo Store"]["pushover_receipts"] == ["R123"]
    calls.clear()
    c.send_alerts([(prod, OUT_OF_STOCK, "r")], state, False)
    assert any(x["url"].endswith("/receipts/R123/cancel.json") for x in calls)
    assert "pushover_receipts" not in state["Nintendo Store"]


def test_each_person_gets_their_own_pushover_alert(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    monkeypatch.setenv("PUSHOVER_USER", "me_key, friend_key ,")
    receipts_by_user = {"me_key": "R1", "friend_key": "R2"}
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(data)
        return _FakeResp({"status": 1, "receipt": receipts_by_user.get(data.get("user"))})
    monkeypatch.setattr(c.requests, "post", fake_post)
    receipts = c.notify("Zelda Switch 2 IN STOCK at Nintendo Store", "b", "u", tag="in_stock")
    assert [d["user"] for d in calls] == ["me_key", "friend_key"]   # separate messages
    assert all(d["priority"] == 2 for d in calls) and receipts == ["R1", "R2"]


def test_one_bad_key_does_not_block_the_other_person(monkeypatch, capsys):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    monkeypatch.setenv("PUSHOVER_USER", "bad_key,friend_key")

    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResp({"receipt": "R2"}, fail=data.get("user") == "bad_key")
    monkeypatch.setattr(c.requests, "post", fake_post)
    assert c.notify("t", "b", "u", tag="in_stock") == ["R2"]
    out = capsys.readouterr().out
    assert "person #1 failed" in out and "bad_key" not in out   # never print a key


def test_sold_out_cancels_every_persons_alert_and_old_single_receipt(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    calls = _capture_posts(monkeypatch)
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state = {"Nintendo Store": {"status": IN_STOCK, "known": IN_STOCK, "pushover_receipts": ["R1", "R2"]}}
    c.send_alerts([(prod, OUT_OF_STOCK, "r")], state, False)
    cancelled = [x["url"] for x in calls if x["url"].endswith("/cancel.json")]
    assert cancelled == [f"{c.PUSHOVER_API}/receipts/R1/cancel.json", f"{c.PUSHOVER_API}/receipts/R2/cancel.json"]
    # state saved by the previous version (single receipt) is still cancelled
    calls.clear()
    state = {"Nintendo Store": {"status": IN_STOCK, "known": IN_STOCK, "pushover_receipt": "R9"}}
    c.send_alerts([(prod, OUT_OF_STOCK, "r")], state, False)
    assert any(x["url"].endswith("/receipts/R9/cancel.json") for x in calls)


def test_pushover_retry_interval_is_configurable(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    calls = _capture_posts(monkeypatch)
    monkeypatch.setenv("PUSHOVER_RETRY_SECONDS", "300")
    c.notify("t", "b", "u", tag="in_stock")
    monkeypatch.setenv("PUSHOVER_RETRY_SECONDS", "5")   # below Pushover's minimum -> 30
    c.notify("t", "b", "u", tag="in_stock")
    assert [x["data"]["retry"] for x in calls] == [300, 30]


def test_pushover_expire_is_configurable_and_bounded(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch, ntfy=False)
    calls = _capture_posts(monkeypatch)
    for v in ("600", "10", "99999"):   # custom; below retry -> retry; above Pushover max -> 10800
        monkeypatch.setenv("PUSHOVER_EXPIRE_SECONDS", v)
        c.notify("t", "b", "u", tag="in_stock")
    assert [x["data"]["expire"] for x in calls] == [600, 180, 10800]


def test_reminder_goes_to_ntfy_only(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch)            # both ntfy and Pushover configured
    calls = _capture_posts(monkeypatch)
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state = {"Nintendo Store": {"status": IN_STOCK, "known": IN_STOCK,
                                "since": 1794856586.0, "reminded": False}}
    monkeypatch.setattr(c.time, "time", lambda: 1794856586.0 + c.REMINDER_SECS)
    c.send_alerts([(prod, IN_STOCK, "buy button")], state, False)
    assert state["Nintendo Store"]["reminded"] is True
    assert any("ntfy" in x["url"] for x in calls)
    assert not any("pushover" in x["url"] for x in calls)


def test_in_stock_and_sold_out_still_go_to_both_channels(monkeypatch):
    import check_stock as c
    _pushover_env(monkeypatch)
    calls = _capture_posts(monkeypatch)
    prod = {"retailer": "Nintendo Store", "url": "u"}
    state = {}
    for s in (OUT_OF_STOCK, IN_STOCK, OUT_OF_STOCK):
        c.send_alerts([(prod, s, "r")], state, False)
    urls = [x["url"] for x in calls]
    assert sum("ntfy" in u for u in urls) == 2                      # IN STOCK + sold out
    assert sum(u.endswith("/messages.json") for u in urls) == 2     # IN STOCK + sold out
