# FatSecret loopback proxy — gerçek kurulum (2026-07-11'de doğrulandı)

Port 3000'deki "FatSecret proxy" **ayrı bir süreç DEĞİLDİR** — EC2 host'undaki
nginx'in bir server bloğudur:

```
/etc/nginx/sites-available/fatsecret-proxy   (sites-enabled'a symlink'li)

server {
    listen 127.0.0.1:3000;          # YALNIZ loopback — aşağıdaki geçmişe bak
    location / {
        proxy_pass https://platform.fatsecret.com;
        proxy_set_header Host platform.fatsecret.com;
        ...
    }
}
```

Zincir: Flask (container) → `FATSECRET_BASE_URL` =
`https://fitx-chatbot.duckdns.org/fatsecret` → host nginx :443
(`location = /fatsecret/rest/server.api`) → upstream `127.0.0.1:3000`
(bu blok) → `platform.fatsecret.com`. Bearer token'ı **uygulama** ekler;
bu blok kimlik bilgisi taşımaz.

## Süpervizyon (I4 kapanışı)

Blok nginx'in içinde yaşadığı için süpervizyonu **nginx'in systemd servisi**
sağlar — ayrı bir systemd unit'i GEREKMEZ (bu dosyanın yerini aldığı
`fatsecret-proxy.service.example` şablonu bu yüzden kaldırıldı). deploy.yml her
deploy'da `127.0.0.1:3000` dinleyicisini kontrol eder; `/health?deep=1`
`fatsecret_proxy` alanında uçtan uca raporlar (bilgilendirici — gate'i düşürmez).

## Güvenlik geçmişi (2026-07-11'de kapatıldı)

- Blok `listen 3000` (0.0.0.0) idi **ve** SG (`launch-wizard-1`) 3000'i tüm
  internete açıyordu → sunucu, platform.fatsecret.com'a herkese açık bir relay
  idi. Düzeltme: `listen 127.0.0.1:3000` + SG kuralı revoke edildi.
- Canlı `fitx` site config'i upstream olarak public IP (`18.153.156.28:3000`)
  kullanıyordu (repo `nginx.conf`'undan drift). Loopback bind'e geçince 502
  verdi; upstream `127.0.0.1:3000`'e çevrildi.

## Değişiklik yaparken

- **listen adresi değişikliği `nginx reload` ile UYGULANMAZ**: eski soket yeni
  bind'i engeller, nginx emerg loglar ve sessizce eski sokette kalır —
  `systemctl restart nginx` gerekir.
- Canlı site config'leri certbot/elle düzenlenmiştir; repo dosyasını üzerine
  `cp`'leme (bkz. nginx.conf içindeki uyarı) — satırları elle merge et.
- Doğrulama: host'ta `ss -ltn | grep 3000` → yalnız `127.0.0.1:3000`;
  `curl -s 'http://127.0.0.1:5000/health?deep=1'` → `"fatsecret_proxy":"ok"`.
