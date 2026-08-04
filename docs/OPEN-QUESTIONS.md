# Açık Sorular / Blocker Kaydı

Bu dosya, cevaplanmadan ilerlenemeyecek soruların kaydıdır. Her soru
cevaplandığında **buraya cevabı ve tarihi yazılır** — silinmez, üstü çizilir.
Karar geçmişini burada tutuyoruz.

Son güncelleme: 2026-08-02

---

## 🔴 B1 — Y ekseninde kaç motor var?

**Durum:** AÇIK  
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

**Cevap:** _(bekliyor)_

---

## 🔴 B2 — Encoder pinleri GPIO olarak kullanılabiliyor mu?

**Durum:** AÇIK — B1'den bağımsız, ama B1 çift Y çıkarsa kritikleşiyor  
**Kim cevaplar:** `mesaflash --readhmid` çıktısı  
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

**Cevap:** _(bekliyor)_

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

## 🟡 B5 — Bildirilen saat frekansı gerçek mi?

**Durum:** AÇIK  
**Kim cevaplar:** Osiloskop / mantık analizörü — **yazılımdan tespit edilemez**  
**Neyi bloke ediyor:** Faz 9 (kalibrasyon) — ama semptomu Faz 5'te de çıkabilir

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

**Cevap:** _(bekliyor)_

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
