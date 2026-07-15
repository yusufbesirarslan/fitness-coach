"""Sprint 4 WS8 — arka-plan iş gövdeleri (RQ task'ları).

Bu fonksiyonlar HEM worker sürecinde (RQ tarafından qualified-name ile çağrılır)
HEM de satır-içi fallback'te (enqueue_or_run, kuyruk yokken) koşabilir. Worker
sürecinde Flask app/DB context'i YOKTUR → gövdeler taze bir `create_app()` app
context'i içinde çalışır. Satır-içi çağrıda zaten bir context olabilir; iç içe
app_context güvenlidir (Flask push/pop).

Not: yalnızca `summarize_conversation` şu an bağlıdır (PR2'nin satır-içi lazy
özetleme yolunun arka-plan hali, ai_pipeline._memory_stage üzerinden). Ek task
tipleri (besin analizi, haftalık içgörü, öneriler) bu desene aynen eklenir:
top-level fonksiyon + `_in_app_context` sarmalayıcı + `enqueue_or_run` çağrısı.
"""
import logging
import os

_log = logging.getLogger(__name__)

# Worker sürecinde app'i TEK KEZ kur ve önbelleğe al. Aksi halde her job yeni bir
# create_app() (→ init_database: create_all + migration) koşardı: hem pahalı hem
# de web konteyneriyle boot-migration yarışı. Worker şemayı YÖNETMEZ (web sahibi);
# bu yüzden create_app'ten önce FITX_SKIP_DB_INIT=1 ayarlanır.
_worker_app = None


def _in_app_context(fn):
    """Task gövdesini bir app context içinde çalıştır.

    - Zaten context varsa (satır-içi/inline — istek yolu ya da test): ONU yeniden
      kullan (yeni app ayrı/boş DB engine'i açardı). Çağıran session'ı yönetir.
    - Worker sürecinde (context yok): önbelleğe alınmış tek app'in context'ini
      push et ve her job sonrası `db.session.remove()` ile session'ı temizle
      (worker uzun ömürlüdür; bayat session sonraki job'a sızmasın)."""
    from flask import has_app_context
    if has_app_context():
        return fn()
    global _worker_app
    if _worker_app is None:
        os.environ.setdefault("FITX_SKIP_DB_INIT", "1")  # worker şema migrate ETMEZ
        from app import create_app
        _worker_app = create_app()
    with _worker_app.app_context():
        from app.extensions import db
        try:
            return fn()
        finally:
            db.session.remove()


def summarize_conversation(conversation_id):
    """Bir konuşmanın özetlenmemiş eski turlarını conversation.summary'ye katla.

    İdempotent: `memory_manager.maybe_summarize` eşik + summarized_upto_id
    kontrollüdür — tekrar çalışmak ya da yarışmak çift-özet üretmez, yalnızca
    gereksiz LLM çağrısı olmaz (eşik altındaysa False döner). Konuşma yoksa
    (silinmiş) sessizce False.
    """
    def _body():
        from app.extensions import db
        from app.models import CoachConversation
        from app.services import ai_metrics, memory_manager
        conv = db.session.get(CoachConversation, conversation_id)
        if conv is None:
            _log.info("[JOBS] summarize: konusma %s bulunamadi", conversation_id)
            return False
        did = memory_manager.maybe_summarize(conv)
        ai_metrics.increment("SummarizeJob",
                             dimensions={"result": "done" if did else "skip"})
        return did
    return _in_app_context(_body)
