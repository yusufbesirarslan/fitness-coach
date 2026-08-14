"""Hardening PR4 — kapasite/aşırı-yük/kurtarma dayanıklılık kanıtları.

Bu dosya PR #199'un getirdiği MEVCUT mimariyi kanıtlar; ikinci bir eşzamanlılık
mekanizması KURMAZ. Kanıtlanan sözleşme:

1. Doygunluk    — izinler tavanına ulaşır ve fazladan istek SINIRLI sürede
                  reddedilir/degrade olur, süresiz BEKLEMEZ.
2. Rezerv       — sağlayıcı işi doyduğunda bile /health'e thread kalır.
3. Kurtarma     — başarı/hata/iptal sonrası izinler ESKİ seviyeye döner
                  (kapasite sızıntısı yok).
4. Deadline     — kapı beklemesi AYNI mutlak deadline'ı tüketir; deadline
                  dolduktan sonra yeni sağlayıcı çağrısı BAŞLAMAZ.
5. Kilit disipl.— DB transaction'ı/satır kilidi sağlayıcı I/O'sunu KAPSAMAZ.
6. Sınıflama    — doygunluk ile sağlayıcı arızası AYRI kalır; gövde sağlayıcı
                  içini SIZDIRMAZ.
7. Metrikler    — boyutlar sabit kümeli; enstrümantasyon arızası isteği
                  DEĞİŞTİRMEZ.

Zamana dayalı `sleep` ile yarış KURULMAZ: bariyerler (`threading.Event`),
sahte monotonik saat ve deterministik problar kullanılır.

    python -m pytest tests/test_capacity_invariants.py -v
"""
import ast
import contextlib
import os
import threading

import pytest

from app.services import ai_gate, runtime_metrics


@pytest.fixture(autouse=True)
def _clean_capacity_state():
    """Her test taze sayaç/tampon ile başlasın (süreç-geneli modül durumu)."""
    runtime_metrics.reset_for_test()
    yield
    runtime_metrics.reset_for_test()


_DECOY_KEYSET = None


def _keyset_without_bogus_kid():
    """`bogus-kid` İÇERMEYEN geçerli bir JWKS (joserfc boş set kabul etmez).

    Süreç başına bir kez üretilir: RSA anahtar üretimi pahalıdır ve testler
    yalnızca "aranan kid burada YOK" durumuna ihtiyaç duyar.
    """
    global _DECOY_KEYSET
    if _DECOY_KEYSET is None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from joserfc.jwk import KeySet, RSAKey
        decoy = RSAKey.import_key(
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            parameters={"kid": "other-kid", "use": "sig", "alg": "RS256"})
        _DECOY_KEYSET = KeySet.import_key_set(
            {"keys": [decoy.as_dict(private=False)]})
    return _DECOY_KEYSET


def _drain_all(semaphore):
    """Semaforu tamamen boşalt; alınan izin sayısını döndür."""
    taken = 0
    while semaphore.acquire(blocking=False):
        taken += 1
    return taken


def _release(semaphore, count):
    for _ in range(count):
        semaphore.release()


# ── 1. Doygunluk: sınırlı bekleme, süresiz blok YOK ────────────────────────

def test_saturated_shared_gate_rejects_within_bounded_wait():
    """Tüm izinler tutulurken fazladan istek BEKLEMEDEN reddedilir.

    Bu, isteğin süresiz asılı kalmasının (thread park) ALTERNATİFİDİR: kapı
    dolduğunda çağıran BlockingConcurrencyLimit alır ve route bunu belirlenimci
    503'e/degrade yanıta çevirir.
    """
    taken = _drain_all(ai_gate._ai_slots)
    try:
        assert taken == ai_gate.AI_MAX_CONCURRENCY, (
            "kapı tavanı yapılandırılan izin sayısıyla eşleşmeli")
        with pytest.raises(ai_gate.BlockingConcurrencyLimit):
            with ai_gate.blocking_concurrency_slot(wait_seconds=0):
                pytest.fail("doygun kapıdan izin alınmamalıydı")
    finally:
        _release(ai_gate._ai_slots, taken)


def test_bounded_wait_is_capped_by_the_outer_deadline():
    """Kapı beklemesi, yapılandırılan bekleme ile deadline'ın KÜÇÜĞÜdür.

    Sahte monotonik saat: gerçek zaman beklemeden, geçirilen timeout'un
    kalan bütçeyi AŞMADIĞI doğrulanır.
    """
    now = [1000.0]
    seen = {}

    class _Probe:
        def acquire(self, timeout=None):
            seen["timeout"] = timeout
            return False

    # wait=30 sn istense de deadline yalnızca 2 sn sonrada → 2 sn kazanır.
    assert not ai_gate._acquire_before_deadline(
        _Probe(), 30.0, deadline=now[0] + 2.0, clock=lambda: now[0])
    assert seen["timeout"] == pytest.approx(2.0)

    # Deadline zaten dolmuşsa bekleme SIFIRDIR (negatife düşmez).
    assert not ai_gate._acquire_before_deadline(
        _Probe(), 30.0, deadline=now[0] - 5.0, clock=lambda: now[0])
    assert seen["timeout"] == 0.0


# ── 2. Rezerv: sağlayıcı doygunluğu tüm thread'leri yutmaz ─────────────────

def test_boot_ceiling_leaves_health_reserve_at_defaults():
    ceiling = ai_gate.blocking_thread_ceiling()
    reserve = ai_gate.WEB_THREADS - ceiling
    assert reserve >= ai_gate.THREAD_RESERVE_MIN, (
        f"tavan({ceiling}) {ai_gate.WEB_THREADS} thread'e karşı yalnız "
        f"{reserve} rezerv bırakıyor")


def test_full_provider_saturation_still_reports_reserve_above_floor():
    """AI + scrape kapıları TAMAMEN doluyken bile ölçülen rezerv taban üstünde.

    Bu, aritmetiğin değil ÖLÇÜMÜN testidir: izinler gerçekten tutulur, sonra
    canlı snapshot okunur.
    """
    ai_taken = _drain_all(ai_gate._ai_slots)
    scrape_taken = _drain_all(ai_gate._scrape_slots)
    for _ in range(ai_taken):
        ai_gate._enter("ai")
    for _ in range(scrape_taken):
        ai_gate._enter("scrape")
    try:
        snapshot = ai_gate.capacity_snapshot()
        assert snapshot["ai_active"] == ai_taken
        assert snapshot["scrape_active"] == scrape_taken
        assert snapshot["thread_reserve"] >= snapshot["thread_reserve_floor"]
    finally:
        for _ in range(ai_taken):
            ai_gate._leave("ai")
        for _ in range(scrape_taken):
            ai_gate._leave("scrape")
        _release(ai_gate._ai_slots, ai_taken)
        _release(ai_gate._scrape_slots, scrape_taken)


def test_nested_model_permits_are_not_subtracted_twice():
    """Model izinleri AI izinlerinin İÇİNDE alınır → rezervden iki kez düşülmez.

    İki kez düşülseydi rezerv olduğundan düşük görünür ve rollout abort sinyali
    sahte alarm üretirdi.
    """
    with ai_gate.blocking_concurrency_slot(wait_seconds=0):
        outer = ai_gate.capacity_snapshot()
        with ai_gate.model_concurrency_slot("test", wait_seconds=0):
            inner = ai_gate.capacity_snapshot()
            assert inner["model_active"] == outer["model_active"] + 1
            assert inner["thread_reserve"] == outer["thread_reserve"]


# ── 3. Kurtarma: izinler eski seviyeye döner, sızıntı yok ──────────────────

@pytest.mark.parametrize("failure", [None, RuntimeError, GeneratorExit])
def test_permits_return_to_baseline_after_success_failure_or_cancellation(
        failure):
    """Başarı, sağlayıcı arızası ve akış iptali izin sayısını KALICI düşürmez."""
    before = ai_gate.capacity_snapshot()

    def _run():
        with ai_gate.model_concurrency_slot("test", wait_seconds=0):
            if failure is not None:
                raise failure()

    if failure is None:
        _run()
    else:
        with pytest.raises(failure):
            _run()

    assert ai_gate.capacity_snapshot() == before
    # İzin gerçekten geri döndü: tavan kadar yeniden alınabiliyor.
    taken = _drain_all(ai_gate._model_slots)
    _release(ai_gate._model_slots, taken)
    assert taken == ai_gate.AI_MODEL_MAX_CONCURRENCY


def test_capacity_recovers_after_concurrent_saturation():
    """Eşzamanlı doygunluk çözüldükten sonra sonraki istekler yeniden geçer.

    Bariyerlerle deterministik: N thread izinleri tutar, ana thread reddedildiğini
    doğrular, sonra hepsi bırakılır ve yeni bir alım BAŞARILI olur.
    """
    holders = ai_gate.AI_MAX_CONCURRENCY
    entered = threading.Barrier(holders + 1)
    release = threading.Event()
    errors = []

    def _hold():
        try:
            with ai_gate.blocking_concurrency_slot(wait_seconds=0):
                entered.wait(timeout=10)
                release.wait(timeout=10)
        except Exception as exc:  # pragma: no cover - arıza teşhisi
            errors.append(exc)
            try:
                entered.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass

    threads = [threading.Thread(target=_hold) for _ in range(holders)]
    for thread in threads:
        thread.start()
    try:
        entered.wait(timeout=10)
        assert not errors, errors
        assert ai_gate.capacity_snapshot()["ai_active"] == holders
        with pytest.raises(ai_gate.BlockingConcurrencyLimit):
            with ai_gate.blocking_concurrency_slot(wait_seconds=0):
                pytest.fail("doygunken izin verilmemeliydi")
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)

    assert not errors, errors
    assert ai_gate.capacity_snapshot()["ai_active"] == 0
    with ai_gate.blocking_concurrency_slot(wait_seconds=0):
        pass  # doygunluk sonrası hizmet geri geldi


def test_stream_gate_releases_capacity_when_client_disconnects(app):
    """Akış iptali (call_on_close) izni VE aktif sayacı birlikte geri verir."""
    from flask import Response

    @ai_gate.ai_stream_concurrency_gate
    def _view():
        return Response((c for c in "ab"), mimetype="text/event-stream")

    with app.test_request_context("/"):
        response = _view()
        assert ai_gate.capacity_snapshot()["ai_active"] == 1
        # İstemci akış ortasında koptu → Werkzeug kayıtlı call_on_close
        # geri çağrılarını `close()` ile tetikler.
        response.close()

    snapshot = ai_gate.capacity_snapshot()
    assert snapshot["ai_active"] == 0
    taken = _drain_all(ai_gate._ai_slots)
    _release(ai_gate._ai_slots, taken)
    assert taken == ai_gate.AI_MAX_CONCURRENCY


# ── 4. Boot invariant'ı: desteklenmeyen birleşimde FAIL-CLOSED ─────────────

def test_model_gate_excess_over_route_gate_is_counted_by_invariant(
        app, monkeypatch):
    """Model tavanı dış kapıdan büyükse fazlalık rezervden düşülür.

    İç içe geçme bugün geçerlidir, ama `AI_MODEL_MAX_CONCURRENCY` AYRI
    yapılandırılabilir bir düğmedir: 8'e çekilmiş bir model tavanı, iç içe geçme
    herhangi bir yerde bozulduğu anda rezervi yok ederdi. Invariant bu yüzden
    yapılandırmaya bakar, çağrı grafiğine değil.
    """
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(ai_gate, "AI_MODEL_MAX_CONCURRENCY", 8)

    with pytest.raises(RuntimeError, match="thread reserve"):
        ai_gate.enforce_gate_invariants(app)


def test_model_gate_at_or_below_route_gate_is_accepted(app, monkeypatch):
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(ai_gate, "AI_MODEL_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 1)

    ai_gate.enforce_gate_invariants(app)  # patlamamalı


def test_db_pool_smaller_than_thread_count_is_fatal(app, monkeypatch):
    """Havuz thread'leri karşılayamıyorsa rezerv kâğıt üzerinde kalır.

    Kapılar sağlayıcı beklemesini sınırlar; havuz checkout'u sınırlamazsa
    rezerv edilen thread'ler /health'i yine servis edemez.
    """
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 1)
    app.config["DB_POOL_SIZE"] = 2
    app.config["DB_POOL_MAX_OVERFLOW"] = 1

    with pytest.raises(RuntimeError, match="db pool"):
        ai_gate.enforce_gate_invariants(app)


def test_db_pool_check_is_skipped_when_pooling_is_not_configured(
        app, monkeypatch):
    """SQLite (lokal/test) QueuePool kullanmaz → havuz invariant'ı uygulanmaz."""
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 1)
    app.config["DB_POOL_SIZE"] = None
    app.config["DB_POOL_MAX_OVERFLOW"] = None

    ai_gate.enforce_gate_invariants(app)  # patlamamalı


def test_configured_pool_covers_configured_threads():
    """Varsayılan yapılandırma kendi invariant'ını GEÇER (belge değil, kod)."""
    from app import config as config_mod

    threads = config_mod._positive_env_int("FITX_WEB_THREADS", 8)
    size = config_mod._positive_env_int("DB_POOL_SIZE", threads)
    overflow = max(0, config_mod._env_int("DB_POOL_MAX_OVERFLOW", 4))
    assert size + overflow >= threads


# ── 5. Kilit disiplini: sağlayıcı I/O'su transaction İÇİNDE değil ──────────

def _function_source(path, name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} bulunamadı: {path}")


def _calls_in(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


_PROVIDER_NETWORK_CALLS = frozenset(
    {"refresh_tokens", "authenticate", "initiate_auth"})


def _network_lines(node):
    return [n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _PROVIDER_NETWORK_CALLS]


def _row_lock_lines(node):
    """Satır kilidi alan çağrılar: `.with_for_update()` VE `with_for_update=True`."""
    lines = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Attribute) and n.func.attr == "with_for_update":
            lines.append(n.lineno)
        elif any(kw.arg == "with_for_update" for kw in n.keywords):
            lines.append(n.lineno)
    return lines


def test_no_mobile_auth_function_holds_a_row_lock_across_provider_io():
    """PR #199'un iki fazlı rotasyonu YAPISAL olarak korunur.

    NEEDED_FIXES 2026-08-02 #1: `refresh()` bir `SELECT … FOR UPDATE` satır
    kilidini BLOKLAYICI Cognito çağrısı boyunca tutuyordu — kilit + DB bağlantısı
    ~20 sn ağ gecikmesince tutulur, aynı aileyi yenileyen istekler seri hâle
    gelir, yeterince takılan yenileme havuzu tüketir → uygulama geneli 503.

    PR #199 bunu iki faza AYIRDI: ağ (`_renew_provider_tokens`) ve kilit
    (`_snapshot_refresh` / `_lock_and_revalidate_refresh`) AYRI fonksiyonlar.
    Bu test o ayrımı sabitler: HİÇBİR fonksiyon hem sağlayıcı ağ çağrısı hem
    satır kilidi İÇEREMEZ. (Sıra kontrolünden güçlüdür: birleştirme denemesi
    sırası doğru olsa bile YAKALANIR.)
    """
    path = os.path.join("app", "services", "mobile_auth.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    offenders = []
    saw_network = False
    saw_lock = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        network = _network_lines(node)
        locks = _row_lock_lines(node)
        saw_network = saw_network or bool(network)
        saw_lock = saw_lock or bool(locks)
        if network and locks:
            offenders.append(
                f"{node.name}: ağ={network} kilit={locks}")

    # Kendini doğrulayan test: her iki desen de bulunamıyorsa arama bozulmuştur.
    assert saw_network, "sağlayıcı ağ çağrısı bulunamadı — tarama bozuk"
    assert saw_lock, "satır kilidi bulunamadı — tarama bozuk"
    assert not offenders, (
        "satır kilidi sağlayıcı I/O'suyla AYNI fonksiyonda tutuluyor: "
        + "; ".join(offenders))


def test_gated_wearable_routes_do_not_hold_the_slot_across_token_write():
    """Giyilebilir callback'inde izin YALNIZCA ağ alışverişini sarar."""
    node = _function_source(
        os.path.join("app", "blueprints", "wearables.py"), "wearable_callback")
    for child in ast.walk(node):
        if isinstance(child, ast.With):
            uses_gate = any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None)
                == "blocking_concurrency_slot"
                for item in child.items)
            if uses_gate:
                assert "save_wearable_tokens" not in _calls_in(child), (
                    "token yazımı kapı izni tutulurken yapılıyor")


def test_provider_backed_wearable_routes_are_gated():
    """Üç sağlayıcı-destekli route da PAYLAŞILAN kapasiteyi tüketir."""
    path = os.path.join("app", "blueprints", "wearables.py")
    for name in ("wearable_callback", "wearable_sync", "whoop_resource"):
        node = _function_source(path, name)
        assert "blocking_concurrency_slot" in _calls_in(node), (
            f"{name} sağlayıcıya gider ama paylaşılan kapıyı KULLANMIYOR")


# ── 6. Arıza sınıflaması: doygunluk ≠ sağlayıcı arızası ────────────────────

def test_saturation_and_provider_failure_stay_distinguishable():
    """Doygunluk BlockingConcurrencyLimit; sağlayıcı arızası kendi tipini korur."""
    taken = _drain_all(ai_gate._model_slots)
    try:
        with pytest.raises(ai_gate.BlockingConcurrencyLimit):
            with ai_gate.model_concurrency_slot("test", wait_seconds=0):
                pass
    finally:
        _release(ai_gate._model_slots, taken)

    class _ProviderTimeout(Exception):
        pass

    with pytest.raises(_ProviderTimeout):
        with ai_gate.model_concurrency_slot("test", wait_seconds=0):
            raise _ProviderTimeout("upstream read timeout")


def test_overload_response_body_does_not_leak_provider_internals(app):
    """Aşırı yük gövdesi sağlayıcı adı/URL/istisna metni SIZDIRMAZ."""
    from app.blueprints import wearables

    with app.test_request_context("/"):
        response = wearables._overload_response()
        body = response.get_data(as_text=True).lower()

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "15"
    for leak in ("whoop", "google", "http", "traceback", "token", "oauth"):
        assert leak not in body, f"aşırı yük gövdesi '{leak}' sızdırıyor"


def test_provider_error_categories_are_a_fixed_set():
    """Metrik `Outcome` boyutu SABİT kümelidir — kardinalite patlamaz."""
    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    class WeirdVendorSpecificError(Exception):
        pass

    assert ai_gate._provider_error_category(RateLimitError()) == "rate_limit"
    assert ai_gate._provider_error_category(APITimeoutError()) == "timeout"
    assert ai_gate._provider_error_category(APIConnectionError()) == "connection"
    assert ai_gate._provider_error_category(GeneratorExit()) == "cancelled"
    # Tanınmayan HER şey tek kovaya düşer.
    assert ai_gate._provider_error_category(
        WeirdVendorSpecificError()) == "error"


# ── 7. Metrikler: sınırlı boyut, arıza isteği değiştirmez ──────────────────

def test_capacity_gauges_carry_no_dimensions(monkeypatch):
    """Kapasite gauge'ları süreç-geneli tekil sinyaldir → boyut YOK (PII de yok)."""
    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: True)
    ai_gate.record_capacity_gauges()

    names = {name for name, _ in runtime_metrics._gauges}
    assert {"ThreadReserve", "AiSlotsActive", "AiModelSlotsActive",
            "ScrapeSlotsActive"} <= names
    for name, dims_key in runtime_metrics._gauges:
        assert dims_key == (), f"{name} boyut taşıyor: {dims_key}"


def test_thread_reserve_gauge_tracks_measured_usage(monkeypatch):
    """`ThreadReserve` GERÇEK kullanımı izler — sabit bir boot değeri değil.

    docs/ROLLOUT.md ve MOBILE_AUTH_ENABLED kaydı bu gauge'ı rollout abort sinyali
    olarak adlandırıyordu; PR4 öncesi HİÇ yayınlanmıyordu.
    """
    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: True)

    ai_gate.record_capacity_gauges()
    idle = runtime_metrics._gauges[("ThreadReserve", ())]

    with ai_gate.blocking_concurrency_slot(wait_seconds=0):
        ai_gate.record_capacity_gauges()
        busy = runtime_metrics._gauges[("ThreadReserve", ())]

    assert busy == idle - 1

    ai_gate.record_capacity_gauges()
    assert runtime_metrics._gauges[("ThreadReserve", ())] == idle


def test_sampler_failure_never_breaks_the_flush_cycle(monkeypatch):
    """Enstrümantasyon arızası yutulur — ölçüm asla davranışı değiştirmez."""
    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: True)
    calls = []

    def _boom():
        calls.append("boom")
        raise RuntimeError("sampler patladı")

    def _ok():
        calls.append("ok")
        runtime_metrics.set_gauge("ProbeGauge", 1)

    runtime_metrics.register_sampler(_boom)
    runtime_metrics.register_sampler(_ok)

    runtime_metrics._run_samplers()  # yükselmemeli

    assert calls == ["boom", "ok"], "arızalı örnekleyici sonrakini engelledi"
    assert ("ProbeGauge", ()) in runtime_metrics._gauges


def test_register_sampler_is_idempotent():
    def _sampler():
        pass

    runtime_metrics.register_sampler(_sampler)
    runtime_metrics.register_sampler(_sampler)
    assert runtime_metrics._samplers.count(_sampler) == 1


def test_shutdown_flush_is_safe_when_metrics_are_disabled(monkeypatch):
    """Kapanış flush'ı kapalı yapılandırmada ve hata altında SESSİZ no-op."""
    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: False)
    runtime_metrics.flush_on_shutdown()  # patlamamalı

    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: True)

    def _explode():
        raise RuntimeError("cloudwatch down")

    monkeypatch.setattr(runtime_metrics, "flush", _explode)
    runtime_metrics.flush_on_shutdown()  # yine patlamamalı


# ── 8. JWKS zorunlu tazeleme: sınırlı, tek-uçuşlu ─────────────────────────

def test_unknown_kid_storm_triggers_at_most_one_forced_jwks_fetch(monkeypatch):
    """Uydurma `kid` seli TEK ağ çağrısına indirgenir.

    Öncesinde her bilinmeyen kid koşulsuz 5 sn'lik BLOKLAYICI bir JWKS fetch'i
    tetikliyordu; bu çağrı hiçbir kapının arkasında değil ve thread rezervinde
    sayılmıyor. Kimliği doğrulanmamış bir akış tüm web thread'lerini park
    ettirebilirdi.
    """
    from app.services import cognito_jwt

    cognito_jwt._reset_cache()
    empty = _keyset_without_bogus_kid()
    forced = {"n": 0}

    def _fake_load(force=False):
        if force:
            forced["n"] += 1
        return empty

    monkeypatch.setattr(cognito_jwt, "_load_jwks", _fake_load)
    try:
        for _ in range(25):
            assert cognito_jwt._forced_refresh_for("bogus-kid") is None
        assert forced["n"] == 1, (
            f"25 bilinmeyen-kid isteği {forced['n']} zorunlu fetch tetikledi; "
            "soğuma penceresi bunu 1'de tutmalı")
    finally:
        cognito_jwt._reset_cache()


def test_forced_jwks_refresh_resumes_after_the_cooldown_window(monkeypatch):
    """Soğuma penceresi KALICI bir kilitlenme değildir — pencere geçince tazelenir."""
    from app.services import cognito_jwt

    cognito_jwt._reset_cache()
    monkeypatch.setattr(
        cognito_jwt, "JWKS_FORCED_REFRESH_COOLDOWN_SECONDS", 60.0)
    empty = _keyset_without_bogus_kid()
    forced = {"n": 0}

    def _fake_load(force=False):
        if force:
            forced["n"] += 1
        return empty

    monkeypatch.setattr(cognito_jwt, "_load_jwks", _fake_load)
    try:
        cognito_jwt._forced_refresh_for("bogus-kid")
        assert forced["n"] == 1
        cognito_jwt._forced_refresh_for("bogus-kid")
        assert forced["n"] == 1, "pencere içinde ikinci fetch olmamalı"

        # Sahte saat penceresi geçirir (gerçek beklemeye gerek yok).
        cognito_jwt._last_forced_refresh -= 120.0
        cognito_jwt._forced_refresh_for("bogus-kid")
        assert forced["n"] == 2, "pencere geçtikten sonra tazeleme sürmeli"
    finally:
        cognito_jwt._reset_cache()


def test_failed_forced_refresh_still_opens_the_cooldown(monkeypatch):
    """JWKS ucu DÜŞÜKKEN de soğuma işler — düşen uca sel gönderilmez."""
    from app.services import cognito_jwt

    cognito_jwt._reset_cache()
    empty = _keyset_without_bogus_kid()
    forced = {"n": 0}

    def _fake_load(force=False):
        if not force:
            return empty
        forced["n"] += 1
        raise cognito_jwt.TokenValidationError("jwks_unavailable")

    monkeypatch.setattr(cognito_jwt, "_load_jwks", _fake_load)
    try:
        with pytest.raises(cognito_jwt.TokenValidationError):
            cognito_jwt._forced_refresh_for("bogus-kid")
        # İkinci istek ağ'a ÇIKMAZ: kesin ret döner (None → invalid_key).
        assert cognito_jwt._forced_refresh_for("bogus-kid") is None
        assert forced["n"] == 1
    finally:
        cognito_jwt._reset_cache()


# ---------------------------------------------------------------------------
# FatSecret besin yüzeyleri — triage 2026-08-07 #2
#
# PR #199 `food_search`ü rezerv-sayılan slota aldı ama kardeş FatSecret
# route'ları (barkod, porsiyon-by-id, porsiyon-by-name) ve daha sonra eklenen
# mobil keşif yüzeyi kapısız kaldı: sağlayıcı gecikme atağında kullanıcılar-arası
# bir yığın 8 web thread'ini park edip /health'i kuyruğa sokabilirdi. Kullanıcı
# başı rate limit kullanıcılar-arası yığını sınırlamaz.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _saturated_blocking_capacity():
    """Paylaşılan bloklayıcı izinlerin TAMAMINI tut (bekleme 0 → anında ret)."""
    held = 0
    try:
        while ai_gate._ai_slots.acquire(blocking=False):
            held += 1
        assert held, "izin alınamadı — doygunluk kurulumu bozuk"
        yield
    finally:
        for _ in range(held):
            ai_gate._ai_slots.release()


@pytest.mark.parametrize("path", [
    "/api/food/barcode?code=5000159407236",
    "/api/food/77777/servings",
    "/api/food/servings-by-name?name=yumurta",
])
def test_fatsecret_food_routes_shed_load_instead_of_parking_threads(
        client, auth_user, path):
    with _saturated_blocking_capacity():
        response = client.get(path)
    assert response.status_code == 503, (
        f"{path} kapasite dolayken sağlayıcıya gidiyor (thread park eder)")
    assert response.headers.get("Retry-After") == "15"
    # Doygunluk bir "besin bulunamadı" DEĞİLDİR: istemci 404/boş sonuç GÖRMEMELİ.
    assert "error" in response.get_json()


def test_barcode_cache_hit_does_not_consume_a_blocking_permit(client, auth_user):
    """Slot YALNIZCA ağ turunu sarar — cache HIT saf DB'dir.

    Aksi halde ucuz tarama akışı sağlayıcıya hiç gitmediği hâlde 503 yerdi.
    """
    from app.extensions import db
    from app.models import BarcodeFoodCache

    db.session.add(BarcodeFoodCache(
        barcode="5000159407236", food_id="77777", food_name="Protein Bar",
        brand="Acme", payload={"food_id": "77777", "name": "Protein Bar",
                               "brand": "Acme", "servings": []}))
    db.session.commit()

    with _saturated_blocking_capacity():
        response = client.get("/api/food/barcode?code=5000159407236")
    assert response.status_code == 200


def test_mobile_food_discovery_provider_calls_are_gated():
    """Mobil keşif yüzeyi de PAYLAŞILAN kapasiteyi tüketir (aynı sınıf)."""
    path = os.path.join("app", "services", "mobile_food_discovery.py")
    for name in ("search", "servings", "barcode_lookup"):
        node = _function_source(path, name)
        assert "blocking_concurrency_slot" in _calls_in(node), (
            f"mobile_food_discovery.{name} sağlayıcıya gider ama kapısız")


def test_barcode_lookup_gate_wraps_only_the_network_round_trip():
    """Kapı izni DB yazımını (cache satırı + commit) KAPSAMAMALI."""
    node = _function_source(
        os.path.join("app", "services", "barcode.py"), "get_barcode_product")
    for child in ast.walk(node):
        if not isinstance(child, ast.With):
            continue
        if any(isinstance(item.context_expr, ast.Call)
               and getattr(item.context_expr.func, "id", None)
               == "blocking_concurrency_slot"
               for item in child.items):
            assert "commit" not in _calls_in(child), (
                "cache yazımı kapı izni tutulurken commit ediliyor")
