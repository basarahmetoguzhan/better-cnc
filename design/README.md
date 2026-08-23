# design/ — Arayüz Prototipi

Bu dizin Claude Design'ın ürettiği prototipi barındırıyor. **Referans, hedef değil.**

| Dosya | Ne |
|---|---|
| `prototype-source.jsx` | **Bunu oku.** Prototipin çıkarılmış kaynağı — tüm veri yapıları, state, ekran mantığı |
| `prototype-bundled.html` | Orijinal teslim. Kendi kendine açılan, gzip'li base64 varlıklarla paketlenmiş tek dosya. Tarayıcıda açılır |

`prototype-source.jsx` nasıl çıkarıldı: bundled HTML'in içinde
`<script type="__bundler/template">` var, onun içinde de
`<script type="text/x-dc">` etiketi. HTML-unescape edilince kaynak çıkıyor.
Elle tekrar çıkarmaya gerek yok, iş yapıldı.

---

## Prototipi neden birebir uygulamıyoruz

Prototip düzen ve görsel dil için iyi. Ama iki tür problemi var:

**Uydurma sayılar.** Giriş haritası, eksen ölçekleri, step zamanlaması, latency,
çekirdek mimarisi — hepsi gerçek donanımla çelişiyor. Doğrulanmış değerler
`docs/DURUM.md` ve `configs/test-rig/` içinde.

**Eksik kavramlar.** Acil stopun donanım/yazılım ayrımı, referans alma durumunun
bir kapı olması, paket kaybı ve watchdog göstergeleri, "yumuşak limit tanımsız"
durumu, MDI metin girişi.

Tam liste ve düzeltmeler: `docs/11-ui-implementation.md`

---

## Korunacaklar

- 6 ekranlı yapı: Kontrol / Program / Probing / Takımlar / Alarmlar / Makine
- Alarm kaydında kaynak atfı (`gpio.0NN`, `hm2_eth`, `homing`, `yorumlayıcı`)
- Probing'in adım adım akışı
- Koyu tema, Türkçe dil
- **Kart bilgisi paneli doğru** — Zhulong V2.0 (Mesa 7i92), `hm2_7i92.0`,
  Spartan-6 XC6SLX9, 34 IO (2×17), 100 MHz, `hm2_eth` 10.10.10.10

---

## Tasarım kararı

Arayüz **mermer makinesi** için tasarlanıyor. Test tezgahı onun **alt kümesi.**
Test tezgahında olmayan yetenekler **gizlenmeyecek, sebebiyle birlikte devre dışı
görünecek** — gizlenirse mermer makinesine geçerken unutulur.

Detay: `docs/MERMER-GECIS.md`
