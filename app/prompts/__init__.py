# Prompt Engineering Layer (Sprint 4 WS4): tüm LLM istem şablonları uygulama
# mantığından AYRI, bu pakette yaşar. Modüller yalnızca string/şablon üretir —
# DB, Flask veya sağlayıcı istemcisi import ETMEZ (saf, bağımsız test edilebilir).
#
# Harita:
#   system.py    — koç sistem promptu + dil direktifleri (build_coach_system)
#   goals.py     — hedefe (kas kazanma / kilo verme) duyarlı plan-koçluğu promptu
#   nutrition.py — beslenme promptları (besin arama, EN normalizasyon, menü
#                  çıkarımı, porsiyon/makro tahmini, öğün toplamı) + PORTION_SANITY_RULE
#   workout.py   — antrenman planı promptları (training_generation'a delege)
#   progress.py  — haftalık check-in / ilerleme geri bildirimi promptu
