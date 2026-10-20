# 07 — Test Tezgahı: GRBL Ayar Analizi

Kaynak: `docs/board-dumps/grbl-eeprom-2026-08-03.json` (gSender Firmware Tool export, 2026-08-03)  
Uygulama ayarları: `docs/board-dumps/gsender-app-settings-2026-08-03.json`

Durum: **VERIFIED** — makinenin gerçek EEPROM'undan okundu, türetilmiş değil.

---

## Özet: 34 ayarın sadece 4'ü değiştirilmiş

EEPROM dökümü fabrika GRBL 1.1 varsayılanlarıyla karşılaştırıldığında:

| Ayar | Fabrika | Gerçek | Anlamı |
|---|---|---|---|
| `$22` | 0 | **1** | Homing açılmış → switch'ler var ve kullanılıyor |
| `$100` | 250.000 | **833.300** | X steps/mm kalibre edilmiş |
| `$101` | 250.000 | **823.500** | Y steps/mm kalibre edilmiş |
| `$102` | 250.000 | **1250.000** | Z steps/mm kalibre edilmiş |

**Diğer 30 ayarın hepsi fabrika değeri.** Bu, aşağıdaki üç sonucu doğuruyor.

### 1. Firmware GRBL 1.1

Varsayılan seti birebir GRBL 1.1'in kendisi. Özellikle `$10 = 1` belirleyici —
GRBL 0.9'da bu değerin varsayılanı 3'tü. grblHAL de değil: gSender'ın uygulama
ayarlarında `widgets/connection/controller/type = "Grbl"` yazıyor (dosyanın
başındaki `defaultFirmware = grblHAL` sadece uygulamanın tercih varsayılanı,
bağlantıda kullanılan değer değil).

Bağlantı: `COM3`, 115200 baud, seri.

### 2. Hız, ivme ve strok değerleri MAKİNE HAKKINDA HİÇBİR ŞEY SÖYLEMİYOR

`$110-112 = 500 mm/dk`, `$120-122 = 10 mm/sn²`, `$130-132 = 200 mm` —
üçü de dokunulmamış fabrika değeri. Yani:

- Makine hiç hız/ivme ayarı görmemiş. 500 mm/dk (8.3 mm/sn) bir router için
  aşırı yavaş, 10 mm/sn² ivme sürünme hızında.
- **200 mm strok değeri doğrulanmamıştır.** GRBL'in varsayılanı da 200. Üstelik
  `$20 = 0` (yumuşak limitler kapalı) olduğu için GRBL bu sayıyı hiç
  kullanmamış — yanlış olsa bile kimse fark etmezdi.
- gSender profilindeki 200×200×200 mm ölçüsü de aynı sayının yankısı, bağımsız
  bir doğrulama değil.

**Yapılacak: gerçek strok elle ölçülecek.** Her ekseni uçtan uca sür, mesafeyi
kaydet.

### 3. Makinenin şu anda hiçbir çarpışma koruması yok

| | Değer | Anlam |
|---|---|---|
| `$20` | 0 | Yumuşak limit **kapalı** |
| `$21` | 0 | Donanımsal limit **kapalı** |
| `$22` | 1 | Homing **açık** |

Switch'ler sadece homing sırasında okunuyor, çalışma sırasında değil. Yani
jog ya da program sırasında eksen sonuna dayanırsa hiçbir şey durdurmuyor.
LinuxCNC'de bunu düzelteceğiz — yumuşak limitler bedava, `$21`'in karşılığı da switch'leri HAL'de
`joint.N.neg-lim-sw-in` / `joint.N.pos-lim-sw-in` pinlerine bağlayarak kurulabilir ([06](06-grbl-to-linuxcnc.md) "Conceptual differences" §2; henüz bağlı değil — [PLAN-BUGUN.md](PLAN-BUGUN.md) Faz 5).

---

## ⚠ gSender'daki "LongMill MK2 30x30" profili yanıltıcı

Uygulama ayarlarında seçili profil Sienci Labs LongMill MK2. Gerçek EEPROM ile
karşılaştırınca hiçbir alanı tutmuyor:

| | Gerçek makine | LongMill MK2 profili |
|---|---:|---:|
| `$100` X steps/mm | 833.300 | 200.000 |
| `$101` Y steps/mm | 823.500 | 200.000 |
| `$102` Z steps/mm | 1250.000 | 200.000 |
| `$110` X max hız | 500 | 4000 |
| `$120` X ivme | 10 | 750 |
| `$130/131/132` strok | 200 / 200 / 200 | 810 / 855 / 120 |
| `$2` step invert | 0 | 1 |
| `$3` dir invert | 0 | 1 |
| `$23` homing yön | 0 | 3 |
| `$30` max spindle | 1000 | 30000 |

Profil sadece gSender'ın arayüzünde seçili duran bir şablon; makineye hiç
uygulanmamış. Ayrıca profilin kendi içinde de tutarsız: `endstops = False`
diyor ama EEPROM'da `$22 = 1`, yani homing açık ve switch'ler var.

**Sonuç: profili tamamen yok say. Tek geçerli kaynak EEPROM dökümü.**

---

## LinuxCNC karşılıkları

Birim dönüşümleri yapılmış hâliyle. `docs/06-grbl-to-linuxcnc.md`'deki
kurallara göre.

### Eksen ölçekleri — GÜVENİLİR

```ini
[JOINT_0]  SCALE = 833.300     # $100, X
[JOINT_1]  SCALE = 823.500     # $101, Y
[JOINT_2]  SCALE = 1250.000    # $102, Z
```

Bu üç sayı ampirik olarak kalibre edilmiş. Kanıt: X ve Y birbirine çok yakın
ama **eşit değil** (833.3 / 823.5, %1.2 fark). Nominal olarak aynı mekaniğe
sahip iki eksen ancak ölçülerek ayrı ayrı ayarlanmışsa böyle çıkar. Yine de
LinuxCNC'ye geçtikten sonra komparatörle doğrulanacak.

> **2026-10-18:** "GÜVENİLİR" GRBL + eski sürücü için geçerliydi. Sürücüler artık TB6560 ve
> mikro adım aynı olmayabilir (T5) — bu kurulumda `SCALE` henüz doğrulanmadı
> ([PLAN-BUGUN.md](PLAN-BUGUN.md) Faz 3, [DURUM.md:88](DURUM.md)).

`$3 = 0` → yön ters çevirme yok → `SCALE` işaretleri pozitif kalır.

### Hız ve ivme — GÜVENİLMEZ, sadece başlangıç değeri

```ini
MAX_VELOCITY     = 8.333    # $110 = 500 mm/dk ÷ 60. FABRİKA VARSAYILANI
MAX_ACCELERATION = 10.0     # $120 = 10 mm/sn². FABRİKA VARSAYILANI
```

İlk çalıştırma için **iyi bir şey**: makine sürünerek hareket eder, bir şey
ters giderse hasar vermez. Ama gerçek kapasiteyi yansıtmıyor, sonra
yükselteceğiz.

### Homing

```ini
HOME_SEARCH_VEL = 8.333     # $25 = 500 mm/dk ÷ 60, pozitif ($23 = 0)
HOME_LATCH_VEL  = 0.417     # $24 = 25 mm/dk ÷ 60
```

`$23 = 0` → üç eksen de **pozitif** yönde home ediyor. Bu sıra dışı — çoğu
makinede Z pozitif (yukarı) ama X/Y negatif yönde home eder. Fiziksel olarak
doğrulanmalı: switch'ler hangi uçta? (2026-10-18: X ve Z için elle doğrulandı, Y'nin + ucu açık — aşağıda T2)

`$27 = 1.000` mm pull-off → LinuxCNC'de `HOME_OFFSET` mantığıyla karşılanır.

### Step zamanlaması

```ini
[JOINT_n]
STEPLEN   = 10000    # $0 = 10 µs × 1000
```

`$0 = 10 µs` de fabrika varsayılanı, sürücüye göre ayarlanmamış. (Bu satır sürücü modeli
bilinmeden yazılmıştı: A4988 minimum 1 µs, DRV8825 minimum 1.9 µs ister diye 5-10 kat fazla,
güvenli ama savurgan sayılmıştı.) **2026-10-18:** test tezgahının sürücüleri **TB6560**; LinuxCNC
başlangıç değerleri (`STEPLEN` yine 10 µs) ve gerekçesi [09-tb6560-drivers.md](09-tb6560-drivers.md)'de
— INFERRED, ölçülmedi.

**İleriye dönük önemli** (2026-10-18'de TB6560'a göre düzeltildi): `steplen + stepspace = 20 µs`
teorik 50 kHz step hızı demek (Z'de 1250 steps/mm ile 2400 mm/dk). Ama ilk darboğaz bu değil:
TB6560'ın STEP optokuplörü üretici rakamıyla **15 kHz** ile sınırlı; %20 payla mevcut ölçekte
Z ~576, X ~864 mm/dk ([09-tb6560-drivers.md](09-tb6560-drivers.md), [DURUM.md:99](DURUM.md)).
Hız için kaldıraç `steplen` değil, mikro adımı düşürmek. 15 kHz rakamı bağımsız doğrulanmadı
(docs/09 T7).

---

## ⚠ Termal uyarı — LinuxCNC'ye geçişte davranış değişiyor

`$1 = 25` (step idle delay, ms). GRBL hareket bittikten 25 ms sonra motorların
enerjisini kesiyor. Yani motorlar zamanın çoğunda **enerjisiz** duruyor.

LinuxCNC varsayılan olarak böyle çalışmaz — motorlar sürekli enerjili kalır.
Sonuç:

- Motorlar ve TB6560 sürücü kartları **belirgin şekilde daha çok ısınacak**
- TB6560'ta akım DIP switch'le kademeli seçiliyor ([09-tb6560-drivers.md](09-tb6560-drivers.md)
  "Akım ayarı"). Kademe yüksek seçildiyse sürekli enerjide termal koruma devreye girip
  sürücü kendini kapatabilir — bu adım kaybı olarak görünür
- İlk uzun testte sürücü kartlarının ve motorların sıcaklığını elle kontrol et. Dokunulamayacak
  kadar sıcaksa akım kademesini düşür ya da fan ekle

Bu, ayrıca mermer makinesinde de sorulacak bir soru: eski kontrolcü motorları
duruşta enerjisiz bırakıyor muydu?

---

## Açık kalan sorular

| # | Soru | Nasıl cevaplanır |
|---|---|---|
| T1 | Gerçek strok ne kadar? | Her ekseni uçtan uca sür, ölç. `$130-132` fabrika değeri, güvenilmez |
| T2 🟡 | Switch'ler hangi uçta? | **Kısmen kapandı** — elle doğrulandı ([DURUM.md:69-82](DURUM.md)): `gpio.020` X sağ (X+ = sağ → maksimum uç), `gpio.021` X sol, `gpio.024` Z üst, Z altta switch yok. `gpio.022` Y arka / `gpio.023` Y ön; hangisinin + uç olduğu Y DIR arızası çözülünce belli olacak. Home yönleri INI'de hâlâ `ASSUMPTION` (GRBL'de `$23 = 0`: üç eksen + yönde home ediyordu — yukarıdaki Homing notu) |
| T3 ✅ | Kaç switch var, nasıl bağlı? | **Kapandı** — 5 switch, Zhulong IO 20–24'e bağlı. Boşta `.in = TRUE`, basınca FALSE → HAL'de `.in_not` kullanılacak ([DURUM.md:71](DURUM.md)). Bu ve `$5 = 0` NO switch olduğunu destekliyor, kanıtlamıyor |
| T4 ✅ | Sürücü modeli? | **TB6560** (tezgahta şu an çalışan sürücüler; shield'in eski modülleri kayıtlı değil) — kapandı ([09-tb6560-drivers.md](09-tb6560-drivers.md)) |
| T5 | Mikro adım jumper konumu? | Sürücüler artık TB6560 ([09](09-tb6560-drivers.md)). `$100-102` eski sürücünün ayarına göre kalibre edildi; TB6560'ın mikro adımı aynı değilse ölçek bozulur. Doğrulama: [PLAN-BUGUN.md](PLAN-BUGUN.md) Faz 3 (ölçek ölçümü). Bkz. docs/09 T5 |
| T6 | Motorlar sürekli enerjide ne kadar ısınıyor? | İlk uzun testte elle kontrol |

---

## Ham veri

Değişmemiş orijinal export'lar `docs/board-dumps/` altında:

- `grbl-eeprom-2026-08-03.json` — 34 GRBL ayarı
- `gsender-app-settings-2026-08-03.json` — gSender uygulama yapılandırması,
  içinde yanıltıcı LongMill profili
