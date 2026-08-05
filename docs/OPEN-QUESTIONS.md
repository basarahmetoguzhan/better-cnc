# Açık Sorular / Blocker Kaydı

Bu dosya, cevaplanmadan ilerlenemeyecek soruların kaydıdır. Her soru
cevaplandığında **buraya cevabı ve tarihi yazılır** — silinmez, üstü çizilir.
Karar geçmişini burada tutuyoruz.

Son güncelleme: 2026-08-05

---

## 🟡 B1 — Y ekseninde kaç motor var?

**Durum:** AÇIK — 2026-08-05'te 🔴'dan 🟡'ya düşürüldü (B2 kapandı, giriş kısıtı yok)  
**Kim cevaplar:** Oğuzhan, panoyu açıp sayarak  
**Neyi bloke ediyor:** Faz 5 (ilk config), Faz 6 (kablolama), Faz 7 (güvenlik zinciri)

Bu tek sayı giriş bütçesini belirliyor:

| | Home | E-stop | Takım sensörü | Toplam | Kartta var |
|---|---:|---:|---:|---:|---:|
| Tek motorlu Y | 3 | 1 | 1 | **5** | 5 ✅ sınırda |
| Çift motorlu Y (portal) | 4 | 1 | 1 | **6** | 5 ❌ **sığmıyor** |

**Çift Y çıkarsa ne değişir:**

- LinuxCNC tarafı sorun değil. `trivkins coordinates=XYYZ` ile 4 joint tanımlanır
  (joint 0 = X, 1 = Y sol, 2 = Y sağ, 3 = Z), 3 eksene eşlenir.
  Portal kareleme negatif `HOME_SEQUENCE` ile yapılır — aynı mutlak değere sahip
  joint'ler son hareketi senkron tamamlar
  ([ini-homing.adoc:262-267](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L262-L267),
  [code-notes.adoc:1411](../reference/linuxcnc/docs/src/code/code-notes.adoc#L1411)).
- Zhulong step tarafı da sorun değil: 6 AXIS konnektörü var, 4 lazım.
- **Tek sıkışan yer izole girişler.** Kartta 5 tane, DB25/P1 konnektörü yok
  (bkz. [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)),
  genişleme yalnızca Smart Serial üzerinden.

**⚠ Dikkat:** Tek Y çıksa bile 5/5 doluyuz. Kapı switch'i, probe, spindle arıza
girişi, su debi sensörü için **sıfır boşluk.** B2 her hâlükârda cevaplanmalı.

---

### 🟢 GÜNCELLEME (2026-08-05) — giriş bütçesi kısıtı ORTADAN KALKTI

Yukarıdaki "5 ❌ sığmıyor" satırı **artık geçerli değil.** [B2](#-b2--encoder-pinleri-gpio-olarak-kullanılabiliyor-mu--kapandi-evet)
kapandı: `num_encoders=0` ile IO 25-33 tam GPIO oluyor, yani **5 değil 14 giriş**
var. Çift motorlu portal Y'nin istediği 6 giriş rahatça sığıyor, üstüne kapı
switch'i / probe / debi sensörü için de yer kalıyor.

| | Home | E-stop | Takım | Toplam | Kullanılabilir | Sonuç |
|---|---:|---:|---:|---:|---:|---|
| Tek motorlu Y | 3 | 1 | 1 | 5 | 14 | ✅ 9 boşta |
| Çift motorlu Y | 4 | 1 | 1 | 6 | 14 | ✅ 8 boşta |

Ayrıca kart 6 StepGen sağlıyor ve biz 3 kullanıyoruz, yani dördüncü eksen
(Y-sağ) için StepGen 3, IO 10/11'de firmware değişikliği olmadan hazır
([10-7i92-pinout-verified.md](10-7i92-pinout-verified.md)).

**Yani B1 artık bir tasarım kısıtı değil, sadece bir bilgi eksiği.** Cevabı hâlâ
gerekiyor — kaç joint tanımlayacağımızı, `trivkins coordinates=XYYZ` mi `XYZ` mi
kullanacağımızı ve kaç home switch'i kablolayacağımızı belirliyor — ama artık
"cevap yanlış çıkarsa yeniden tasarım" riski yok. **🔴 → 🟡 düşürülebilir**, yine de
Faz 5 öncesi cevaplanması gereken ilk soru.

Not: kart 7i96 değil 7i92 çıktı, yani toplam IO 51 değil 34. Bu yukarıdaki
sayıları düşürdü ama sonucu değiştirmedi.

**Cevap:** _(bekliyor — panoyu açıp saymak yeterli)_

---

## ✅ B2 — Encoder pinleri GPIO olarak kullanılabiliyor mu? — KAPANDI: **EVET**

**Durum:** ✅ **KAPANDI (2026-08-05)**  
**Cevaplayan:** `mesaflash --device 7i92 --addr 10.10.10.10 --print-pd`  
**Neyi bloke ediyor:** Faz 5, Faz 6  
**İlgili:** [02-board-bringup.md](02-board-bringup.md) §3.4 Q4

Açık çevrim step motorda encoder kullanmıyoruz. Kartta 3 encoder konnektörü var,
yani **9 FPGA pini boşta duruyor.** Bunları GPIO olarak kullanabilirsek giriş
sayısı 5'ten 14'e çıkar ve B1 sorunu kendiliğinden çözülür.

Karar tamamen bitfile'a ait. `hm2_print_pin_usage()` iki ayırt edilebilir form
basıyor ([pins.c:887-913](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L887-L913)):

```
IO Pin 0NN (...): IOPort                          → pin boşta, GPIO ✅
IO Pin 0NN (...): Encoder #0, pin A (Input)       → firmware kilitlemiş ❌
```

**⚠ Kısmi cevap uyarısı:** Bu FPGA seviyesinde bir gerçek. Pinin GPIO'ya
açılması, terminalin arkasındaki **analog ön uç** hakkında hiçbir şey söylemez.
Encoder girişleri diferansiyel alıcı ya da farklı bir seviye devresi
arkasındaysa, 24V endüktif sensörü doğrudan bağlayamayabiliriz. Ohmmetre ile
kontrol şart.

**Cevap (2026-08-05): EVET — 9 pin GPIO olarak kullanılabilir.**

`num_encoders=0` verildiğinde QCount modülünün üç örneği de instantiate edilmiyor
ve **IO 25-33 tam GPIO olarak açılıyor**
([hostmot2.adoc:400-401](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L400-L401)):
"General Purpose I/O pins on the board which are not used by a module instance
are exported to HAL as 'full' GPIO pins."

Pin tanımlayıcıları encoder pinlerinde Secondary Tag = QCount gösteriyor, yani
firmware onları talep *edebiliyor* ama etmek zorunda değil:
[printpd:110-154](board-dumps/printpd-10.10.10.10-2026-08-05.txt).

**Giriş bütçesi — sorun tamamen çözüldü:**

| Kaynak | Pin | Not |
|---|---:|---|
| Kalıcı GPIO (IO 20-24) | 5 | Secondary Tag **hiç yok** — hiçbir config ile kaybedilemez |
| Encoder pinleri (IO 25-33) | 9 | `num_encoders=0` ile serbest |
| Kullanılmayan StepGen 3-5 (IO 10-15) | 6 | `num_stepgens=3` ile serbest |
| PWM (IO 16) | 1 | serbest, ama mermer makinede spindle için gerekecek |
| SSerial (IO 0-3) | 4 | `sserial_port_0=xxxxxxxx` ile serbest |
| **Toplam GPIO** | **28** | 34 pinin 28'i; 6'sı üç stepgen'de |

**B1 (çift Y motoru) artık giriş bütçesi açısından sorun değil.** En kötü senaryo —
çift motorlu portal Y — 4 home + 1 e-stop + 1 takım sensörü = **6 giriş** istiyor.
Elimizde 5 kalıcı + 9 encoder = **14 giriş** var, artı dördüncü eksen için
StepGen 3 (IO 10/11) firmware değişikliği olmadan hazır. Rahatça sığıyor.

Not: kart 7i96 değil **7i92** çıktı (34 IO, 51 değil) — bu, kullanılabilir pin
sayısını düşürdü ama yine de fazlasıyla yetiyor. Bkz.
[10-7i92-pinout-verified.md](10-7i92-pinout-verified.md).

**⚠ AÇIK KALAN KAVEAT — ön uç devresi doğrulanmadı.** Yukarıdaki uyarı aynen
geçerli. Bu FPGA seviyesinde bir gerçek; dökümler pin tanımlayıcılarını gösteriyor,
klemensin arkasındaki devreyi göstermiyor. **24 V sensörü doğrudan bağlamadan önce
ohmmetre ile kontrol et.** Bu B2'yi açık tutmuyor — soru "GPIO olarak
kullanılabilir mi" idi, FPGA tarafında cevap kesin EVET — ama kullanım öncesi
ayrı bir donanım kontrolü gerekiyor. Fiilen B6'nın encoder klemensleri için
tekrarı.

---

## ✅ B3 — AXIS blokları diferansiyel mi, tek uçlu mu? — KAPANDI

**Cevap (2026-08-03, üretici diyagramı):** **Tek uçlu.**
Her AXIS bloğunun 4 terminali: `GND` `STEP` `DIR` `5V`.
Dördüncü terminal diferansiyel çiftin eşi değil, **5V beslemesi** — optokuplörlü
sürücülerin anot ucunu beslemek için konmuş.

Sonuç: ortak anot bağlantı kullanılıyor, `5V → CLK+/CW+`, `STEP → CLK−`,
`DIR → CW−`. Detay: [09-tb6560-drivers.md](09-tb6560-drivers.md).

---

## ✅ B4 — Sürücüler 5V mi 24V sinyal bekliyor? — KAPANDI (test tezgahı için)

**Cevap (2026-08-03):** Test tezgahında sürücüler **TB6560**. Girişleri
optokuplörlü ve Zhulong'un 5V hattıyla doğrudan sürülüyor — seviye çevirici
gerekmiyor.

Detaylı zamanlama, akım ayarı ve 15 kHz hız tavanı:
[09-tb6560-drivers.md](09-tb6560-drivers.md).

> **Mermer makinesi için hâlâ AÇIK.** Oradaki sürücü kutuları farklı; etiket ve
> DIP fotoğrafı bekleniyor ([01-machine-survey.md](01-machine-survey.md) §2).

---

## 🔵 B5 — Bildirilen saat frekansı gerçek mi? — **DÜŞÜRÜLDÜ: blocker değil**

**Durum:** 🔵 **DÜŞÜK ÖNCELİK (2026-08-05'te blocker'dan düşürüldü)** — açık, ama
hiçbir şeyi bloke etmiyor  
**Kim cevaplar:** Osiloskop / mantık analizörü — **yazılımdan tespit edilemez**  
**Neyi bloke ediyor:** ~~Faz 9~~ → **hiçbir şey.** İyi hijyen, gereklilik değil.

`stepgen.c`'de `steplen`, `stepspace`, `dirsetup`, `dirhold` parametrelerinin
dördü de `clock_frequency` ile ölçekleniyor
([stepgen.c:348-385](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L348-L385)).
Bu değer firmware'in IDROM'undan geliyor
([hostmot2.c:780-784](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L780-L784)) —
sürücü onu sorgulamadan kabul ediyor.

Firmware yanlış beyan ederse step darbeleri sessizce yanlış genişlikte üretilir:

```
steplen = 2000 ns, beyan edilen 33.33 MHz  → reg = 66 sayım
gerçek saat 50 MHz ise                     → 66 × 20 ns = 1320 ns
                                              istenenin %66'sı, hata mesajı yok
```

Karttaki 50 MHz kristal **tek başına şüphe sebebi değil** — o FPGA'nın giriş
saati, iç saatler DCM/PLL ile türetiliyor. Mesa'nın kendi 7i96'sı da 50 MHz
osilatörle çalışıp 33.33 MHz ClockLow bildiriyor.

En azından dmesg'in bastığı `clock_frequency: %d Hz` satırı kaydedilmeli
([stepgen.c:1225](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L1225)).
Devreye alma sırasında adım kaybı görülürse ilk şüpheli bu.

---

### Kısmi cevap (2026-08-05) — saat **100 MHz**, ve şüphe yanlış yerdeydi

Dökümler saati verdi:

```
Clock Low frequency:  100.0000 MHz     readhmid:17
Clock High frequency: 200.0000 MHz     readhmid:18
StepGen ClockFrequency: 100.000 MHz    readhmid:68   ← stepgen bunu kullanıyor
PWM     ClockFrequency: 200.000 MHz    readhmid:77   ← 200 MHz'deki tek modül
```

**Yukarıdaki 33.33 MHz hesabı geçersiz** — o rakam stok 7i96'dan geliyordu ve
kart 7i96 değil, 7i92 çıktı. Doğru sayılar:

```
steplen = 10000 ns, beyan edilen 100 MHz  → reg = 1000 sayım  (limit 16383)
dirsetup = 50000 ns                       → reg = 5000 sayım
100 MHz'de temsil edilebilir maksimum     → 163.8 us
```

Hepsi rahatça sığıyor; en büyük değerimiz aralığın %31'ini kullanıyor, taşma
ya da clamp uyarısı beklenmiyor.

**50 MHz kristal konusunda yukarıdaki not doğruydu ama yeterince güçlü değildi:**
kristal iç saat hakkında **hiçbir kanıt değil**. FPGA 100/200 MHz'i ondan PLL ile
türetiyor. Fotoğraftaki kristali şüphe sebebi saymak, kanıtı fazla okumaktı.

### Neden artık blocker değil

1. **IDROM kendi içinde tutarlı.** Geometri kontrolleri geçti
   (`hostmot2.c:692, :698, :708`) — IDROM'un beyan ettiği 2 × 17 = 34, sürücünün
   7i92 için hardcode ettiğiyle birebir uyuşuyor. Modül saatleri de tutarlı
   (biri hariç hepsi ClockLow, PWM ClockHigh).
2. **Kart Mesa'nın gerçek bir 7i92'si gibi davranıyor**, uydurma bir klon gibi
   değil. Firmware'in saat konusunda yalan söylemesi için bir sebep yok.
3. **Kayıt zaten alınıyor.** `test-rig.hal` `debug_modules=1 debug_idrom=1` ile
   yükleniyor, yani her açılışta dmesg'e `clock_frequency` satırı basılıyor.

**Kalan risk:** IDROM yalnızca firmware'in ne *iddia ettiğini* söyler. Bunu
kesinleştirmenin tek yolu hâlâ osiloskop. Ama artık "yapılmadan devam edilemez"
değil, "sıra gelince yapılır" kategorisinde.

**Yapılacak (öncelik: düşük):** [02-board-bringup.md](02-board-bringup.md) Step 7,
test tezgahında — ucuz makine, sonuçsuz hata. Adım kaybı semptomu görülürse
öncelik derhal yükselir.

**Cevap:** Saat 100 MHz (IDROM). Bağımsız fiziksel doğrulama bekliyor, blocker değil.

---

## 🟡 B6 — İzole girişler 24V sensörü doğrudan kabul ediyor mu?

**Durum:** AÇIK  
**Kim cevaplar:** Optokuplör parça numarasını okumak + ohmmetre  
**Neyi bloke ediyor:** Faz 6

Giriş bloğunun üstünde her giriş için `472` (4.7 kΩ) ve `511` (510 Ω) direnç
çifti görünüyor (OBSERVED). 4.7k seri direnç + optokuplör kombinasyonu 24V'ta
yaklaşık 5 mA sürer — tipik opto sürme akımı. Bu, girişlerin 24V için
tasarlandığına işaret ediyor ama **INFERRED**, doğrulanmadı.

**Cevap:** _(bekliyor)_

---

## 🔴 B7 — İkinci, izole 24V güç kaynağı

**Durum:** AÇIK — **yeni**  
**Kim cevaplar:** Satın alma  
**Neyi bloke ediyor:** Faz 8 (spindle + su kilitlemesi). Test tezgahında gerekmiyor.

Üretici diyagramı kartta **iki ayrı 24V girişi** olduğunu gösteriyor:

1. `24V 主电源输入` — ana besleme, kartı çalıştırır
2. `外部设备24V供电` — harici cihaz beslemesi, **yalnızca** PIN18/PIN19 NMOS
   çıkışlarını ve 0-10V analog çıkışı besler

Üreticinin uyarısı birebir: *"Bu güç kaynağı ana güç kaynağından karşılıklı
olarak izole olmalıdır, ikisi ortak şaseye bağlanamaz."*

Sonuç: **OUT1, OUT2 ve 0-10V çıkışlarını kullanmak için ikinci ve galvanik
olarak izole bir 24V kaynak şart.** Tek kaynaktan ikisini birden besleyemezsin,
GND'leri de birleştiremezsin.

Mermer makinesinde her ikisi de lazım — su pompası OUT1'den, VFD hız kontrolü
0-10V'tan. Yani malzeme listesine ikinci bir SMPS giriyor.

İyi haber: OUT1/OUT2 NMOS çıkışları **24V/50W yükü doğrudan sürüyor**, yani
su pompası için ayrı röle gerekmiyor (pompa 50 W altındaysa — envanterde
ölçülecek).

Detay: [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md).

**Cevap:** _(bekliyor)_

---

## Kapanmış sorular

| # | Soru | Cevap | Tarih |
|---|---|---|---|
| B3 | AXIS blokları diferansiyel mi? | Hayır, tek uçlu: `GND/STEP/DIR/5V` | 2026-08-03 |
| B4 | Sürücüler 5V mi 24V mi? | Test tezgahı: TB6560, optokuplörlü, 5V doğrudan. Mermer makinesi için hâlâ açık | 2026-08-03 |
| — | Giriş etiketleri (20-24) global IO numarası mı? | Evet. Üretici diyagramında `PIN20`…`PIN24` | 2026-08-03 |
| — | Test tezgahı firmware'i ne? | GRBL 1.1 (varsayılan parmak izinden) | 2026-08-03 |
| — | Hangi RJ45 Ethernet? | En alttaki, LED'li olan | 2026-08-03 |
