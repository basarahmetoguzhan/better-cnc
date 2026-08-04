# 09 — TB6560 Sürücüler: Bağlantı, Zamanlama, Hız Tavanı

Durum: sürücü modeli **OBSERVED** (kullanıcı doğruladı, fotoğrafta 4 adet
TB6560 kartı Zhulong'a bağlı). Zamanlama değerleri **INFERRED** — ilk testte
ölçülerek doğrulanacak.

---

## 🛑 Önce bu: mikro adım değişirse SCALE de değişir

GRBL'den aldığımız `$100-102` değerleri **eski sürücünün mikro adım ayarına
göre** kalibre edildi. TB6560'a geçerken mikro adımı aynı tutmazsan ölçek bozulur.

```
SCALE_yeni = SCALE_eski × (mikroadım_yeni ÷ mikroadım_eski)
```

Örnek: eski sürücü 1/16'daysa ve TB6560'ı 1/8'e kurarsan, `SCALE` **yarıya**
iner (833.3 → 416.65).

**Eski sürücünün mikro adım jumper konumunu TB6560'a geçmeden önce not al.**
Not alınmadıysa telafisi ampirik: 100 mm komut ver, gerçek mesafeyi ölç, oranla
düzelt.

---

## TB6560 mikro adım seçenekleri — 1/4 ve 1/32 YOK

TB6560AHQ dört uyarma modu destekliyor: 2-faz (tam adım), 1-2 faz (1/2),
2W1-2 faz (1/8), 4W1-2 faz (1/16).

**1/4 ve 1/32 yok.** Diğer sürücülerden alışkın olduğun kademeler burada
bulunmuyor.

200 adım/tur motorla (KH42KM2, 1.8°) ima edilen vida hatveleri:

| Mikro adım | adım/tur | X hatve (833.3) | Z hatve (1250) |
|---|---:|---:|---:|
| 1/1 | 200 | 0.24 mm | 0.16 mm |
| 1/2 | 400 | 0.48 mm | 0.32 mm |
| 1/8 | 1600 | 1.92 mm | 1.28 mm |
| **1/16** | 3200 | **3.84 mm** | **2.56 mm** |

1/16 en makul satır. Fiziksel vidayla karşılaştır: X'te ~3.8 mm, Z'de ~2.6 mm
hatve görüyorsan tutarlı.

---

## Bağlantı: Zhulong AXIS bloğu → TB6560

Zhulong'un her AXIS bloğu tek uçlu: **`GND` `STEP` `DIR` `5V`**
(bkz. [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md)).

TB6560 girişleri optokuplörlü. **Ortak anot** bağlantı — kartın her eksende
5V terminali vermesinin sebebi tam olarak bu:

| Zhulong AXIS n | TB6560 | Not |
|---|---|---|
| `5V` | `CLK+` **ve** `CW+` | Optokuplör anotları, ikisi birlikte |
| `STEP` | `CLK−` | |
| `DIR` | `CW−` | |
| `GND` | — | Ortak anotta kullanılmaz |
| — | `EN+` / `EN−` | Bağlama. Çoğu kart boşta enable olur; kartında jumper varsa kontrol et |

**Yön ters çıkarsa** `stepgen`'de dirinvert parametresi **yoktur** —
hostmot2 sürücüsünde yön çevirme `position-scale`'i **negatif** yaparak
yapılır, yani INI'de `SCALE = -833.300`.

Fiziksel step ve dir pinlerinin yerini değiştirmek gerekirse
`stepgen.NN.swap_step_dir` parametresi var.

---

## ⚡ Hız tavanı — bu kartın en önemli kısıtı

TB6560 kartlarında STEP hattında 6N137 hızlı optokuplör var, ama üretici
spesifikasyonu **maksimum 15 kHz STEP frekansı** veriyor. Bu, motorun ya da
mekaniğin değil, **kartın** koyduğu bir tavan.

%20 emniyet payıyla (12 kHz) mevcut ölçeklerde ulaşılabilir maksimum hız:

| Eksen | SCALE | 12 kHz'de max hız | mm/dk |
|---|---:|---:|---:|
| X | 833.3 | 14.4 mm/sn | **864** |
| Y | 823.5 | 14.6 mm/sn | **874** |
| Z | 1250.0 | 9.6 mm/sn | **576** |

**Bu, makinenin şu anki 500 mm/dk hızından çok da yüksek değil.** Özellikle Z
neredeyse aynı yerde kalıyor.

Hız istiyorsan tek kaldıraç **mikro adımı düşürmek**. 1/16 yerine 1/8'e inersen
`SCALE` yarıya iner ve tavan ikiye katlanır:

| Eksen | 1/8'de SCALE | 12 kHz'de mm/dk |
|---|---:|---:|
| X | 416.65 | **1728** |
| Z | 625.0 | **1152** |

Bedeli çözünürlük ve düşük hızda titreşim. Ahşap bir DIY makinede 1/8 gayet
makul.

> Not: 15 kHz üretici rakamı, bağımsız doğrulanmadı. İlk testte ampirik olarak
> sınanmalı — hızı kademe kademe artırıp adım kaybının başladığı noktayı bul.

---

## LinuxCNC zamanlama parametreleri

Optokuplörlü sürücüde cömert davranmak bedava — bu hızlarda kayıp yok.
Başlangıç değerleri:

```ini
[JOINT_n]
STEPLEN   = 10000     # 10 µs. GRBL'de $0 = 10 µs ile çalışıyordu, aynısı
STEPSPACE = 10000     # 10 µs
DIRSETUP  = 50000     # 50 µs — DIR hattı genelde daha yavaş optodan geçer
DIRHOLD   = 50000     # 50 µs
```

`steplen + stepspace = 20 µs` teorik 50 kHz demek, ama gerçek tavan zaten
optokuplörün 15 kHz'i. Yani bu değerler bağlayıcı kısıt değil, rahatça
bırakılabilir.

İlerde ayar yapılacaksa: önce `DIRSETUP`/`DIRHOLD`'u 20 µs'ye indir, sorun
çıkmazsa `STEPLEN`'i 5 µs'ye çek. Sıra önemli — yön zamanlaması hatası
tek yönde adım kaybı olarak görünür ve teşhisi zordur.

HAL parametre adları (`stepgen.c` doğrulaması):
`stepgen.NN.steplen`, `.stepspace`, `.dirsetup`, `.dirhold`,
`.position-scale`, `.maxvel`, `.maxaccel`, `.swap_step_dir`

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

Yedek kart bulundur. Dört tane var, biri gidince test durmasın.

---

## Açık kalan sorular

| # | Soru | Nasıl cevaplanır |
|---|---|---|
| T4 ✅ | Sürücü modeli | **TB6560** — kapandı |
| T5 | Eski sürücünün mikro adım ayarı neydi? | Shield'in jumper'larına bak. Bakılmadıysa 100 mm ölçerek kalibre et |
| T7 | 15 kHz tavanı gerçek mi? | Hızı kademeli artır, adım kaybının başladığı noktayı bul |
| T8 | Motor gerçek akımı? | Eski sürücünün Vref'i, ya da en düşük çalışan kademeden yukarı |
