# 09 — TB6560 Sürücüler: Bağlantı, Zamanlama, Hız Tavanı

Durum: sürücü modeli **OBSERVED** (kullanıcı doğruladı, fotoğrafta 4 adet
TB6560 kartı Zhulong'a bağlı — X, Y×2, Z; Y'nin iki sürücüsü olduğu 2026-11-12'de fark edildi). Zamanlama değerleri **INFERRED** — scope ile
ölçülmedi; 2026-11-12'de bu değerlerle (STEPLEN 10 µs) Faz 1–8 geçti. Bağlantı ve sinyal seviyeleri 2026-11-12'de ölçüldü (aşağıda).

---

## 🛑 Önce bu: mikro adım değişirse SCALE de değişir

~~GRBL'den aldığımız `$100-102` değerleri **eski sürücünün mikro adım ayarına
göre** kalibre edildi.~~ (2026-11-12: bu mekanik için hiç kalibre edilmemişlerdi — ölçülen değerlerin X/Y'de ~8.3×, Z'de ~10.2× üstündeydi.) Mikro adımı değiştirirsen ölçek bozulur.

```
SCALE_yeni = SCALE_eski × (mikroadım_yeni ÷ mikroadım_eski)
```

Örnek: eski sürücü 1/16'daysa ve TB6560'ı 1/8'e kurarsan, `SCALE` **yarıya**
iner (ölçülen X için 100.085 → 50.04).

**Eski sürücünün mikro adım jumper konumunu TB6560'a geçmeden önce not al.** (2026-10-18: geçiş yapıldı, eski jumper konumu kayıtlı değil — T5 açık. 2026-11-12: ölçek için gerek kalmadı, doğrudan ölçüldü.)
Not alınmadıysa telafisi ampirik: 100 mm komut ver, gerçek mesafeyi ölç, oranla
düzelt (mesafe strokun elverdiği kadar). Bu yol 2026-11-12'de uygulandı (Faz 3): X **100.085**, Y **98.592**, Z **122.274** adım/mm, doğrulama %1 içinde.

---

## TB6560 mikro adım seçenekleri — 1/4 ve 1/32 YOK

TB6560AHQ dört uyarma modu destekliyor: 2-faz (tam adım), 1-2 faz (1/2),
2W1-2 faz (1/8), 4W1-2 faz (1/16).

**1/4 ve 1/32 yok.** Diğer sürücülerden alışkın olduğun kademeler burada
bulunmuyor.

200 adım/tur motorla (KH42KM2, 1.8°) ima edilen tur başına ilerleme (vida hatvesi), 2026-11-12'de ölçülen ölçekle:

| Mikro adım | adım/tur | X hatve (100.085) | Z hatve (122.274) |
|---|---:|---:|---:|
| 1/1 | 200 | 2.00 mm | 1.64 mm |
| 1/2 | 400 | 4.00 mm | 3.27 mm |
| 1/8 | 1600 | 15.99 mm | 13.09 mm |
| 1/16 | 3200 | 31.97 mm | 26.17 mm |

Hangi satırın doğru olduğu mikro adım jumper'ı ya da mekaniğin tur başına ilerlemesi görülünce belli olur (T5); ölçek için gerekmiyor, doğrudan ölçüldü.
(Eski 833.3 / 1250'ye göre 1/16 satırı makul görünüyordu: X ~3.8, Z ~2.6 mm — o değerler bu mekanik için yanlıştı.)

---

## Bağlantı: Zhulong AXIS bloğu → TB6560

Zhulong'un her AXIS bloğu tek uçlu: **`GND` `STEP` `DIR` `5V`**
(bkz. [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md)).

TB6560 girişleri optokuplörlü. Kartın her eksende 5V terminali vermesi **ortak anot** bağlantı içindir. **Bu tezgah ise ORTAK KATOT bağlı** (2026-11-12, makinede görüldü):
5V terminali boş, sürücülerin `CLK−`/`CW−` uçları GND'de, `STEP`/`DIR` anotları doğrudan sürüyor; kartın çıkışları **3.3 V lojik**. Ölçümler ve "bir çıkış = bir sürücü" kuralı: aşağıda "Bu tezgahta ölçülen seviyeler".

| Zhulong AXIS n | TB6560, ortak anot (kartın 5V'u) | TB6560, ortak katot (**bu tezgah**) |
|---|---|---|
| `5V` | `CLK+` **ve** `CW+` (optokuplör anotları, ikisi birlikte) | boş |
| `STEP` | `CLK−` | `CLK+` |
| `DIR` | `CW−` | `CW+` |
| `GND` | — (ortak anotta kullanılmaz) | `CLK−` **ve** `CW−` |
| — | `EN+` / `EN−`: bağlama. Çoğu kart boşta enable olur; kartında jumper varsa kontrol et | aynı kural (tezgahtaki EN bağlantısı kayıtlı değil) |

**Yön ters çıkarsa** `stepgen`'de dirinvert parametresi **yoktur** —
hostmot2 sürücüsünde yön çevirme `position-scale`'i **negatif** yaparak
yapılır, yani INI'de `SCALE = -100.085` (X için).

Fiziksel step ve dir pinlerinin yerini değiştirmek gerekirse
`stepgen.NN.swap_step_dir` parametresi var.

---

## ⚡ Hız tavanı — bu kartın en önemli kısıtı

TB6560 kartlarında STEP hattında 6N137 hızlı optokuplör var, ama üretici
spesifikasyonu **maksimum 15 kHz STEP frekansı** veriyor. Bu, motorun ya da
mekaniğin değil, **kartın** koyduğu bir tavan.

%20 emniyet payıyla (12 kHz) ölçülen ölçeklerde (2026-11-12) ulaşılabilir maksimum hız, ve ayarlı hızlardaki step frekansı:

| Eksen | SCALE | 12 kHz'de max hız | mm/dk | `MAX_VELOCITY` → step | `STEPGEN_MAX_VEL` → step |
|---|---:|---:|---:|---:|---:|
| X | 100.085 | 119.9 mm/sn | **7194** | 10 mm/s → 1.00 kHz | 12 mm/s → 1.20 kHz |
| Y | 98.592 | 121.7 mm/sn | **7303** | 10 mm/s → 0.99 kHz | 12 mm/s → 1.18 kHz |
| Z | 122.274 | 98.1 mm/sn | **5888** | 5 mm/s → 0.61 kHz | 6 mm/s → 0.73 kHz |

**Tavan artık bağlamıyor:** ayarlı en yüksek step frekansı (X'te `STEPGEN_MAX_VEL` ile 1.20 kHz) 12 kHz'in onda biri kadar. Eski ölçekle (833.3 / 823.5 / 1250)
hesaplanan 864 / 874 / 576 mm/dk yanlıştı — o ölçekler bu mekanik için ~8.3× / 10.2× yüksekti.

Tavan bir gün bağlarsa kaldıraç yine **mikro adımı düşürmek**: mikro adımı yarıya indirmek
`SCALE`'i yarıya indirir ve tavanı ikiye katlar (bugünkü mikro adım bilinmiyor, T5):

| Eksen | Yarı mikro adımda SCALE | 12 kHz'de mm/dk |
|---|---:|---:|
| X | 50.04 | **14 388** |
| Z | 61.14 | **11 777** |

Bedeli çözünürlük ve düşük hızda titreşim. Ahşap bir DIY makinede 1/8 gayet
makul.

> Not: 15 kHz üretici rakamı, bağımsız doğrulanmadı. İlk testte ampirik olarak
> sınanmalı — hızı kademe kademe artırıp adım kaybının başladığı noktayı bul. (2026-11-12: ayarlı step frekansı en çok ~1.2 kHz; tavan hiç sınanmadı.)

---

## LinuxCNC zamanlama parametreleri

Optokuplörlü sürücüde cömert davranmak bedava — bu hızlarda kayıp yok.
Başlangıç değerleri (2026-11-12: değişmedi, Faz 1–8 bunlarla geçti):

```ini
[JOINT_n]
STEPLEN   = 10000     # 10 µs. GRBL'de $0 = 10 µs ile çalışıyordu, aynısı
STEPSPACE = 10000     # 10 µs
DIRSETUP  = 50000     # 50 µs — DIR hattı genelde daha yavaş optodan geçer
DIRHOLD   = 50000     # 50 µs
```

`steplen + stepspace = 20 µs` teorik 50 kHz demek, ama gerçek tavan zaten
optokuplörün 15 kHz'i. Yani bu değerler bağlayıcı kısıt değil, rahatça
bırakılabilir. Çalışma anında daha uzun darbelerle yapılan bir deneme (2026-11-12) hiçbir şeyi değiştirmedi.

İlerde ayar yapılacaksa: önce `DIRSETUP`/`DIRHOLD`'u 20 µs'ye indir, sorun
çıkmazsa `STEPLEN`'i 5 µs'ye çek. Sıra önemli — yön zamanlaması hatası
tek yönde adım kaybı olarak görünür ve teşhisi zordur.

HAL parametre adları (`stepgen.c` doğrulaması):
`stepgen.NN.steplen`, `.stepspace`, `.dirsetup`, `.dirhold`,
`.position-scale`, `.maxvel`, `.maxaccel`, `.swap_step_dir`

---

## Bu tezgahta ölçülen seviyeler — bir çıkış, bir sürücü (2026-11-12)

Multimetre, DC, AXIS bloğunun `GND`'sine göre. Tezgah ortak katot bağlı (yukarıda "Bağlantı"):
kartın `STEP`/`DIR` çıkışı optokuplörün LED'ini doğrudan sürüyor, `5V` klemensi boş.

| Ölçüm | AXIS 0 (X, tek sürücü) | AXIS 1 (Y, iki sürücü paralel) |
|---|---:|---:|
| `DIR` yüksek | 3.11 V | **2.43 V** |
| `DIR` düşük | 0.2 V | — |
| `STEP` boşta | 3.21 V | **2.55 V** |

Kartın çıkışları 3.3 V lojik. İki optokuplör aynı çıkışa paralel bağlıyken seviye düştü ve iki Y
sürücüsü de yönü hiç görmedi — "Y DIR arızası". **Kural: bir 3.3 V çıkış yalnız bir sürücünün
optokuplörünü sürer.** Çözüm: ikinci Y sürücüsü AXIS 3 = `stepgen.03`'e (IO 10/11) taşındı,
`stepgen.01` ile aynı ayarları ve aynı komutu alıyor (`num_stepgens=4`). O günden beri Y iki yöne
gidiyor; Faz 3 doğrulamasında portalın iki yanı 50.005 mm komutta sol 50.3, sağ 49.7 mm gitti.
Dört TB6560'ın dördü de kullanımda: X, Y×2, Z.

---

## Akım ayarı

TB6560 kartlarında akım DIP switch'le kademeli seçiliyor. Motor
**KH42KM2R075** — parça numarasındaki `R075` büyük olasılıkla **0.75 A/faz**
demek, ama doğrulanmadı.

LinuxCNC motorları sürekli enerjili tuttuğu için
(bkz. [07-test-machine-grbl-analysis.md](07-test-machine-grbl-analysis.md)
termal uyarısı):

1. **En düşük çalışan kademeden başla.** Makineyi hareket ettirebiliyorsa yeter.
2. Adım kaybı görürsen bir kademe yükselt.
3. İlk uzun testte motorlara ve sürücü kartlarına elle dokun. Dokunulamayacak
   kadar sıcaksa kademeyi düşür.

TB6560'ın "decay mode" DIP ayarı da var — varsayılanı bırak, sorun çıkarsa
oynanacak son şey.

---

## ⚠ TB6560 kırılganlığı

Bu çipin kötü bir ünü var. İki kural:

1. **Güç açıkken motor kablosunu asla takıp çıkarma.** Endüktif ark çipi
   anında öldürür. Bu, TB6560'ları öldürmenin bir numaralı yolu.
2. **Besleme 10–35 V.** Üst sınıra yaklaşma; 24 V güvenli, 35 V'ta çip ısınır
   ve marj kalmaz.

Yedek kart bulundur, biri gidince test durmasın. Dört tane var ama dördü de kullanımda (X, Y×2, Z — 2026-11-12): yedek yok, alınmalı.

---

## Açık kalan sorular

| # | Soru | Nasıl cevaplanır |
|---|---|---|
| T4 ✅ | Sürücü modeli | **TB6560** — kapandı |
| T5 🔵 | Eski sürücünün mikro adım ayarı neydi? | Ölçek için artık gerekmiyor: SCALE 2026-11-12'de ölçüldü (X 100.085, Y 98.592, Z 122.274). TB6560'ların jumper'ları da hâlâ okunmadı |
| T7 | 15 kHz tavanı gerçek mi? | Hızı kademeli artır, adım kaybının başladığı noktayı bul. Acil değil: ayarlı en yüksek step frekansı ~1.2 kHz |
| T8 | Motor gerçek akımı? | Eski sürücünün Vref'i, ya da en düşük çalışan kademeden yukarı |
| T9 ✅ | Bir çıkış iki sürücüyü sürebilir mi? | **Hayır** (2026-11-12): iki optokuplör paralelken DIR yüksek seviyesi 2.43 V'a düştü, iki Y sürücüsü de yönü görmedi → her sürücüye ayrı çıkış (yukarıda "Bu tezgahta ölçülen seviyeler") |
