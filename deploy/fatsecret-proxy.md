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

## nginx startup DNS resilience (2026-08-28)

The static `proxy_pass https://platform.fatsecret.com` directive above is
resolved by `nginx -t` during the service's `ExecStartPre`. A transient DNS
failure can therefore fail the entire nginx activation, including the public
80/443 listeners. Ordering nginx after `nss-lookup.target` does not prove that
DNS answers are ready.

The repository-owned systemd drop-in is:

```
deploy/systemd/nginx.service.d/dns-restart-resilience.conf
```

Install it reproducibly on the host:

```sh
sudo install -d -m 0755 /etc/systemd/system/nginx.service.d
sudo install -m 0644 \
  deploy/systemd/nginx.service.d/dns-restart-resilience.conf \
  /etc/systemd/system/nginx.service.d/dns-restart-resilience.conf
sudo systemctl daemon-reload
sudo systemd-analyze verify nginx.service
sudo nginx -t
sudo systemctl restart nginx
```

Effective behavior: `Restart=on-failure`, `RestartSec=10s`, with at most 12
activations in 300 seconds. This covers the proven short resolver restart race
without an unbounded high-frequency loop. If DNS remains unavailable for about
110 seconds, start limiting leaves nginx failed for operator intervention; the
external Route 53 health check and CloudWatch alarm remain the detection path.

Rollback:

```sh
sudo rm /etc/systemd/system/nginx.service.d/dns-restart-resilience.conf
sudo systemctl daemon-reload
sudo systemctl reset-failed nginx
```

Then verify `systemctl show nginx -p Restart -p RestartUSec` reports the vendor
defaults. Removing the drop-in does not change nginx site configuration or
restart the running service by itself.
