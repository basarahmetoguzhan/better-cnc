# Makine Envanteri — better-cnc retrofit

Doldurulma tarihi: ______________  
Makine marka/model: ______________

> Kural: **Hiçbir kabloyu sökmeden önce fotoğrafla ve etiketle.**  
> Her klemens, her konnektör, her DIP switch bloğu. Sökerken emin olduğun bağlantı,
> üç hafta sonra tekrar takarken emin olmayacak.

---

## 0. ÖNCE BU — Eski kontrolcünün ayarları (geri dönüşü yok)

Eski Windows yazılımını aç, aşağıdaki ekranların **ekran görüntüsünü** al.
Kontrolcüyü söktükten sonra bu bilgilere erişemezsin.

- [ ] Yazılım adı ve sürümü: ______________
- [ ] Steps/mm (veya pulse/mm) — X: ______ Y: ______ Z: ______
- [ ] Max hız (max velocity) — X: ______ Y: ______ Z: ______  birim: ______
- [ ] İvme (acceleration) — X: ______ Y: ______ Z: ______  birim: ______
- [ ] Yumuşak limitler (soft limits) / çalışma alanı — X: ______ Y: ______ Z: ______
- [ ] Home yönü ve sırası (hangi eksen önce, hangi yöne gidiyor): ______________
- [ ] Home hızı ve latch (geri dönüş) hızı: ______________
- [ ] Backlash telafisi girili mi, değerleri: ______________
- [ ] Spindle max RPM ve 0-10V ölçekleme ayarı: ______________
- [ ] Varsa tool length sensörünün konumu / offset değeri: ______________

**Bu tablo projenin en değerli tek bilgisi.** LinuxCNC'de `SCALE` değerini
buradan türeteceğiz, sonra dial indicator ile doğrulayacağız.

---

## 1. Motorlar (her eksen için ayrı)

Etiketi okunmuyorsa motoru fotoğrafla, model numarasından bulunur.

| | X | Y | Z |
|---|---|---|---|
| Model / üretici | | | |
| NEMA boyu (23 / 34 / 42) | | | |
| Adım açısı (1.8° / 0.9°) | | | |
| Faz akımı (A) | | | |
| Tip: step / closed-loop step / servo | | | |
| Encoder var mı (varsa PPR) | | | |

> Not: Y ekseninde **iki motor** olabilir (portal iki yandan tahrikli).
> Varsa bu önemli — LinuxCNC'de gantry (tandem) joint yapılandırması gerekir,
> tek eksende iki joint tanımlanır. Kaç motor olduğunu mutlaka say.

Y ekseninde motor sayısı: ______

---

## 2. Sürücü kartları (driver)

DIP switch bloklarını **yakın çekim fotoğrafla** — anahtarların yönü okunacak kadar net.

| | X | Y | Z |
|---|---|---|---|
| Model / üretici | | | |
| Besleme gerilimi (V) | | | |
| Mikrostep ayarı (DIP'ten okunan) | | | |
| Akım ayarı (DIP'ten okunan) | | | |
| Sinyal giriş gerilimi (5V / 24V) | | | |
| Giriş tipi: ortak anot / ortak katot / diferansiyel | | | |
| ENABLE girişi var mı, aktif seviyesi | | | |
| ALARM / hata çıkışı var mı | | | |

**Kritik kontrol:** Zhulong kartı 5V TTL step/dir üretiyor. Sürücüler 24V sinyal
bekliyorsa araya seviye çevirici gerekir. Sürücü üstünde `PUL+ / PUL- / DIR+ / DIR-`
yazıyorsa diferansiyel, `5V` etiketi arıyoruz. Kutu içinde direnç varsa
(genelde 2K) sürücü 24V için ayarlanmış demektir.

Sonuç — seviye çevirici gerekiyor mu: ______________

---

## 3. Mekanik oran (steps/mm'i doğrulamak için)

| | X | Y | Z |
|---|---|---|---|
| Tahrik tipi: vidalı mil / kremayer / kayış | | | |
| Vidalı mil hatvesi (mm/tur) | | | |
| Kremayer: modül ve pinyon diş sayısı | | | |
| Redüktör oranı (varsa) | | | |
| Kayış-kasnak oranı (varsa) | | | |
| Kullanılabilir strok (mm) | | | |

---

## 4. Switch'ler — burada dikkat, sadece 5 izole giriş var

| Switch | Var mı | Tip (mekanik/endüktif) | NPN / PNP | NO / NC | Besleme (V) |
|---|---|---|---|---|---|
| X home | | | | | |
| Y home | | | | | |
| Z home | | | | | |
| X limit (±) | | | | | |
| Y limit (±) | | | | | |
| Z limit (±) | | | | | |
| Tool length sensörü | | | | | |
| Acil stop (e-stop) | | | | | |

> Endüktif sensörler genelde PNP + NO gelir ama Çin makinelerinde NPN de yaygın.
> Sensörün üstünde yazar; yoksa multimetre ile ölç.
>
> **Plan:** 3 home + e-stop + tool sensor = 5 giriş, dolu. Limit switch'ler
> **seri bağlanıp** home hattına eklenir (NC seri zincir — herhangi biri açılırsa
> hat kopar). Bu standart LinuxCNC pratiği, home ve limit aynı pini paylaşır.

---

## 5. Spindle / VFD

- [ ] VFD marka ve model: ______________
- [ ] Güç (kW): ______  Max RPM: ______  Min çalışma RPM: ______
- [ ] Su soğutmalı mı: ______________
- [ ] Şu anki kontrol yöntemi: 0-10V / panel / Modbus / dijital hız kademesi
- [ ] VFD kontrol klemensleri (fotoğrafla): analog giriş, FWD/CW, COM
- [ ] "Spindle at speed" geri bildirim çıkışı var mı: ______________
- [ ] VFD parametre listesi yedeklendi mi: ______________

---

## 6. Su / soğutma — mermer için kritik

- [ ] Su pompası nasıl kontrol ediliyor (röle / manuel / kontrolcüden): ______________
- [ ] Debi (flow) sensörü var mı: ______________
- [ ] Pompanın gerilimi ve akımı: ______________

> HAL'de kilitleme kuracağız: **su yoksa spindle çalışmaz.** Elmas uç susuz
> kesimde saniyeler içinde gider. Debi sensörü yoksa en azından pompa rölesinin
> geri bildirimini kullanırız.

---

## 7. Acil stop zinciri

- [ ] E-stop butonuna basınca şu an ne kesiliyor: ______________
- [ ] Kontaktör / güvenlik rölesi var mı, model: ______________
- [ ] Sürücülerin gücü e-stop'tan geçiyor mu: ______________

> **Pazarlık konusu değil:** E-stop, sürücü/spindle gücünü **donanımsal olarak**
> kesmeli. LinuxCNC'nin haberi olması iyidir ama yazılım güvenlik fonksiyonu
> değildir. Pi çökse bile makine durmalı.

---

## 8. Güç beslemesi

- [ ] Panoda 24V DC besleme var mı, kaç amper: ______________
- [ ] Boşta kalan kapasite (Zhulong kartı + Pi için): ______________
- [ ] 5V besleme var mı: ______________

---

## 9. Eski kontrolcü — sökmeden önce

- [ ] Kontrolcü marka/model: ______________
- [ ] Bilgisayarla bağlantı tipi (USB / paralel / Ethernet / PCI kart): ______________
- [ ] Tüm klemens bloklarının fotoğrafı çekildi: ______________
- [ ] Her kablo etiketlendi: ______________
- [ ] Kablo etiket şeması bu dizine yazıldı: ______________

> Eski kontrolcüyü **atma, bozma.** Yeni sistem çalışana kadar geri dönüş
> yolun olsun. İdeali: yeni kartı paralel monte et, step/dir kablolarını
> klemens üzerinden aktar, sorun çıkarsa 10 dakikada geri al.

---

## Fotoğraf listesi (çekilecekler)

- [ ] Pano genel görünüm, kapak açık
- [ ] Her sürücü kartı, DIP switch'ler okunacak netlikte
- [ ] Her motor etiketi
- [ ] VFD ön panel + klemens bloğu
- [ ] Eski kontrolcünün tüm klemensleri
- [ ] Her home/limit switch'in makine üzerindeki konumu
- [ ] Eski yazılımın tüm ayar ekranları
