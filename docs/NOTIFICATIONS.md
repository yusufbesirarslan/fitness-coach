# Bildirimler (Notifications) — Sprint 5 PR1

Sosyal olay bildirimleri: bir pump check beğenildiğinde/yorumlandığında,
arkadaşlık isteği geldiğinde veya kabul edildiğinde alıcıya satır yazılır.
Tüm okuma yolları `current_user.id`'ye scope'ludur — kimse başkasının
bildirimini göremez.

## Model — `Notification` (`app/models.py`)

| Kolon | Tip | Not |
|---|---|---|
| `user_id` | FK→user, CASCADE, index | **alıcı** |
| `actor_id` | FK→user, CASCADE, nullable, index | tetikleyen; `NULL` = sistem |
| `ntype` | String(30) | kanonik İngilizce slug (aşağıda) |
| `target_type` | String(20), nullable | `pump_check` \| `friendship` \| `feed_item` \| `challenge` |
| `target_id` | Integer, nullable | hedef/ayırt-edici kayıt id'si (bkz. dedup) |
| `payload` | JSONB/JSON, nullable | serbest ek veri |
| `is_read` | Boolean, default false | |
| `created_at` | DateTime, index | |

İndeksler: `user_id`, `actor_id`, `created_at` tekil + bileşik
`ix_notification_user_read (user_id, is_read)` (okunmamış sayacı/liste için).

Geçerli `ntype` değerleri:
- PR1 (sosyal): `pump_check_like`, `pump_check_comment`, `friend_request`,
  `friend_accept`.
- PR2 (Feed V2): `feed_like`, `feed_comment`, `repost`, `quote_repost`
  (`target_type='feed_item'`).
- PR3 (Challenges): `challenge_complete` (`target_type='challenge'`, sistem
  bildirimi → `actor_id=NULL`).

Görünen metin sunucuda ÜRETİLMEZ — istemci i18n `notif.<ntype>` anahtarından
`{username}` ile kurar (display-map kuralı; kanonik slug İngilizce kalır).

## Servis — `app/services/notifications.py`

- `notify(user_id, ntype, actor_id=None, target_type=None, target_id=None, payload=None)`
  — satırı **session'a ekler, COMMIT ETMEZ**: tetikleyen eylemle (beğeni/yorum/
  istek) aynı transaction'da atomiktir; eylem rollback olursa bildirim de gider.
  Asla exception sızdırmaz (bildirim yazılamaması ana eylemi kırmaz).
- `unread_count(user_id)` — okunmamış sayısı.
- `mark_read(user_id, ids=None, mark_all=False)` — YALNIZCA kendi satırları
  (`WHERE user_id`); kendi başına uç-nokta eylemi olduğu için **commit eder**.
- `serialize_notification(n)` — camelCase dict (`{id, type, actor{username,avatar}|None,
  targetType, targetId, payload, isRead, createdAt}`).
- `purge_old(now=None)` — saklama süpürmesi (aşağıda); commit eder.
- `purge_content_notifications(pump_check_ids=None, feed_item_ids=None)` — silinen
  içeriğe (pump check / feed item) İŞARET EDEN bildirimleri süpürür (öksüz-hedef
  temizliği; aşağıda). Session'a uygular, **COMMIT ETMEZ** (çağıranın silme
  transaction'ında atomik). Asla exception sızdırmaz; silinen sayısını döner.

### Okunmamış-dedup (spam kalkanı)
`notify`, aynı `(user_id, actor_id, ntype, target_type, target_id)` beşlisi için
**OKUNMAMIŞ** bir satır varsa yenisini yazmaz — bu, beğen→geri-al→beğen döngüsünün
bildirim yağmuruna dönmesini engeller. Alıcı bildirimi okuduktan sonra aynı olay
tekrar meydana gelirse yeniden bildirilir. Ayrıca `actor_id == user_id` ise
(kendi eylemi) hiç yazılmaz.

**Kimlik `target_id`'de taşınır — `payload` dedup'a girmez.** Tekrar eden ya da
çok-hedefli olaylarda ayırt edici değer `target_id`'ye konur, aksi halde farklı
gerçek olaylar tek bildirime çöker:
- `repost` / `quote_repost` → `target_id = repost edilen pump check id` (ref_id).
  Yeni FeedItem.id commit öncesi bilinmediğinden kaynak gönderi id'si kimlik
  olarak kullanılır; böylece farklı gönderilerin repost'ları çakışmaz.
- `challenge_complete` → `target_id = UserChallengeProgress.row.id` (haftalık
  tamamlama satırı; `(user, challenge, period_key)` başına tekil). Aynı
  challenge'ın farklı haftalardaki tamamlamaları böylece ayrı bildirilir.

Bu iki türde de gerçek tekrar (aynı gönderi / aynı challenge-aynı hafta) yine
dedup edilir — çünkü aynı `target_id`'yi üretir.

## Tetikleme noktaları
Hepsi ilgili eylemle aynı transaction'da, **commit'ten önce**.

`app/blueprints/social.py`:
- `pump_check_like` → sahibe `pump_check_like` (yalnızca yeni beğenide).
- `pump_check_comment_create` → sahibe `pump_check_comment`.
- `friend_request` → hedefe `friend_request` (hem taze-insert hem
  rejected-reuse yolu).
- `friend_accept` → gönderene `friend_accept` (guarded UPDATE'i kazanan tarafta).
- `feed_item like/comment` → sahibe `feed_like` / `feed_comment` (`target_id =
  feed item id`).
- `feed_repost` → orijinal sahibine `repost` / `quote_repost` (`target_id =
  ref_id`; kendi gönderisini repost edende no-self-notify ile yazılmaz).

`app/services/challenges.py` (`_try_complete`, sistem bildirimi):
- Bir challenge tamamlanınca alıcıya `challenge_complete` (`actor_id=NULL`,
  `target_id = progress row id`).

Giden istek iptal edilince (`DELETE /friend/request/<id>`) alıcıdaki artık
hayalet olan okunmamış `friend_request` bildirimi aynı transaction'da süpürülür.

### İçerik silme → öksüz-hedef temizliği
Bir pump check kalıcı silinince (`DELETE /pump-check-gallery/<id>`,
`app/blueprints/profile.py`) onu ve ona atıfta bulunan repost/quote FeedItem'larını
HEDEF ALAN bildirimler ölü hedefe düşerdi (tıklanınca "içerik yok" stub'ı). Rota,
FeedItem repost'larını (+ like/yorumlarını) sildikten sonra aynı transaction'da
`purge_content_notifications(pump_check_ids=[check.id], feed_item_ids=ref_items)`
çağırır. Hedef kimliği bildirim TÜRÜNE göre farklı tabloya işaret ettiğinden
(`pump_check.id` ile `feed_item.id` sayısal ÇAKIŞABİLİR) süpürme ntype ile
HASSAS eşlenir:
- `pump_check_like` / `pump_check_comment` (`target_type='pump_check'`) → `pump_check_ids`.
- `repost` / `quote_repost` (`target_type='feed_item'` ama `target_id`=KAYNAK pump
  check id — dedup kimliği) → `pump_check_ids`.
- `feed_like` / `feed_comment` (`target_type='feed_item'`, `target_id`=FeedItem.id)
  → `feed_item_ids`.

Not: Feed V2 yalnızca `pump_check` repost eder (`_REPOST_REF_TYPES`), FeedItem'ı
DEĞİL — çok-seviyeli repost zinciri oluşamaz, dolayısıyla tek seviye temizlik
öksüz zincir bırakmaz. Kullanıcı silme (`cli.py _purge_user`) ayrı yoldur:
bildirimleri alıcı (child-model döngüsü) + `actor_id` filtresiyle süpürür.

## Uçlar (`app/blueprints/notifications.py`, prefix yok, hepsi `@require_auth`)
- `GET /notifications` — sayfa kabuğu (`notifications.html`).
- `GET /notifications/data?before_id=&limit=` — keyset (id desc, n+1 lookahead),
  `limit` [1,50] varsayılan 20 → `{notifications, hasMore, nextBeforeId, unreadCount}`.
- `GET /notifications/unread-count` → `{count}`.
- `POST /notifications/read` gövde `{ids:[..]}` | `{all:true}` →
  `{ok, unreadCount}`; geçersiz gövde → 400.

Nav zili (`templates/_nav.html`) `unread-count`'u açılışta + `visibilitychange`'de
çeker ve `#notif-badge`'i günceller.

## Saklama / süpürme
`purge_old`: okunmuş satırlar 30 günde (`PRUNE_READ_DAYS`), her satır en geç 90
günde (`PRUNE_ALL_DAYS`) silinir. Günlük self-heal olarak
`app/hooks.py maybe_weekly_rollover`'daki mevcut günlük purge bloğuna bağlıdır
(yeni cron/anahtar yok). Kapatma anahtarı gerekmez — Redis'siz/worker'sız çalışır.

## Yeni bir `ntype` eklerken
1. `notify(...)` çağrısını tetikleyen eyleme, commit'ten önce ekle.
2. Slug'ı bu dosyaya ekle (kanonik İngilizce).
3. `locales/{tr,en}.json`'a `notif.<ntype>` metnini `{username}` ile ekle.
4. `notifications.html` içindeki `ICONS` haritasına ikon; gerekiyorsa
   `targetUrl()`'a yeni `target_type` yönlendirmesi ekle.
5. Servis + rota testlerini genişlet (`tests/test_notifications.py`,
   `tests/test_notification_routes.py`).

## Cascade / kullanıcı silme
`Notification`, `app/cli.py _user_child_models()` listesinde (alıcı satırları
child-model döngüsüyle silinir) ve `_purge_user` ayrıca `actor_id == uid`
satırlarını açık filtreyle siler (SQLite FK cascade zorlamaz).
`tests/test_cascade_delete.py` bu kapsamı introspeksiyonla doğrular.
