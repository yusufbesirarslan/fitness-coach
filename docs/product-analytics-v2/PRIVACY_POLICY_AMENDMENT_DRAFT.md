# Privacy Policy amendment: product analytics (CONDITIONAL DRAFT, NOT FOR PUBLICATION)

Status: draft text for counsel. **Do not publish, link or ship.** Publication
needs counsel approval, the executed PostHog DPA + standard contract (KVKK
Art. 9(4)(c), notified within 5 business days), the final retention values and
founder GO.

Two variants are drafted:

* **C** matches the current source (independent opt-in).
* **L** applies only if counsel approves the narrow legitimate-interest model
  (LEGAL_BASIS_ASSESSMENT.md §3–§4).

Bracketed items are open values. The exact event inventory is
`catalog.json` (README §3).

---

## EN — Variant C (opt-in; current source)

### Product analytics (optional)

**What and why.** If you switch on *Product analytics* in Settings → Privacy &
data, we record how the app's features are used, so we can find where
onboarding is hard, whether plans get created, whether the Coach works and
whether features fail. The analytics are:

* events such as "onboarding completed", "a training plan was created",
  "a workout was completed", "a Coach reply was delivered", "a weekly check-in
  was submitted", or "a screen was opened"
* the time of each event
* a few fixed categories, such as which way a plan was created

We **never** include your name, e-mail, account ID, body measurements, photos,
meals or nutrition contents, your Coach messages or replies, check-in answers,
free text, device, operating system, location or IP address.

**Off by default.** Nothing is collected unless you switch it on. Switching it
on is separate from accepting the Terms, the health-data consent and AI
features. Refusing changes nothing else in the app.

**Legal basis.** Your explicit consent (KVKK Art. 5(1); [GDPR Art. 6(1)(a)
where applicable]). You can withdraw it at any time with the same switch.

**Pseudonym.** Events are linked to a random identifier created when you
switch analytics on (and replaced [every N days] and whenever you switch it
off and on again). Our analytics provider never receives the link to your
account. We keep that link only to honour withdrawal and deletion, so the data
is pseudonymous, not anonymous.

**Recipient and transfer.** [PostHog contracting entity], our analytics
processor, hosts the data in its EU region (European Union). For users in
Türkiye this is a transfer abroad under KVKK Art. 9. It is made under [the
standard contract announced by the Board, notified to the Authority]. Events
reach the provider only from our servers.

**Retention.**

* Undelivered events: at most [48 hours] on our servers.
* Event data at the provider: at most [90 days] (enforced by replacing the
  pseudonym and deleting the old one).
* Aggregated statistics that cannot identify anyone: up to [12 months].

**Withdrawal and deletion.** When you switch analytics off, the app stops at
once and our servers delete events not yet delivered. We ask the provider to
delete the events already delivered under your pseudonym. The provider
processes deletions in scheduled batches, usually within [N] days. We cannot
recall data that has already been aggregated anonymously. Deleting your
account triggers the same deletion.

**Your rights.** You have the rights in KVKK Art. 11 [and GDPR Art. 15–21],
including to learn whether your data is processed, to request correction or
deletion, and to learn about transfers. Contact: [privacy contact].

## EN — Variant L (only if counsel approves legitimate interest)

### Product analytics

**Core usage statistics.** To operate and improve AxisAI, we record a small
set of events about how the app's core features work:

* onboarding completed
* a training plan was proposed or created
* a Coach reply was delivered
* a Coach change to your training plan was proposed, confirmed or applied
* a plan generation failed

Each event has its time and a fixed category. Nothing else is recorded. Our
legal basis is our legitimate interest in running and improving the app (KVKK
Art. 5(2)(f)), within the limits below. **You can switch this off at any time
in Settings → Privacy & data. We then stop and delete as described below.**

**Optional activity insights.** Events about workouts, weekly check-ins,
Progress and Nutrition are collected **only if you switch on** *Activity
insights* in Settings → Privacy & data. The basis is your explicit consent,
which you can withdraw at any time.

[The "What we never include", Pseudonym, Recipient and transfer, Retention,
Withdrawal and deletion, and Your rights paragraphs are as in Variant C.]

---

## TR — Varyant C (açık rıza; mevcut kaynak)

### Ürün analitiği (isteğe bağlı)

**Ne ve neden.** Ayarlar → Gizlilik ve veriler bölümünde *Ürün analitiği*
seçeneğini açarsanız, uygulama özelliklerinin nasıl kullanıldığını kaydederiz.
Böylece kayıt sürecinin nerede zorlaştığını, planların oluşturulup
oluşturulmadığını, Koç'un çalışıp çalışmadığını ve özelliklerin hata verip
vermediğini anlayabiliriz. Analitik şunlardan oluşur:

* "kayıt tamamlandı", "antrenman planı oluşturuldu", "antrenman tamamlandı",
  "Koç yanıtı iletildi", "haftalık değerlendirme gönderildi" veya "bir ekran
  açıldı" gibi olaylar
* her olayın zamanı
* planın hangi yolla oluşturulduğu gibi birkaç sabit kategori

Adınız, e-posta adresiniz, hesap kimliğiniz, vücut ölçüleriniz, fotoğraflarınız,
öğünleriniz veya beslenme içerikleriniz, Koç'a yazdıklarınız veya Koç'un
yanıtları, değerlendirme cevaplarınız, serbest metin, cihaz, işletim sistemi,
konum veya IP adresiniz **hiçbir zaman** dahil edilmez.

**Varsayılan olarak kapalı.** Siz açmadıkça hiçbir veri toplanmaz. Bu seçenek
Kullanım Koşulları'nın kabulünden, sağlık verisi rızasından ve yapay zekâ
özelliklerinden ayrıdır. Reddetmeniz uygulamada başka hiçbir şeyi değiştirmez.

**Hukuki sebep.** Açık rızanız (KVKK m. 5/1; [uygulanabildiği ölçüde GDPR m.
6/1-a]). Rızanızı aynı seçenekle dilediğiniz zaman geri alabilirsiniz.

**Takma kimlik.** Olaylar, analitiği açtığınızda oluşturulan rastgele bir
kimlikle ilişkilendirilir. Bu kimlik [her N günde bir] ve analitiği kapatıp
yeniden açtığınızda yenilenir. Analitik hizmet sağlayıcımız bu kimliğin
hesabınızla bağlantısını hiçbir zaman almaz. Bu bağlantıyı yalnızca rızanın
geri alınması ve silme taleplerini yerine getirmek için saklarız. Bu nedenle
veriler anonim değil, takma adlandırılmış (pseudonim) verilerdir.

**Alıcı ve yurt dışına aktarım.** Analitik veri işleyenimiz [PostHog sözleşme
tarafı], verileri Avrupa Birliği'ndeki bölgesinde barındırır. Türkiye'deki
kullanıcılar için bu, KVKK m. 9 kapsamında yurt dışına aktarımdır ve [Kurul
tarafından ilan edilen ve Kuruma bildirilen standart sözleşme] ile yapılır.
Olaylar hizmet sağlayıcıya yalnızca sunucularımızdan iletilir.

**Saklama süresi.**

* İletilmemiş olaylar: sunucularımızda en fazla [48 saat].
* Hizmet sağlayıcıdaki olay verileri: en fazla [90 gün] (takma kimlik
  yenilenerek ve eskisi sildirilerek uygulanır).
* Kimseyi tanımlamaya imkân vermeyen toplu istatistikler: en fazla [12 ay].

**Rızanın geri alınması ve silme.** Analitiği kapattığınızda uygulama
toplamayı derhal durdurur ve sunucularımız henüz iletilmemiş olayları siler.
Takma kimliğinizle daha önce iletilmiş olayların silinmesini hizmet
sağlayıcıdan talep ederiz. Hizmet sağlayıcı silme işlemlerini planlı toplu
işlemlerle, genellikle [N] gün içinde gerçekleştirir. Anonim olarak
toplulaştırılmış veriler geri çağrılamaz. Hesabınızı silmeniz de aynı silme
işlemini başlatır.

**Haklarınız.** KVKK m. 11 [ve GDPR m. 15–21] kapsamındaki haklara
sahipsiniz. Bunlar arasında verilerinizin işlenip işlenmediğini öğrenme,
düzeltilmesini veya silinmesini isteme ve aktarıldığı üçüncü kişileri bilme
hakları vardır. İletişim: [gizlilik iletişim adresi].

## TR — Varyant L (yalnızca hukuk danışmanı meşru menfaati onaylarsa)

### Ürün analitiği

**Temel kullanım istatistikleri.** AxisAI'ı işletmek ve geliştirmek için temel
özelliklerin nasıl çalıştığına dair sınırlı sayıda olayı kaydederiz:

* kayıt tamamlandı
* bir antrenman planı önerildi veya oluşturuldu
* bir Koç yanıtı iletildi
* Koç'un antrenman planınızda önerdiği bir değişiklik önerildi, onaylandı veya
  uygulandı
* plan oluşturma başarısız oldu

Her olay yalnızca zamanını ve sabit bir kategoriyi içerir, başka hiçbir şey
kaydedilmez. Hukuki sebebimiz, aşağıdaki sınırlar içinde uygulamayı işletme ve
geliştirmeye yönelik meşru menfaatimizdir (KVKK m. 5/2-f). **Bunu Ayarlar →
Gizlilik ve veriler bölümünden dilediğiniz zaman kapatabilirsiniz. Bu durumda
toplamayı durdurur ve aşağıda açıklandığı şekilde sileriz.**

**İsteğe bağlı aktivite içgörüleri.** Antrenman, haftalık değerlendirme,
İlerleme ve Beslenme ile ilgili olaylar, yalnızca Ayarlar → Gizlilik ve
veriler bölümünde *Aktivite içgörüleri* seçeneğini **siz açarsanız** toplanır.
Hukuki sebep, dilediğiniz zaman geri alabileceğiniz açık rızanızdır.

[Varyant C'deki "hiçbir zaman dahil edilmez", Takma kimlik, Alıcı ve yurt dışına
aktarım, Saklama süresi, Geri alma ve silme ve Haklarınız paragrafları aynen
geçerlidir.]
