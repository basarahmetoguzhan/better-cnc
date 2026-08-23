# Mermer Tezgahına Geçiş — Unutulmayacaklar

Test tezgahı mermer makinesinin **alt kümesi** olarak kuruluyor. Bu dosya, gerçek
makineye geçerken açılması/değiştirilmesi gereken her şeyin listesi.

Kural: test tezgahında kapalı olan her yetenek, burada **neden kapalı** ve
**açmak için ne gerekiyor** bilgisiyle kayıtlı olmalı. Kapalı bir şey sessizce
unutulursa mermer makinesinde eksik çıkar.

---

## 1. Donanım satın alınacaklar

| Ne | Neden | Durum |
|---|---|---|
| **İkinci, izole 24V güç kaynağı** | `OUT1`/`OUT2` NMOS çıkışları ve 0-10V analog çıkış **yalnızca** ikinci 24V girişinden besleniyor. Üretici ortak şaseyi açıkça yasaklıyor. Bu kaynak olmadan su pompası ve VFD hız kontrolü **hiç çalışmaz** | ❌ alınmadı (B7) |
| Su debi sensörü | Su kilidi için. Yoksa pompa rölesinin geri bildirimiyle idare edilir ama zayıf | ❌ |
| Takım boyu sensörü | Elmas uçların boy telafisi | ❌ |
| Seviye çevirici (gerekirse) | Mermer makinesinin sürücüleri 24V sinyal isterse. Test tezgahında TB6560 optokuplörlü 5V ile çalışıyor | ⏳ envanter bekliyor (B4) |

> **24V TUZAĞI:** kartta iki adet `24V GND` klemensi var ve **serigrafide ikisi de
> aynı yazıyor.** Yanlışına bağlanırsa kart yarı canlı görünür (bir LED yanar) ama
> FPGA konfigüre olmaz, Ethernet linki hiç kalkmaz. Ana besleme, büyük `470 16V`
> kondansatör ve `150` bobinin yanındaki klemens. Detay: `docs/08`.

---

## 2. Arayüzde test tezgahında KAPALI olan modüller

Her biri mermer makinesinde açılacak. Açmak için gereken koşul yanında.

| Modül | Test tezgahında | Açmak için gereken |
|---|---|---|
| **İğ mili (spindle)** | yok | VFD bağlantısı + `num_pwmgens=1` (IO 16) + `CW` çıkışı (IO 17) + ikinci 24V kaynak |
| **0-10V hız kontrolü** | yok | PWM modülü aktif + izole 24V |
| **Su pompası** | yok | `OUT1` (IO 18, NMOS 24V/50W) + izole 24V. Pompa 50 W altındaysa röle gerekmiyor |
| **Su debi sensörü** | yok | Bir GPIO girişi + sensör |
| **Su kilidi (interlock)** | yok | HAL mantığı: **debi yoksa iğ mili dönmez.** Elmas uç susuz kesimde saniyeler içinde gider |
| **Takım boyu probu** | yok | GPIO girişi + sensör. Probing ekranının Z modu buna bağlı |
| **Donanımsal e-stop girişi** | yok (GUI e-stop kullanılıyor) | Bir GPIO girişi + kontaktör/güvenlik rölesi |
| **4. eksen / gantry Y** | yok | `num_stepgens=4`, `stepgen.03` zaten IO 10/11'de hazır, firmware değişikliği gerekmiyor |

**Arayüz kuralı:** bu modüller test tezgahında **gizlenmeyecek, disabled görünecek.**
Sebebi ekranda yazacak ("bu makinede iğ mili yok"), böylece mermer makinesine
geçince neyin eksik olduğu kendiliğinden ortaya çıkar.

---

## 3. Config'de değişecekler

### hm2_eth config string

Test tezgahı:
```
num_stepgens=3 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx
```

Mermer makinesi (tek Y):
```
num_stepgens=3 num_encoders=0 num_pwmgens=1 sserial_port_0=xxxxxxxx
```

Mermer makinesi (çift Y / gantry):
```
num_stepgens=4 num_encoders=0 num_pwmgens=1 sserial_port_0=xxxxxxxx
```

> `sserial_port_0=xxxxxxxx` — küçük harf `x` devre dışı bırakır.
> `00000000` **bırakmaz**, 8 kanalı mod 0'da **açar**. Man page varsayılanı budur.

### Kinematik

Tek Y: `trivkins coordinates=XYZ`, 3 joint.

Çift Y (gantry): `trivkins coordinates=XYYZ`, **4 joint**
(joint 0 = X, 1 = Y sol, 2 = Y sağ, 3 = Z).
İki Y joint'i için **negatif `HOME_SEQUENCE`** — aynı mutlak değere sahip joint'ler
son hareketi senkron tamamlar ve portalı kareye getirir
(`ini-homing.adoc:262-267`).

### Ölçek ve limitler

Test tezgahının hiçbir sayısı taşınmaz. Mermer makinesi için:
- `SCALE` — eski Windows kontrolcüsünün steps/mm değerlerinden türetilecek,
  sonra komparatörle doğrulanacak
- `MAX_VELOCITY` / `MAX_ACCELERATION` — gantry kütlesine göre baştan ayarlanacak.
  Test tezgahının 3 mm/s ve 10 mm/s² değerleri anlamsız
- Strok / soft limitler — ölçülecek
- Step zamanlaması — mermer makinesinin sürücülerine göre. TB6560 için
  hesaplanan 10 µs / 50 µs değerleri o sürücüler için geçerli değil

---

## 4. Giriş bütçesi

Test tezgahı 5 girişin 5'ini kullanıyor (`gpio.020`–`024`), e-stop için yer yok.

Mermer makinesinde **`num_encoders=0` sayesinde IO 25–33 de GPIO** — toplam 14 giriş
fiziksel klemenste. Tek Y senaryosunda:

| Giriş | Pin | Not |
|---|---|---|
| X home + limit | 20 | |
| Y home + limit | 21 | Çift Y'de 21 ve 22 ayrı |
| Z home + limit | 22 | |
| Acil stop | 23 | **Donanımsal zincire ek olarak** |
| Takım boyu sensörü | 24 | |
| Su debi sensörü | 25 | Encoder 0 klemensi |
| Kapı switch'i (varsa) | 26 | |
| Yedek | 27–33 | |

> ⚠ Encoder klemenslerinin **arkasındaki analog devre doğrulanmadı.** FPGA tarafı
> serbest ama diferansiyel alıcı veya farklı seviye devresi varsa 24V endüktif
> sensör doğrudan bağlanamayabilir. Ohmmetre ile kontrol şart.

---

## 5. Güvenlik — mermer makinesinde pazarlık konusu değil

| Konu | Gereklilik |
|---|---|
| **Acil stop** | Sürücü ve iğ mili gücünü **donanımsal** kesmeli. LinuxCNC'nin haberi olması iyidir ama yazılım güvenlik fonksiyonu değildir. Pi/PC çökse bile makine durmalı |
| **Su kilidi** | Debi yoksa iğ mili çalışmaz. HAL'de kurulacak |
| **Soft limitler** | Strok ölçülüp yazılacak. Test tezgahında da eksik, orada da tamamlanacak |
| **Limit switch'ler** | Home ile paylaşılıyor → her eklemde `HOME_IGNORE_LIMITS = YES`. Bunun sonucu: **homing sırasında limitleri durduracak hiçbir şey yok.** Switch'in yanlış tarafından başlanırsa sert çarpışma (`ini-homing.adoc:46`). Homing öncesi eksenin doğru tarafta olduğu doğrulanmalı |
| **Homing sırası** | Önce Z (geri çekilsin), sonra X, sonra Y |

---

## 6. Termal — davranış değişikliği

Eski kontrolcü motorları duruşta enerjisiz bırakıyorsa (GRBL'de `$1 = 25 ms`
öyleydi), **LinuxCNC bırakmaz** — motorlar sürekli enerjili kalır.

Sonuç: motorlar ve sürücüler belirgin şekilde daha çok ısınır. Akım limiti o görev
döngüsü varsayılarak ayarlanmışsa termal koruma devreye girip adım kaybı olarak
görünür.

**Envanterde sorulacak:** eski kontrolcü duruşta motor akımını kesiyor muydu?
Harici sürücülerin (DM542/TB6600 tarzı) otomatik duruş akımı düşürme özelliği
varsa bu sorun kendiliğinden çözülür.

---

## 7. Doküman bakımı

Mermer makinesine geçerken:

- `docs/01-machine-survey.md` — doldurulmuş hâli commit edilecek
- `docs/10-7i92-pinout-verified.md` — pin haritası aynı kalır, kart aynı
- `configs/` — `marble/` diye ikinci config dizini. Test tezgahı config'i
  **silinmeyecek**, referans ve regresyon testi olarak kalacak
- `docs/OPEN-QUESTIONS.md` — B1, B4, B6, B7 orada bekliyor
- `docs/DURUM.md` — mermer makinesi için ayrı bölüm

---

## 8. Test tezgahından taşınacak kanıtlanmış bilgi

Bunlar makineden bağımsız, doğrudan geçerli:

- Kart `7I92` olarak tanıtıyor, HAL öneki `hm2_7i92.0`
- 34 IO, 2 port × 17, ClockLow 100 MHz
- `AXIS n` konnektörü → `stepgen.0n`
- Üretici serigrafisindeki pin numaraları = global IO numarası
- IO 17–24 kalıcı saf GPIO, hiçbir modül alamaz
- Girişler idle'da HIGH, tetiklenince LOW → HAL'de `.in_not`
- `sserial_port_0=xxxxxxxx` doğru devre dışı bırakma biçimi
- İki 24V klemensi aynı etiketli, ana besleme buck çeviricinin yanındaki
