# Nerede Kaldık — Oturum Durumu

Son güncelleme: **2026-08-05, gece**

Bu dosya oturumlar arası hafıza. Devam ederken **ilk bunu oku.**

---

## Tek satırlık özet

Zhulong kartı tamamen çözüldü ve LinuxCNC'de çalışıyor. **Üç eksen de jog ediyor.**
Tek açık arıza: **Y ekseninin DIR kablosu** — yön değişmiyor, hep öne gidiyor.

---

## Çalışan ortam

| | |
|---|---|
| Makine | Ev yapımı ahşap 3 eksen router (test tezgahı) |
| Kontrolcü | Windows laptop (Monster), **LinuxCNC 2.9.10 live USB**, persistence 10 GB açık |
| Çekirdek | `6.12.95+deb13-rt-amd64` (PREEMPT_RT) |
| Kullanıcı | `user` / `live` |
| NIC | `enp3s0` (Realtek RTL8125, 100 Mbps Full'e sabitlendi) |
| PC IP | `10.10.10.1/24` |
| Kart IP | `10.10.10.10` |
| Repo | `~/better-cnc` (live PC'de), GitHub: `basarahmetoguzhan/better-cnc` (public) |

**Live boot'tan sonra her seferinde:**

```bash
sudo ip addr add 10.10.10.1/24 dev enp3s0     # persistence tutmazsa
cd ~/better-cnc && git pull
halrun -U
linuxcnc ~/better-cnc/configs/test-rig/test-rig.ini
```

> `192.168.1.1/24` adresini `enp3s0`'a **verme** — ev ağıyla çakışıp interneti kesiyor.

---

## Doğrulanan donanım gerçekleri

Kaynak: `docs/board-dumps/readhmid-10.10.10.10-2026-08-05.txt` ve `printpd-*.txt`

| | |
|---|---|
| Kart | **Mesa 7i92 türevi** (7i96 DEĞİL). LBP16'da `7I92`, IDROM'da `MESA7I92` |
| HAL öneki | **`hm2_7i92.0`** |
| IO | **34 pin** = 2 port × 17 |
| Saat | ClockLow **100 MHz**, ClockHigh 200 MHz |
| StepGen | 6 adet, IO 4–15. **AXIS n → `stepgen.0n`** |
| QCount | 3 adet, IO 25–33 (ikincil — `num_encoders=0` ile GPIO olur) |
| PWM | 1 adet, IO 16 |
| SSerial | 1 modül 2 kanal, IO 0–3 |
| Saf GPIO | **IO 17–24** — ikincil fonksiyonu hiç yok, hiçbir config alamaz |

**Config string (çalıştığı doğrulandı):**
```
num_stepgens=3 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx
```
`xxxxxxxx` SSerial'i gerçekten kapatıyor. `00000000` **kapatmaz**, 8 kanalı mod 0'da açar.

**Üretici serigrafisi = global IO numarası:**
`CW`=17, `OUT1`=18, `OUT2`=19, girişler `20–24`

---

## Switch haritası (elle tetiklenerek doğrulandı)

İdle durumda hepsi **`.in = TRUE`**, basınca FALSE → HAL'de **`.in_not`** kullanılacak.

| Pin | Fiziksel konum |
|---|---|
| `gpio.020` | X sağ |
| `gpio.021` | X sol |
| `gpio.022` | Y arka |
| `gpio.023` | Y ön |
| `gpio.024` | Z üst |
| — | **Z alt: switch yok** |

**Jog yönleri:** X+ = sağ, Z+ = yukarı. **Y = belirsiz** (DIR arızası yüzünden).

---

## Makine parametreleri

GRBL EEPROM'undan (`docs/07`), ampirik kalibre edilmiş, **güvenilir**:

```
SCALE   X = 833.300    Y = 823.500    Z = 1250.000   adım/mm
```

Fabrika varsayılanı olduğu için **güvenilmez**, ölçülecek:
```
$110-112 = 500 mm/dk    $120-122 = 10 mm/sn²    $130-132 = 200 mm strok
```

**Sürücüler: TB6560.** Optokuplör tavanı **15 kHz** → mevcut ölçekte X ~864, Z ~576 mm/dk.
Hız istenirse mikro adımı düşürmek gerekiyor (`docs/09`).

**Motorlar:** Japan Servo KH42KM2, NEMA 17, 1.8° = 200 adım/tur.

---

## Tamamlananlar

- [x] Faz 0 — Upstream recon, 10 doküman
- [x] Git repo + GitHub (public)
- [x] Pi 3 elendi (Ethernet USB arkasında, `hm2_eth` için uygun değil)
- [x] Live USB + persistence
- [x] Kart bulundu, IDROM okundu, 7i96→7i92 düzeltmesi
- [x] Config yazıldı, hatasız yükleniyor
- [x] Makine enable, üç stepgen aktif
- [x] **Üç eksen de jog ediyor**
- [x] Switch'ler bağlandı ve pin haritası çıkarıldı
- [x] Paket sağlığı: 10 saniyede 0 hata (`packet-error-total` sabit)

---

## Sıradaki adımlar

**1. Y ekseni DIR kablosu.** AXIS 1 bloğunda (`GND STEP DIR 5V`) DIR kablosunu kontrol et.
Y'nin TB6560'ında `CW−` bağlı mı, `CW+` 5V'a gidiyor mu. X ve Z ile karşılaştır.
Multimetreyle: AXIS 1 `DIR` ↔ `GND` arası Y+/Y− jog'da 0V↔5V değişiyor mu.

**2. Y yönü belirlenince** switch'lerin + / − limit ataması netleşir.

**3. HAL'de switch net'lerini bağla** (şu an yorum satırı):
```
gpio.020 → X pozitif uç      gpio.021 → X negatif uç
gpio.022/023 → Y (yön belli olunca)
gpio.024 → Z home + pozitif limit
```
Her eklem için `HOME_IGNORE_LIMITS = YES` (home ve limit aynı switch).

**4. Ölçek doğrulama:** 10 mm jog, cetvelle ölç.

**5. Gerçek strok ölçümü** → soft limitler.

**6. Homing.** ⚠ Sıralama: **Z → Y → X**. Kaynak `configs/test-rig/test-rig.ini`:
`[JOINT_2] Z HOME_SEQUENCE = 0`, `[JOINT_1] Y = 1`, `[JOINT_0] X = 2`.
Kritik olan Z'nin ilk olması (takım geri çekilsin); X ile Y arasındaki sıra
güvenlik açısından fark etmiyor. **LinuxCNC INI'ye uyar, dokümana değil** — bu iki
kaynak çelişirse INI kazanır.
`HOME_SEARCH_VEL`'i ilk denemede 1 mm/s'ye düşür. `HOME_IGNORE_LIMITS = YES` olduğu
için switch bulunamazsa **durduracak hiçbir şey yok** (`ini-homing.adoc:46`).

---

## Bilinen zararsız uyarılar

| Mesaj | Durum |
|---|---|
| `iopl() failed ... parallel port access is not allowed` | Paralel port, kullanmıyoruz |
| `pin_new(...) called with already-initialized memory` | `hal_lib.c:690` — `return` yok, pin normal oluşuyor. IO 0/1 = SSerial pinleri, hiçbir yere bağlı değil |

---

## Açık sorular

Tam liste: [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)

| # | Soru | Durum |
|---|---|---|
| B1 | Mermer makinesinde Y'de kaç motor | 🟡 amber — B2 çözüldüğü için artık mimari değiştirmiyor |
| B2 | Encoder pinleri GPIO olur mu | ✅ **EVET** — 14 giriş kullanılabilir |
| B3 | AXIS blokları diferansiyel mi | ✅ Hayır, tek uçlu `GND/STEP/DIR/5V` |
| B4 | Sürücü sinyal gerilimi | ✅ Test tezgahı: TB6560, optokuplörlü 5V. Mermer makinesi açık |
| B5 | Saat frekansı doğru mu | 🟢 100 MHz, IDROM'dan okunuyor, blocker değil |
| B6 | Girişler 24V kabul ediyor mu | 🟡 Açık — test tezgahında 5V ile çalışıyor |
| B7 | İkinci izole 24V kaynak | 🔴 Mermer makinesi için gerekli, alınmadı |
| — | **Y DIR kablosu** | 🔴 **Aktif arıza** |

---

## Mermer makinesi (Faz 2, beklemede)

Arkadaşı envanteri dolduruyor (`docs/01-machine-survey.md`).
En kritik satır: **Y ekseninde kaç motor.** B2 çözüldüğü için artık yeniden tasarım
gerektirmiyor ama INI'yi belirliyor.

İkinci izole 24V kaynak alınmalı — su pompası (OUT1) ve VFD 0-10V onsuz çalışmaz.
