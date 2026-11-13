# 07 — Test Tezgahı: GRBL Ayar Analizi

Kaynak: `docs/board-dumps/grbl-eeprom-2026-08-03.json` (gSender Firmware Tool export, 2026-08-03)  
Uygulama ayarları: `docs/board-dumps/gsender-app-settings-2026-08-03.json`

Durum: **VERIFIED** — makinenin gerçek EEPROM'undan okundu, türetilmiş değil. (Doğrulanan, dökümün kendisi: içindeki `$100-102` ölçekleri bu tezgahın mekaniğine uymadı — 2026-11-12, aşağıda "Eksen ölçekleri".)

---

## Özet: 34 ayarın sadece 4'ü değiştirilmiş

EEPROM dökümü fabrika GRBL 1.1 varsayılanlarıyla karşılaştırıldığında:

| Ayar | Fabrika | Gerçek | Anlamı |
|---|---|---|---|
| `$22` | 0 | **1** | Homing açılmış → switch'ler var ve kullanılıyor |
| `$100` | 250.000 | **833.300** | X steps/mm değiştirilmiş — ama bu mekanik için kalibre değil (LinuxCNC'de ölçülen 100.085; ~8.33×, 2026-11-12) |
| `$101` | 250.000 | **823.500** | Y steps/mm değiştirilmiş — kalibre değil (ölçülen 98.592; ~8.35×) |
| `$102` | 250.000 | **1250.000** | Z steps/mm değiştirilmiş — kalibre değil (ölçülen 122.274; ~10.22×) |

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
kaydet. (2026-11-12: ölçüldü — switch'ten switch'e X 403.4, Y 391.3 mm; Z üst switch'ten tablaya ~100 mm. GRBL'in 200 mm'si hiçbir eksene uymuyor.)

### 3. GRBL altında makinenin hiçbir çarpışma koruması yoktu (LinuxCNC'de 2026-11-12'den beri var: switch limitleri + yumuşak limitler)

| | Değer | Anlam |
|---|---|---|
| `$20` | 0 | Yumuşak limit **kapalı** |
| `$21` | 0 | Donanımsal limit **kapalı** |
| `$22` | 1 | Homing **açık** |

Switch'ler sadece homing sırasında okunuyor, çalışma sırasında değil. Yani
jog ya da program sırasında eksen sonuna dayanırsa hiçbir şey durdurmuyor.
LinuxCNC'de bunu düzelteceğiz — yumuşak limitler bedava, `$21`'in karşılığı da switch'leri HAL'de
`joint.N.neg-lim-sw-in` / `joint.N.pos-lim-sw-in` pinlerine bağlayarak kurulabilir ([06](06-grbl-to-linuxcnc.md) "Conceptual differences" §2; 2026-11-12'den beri bağlı ve doğrulandı — Faz 5, `configs/test-rig/test-rig.hal:301-327`).

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

### Eksen ölçekleri — ~~GÜVENİLİR~~ YANLIŞ ÇIKTI (2026-11-12)

```ini
[JOINT_0]  SCALE = 833.300     # $100, X — LinuxCNC'de ölçülen: 100.085
[JOINT_1]  SCALE = 823.500     # $101, Y — ölçülen: 98.592
[JOINT_2]  SCALE = 1250.000    # $102, Z — ölçülen: 122.274
```

~~Bu üç sayı ampirik olarak kalibre edilmiş. Kanıt: X ve Y birbirine çok yakın
ama **eşit değil** (833.3 / 823.5, %1.2 fark). Nominal olarak aynı mekaniğe
sahip iki eksen ancak ölçülerek ayrı ayrı ayarlanmışsa böyle çıkar.~~ Bu çıkarım yanlıştı:
LinuxCNC'ye geçtikten sonra ölçüldü ve üç değer de bu mekanik için çok yüksek çıktı (aşağıdaki not).

> **2026-10-18:** "GÜVENİLİR" GRBL + eski sürücü için geçerli sanılıyordu. Sürücüler artık TB6560 ve
> mikro adım aynı olmayabilir (T5) — bu kurulumda `SCALE` o gün henüz doğrulanmamıştı.
> **2026-11-12 (Faz 3, [DURUM.md:88](DURUM.md)):** ölçüldü — X **100.085**, Y **98.592**, Z **122.274** adım/mm. GRBL'deki değerler X ve Y'de ~8.3×, Z'de ~10.2× yüksekti; bu mekanik için hiç kalibre edilmemişlerdi. Döküm ve yukarıdaki analiz tarihçe olarak duruyor.

`$3 = 0` → yön ters çevirme yok → `SCALE` işaretleri pozitif kalır.

### Hız ve ivme — GÜVENİLMEZ, sadece başlangıç değeri

```ini
MAX_VELOCITY     = 8.333    # $110 = 500 mm/dk ÷ 60. FABRİKA VARSAYILANI
MAX_ACCELERATION = 10.0     # $120 = 10 mm/sn². FABRİKA VARSAYILANI
```

İlk çalıştırma için **iyi bir şey**: makine sürünerek hareket eder, bir şey
ters giderse hasar vermez. Ama gerçek kapasiteyi yansıtmıyor, sonra
yükselteceğiz. (2026-11-12: INI'de X/Y 10, Z 5 mm/s ve 50 mm/s² — gerekçe [DURUM.md](DURUM.md)'nin sonundaki tarihli bölümde.)

### Homing

```ini
HOME_SEARCH_VEL = 8.333     # $25 = 500 mm/dk ÷ 60, pozitif ($23 = 0)
HOME_LATCH_VEL  = 0.417     # $24 = 25 mm/dk ÷ 60
```

`$23 = 0` → üç eksen de **pozitif** yönde home ediyor. Bu sıra dışı — çoğu
makinede Z pozitif (yukarı) ama X/Y negatif yönde home eder. Fiziksel olarak
doğrulanmalı: switch'ler hangi uçta? (2026-11-12: LinuxCNC'de X ve Y − yönde — sol ve ön switch'e —, Z + yönde — üst switch'e — home ediyor, `HOME_SEARCH_VEL` −5 / −5 / +4 — aşağıda T2)

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
— INFERRED, scope ile ölçülmedi; 2026-11-12'de bu değerlerle Faz 1–8 geçti.

**İleriye dönük önemli** (2026-10-18'de TB6560'a göre düzeltildi, 2026-11-12'de ölçülen ölçekle yeniden hesaplandı): `steplen + stepspace = 20 µs`
teorik 50 kHz step hızı demek (Z'de 122.274 steps/mm ile ~24 500 mm/dk). Ama ilk darboğaz bu değil:
TB6560'ın STEP optokuplörü üretici rakamıyla **15 kHz** ile sınırlı; %20 payla ölçülen ölçekte
Z ~5890, X ~7190 mm/dk ([09-tb6560-drivers.md](09-tb6560-drivers.md), [DURUM.md:99](DURUM.md)) — ayarlı en yüksek hızların (X 10, Z 5 mm/s ≈ 1.0 / 0.61 kHz) çok üstünde.
Eski ölçekle (833.3 / 1250) hesaplanan 864 / 576 mm/dk yanlıştı. Tavan şimdilik bağlamıyor; 15 kHz rakamı bağımsız doğrulanmadı
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
| T1 ✅ | Gerçek strok ne kadar? | **Kapandı (2026-11-12, Faz 4)** — switch'ten switch'e X 403.4, Y 391.3 mm; Z üst switch'ten tablaya ~100 mm. Yumuşak limitler X 3..399, Y 3..387, Z −96.5..−3. `$130-132` (200) fabrika değeriydi, hiçbir eksene uymuyor |
| T2 ✅ | Switch'ler hangi uçta? | **Kapandı (2026-11-12)** — elle doğrulandı ([DURUM.md:69-82](DURUM.md)): `gpio.020` X sağ (X+ = sağ → maksimum uç), `gpio.021` X sol, `gpio.022` Y arka (Y+ = arka → maksimum uç), `gpio.023` Y ön, `gpio.024` Z üst, Z altta switch yok. HAL'de bağlandı (Faz 5); home yönleri X/Y − (sol/ön), Z + (üst), homing tekrarlanabilir (Faz 6). (GRBL'de `$23 = 0`: üç eksen + yönde home ediyordu — yukarıdaki Homing notu) |
| T3 ✅ | Kaç switch var, nasıl bağlı? | **Kapandı** — 5 switch, Zhulong IO 20–24'e bağlı. Boşta `.in = TRUE`, basınca FALSE → HAL'de `.in_not` kullanılıyor, Faz 5'ten beri ([DURUM.md:71](DURUM.md)). Bu ve `$5 = 0` NO switch olduğunu destekliyor, kanıtlamıyor |
| T4 ✅ | Sürücü modeli? | **TB6560** (tezgahta şu an çalışan sürücüler; shield'in eski modülleri kayıtlı değil) — kapandı ([09-tb6560-drivers.md](09-tb6560-drivers.md)) |
| T5 🔵 | Mikro adım jumper konumu? | **Ölçek için artık gerekmiyor** — SCALE Faz 3'te doğrudan ölçüldü (2026-11-12: X 100.085, Y 98.592, Z 122.274). ~~`$100-102` eski sürücünün ayarına göre kalibre edildi~~ — bu mekanik için kalibre değildi (~8.3× / 10.2×). Jumper konumu hâlâ okunmadı. Bkz. docs/09 T5 |
| T6 | Motorlar sürekli enerjide ne kadar ısınıyor? | İlk uzun testte elle kontrol |

---

## Ham veri

Değişmemiş orijinal export'lar `docs/board-dumps/` altında:

- `grbl-eeprom-2026-08-03.json` — 34 GRBL ayarı
- `gsender-app-settings-2026-08-03.json` — gSender uygulama yapılandırması,
  içinde yanıltıcı LongMill profili
