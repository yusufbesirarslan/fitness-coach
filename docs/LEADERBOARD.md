# Leaderboard (liderlik tablosu)

İki zaman dilimi (`all_time` / `weekly`) × iki kapsam (`global` / `friends`).
Kaynak daima Postgres; Redis sorted set yalnızca hızlı okuma yolu. `app/services/
gamification.py` + `app/blueprints/gamification.py`.

## Skor

`_lb_score(xp, streak) = xp*100000 + min(streak, 99999)` — XP birincil,
streak tiebreak (aynı XP'de yüksek streak önde). `all_time` skoru `rank_points`,
`weekly` skoru `weekly_xp` üzerinden. `weekly_xp` Istanbul günlerinde birikir.

## Redis / Postgres yolu

- İki sorted set: `LB_ALLTIME_KEY`, `LB_WEEKLY_KEY` (config).
- `lb_sync_user(user)` tek kullanıcıyı iki sete yazar; Redis yoksa/çökerse sessizce
  geçer (Postgres kaynak). `lb_rebuild()` boot + haftalık rollover sonrası setleri
  Postgres'ten sıfırdan kurar.
- **Redis'e commit ÖNCESİ dokunma:** skor senkronu `after_commit` kancasındadır
  (`_mark_lb_dirty` bir bayrak koyar; commit BAŞARILI olunca sync tetiklenir). Aksi
  halde rollback olan bir istek leaderboard'u yukarı sürükleyebilirdi (H1-Redis).
- `/leaderboard/data`: Redis varsa `_leaderboard_via_redis`, hata/kapalıysa
  `_leaderboard_via_postgres` (`ORDER BY`). İkisi de aynı JSON şeklini döner.

## Haftalık sıfırlama sınırı + `resetAt`

Hafta sınırı **Pazar 23:59 Istanbul**. `run_weekly_rollover` haftalık board'u
snapshot'lar, top-3'e XP verir (`award_xp(..., count_challenge_xp=False)` — challenge
`xp_earned`'ini beslemez), `weekly_xp`'yi sıfırlar, `lb_rebuild` çağırır. Idempotent
(`WeeklyResetLog`). Tetik: EC2 host cron + uygulama-içi günlük self-heal (Redis NX
kilidi).

**`resetAt` sözleşmesi (Sprint 5 PR3 düzeltmesi):** `/leaderboard/data` artık
`"resetAt": <ISO UTC + "Z">` döner = `challenges.period_end_utc()` (yaklaşan Pazar
23:59 Istanbul → UTC). İstemci geri sayımı bundan hesaplar. ÖNCE `leaderboard.html`
sabit bir UTC sınırı (`LB_RESET = Pazar 23:59 UTC`) kullanıyordu → Istanbul ile
2-3 saat kayıyordu (yaz saati). Artık sunucu tek kaynak; challenge sayfası aynı
`period_end_utc`'yi `periodEndsAt` olarak kullanır.

## Top-3 ödülleri

Rollover kazananları `WeeklyWinner` satırına yazılır (`notified=False`);
`/leaderboard/reward-check` / `reward-dismiss` kutlama pop-up'ını yönetir.

## Challenge board'ları

Challenge sıralaması ayrıdır (`challenges.challenge_board`) — Redis kullanmaz,
`UserChallengeProgress`'i `progress` desc → `completed_at` asc (nulls-last) →
`user_id` asc ile sıralar; `friends` kapsamı `friends.get_friend_ids`. Ayrıntı:
`docs/CHALLENGES.md`.
