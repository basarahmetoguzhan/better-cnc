# Nerede Kaldık — Oturum Durumu

Son güncelleme: **2026-11-12** (makine durumu: test tezgahında Faz 1–8 geçti — en alttaki tarihli bölüm; dokümandaki homing sırası 2026-08-23'te INI'ye göre düzeltildi)

Bu dosya oturumlar arası hafıza. Devam ederken **ilk bunu oku.**

---

## Tek satırlık özet

Zhulong kartı tamamen çözüldü ve LinuxCNC'de çalışıyor. **Üç eksen de jog ediyor**; 2026-11-12'de test tezgahı **Faz 1–8'i geçti** (Faz 8 şerhli: Z koşu başına ~0,02 mm kayıyor, iş listesinde): ölçek ve strok ölçüldü, switch'ler bağlı, homing ve yumuşak limitler çalışıyor, ilk program hatasız koştu. Faz 9 (kalıcılaştırma) sırada.
~~Tek açık arıza: Y ekseninin DIR hattı~~ → **çözüldü (2026-11-12, Faz 1):** Y'de iki motor / iki TB6560 var ve ikisi de AXIS 1'deydi; kartın 3,3 V'luk tek çıkışı iki optokuplörü süremiyordu (DIR yüksek seviyesi 2,43 V, tek sürücülü X'te 3,11 V). İkinci Y sürücüsü AXIS 3 / `stepgen.03`'e taşındı; Y iki yöne gidiyor. Açık iş listesi en alttaki bölümde.

---

## Çalışan ortam

| | |
|---|---|
| Makine | Ev yapımı ahşap 3 eksen router (test tezgahı) |
| Kontrolcü | **LinuxCNC 2.9.10 live USB**, persistence 10 GB açık — hangi PC'ye takılırsa (2026-08: Monster laptop; 2026-11-12: MSI anakartlı masaüstü PC) |
| Çekirdek | `6.12.95+deb13-rt-amd64` (PREEMPT_RT) |
| Kullanıcı | `user` / `live` |
| NIC | PC'nin kablolu portu; adı PC'ye göre değişir (Monster: `enp3s0`, Realtek RTL8125, 100 Mbps Full'e sabitlendi; MSI PC: `enp12s0`, RTL8125B) |
| PC IP | `10.10.10.1/24` |
| Kart IP | `10.10.10.10` |
| Repo | `~/better-cnc` (live PC'de), GitHub: `basarahmetoguzhan/better-cnc` (public) |

**Live boot'tan sonra her seferinde:**

```bash
sudo ip addr add 10.10.10.1/24 dev enp3s0     # persistence tutmazsa. enp3s0 = Monster; başka PC'de adı farklı (ip -brief link). Kalıcısı, her PC'de: sudo deploy/kiosk.sh board-network
cd ~/better-cnc && git pull
halrun -U
linuxcnc ~/better-cnc/configs/test-rig/test-rig.ini
```

> `192.168.1.1/24` adresini kartın bağlı olduğu porta (Monster'da `enp3s0`) **verme** — ev ağıyla çakışıp interneti kesiyor.

---

## Doğrulanan donanım gerçekleri

Kaynak: `docs/board-dumps/readhmid-10.10.10.10-2026-08-05.txt` ve `printpd-*.txt`

| | |
|---|---|
| Kart | **Mesa 7i92 türevi** (7i96 DEĞİL). LBP16'da `7I92`, IDROM'da `MESA7I92` |
| HAL öneki | **`hm2_7i92.0`** |
| IO | **34 pin** = 2 port × 17 |
| Saat | ClockLow **100 MHz**, ClockHigh 200 MHz |
| StepGen | 6 adet, IO 4–15. **AXIS n → `stepgen.0n`** (test tezgahında 4'ü kullanılıyor: X 00, Y 01 + 03, Z 02 — 2026-11-12) |
| QCount | 3 adet, IO 25–33 (ikincil — `num_encoders=0` ile GPIO olur) |
| PWM | 1 adet, IO 16 |
| SSerial | 1 modül 2 kanal, IO 0–3 |
| Saf GPIO | **IO 17–24** — ikincil fonksiyonu hiç yok, hiçbir config alamaz |

**Config string (çalıştığı doğrulandı; 2026-11-12'den beri `num_stepgens=4` — Y'nin ikinci sürücüsü `stepgen.03`, `test-rig.ini:193`):**
```
num_stepgens=4 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx
```
`xxxxxxxx` SSerial'i gerçekten kapatıyor. `00000000` **kapatmaz**, 8 kanalı mod 0'da açar.

**Üretici serigrafisi = global IO numarası:**
`CW`=17, `OUT1`=18, `OUT2`=19, girişler `20–24`

---

## Switch haritası (elle tetiklenerek doğrulandı)

İdle durumda hepsi **`.in = TRUE`**, basınca FALSE → HAL'de **`.in_not`** kullanılıyor (Faz 5'te bağlandı ve doğrulandı, `test-rig.hal:301-327`).

| Pin | Fiziksel konum — HAL'deki görevi (Faz 5, 2026-11-12) |
|---|---|
| `gpio.020` | X sağ — X'in pozitif limiti |
| `gpio.021` | X sol — X'in home'u + negatif limiti |
| `gpio.022` | Y arka — Y'nin pozitif limiti |
| `gpio.023` | Y ön — Y'nin home'u + negatif limiti |
| `gpio.024` | Z üst — Z'nin home'u + pozitif limiti |
| — | **Z alt: switch yok** — alt ucu yalnız yumuşak limit korur, o da yalnız homing'den sonra; referanssız Z'nin altını hiçbir şey korumuyor (`MIN_LIMIT = -96.5`, tablanın ~3,3 mm üstü) |

**Jog yönleri:** X+ = sağ, Z+ = yukarı, **Y+ = arka** (operatörden uzağa; 2026-11-12, Faz 2 — önce DIR arızası yüzünden belirsizdi).

---

## Makine parametreleri

**Ölçek Faz 3'te ölçüldü (2026-11-12):** X **100.085**, Y **98.592**, Z **122.274** adım/mm (yeni = eski × komut ÷ ölçüm, iki tur; doğrulama üç eksende de %1 içinde). GRBL EEPROM'undan gelen değerler (`docs/07`, aşağıda, tarihçe) bu mekanik için hiç kalibre edilmemişti: X ve Y'de ~8,3×, Z'de ~10,2× yüksekti:

```
SCALE   X = 833.300    Y = 823.500    Z = 1250.000   adım/mm   (GRBL $100-102 — yanlış, kullanılmıyor)
```

LinuxCNC'deki son değerler (2026-11-12): hız X/Y 10, Z 5 mm/s, ivme 50 mm/s², strok Faz 4'te ölçüldü (aşağıda). GRBL'in dokunulmamış fabrika varsayılanları (tarihçe; **güvenilmezdi**):
```
$110-112 = 500 mm/dk    $120-122 = 10 mm/sn²    $130-132 = 200 mm strok
```

**Sürücüler: TB6560** (dört tane: X, Y×2, Z). Optokuplör tavanı **15 kHz** (üretici rakamı) → %20 payla 12 kHz, ölçülen ölçekte X ~7190, Y ~7300, Z ~5890 mm/dk; ayarlı en yüksek hızlar (X/Y 10, Z 5 mm/s) yalnız ~1,0 / 0,99 / 0,61 kHz — tavan artık bağlamıyor.
Hız ve ivmenin gerekçesi: eski ölçekle makine fiilen ~9 mm/s ve ~84 mm/s² ile bütün gün çalışmıştı (en alttaki bölüm). Mikro adımı düşürmek gerekmiyor (`docs/09`).

**Motorlar:** Japan Servo KH42KM2, NEMA 17, 1.8° = 200 adım/tur.

---

## Tamamlananlar (Faz 1–8 ayrıntısı: aşağıdaki «Sıradaki adımlar» ve en alttaki 2026-11-12 bölümü)

- [x] Faz 0 — Upstream recon, 10 doküman
- [x] Git repo + GitHub (public)
- [x] Pi 3 elendi (Ethernet USB arkasında, `hm2_eth` için uygun değil)
- [x] Live USB + persistence
- [x] Kart bulundu, IDROM okundu, 7i96→7i92 düzeltmesi
- [x] Config yazıldı, hatasız yükleniyor
- [x] Makine enable, üç stepgen aktif (2026-11-12'den beri dört: Y'nin ikinci sürücüsü `stepgen.03`)
- [x] **Üç eksen de jog ediyor**
- [x] Switch'ler bağlandı ve pin haritası çıkarıldı; HAL net'leri 2026-11-12'de bağlandı, beşi de elle tetiklenerek doğrulandı (Faz 5)
- [x] Paket sağlığı: 10 saniyede 0 hata (`packet-error-total` sabit); Faz 8'de dört program koşusunda da 0

---

## Sıradaki adımlar (güncel sıra ve kapılar `PLAN-BUGUN.md`'de: ölçek ve strok, switch net'lerinden önce) — **1–6 tamamlandı, 2026-11-12**

**1. ✅ Y ekseni DIR hattı** — çözüldü (2026-11-12): sebep kablo değil, AXIS 1'e paralel bağlı iki Y sürücüsüydü; ikincisi artık AXIS 3'te. ~~AXIS 1 bloğunda (`GND STEP DIR 5V`) DIR kablosunu ve bağlantısını kontrol et.~~
~~Y'nin TB6560'ında `CW−` bağlı mı, `CW+` 5V'a gidiyor mu. X ve Z ile karşılaştır.~~ Tezgah ortak katot bağlı çıktı: 5V klemensi boş, sürücülerin `CLK−`/`CW−` uçları GND'de, kart çıkışları 3,3 V lojik (`docs/09`).
Ölçüm (DC, GND'ye göre): DIR yüksek seviyesi tek sürücülü AXIS 0'da 3,11 V (düşük 0,2 V), iki sürücülü AXIS 1'de 2,43 V; STEP boşta X'te 3,21 V, ortak AXIS 1'de 2,55 V → bir 3,3 V çıkış iki optokuplörü süremiyor.

**2. ✅ Y yönü belirlendi** (Y+ = arka) → switch ataması netleşti: `gpio.022` (arka) Y'nin pozitif limiti, `gpio.023` (ön) Y'nin home'u + negatif limiti.

**3. ✅ HAL'de switch net'leri bağlandı** (Faz 5, `test-rig.hal:301-327`, `.in_not` ile) — güncel sırada ölçek (4) ve strok (5) **bundan önce** yapıldı (`PLAN-BUGUN.md` Faz 3–5):
```
gpio.021 → X home + negatif uç (sol)      gpio.020 → X pozitif uç (sağ)
gpio.023 → Y home + negatif uç (ön)       gpio.022 → Y pozitif uç (arka)
gpio.024 → Z home + pozitif limit
```
Her eklem için `HOME_IGNORE_LIMITS = YES` (home ve limit aynı switch). Makine açıkken her switch elle basılınca `joint N on limit switch error` verdi (N: X sol/sağ 0, Y ön/arka 1, Z üst 2); bir eklem limitte dururken makine açılmıyor.

**4. ✅ Ölçek** (Faz 3): X 100.085, Y 98.592, Z 122.274 adım/mm; doğrulama X −%0,45, Y sol +%0,59 / sağ −%0,61, Z +%0,66 (kapı: %1).

**5. ✅ Gerçek strok ölçüldü** → soft limitler (Faz 4): X 3..399, Y 3..387, Z −96,5..−3 mm; kullanılabilir strok 396 / 384 / 93,5 mm, her limit bir switch tetiklenmesinin ya da tablanın en az 3,3 mm içinde.

**6. ✅ Homing** (Faz 6, tekrarlanabilir). ⚠ Sıralama: **Z → Y → X**. Kaynak `configs/test-rig/test-rig.ini`:
`[JOINT_2] Z HOME_SEQUENCE = 0`, `[JOINT_1] Y = 1`, `[JOINT_0] X = 2`.
Kritik olan Z'nin ilk olması (takım geri çekilsin); X ile Y arasındaki sıra
güvenlik açısından fark etmiyor. **LinuxCNC INI'ye uyar, dokümana değil** — bu iki
kaynak çelişirse INI kazanır.
`HOME_SEARCH_VEL` son hâli X/Y −5,0, Z +4,0 mm/s (latch ±0,2). `HOME_IGNORE_LIMITS = YES` olduğu
için switch bulunamazsa **durduracak hiçbir şey yok** (`ini-homing.adoc:46`). Tekrarlanabilirlik `joint.N.motor-offset` ile (homing sonrası DRO hep HOME'u gösterir): Z 0,0001, Y 0,030, X 0,0017 mm. Faz 7 ✅, Faz 8 ✅ (şerhli: Z kayması), Faz 9 sırada (`PLAN-BUGUN.md`).

---

## Bilinen zararsız uyarılar

| Mesaj | Durum |
|---|---|
| `iopl() failed ... parallel port access is not allowed` | Paralel port, kullanmıyoruz |
| `pin_new(...) called with already-initialized memory` | `hal_lib.c:690` — `return` yok, pin normal oluşuyor. IO 0/1 = SSerial pinleri, hiçbir yere bağlı değil. 2026-11-12: her açılışta (başarılılarda da) `gpio.000.in_not` ve `gpio.001.in` için görülüyor; belleğin neden önceden dolu olduğu bilinmiyor (iş listesinde) |

---

## Açık sorular

Tam liste: [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) (2026-11-12'de test tezgahı için B8–B12 eklendi: Z kayması, Y kareleme, siyah ekran, Wi-Fi, arayüzün gözlemci modu)

| # | Soru | Durum |
|---|---|---|
| B1 | Mermer makinesinde Y'de kaç motor | 🟡 amber — B2 çözüldüğü için artık mimari değiştirmiyor (test tezgahında Y'de iki motor çıktı, 2026-11-12) |
| B2 | Encoder pinleri GPIO olur mu | ✅ **EVET** (FPGA tarafı) — 5 izole + 9 encoder klemensi = 14 giriş; encoder klemenslerinin ön ucu ohmmetreyle doğrulanmadı |
| B3 | AXIS blokları diferansiyel mi | ✅ Hayır, tek uçlu `GND/STEP/DIR/5V` |
| B4 | Sürücü sinyal gerilimi | ✅ Test tezgahı: TB6560, optokuplörlü; kartın 3,3 V çıkışıyla ortak katot sürülüyor, 5V klemensi boş, bir çıkış = bir sürücü (2026-11-12 ölçümü; önce "5V" yazıyordu). Mermer makinesi açık |
| B5 | Saat frekansı doğru mu | 🟢 100 MHz, IDROM'dan okunuyor, blocker değil |
| B6 | Girişler 24V kabul ediyor mu | 🟡 Açık — test tezgahında 5V ile çalışıyor |
| B7 | İkinci izole 24V kaynak | 🔴 Mermer makinesi için gerekli, alınmadı |
| — | **Y DIR hattı** | ✅ **Çözüldü (2026-11-12):** iki Y sürücüsü tek çıkıştaydı; ikincisi AXIS 3 / `stepgen.03`'te |

---

## Mermer makinesi (beklemede — önce test tezgahı bitecek, aşağıdaki 2026-10-18 notu)

Arkadaşı envanteri dolduruyor (`docs/01-machine-survey.md`).
En kritik satır: **Y ekseninde kaç motor.** B2 çözüldüğü için artık yeniden tasarım
gerektirmiyor ama INI'yi belirliyor.

İkinci izole 24V kaynak alınmalı — su pompası (OUT1) ve VFD 0-10V onsuz çalışmaz.

**Karar (2026-10-18):** mermer makineleri **Zhulong kartında kalıyor.** Önce test
tezgahı `PLAN-BUGUN.md`'deki Faz 1–9'un hepsiyle eksiksiz bitirilecek, mermere
ondan sonra geçilecek. (2026-11-12: Faz 1–8 geçti, Faz 9 sırada.)

---

## Not (2026-10-19) — Mac'te simülatör, laptop'taki INI

- Final kontrol bilgisayarı kararı: **Raspberry Pi 4**.
- Mac'te LinuxCNC 2.9.9 simülatörü kuruldu: [`sim/`](../sim/README.md) (Docker, arm64 = Pi 4 mimarisi). Kart, motor ve gerçek zamanlı çekirdek yok; arayüz ve config mantığı orada denenir. Faz 1–8 yine makinede.
- Simülatörde bulundu: `test-rig.ini` `[EMC]VERSION = 1.0` idi. LinuxCNC 2.9 açılışta `update_ini` penceresi çıkarır; onaylanırsa INI'yi yerinde `1.1`'e çevirir ve `configs/test-rig/test-rig.old/` yedeği açar (`scripts/linuxcnc.in:489-501`). Repoda `1.1` yapıldı.
- **Laptop'ta (`~/better-cnc`) bir sonraki açılışta `git pull`'dan ÖNCE `git status`.** `test-rig.ini` değişmiş ve `test-rig.old/` varsa pencere 2026-08'de onaylanmış demektir; `git diff configs/test-rig/test-rig.ini` yalnız VERSION satırını göstermeli. Başka fark varsa makinede çalışan config repodakinden farklıdır — dur, karşılaştır. Yerel değişiklik dururken `git pull` yeni bir `test-rig.ini`'yi çekemez.
- **2026-10-20:** Kiosk (güç → LinuxCNC → bizim arayüz, çıkış yok) ve Mac'ten SSH erişimi hazır: [`deploy/`](../deploy/README.md), simülatörde denendi. Makinede **henüz kurulmadı** (2026-11-12: Mac'ten SSH ile erişiliyor, kiosk hâlâ kurulu değil); ilk adım makine başında bir kez SSH kurulumu (`deploy/machine.sh bootstrap`). Kiosk test tezgahında Faz 1–9 bitmeden açılmayacak (axis gerekiyor). SSH kurulduktan sonra açılıştaki `git pull` bırakılır; kod `deploy/machine.sh sync` ile gider.
- **2026-10-20 (öğleden sonra):** Kartla bağlantı koparsa (watchdog) artık LinuxCNC'yi yeniden başlatmak gerekmiyor: «Yazılım sıfırla» bağlantıyı yeniden kurar ve **referansları siler** (konum kaymış olabilir). Ekrandaki "watchdog/io_error" ifadeleri operatörün anlayacağı dile çevrildi. Simülatörde gerçek `halcmd` ile denendi; makinede ilk denemesi bekleniyor.
- **2026-10-20 (gece):** Kiosk'ta operatör tarayıcıyı görmüyor: Tab da kapatıldı (odak çerçevesi tarayıcıyı ele veriyordu), ipucu balonları kaldırıldı. Chromium'un kendi sayfaları ("Aw, Snap", "bağlanılamıyor") değiştirilemiyor; ekran bekçisi (`browserwatch`) onları hemen arayüzle, başlatma ekranıyla ya da "Bir hata oluştu — lütfen bekleyin" sayfasıyla değiştiriyor. Simülatörde sekme çökünce ~80 ms'de arayüz geri geldi, "Aw, Snap" hiçbir karede yok. Makinede ilk kurulumda bakılacak.
- **2026-10-21 (denetim):** efe6de4 denetlendi (13 alan, her bulgu iki bağımsız çürütücüyle). En önemlisi: gerçek sürücü kablo kopunca `has_bit` değil `io_error` kaldırıyor; makine AÇIK kalıyordu ve «Yazılım sıfırla» hiçbir şeyi temizlemeyecekti — simülatörün taklidi tersini yapıyordu. Düzeltildi (arayüz bağlantı kopunca makineyi 1 s içinde acil stopa alıyor; sıfırlama gerçek sıralamayla), taklit gerçeğe çevrildi, simülatörde gerçek `halcmd` ile yeniden doğrulandı. Ayrıca: takılma dedektörü program koşarken yanlış alarm verebilirdi (CPU zamanı eklendi); SSH kurulumu sshd'yi anahtarlar gelmeden açıyordu (sıra değişti); bekçi kendi hatasında ölünce tarayıcı da çıkıyordu (döngü + hata yakalama); durma sebebi operatöre ulaşmıyordu (yeni arayüzün ilk mesajı). Makinede henüz hiçbiri denenmedi.

---

## 2026-11-12 — test tezgahında Faz 1–8

Makine başında, MSI anakartlı masaüstü PC'de (live USB). Ham veri ve ölçüm kayıtları: [`board-dumps/rig-measurements-2026-11-12.json`](board-dumps/rig-measurements-2026-11-12.json). Aşağıdaki sayılar kalıcı; yukarıdaki satırlar bunlara göre güncellendi.

**Faz 1 — Y DIR (geçti).** Y'de iki motor, iki TB6560 var; ikisi de AXIS 1'deydi. Ölçüm (DC, GND'ye göre): DIR yüksek seviyesi AXIS 1'de 2,43 V, tek sürücülü AXIS 0'da (X) 3,11 V (düşük 0,2 V); STEP boşta X'te 3,21 V, ortak AXIS 1'de 2,55 V. Kartın çıkışları 3,3 V lojik; bir çıkış iki optokuplörü süremiyor. Çözüm: ikinci Y sürücüsü AXIS 3 = `stepgen.03`'e (IO 10/11) taşındı, `num_stepgens=4` (`test-rig.ini:193`); HAL'in sonundaki blok `stepgen.01`'in `[JOINT_1]` ayarlarını `stepgen.03`'e aynen verir, komut ve enable ortak. Kapı geçti: Y iki yöne gidiyor, iki sayaç eşit kalıyor. AXIS 3 = StepGen 3 fiziksel olarak da doğrulandı: Faz 3 doğrulamasında portalın iki yanı da ~50 mm gitti (sol 50,3, sağ 49,7). Tezgah **ortak katot** bağlı: 5V klemensi boş, sürücülerin `CLK−`/`CW−` uçları GND'de, kart çıkışları 3,3 V lojik — `docs/09`'daki ilk (ortak anot) şemadan farklı.

**Faz 2 — yönler.** X+ = sağ, Y+ = arka (operatörden uzağa), Z+ = yukarı.

**Faz 3 — ölçek (geçti).** Son değerler X **100.085**, Y **98.592**, Z **122.274** adım/mm. GRBL'den gelen 833.300 / 823.500 / 1250.000 bu mekanik için hiç kalibre edilmemişti: X ve Y'de ~8,3×, Z'de ~10,2× yüksekti. Yöntem: yeni değer = önceki değer × komut ÷ ölçüm.
- Kaba tur (komut → ölçüm, mm): X 7,4225 → 62,4 ve 7,0770 → 59,0; Y 4,3610 → 36,0; Z 1,5812 → 16,2. Ara değerler: X 99,5, Y 99,8, Z 122,0.
- İnce tur: X 49,6902 → 49,4; Y 49,9873 → 50,6; Z 25,2566 → 25,2.
- Doğrulama (kapı: %1): X 50,026 → 49,8 (−%0,45); Y 50,005 → sol 50,3 (+%0,59) / sağ 49,7 (−%0,61); Z 25,035 → 25,2 (+%0,66).

**Hızlar ve diğer INI değerleri (son hâl, INI'de).** Eklem ve eksen `MAX_VELOCITY` X/Y 10, Z 5 mm/s; `MAX_ACCELERATION` 50 mm/s²; `STEPGEN_MAX_VEL` 12/12/6, `STEPGEN_MAX_ACC` 60; `[DISPLAY]`/`[TRAJ]` `MAX_LINEAR_VELOCITY` 10, `DEFAULT_LINEAR_VELOCITY` 3. `FERROR` 0,5 / `MIN_FERROR` 0,05 (eski `MIN_FERROR` 0,005 bir adımın, ~0,01 mm, altındaydı; eski `FERROR` 0,05 ~5 adımdı). Homing: `HOME_SEARCH_VEL` X/Y −5,0, Z +4,0; `HOME_LATCH_VEL` ±0,2 (değişmedi); `HOME` 5 / 5 / −5; `HOME_OFFSET` 0; `HOME_IGNORE_LIMITS` YES; `HOME_SEQUENCE` Z 0, Y 1, X 2. `STEPLEN`/`STEPSPACE` 10000 ns ve `DIRSETUP`/`DIRHOLD` 50000 ns değişmedi; çalışma anında daha uzun darbelerle yapılan bir deneme hiçbir şeyi değiştirmedi. Gerekçe: eski ölçekle makine fiilen ~9 mm/s ve ~84 mm/s² ile bütün gün çalışmıştı; 5 mm/s ve 50 mm/s²'de homing'in durma mesafesi 0,25 mm, switch'lerin ~1,3 mm'lik fazla yoluna karşı.

**Faz 4 — strok.** Homing öncesi oturum çerçevesinde switch tetiklenme noktaları: X sol −287,93 (üç yaklaşma 0,03 mm içinde), X sağ 115,43; Y ön −271,82, Y arka 119,53; Z üst 100,32 (bırakma 99,79); tabla oturumun ~0'ında. Tetiklenmeden tetiklenmeye X 403,4, Y 391,3 mm; Z üst switch'ten tablaya ~100 mm. Makine çerçevesinde (orijin = latch bırakma kenarı, `HOME_OFFSET` 0): X tetiklenmeleri −0,64 / +402,72; Y −0,67 / +390,68; Z üst tetiklenme +0,52, tabla ~−99,8. **Yumuşak limitler:** X 3..399, Y 3..387, Z −96,5..−3 → kullanılabilir strok 396 / 384 / 93,5 mm; her limit bir switch tetiklenmesinin ya da tablanın en az 3,3 mm içinde. Faz 4'teki bırakma kenarı tahminlerinde yoklama gecikmesi var; homing sonradan küçük bir switch histerezisi gösterdi (Z'de tetiklenme–bırakma ~0,06 mm). Y'nin iki yanının kareleme (racking) kontrolü ertelendi.

**Faz 5 — switch net'leri (geçti).** `test-rig.hal:301-327`. Girişler boşta HIGH, basınca LOW → net'ler `.in_not` ile: `gpio.021` X sol → home + negatif limit, `gpio.020` X sağ → pozitif limit; `gpio.023` Y ön → home + negatif, `gpio.022` Y arka → pozitif; `gpio.024` Z üst → home + pozitif (Z altta switch yok). Bir eklem limitte dururken makine açılmıyor. Makine açıkken her switch elle basılınca `joint N on limit switch error` verdi: N = 0 (X sol), 0 (X sağ), 1 (Y ön), 1 (Y arka), 2 (Z üst). LinuxCNC 2.9 Override Limits'i hareket bir sonraki kez yerine oturur oturmaz kaldırıyor (`control.c:1278-1282`): limitte duran eksenden kurtulmak için override'dan hemen sonra jog ile switch'ten çıkılmalı.

**Faz 6 — homing (geçti).** Home All sırası Z → Y → X. Tek eklem: Machine → Homing → Home <eksen>, ya da ekseni seçip Home tuşu; Manual sekmesindeki düğme Home All'dur. Tekrarlanabilirlik `joint.N.motor-offset` ile ölçüldü, çünkü homing sonrası DRO hep HOME'u gösterir: Z 0,0001, Y 0,030, X 0,0017 mm. X'in motor kablosundaki aralıklı temassızlık onarılmadan önce X tek yönde 0,047 / 0,132 mm kayıyordu (adım kaybı); onarımdan sonra 0,0017 mm.

**Faz 7 — yumuşak limitler (geçti).** MDI `G1 X999` / `X-50` / `Y999` / `Y-50` / `Z10` / `Z-200` `F300` hepsi motion tarafından reddedildi: `Linear move on line N would exceed joint J's positive/negative limit` + `invalid params in linear command` (`command.c:261-267`, `:1003`). Hiçbir eksen kıpırdamadı.

**Faz 8 — ilk program (şerhle kabul).** Yeni dosya [`nc_files/faz8-kare.ngc`](../nc_files/faz8-kare.ngc): X10 Y10'dan başlayan 50 mm kare, F300. Z hareketleri `G53` ile, çünkü G54 ofsetleri sıfır ve Z penceresi −96,5..−3 (örnek programdaki `G0 Z5` reddedilirdi). Dört kez hatasız koştu, hm2 paket hatası 0 kaldı. Üç koşudan sonra homing motor-offset'leriyle adım kaybı kontrolü: Y 0,0001, X 0,036, Z 0,066 mm — Z tek yönde, koşu başına ~0,02 mm kayıyor. Şüphe: Z için 50 mm/s² ivme yerçekimine karşı fazla. İş listesinde: 25 mm/s² dene, tekrarla.

**İşletme bulguları.**
- TERM ile kapatmalardan sonra LinuxCNC bazen sahipsiz bir motion SysV paylaşımlı bellek bölümü bırakıyor (key `0x00000064`, nattch 0). Sonraki açılış AXIS'te "A configuration error is preventing LinuxCNC from starting" ile düşüyor (`s.joints` beklemesi zaman aşımına uğruyor, `axis.py:3554-3562`). Çare: hiçbir LinuxCNC süreci yokken, başlatmadan önce o bölümü `ipcrm` ile silmek; kiosk'ta bunu `deploy/kiosk-session` her LinuxCNC başlatmasından önce `deploy/kiosk.sh clean-shm` ile yapıyor. Kiosk kurulana kadar (axis'le) elle yapılır: komutlar `deploy/README.md`'de.
- Binadaki elektrik kesintisinden sonra PC yalnız GRUB failsafe modunda kullanılabilir açılıyor (`noapic noapm nodma nomce nosmp nosplash vga=788`, tek CPU). Normal modda masaüstü arka planından sonra ekran kararıyor; persistence (`/dev/sda2`, ext3) temiz. Monitör AMD iGPU'da (HDMI); PC'de ayrıca bir NVIDIA GA102 var. Failsafe'te servo-thread `tmax` ~0,52 ms, paket hatası 0 — önceki normal modun (çok çekirdek) 3,4 ms'sinden iyi. Tek çekirdekte Firefox açmak `tmax`'ı 1,25 ms'ye çıkardı.
- USB Wi-Fi çubuğu sık kopuyor. PC'nin IP'si 192.168.1.104'ten .107'ye değişti (`deploy/machine.local`). Erişim noktası istemcilerin MAC adresini değiştirdiği için ARP PC'yi tanıtamıyor; makine adı `debian` kullanılmalı.
- Her LinuxCNC açılışında debug logunda `HAL: ERROR: pin_new(hm2_7i92.0.gpio.000.in_not)` ve `(gpio.001.in) called with already-initialized memory` var; başarılı açılışlarda da çıkıyor, sebebi bilinmiyor, zararsız görünüyor ("Bilinen zararsız uyarılar").

**Arayüz gerçek makinede (salt izleme).** `ui/bettercnc` depo dışındaki bir izleyiciyle AXIS'in yanında yalnız izleme için çalıştırıldı (gerçek `LinuxCncAdapter`, komut kanalı kesik, hata kanalı AXIS'te; ekrandan yalnız acil stop gidebiliyordu). Görülen kusurlar (ayrıntı: `docs/11-ui-implementation.md` "Known gaps"):
1. Yumuşak limitler "ölçülmedi" / "Yumuşak limitler tanımsız" görünüyor — INI'deki ASSUMPTION işaretleri yüzünden. (Faz 9'da düzeltildi: işaretler kalktı.)
2. Y DIR arıza notu ve "Y switch uçları bilinmiyor" metni eskimiş (`profiles.py`). (Faz 9'da düzeltildi.)
3. Alarmlar ekranında canlı switch durumu yok (gerçek adaptör yalnız bağlantı pinlerini okuyor). (Faz 9'da eklendi: Alarmlar'da canlı switch durumu; yalnız sahte LinuxCNC ile test edildi.)
4. Adım zamanlaması canlı HAL'den değil INI'den gösteriliyor. (Faz 9'da eklendi: Makine'de canlı step zamanlaması; yalnız sahte LinuxCNC ile test edildi.)
5. Homing'den sonra durum kapısı "Program başlatılabilir" derken yanında "limitler tanımsız" uyarısı duruyor. (Faz 9'da düzeltildi: işaretler kalkınca uyarı gitti.)
6. Depoda gözlemci modu yok: depodaki uygulama `io_error` varken sayfa yüklenince `state(STATE_ESTOP)` gönderiyor (`_stop_on_link_fault`) ve NML hata kanalını boşaltıyor (`_drain_errors`); ikisi de AXIS'in yanında çalışmayla çelişiyor. (Faz 9'da eklendi: `--observe` — hata kanalı açılmıyor, ekrandan yalnız yazılım acil stop'u gidiyor; kart bağlantısı koparsa giden acil stop bilerek kaldı. Yalnız sahte LinuxCNC ile test edildi, makinede denenmedi.)
7. Küçük yerleşim sorunları.

**Açık iş listesi (2026-11-12 sonrası):**
- Z ivmesi kayması: 25 mm/s² dene, Faz 8 kontrolünü tekrarla (OPEN-QUESTIONS B8).
- X motor kablosunun dayanıklılığı (onarılan temassızlık geri gelmesin).
- Y'nin iki yanının kareleme (racking) kontrolü (B9).
- Normal modda siyah ekran — ekran kartı / açılış (B10).
- Wi-Fi çubuğunun güvenilirliği ya da kablolu LAN (B11).
- Arayüz: `--observe` ile canlı switch durumu ve step zamanlaması makinede AXIS'in yanında denenecek (B12); küçük yerleşim sorunları (`docs/11` "Known gaps").
- BIOS / gecikme ayarı (failsafe parametreleri daha iyi gecikme verdi).
- `gpio.000`/`001` `pin_new` uyarısı.
- Kiosk henüz kurulmadı.
