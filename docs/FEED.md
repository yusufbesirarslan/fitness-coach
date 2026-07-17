# Feed V2 (Sprint 5 PR2)

Modern sosyal fitness feed'i. PumpCheck birincil içerik; buna repost/quote,
kilometre-taşı kartları, beğeni/yorum, harici paylaşım ve görüntüleyen-başı
moderasyon eklenir. Amaç: mevcut PumpCheck like/comment/gallery/chat-share
yollarını KIRMADAN feed'i genişletmek.

## Omurga (spine)

Yeni `FeedItem` tablosu YALNIZCA yeni içerik türlerini tutar (`repost`, `quote`).
Feed sorgu-zamanında `app/services/feed.py`'de ÜÇ keyset kaynağı birleştirilerek
kurulur:

1. **PumpCheck** — `visibility=='feed'`, `friends ∪ self`.
2. **FeedItem** — `friends ∪ self` (repost + quote).
3. **Activity** — `MILESTONE_ACTIVITY_TYPES` allowlist'i, `friends ∪ self`.

`FeedItem.ref_id`'nin **FK'si YOKTUR** (polimorfik dikiş; `ref_type` ayırır).
Askıda kalan referans → "içerik yok" (`unavailable`) stub olarak serileştirilir;
pump-check silmede / kullanıcı purge'ünde temizlenir.

- **Plain repost** orijinal pump check'in etkileşimini yüzeye çıkarır
  (like/comment hedefi = pump check) + denormalize `PumpCheck.reposts_count`.
- **Quote repost** kendi likeable/commentable varlığıdır
  (`FeedItemLike`/`FeedItemComment`, `PumpCheckLike`/`Comment`'i aynen yansıtır).

Her serileştirilen öğe `engagement.target = {type, id}` taşır; frontend like/comment
uçlarını buna göre genel biçimde seçer (`pump_check` → `/pump-check/<id>/…`,
`feed_item` → `/feed/item/<id>/…`). Milestone `engagement=None` (V1'de like/comment yok).

## Kilometre taşları (milestones)

`MILESTONE_ACTIVITY_TYPES = ("level_up", "streak_milestone", "new_friend")`
(PR3'te `challenge_completed` eklenir). Materyalize edilmez — mevcut `Activity`
satırları sorgu-zamanında feed'e katılır. İkon `feed.MILESTONE_ICONS`
(`gamification.ACTIVITY_ICONS` ile hizalı). Arkadaş-görünürlüğü örtüktür (bilgi
zaten leaderboard'da görünür).

## Cursor + sıralama

`encode_cursor(created_at, source, id)` → base64 `"iso|source|id"`;
`decode_cursor` bozuk girdiye dayanıklı (→ ilk sayfa). Global sıra
`(created_at DESC, SOURCE_RANK DESC, id DESC)` — eşit `created_at`'te kaynak-arası
kopuş `SOURCE_RANK={pump_check:3, feed_item:2, activity:1}` ile deterministiktir.

Her kaynak `_keyset_filter` ile cursor'dan strict-küçük satırları `limit+1` çeker;
adaylar Python'da `_rank` (kronolojik sort — **algoritmik dikiş**, ileride
sıralama buraya girer) ile birleşir. `hasMore` = toplam > limit; `nextCursor`
son öğeden kurulur. Dup/skip yok (test: `test_feed_cursor_no_dup_no_skip`).

## Görünürlük ve repost gizlilik kuralı

- `can_view_pump_check` TEK yetki: `feed` → arkadaşlar; `friends` → seçili VE hâlâ
  arkadaş; `private` → yalnız sahip.
- **Repost yalnızca `visibility=='feed'` VE görülebilir içeriğe** izin verir
  (`POST /feed/repost` 403 aksi halde). Friends-only/private repost **kitle
  genişletir** (audience widening) — engellenir.
- Repost-of-restricted: mütekabil olmayan görüntüleyene `unavailable` stub;
  görebilen arkadaşa tam kart.

## Moderasyon (görüntüleyen-başı)

`FeedHide` / `FeedReport` polimorfiktir (`target_type ∈ {pump_check, feed_item,
activity}`). Gizleme yalnızca gizleyeni etkiler (çapraz-kullanıcı sızıntısı yok).
**Şikayet otomatik gizler** (aynı transaction'da `FeedHide` yazar). Kendi içeriğini
gizleme → 400. Tekrar şikayet → 400 (`uq_feed_report_target`).

## Uçlar

| Method | Yol | Not |
|---|---|---|
| GET | `/feed/data?cursor=&per_page=` | `{items, hasMore, nextCursor}` |
| POST | `/feed/repost` | `{ref_type, ref_id, mode:'repost'|'quote', body?}` → notify owner |
| DELETE | `/feed/item/<id>` | sahibe özel; children + floor-0 reposts_count |
| POST/DELETE | `/feed/item/<id>/like` | quote beğeni; notify author |
| GET/POST | `/feed/item/<id>/comments` | keyset (before_id) + canDelete; POST notify |
| DELETE | `/feed/item/<id>/comments/<cid>` | yazar VEYA post sahibi |
| GET | `/pump-check/<id>/comments?before_id=&limit=` | newest-first keyset + canDelete |
| DELETE | `/pump-check/<id>/comments/<cid>` | yazar VEYA post sahibi |
| POST | `/feed/hide` · `/feed/unhide` · `/feed/report` | moderasyon |

Bildirim türleri (ntype): `repost`, `quote_repost`, `feed_like`, `feed_comment`
(hepsi görünen metni istemcide `notif.<ntype>` i18n anahtarından kurar). Rate limit
sabitleri `app/config.py`: `FEED_WRITE_RATELIMIT`, `FEED_REPORT_RATELIMIT`,
`COMMENT_WRITE_RATELIMIT` (env ile ezilebilir).

## Harici paylaşım

`navigator.share` (Web Share API) + pano (clipboard) yedeği — yerelleştirilmiş
metin + `/feed` landing URL'i. **V1'de public tokenize sayfa YOK** (yeni unauth
route yok, token yaşam döngüsü yok, anonim S3 presign yok). Gelecek işi olarak
belgelenmiştir.

## Cascade / purge

- `pump_check_gallery_delete` ve `_purge_user`: silinen/purge'lenen kullanıcının
  pump check'lerine ATIFTA BULUNAN başkalarının repost'ları (ref_id FK'siz) önce
  çocuklarıyla birlikte silinir → askıda referans kalmaz.
- Beş yeni model `_user_child_models()`'de (children önce). Test:
  `test_purge_user_removes_feed_v2_rows_both_directions`,
  `test_gallery_delete_removes_referencing_reposts`.

## Genişletme dikişleri (V1'de UYGULANMAZ)

Algoritmik sıralama (`_rank`), reaksiyonlar, hashtag'ler, Explore, tokenize public
paylaşım sayfası — mimari destekler ama bu PR'da yapılmaz.
