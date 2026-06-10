"""Allow running as: python -m fitx_mcp"""
from fitx_mcp.server import mcp as app

if __name__ == "__main__":
    import os
    import sys
    if "--http" in sys.argv:
        # GÜVENLİK: MCP araçları `user_id`yi düz bir parametre olarak alır ve KENDİ
        # yetkilendirmesini YAPMAZ — herhangi bir çağıran HERHANGİ bir kullanıcının
        # verisini okuyabilir/yazabilir. Bu, stdio taşıması (yerel, tek güvenilir
        # süreç) ve Flask'ın süreç-içi import'u (user_id current_user'dan enjekte
        # edilir) için kabul edilebilir. Ancak streamable-http taşıması, bu
        # kimliksiz/çapraz-kullanıcı erişimini sokete ulaşabilen HER ŞEYE açar.
        # Bu yüzden YALNIZCA loopback'e bağlanır ve kazara açılmasın diye açık bir
        # opt-in'in arkasına alınır. Önüne bir kimlik doğrulama/yetkilendirme katmanı
        # koymadan ASLA herkese açık bir reverse proxy arkasına yerleştirme.
        if os.environ.get("FITX_MCP_ALLOW_HTTP") != "1":
            sys.exit(
                "MCP HTTP taşıması başlatılmıyor: kimliksiz, çapraz-kullanıcı "
                "veritabanı erişimi açığa çıkarır. Riski anlıyorsan (yalnızca "
                "loopback) FITX_MCP_ALLOW_HTTP=1 ayarla."
            )
        app.run(transport="streamable-http", host="127.0.0.1", port=8100)
    else:
        app.run(transport="stdio")
