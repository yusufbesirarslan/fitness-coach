# CSP Sertleştirme: Satır-içi `onclick` → `addEventListener` Göçü

> Durum: **PLANLI / ertelendi** (S2). Bu belge, `script-src-attr 'unsafe-inline'`
> direktifini güvenle kaldırmak için yapılacak göçü tarif eder. Henüz uygulanmadı.

## Amaç

CSP başlığından (`app/hooks.py`, `set_csp_header`) `script-src-attr 'unsafe-inline'`
direktifini kaldırmak. Böylece nitelik tabanlı XSS (`onclick=`, `on*=`) tarayıcı
tarafından bloklanır ve per-request **nonce**, betik çalıştırmanın TEK yolu olur.

Bugün direktif zorunlu çünkü 14 şablonda toplam **134 satır-içi olay handler'ı** var.
`<script>` blokları zaten nonce ile korunuyor; bu yüzden direktif bir **açık delik
değil**, fakat nonce çalışmasının sağladığı korumayı nitelik-handler'ları için zayıflatıyor.

## Envanter (şablon başına handler sayısı)

| Şablon | Handler |
|---|---|
| `nutrition.html` | 24 |
| `training.html` | 23 |
| `setup.html` | 17 |
| `index.html` | 15 |
| `manage_stack.html` | 12 |
| `chat.html` | 9 |
| `progress.html` | 8 |
| `edit_profile.html` | 6 |
| `friends.html` | 6 |
| `_chat_widget.html` | 4 |
| `leaderboard.html` | 3 |
| `login.html` | 3 |
| `register.html` | 3 |
| `quests.html` | 1 |

(Sayım: `grep -rEo 'onclick=|addEventListener\(' templates/` — `addEventListener`
zaten kullanılan yerler bu sayıya dahil değil; gerçek `on*=` nitelikleri hedeftir.)

## Göç deseni (şablon başına)

1. Her `onclick="fn(args)"` → `data-action="fn"` (+ argümanlar için `data-*`) niteliğine taşı.
2. Sayfa başına TEK delege dinleyici ekle, nonce'lu bir blokta:
   ```html
   <script nonce="{{ csp_nonce }}">
   document.addEventListener('click', (e) => {
     const el = e.target.closest('[data-action]'); if (!el) return;
     ({ fn1, fn2, /* ... */ })[el.dataset.action]?.(el);
   });
   </script>
   ```
   `friends.html` (ekle butonu) bu delege deseni **zaten** kullanıyor — referans al.
3. Diğer `on*` handler'ları için tekrarla: `onsubmit`, `onchange`, `oninput`,
   `onkeyup` vb. (form/submit için `submit`, input için `input`/`change` olayları).

## Sıralama (en düşük riskten yükseğe)

1. **Statik / auth sayfaları**: `login`, `register`, `quests`, `leaderboard`
2. **Profil / kurulum**: `edit_profile`, `setup`, `manage_stack`, `progress`
3. **Veri render eden sayfalar**: `chat`, `_chat_widget`, `friends`, `nutrition`,
   `training`, `index`

14 şablonun **TAMAMI** göçtükten SONRA `app/hooks.py:set_csp_header` içinden
`script-src-attr 'unsafe-inline'` satırını sil. O ana dek direktif kalmalı.

## Doğrulama

- Her sayfayı DevTools konsolu açıkken yükle; tüm etkileşimli kontrolleri tıklayarak
  **sıfır CSP ihlali** olduğunu doğrula.
- `script-src-attr 'unsafe-inline'` kaldırıldıktan sonra, kalan bir satır-içi handler
  varsa konsolda "Refused to execute inline event handler" hatası görünür → o şablon
  atlanmış demektir.
- Regresyon: her şablonda en az bir buton + bir form gönderimi elle test edilmeli
  (otomatik test yok; bu saf frontend davranışıdır).

## Neden ertelendi

134 handler'ın 14 şablona yayılması, sırf-frontend ve otomatik testle korunmayan
geniş bir değişiklik. Çekirdek güvenlik (SSRF, presign sahiplik, MCP authz, prompt
injection) ve veri-modeli düzeltmeleriyle aynı PR'a sıkıştırmak regresyon riskini
artırırdı. Nonce zaten `<script>` bloklarını koruduğundan bu bir sertleştirme
iyileştirmesidir, acil bir açık değil.
