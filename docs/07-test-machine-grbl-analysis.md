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
LinuxCNC'de bunu düzelteceğiz — yumuşak limitler bedava, `$21`'in karşılığı da
`MIN_LIMIT_SWITCH` / `MAX_LIMIT_SWITCH` bağlantılarıyla kurulabilir.

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
doğrulanmalı: switch'ler hangi uçta?

`$27 = 1.000` mm pull-off → LinuxCNC'de `HOME_OFFSET` mantığıyla karşılanır.

### Step zamanlaması

```ini
[JOINT_n]
STEPLEN   = 10000    # $0 = 10 µs × 1000
```

`$0 = 10 µs` de fabrika varsayılanı, sürücüye göre ayarlanmamış. A4988 minimum
1 µs, DRV8825 minimum 1.9 µs ister — yani 5-10 kat fazla, güvenli ama savurgan.

**İleriye dönük önemli:** `steplen + stepspace = 20 µs` maksimum 50 kHz step
hızı demek. Z'de 1250 steps/mm ile bu **40 mm/sn = 2400 mm/dk** tavanı koyuyor.
Şu anki 500 mm/dk'da sorun değil ama makineyi hızlandırmak istersek ilk
darboğaz burası olur. `steplen`'i 2-3 µs'ye indirmek tavanı 4 katına çıkarır.

---

## ⚠ Termal uyarı — LinuxCNC'ye geçişte davranış değişiyor

`$1 = 25` (step idle delay, ms). GRBL hareket bittikten 25 ms sonra motorların
enerjisini kesiyor. Yani motorlar zamanın çoğunda **enerjisiz** duruyor.

LinuxCNC varsayılan olarak böyle çalışmaz — motorlar sürekli enerjili kalır.
Sonuç:

- Motorlar ve A4988/DRV8825 modülleri **belirgin şekilde daha çok ısınacak**
- Sürücülerin akım limiti potansiyometreleri bu görev döngüsü varsayılarak
  ayarlanmış olabilir. Sürekli enerjide termal koruma devreye girip
  sürücü kendini kapatabilir — bu adım kaybı olarak görünür
- İlk uzun testte modüllerin sıcaklığını elle kontrol et. Dokunulamayacak
  kadar sıcaksa akım limitini düşür ya da fan ekle

Bu, ayrıca mermer makinesinde de sorulacak bir soru: eski kontrolcü motorları
duruşta enerjisiz bırakıyor muydu?

---

## Açık kalan sorular

| # | Soru | Nasıl cevaplanır |
|---|---|---|
| T1 | Gerçek strok ne kadar? | Her ekseni uçtan uca sür, ölç. `$130-132` fabrika değeri, güvenilmez |
| T2 | Switch'ler hangi uçta? | `$23 = 0` üç eksenin de pozitif yönde home ettiğini söylüyor, fiziksel doğrulama gerek |
| T3 | Kaç switch var, nasıl bağlı? | Shield'in limit header'larına bak. `$5 = 0` → NO switch, GND'ye kapanıyor |
| T4 | Sürücü modeli? | Modül üstündeki çipte yazıyor. `steplen`'i düşürebilmek için gerekli |
| T5 | Mikro adım jumper konumu? | Shield'de jumper'lara bak. `$100-102` bu konuma bağlı — modül sökülürse bozulur |
| T6 | Motorlar sürekli enerjide ne kadar ısınıyor? | İlk uzun testte elle kontrol |

---

## Ham veri

Değişmemiş orijinal export'lar `docs/board-dumps/` altında:

- `grbl-eeprom-2026-08-03.json` — 34 GRBL ayarı
- `gsender-app-settings-2026-08-03.json` — gSender uygulama yapılandırması,
  içinde yanıltıcı LongMill profili
