# sim/ — Mac'te LinuxCNC simülatörü

Gerçek **LinuxCNC 2.9.9**, Mac'te Docker içinde, **arm64** üzerinde çalışır.
Raspberry Pi 4'ün mimarisi de bu. Kart, motor ve gerçek zamanlı çekirdek yok;
bunların yerine aşağıdaki taklitler var. Amaç, yazılım tarafını (arayüz,
`LinuxCncAdapter`, INI/HAL mantığı) makineye ve USB'ye gitmeden denemek.

```bash
make sim-build     # bir kez: imajı kur (~2.9 GB, birkaç dakika)
make sim           # LinuxCNC + arayüz → http://localhost:8770/
make sim-stop      # durdur
make sim-test      # bütün test paketi imajın içinde (arm64, Python 3.13, gerçek linuxcnc modülü)
```

Diğer komutlar `sim/sim.sh` içinde: `logs` (arayüz çıktısı), `log` (LinuxCNC
logu), `halcmd ...`, `shell`. Repo konteynere **salt okunur** bağlanır; Mac'te
yapılan değişiklik konteynerde anında görünür. INI, HAL ya da arayüz kodu
değişince `make sim-stop && make sim` ile yeniden başlat.

## Ne gerçek, ne taklit

| Gerçek | Taklit / yok |
|---|---|
| LinuxCNC 2.9.9 (linuxcnc.org deposu, imzası doğrulanmış) | Kart yok: `hm2_eth` yüklenmez |
| Rig'in INI'si, birebir: `configs/test-rig/test-rig.ini` (yalnız `[HAL]HALFILE` ve `[DISPLAY]DISPLAY` değişir, `make_sim_ini.py`) | Step yok: `motor-pos-cmd` doğrudan `motor-pos-fb`'ye döner → **SCALE ve step zamanlaması denenmez** |
| motmod, trivkins, homing, yumuşak limitler, estop_latch zinciri, task/io | Beş switch `sim_home_switch` ile; konumlar **uydurma**, rig'in stroku ölçülmedi |
| Arayüz `--adapter linuxcnc` ile, gerçek NML ve HAL üzerinden | Bağlantı pinleri (`hm2_7i92.0.*`) `hm2_link_stub.py`'den: değerler okuma değil |
| Python 3.13, Debian 13, arm64 | Gerçek zamanlı çekirdek yok → **gecikme/zamanlama ölçümü anlamsız** |

Switch'ler HAL'de **PLAN-BUGUN Faz 5'teki planlı hâliyle** bağlı (X düzeltilmiş),
rig'deki gibi yorum satırı değil; yoksa homing denenemezdi. Y'nin hangi ucunun
minimum olduğu bilinmiyor (Y DIR arızası); simülatör Y'yi X gibi varsayar.

## Arıza canlandırma

```bash
sim/sim.sh halcmd sets watchdog TRUE                  # watchdog ısırdı → LinuxCNC ESTOP
sim/sim.sh halcmd sets watchdog FALSE                 # temizle, sonra arayüzden "Yazılım sıfırla"
sim/sim.sh halcmd setp hm2_7i92.0.packet-error-total 7
sim/sim.sh halcmd setp hm2_7i92.0.io_error 1
sim/sim.sh halcmd setp hm2_7i92.0.sim-link-down 1   # kablo hâlâ takılı değil: sıfırlama tutmaz
```

Arayüzün «Yazılım sıfırla»sı kart bağlantısı bayraklarını kendisi `halcmd` ile indirir
(rig'de de öyle); `sets watchdog FALSE` artık gerekmiyor.

## 2026-10-19'da bulunanlar

- `test-rig.ini`'de `[EMC]VERSION = 1.0` vardı. LinuxCNC 2.9 `1.1` dışındaki her
  değerde `update_ini`'yi çalıştırır (`scripts/linuxcnc.in:489-501`): ekran yoksa
  açılmaz, ekran varsa onay penceresi çıkarır ve onaylanırsa INI'yi **yerinde
  değiştirip** yanına `test-rig.old/` yedeği açar. Repoda `1.1` yapıldı. Makinedeki
  laptop kopyası bu yüzden repodan farklı olabilir (`docs/DURUM.md` sonundaki not).
- Arayüzde iki tarayıcı hatası (MDI alanına tıklamak boş komut gönderiyordu; MDI
  odaktayken sayfa her saniye MDI'ya geri kayıyordu, acil stop butonuna
  çıkılamıyordu) — düzeltildi, `docs/11` "What is NOT covered" madde 3.
- Üç açık daha bulundu ve 2026-10-20'de düzeltildi: %150 butonu, HAL kaynaklı
  e-stop'ta eski kalan mesaj, LinuxCNC'den önce başlatılan arayüz — `docs/11`
  "Found in `sim/` on 2026-10-19 and fixed".
- Arayüz, LinuxCNC'den **sonra** ve LinuxCNC'yi başlatan kullanıcıyla (aynı
  `HOME`) başlatılmalı: LinuxCNC'nin Python modülü takım tablosunu
  `$HOME/.tool.mmap`'te arar; ilk denemede bulamazsa o süreç bir daha durum
  okuyamaz. Pi 4'te servis kurarken de geçerli.
- `docs/11` "Next steps"teki makine kontrol listesinin hepsi burada geçti; rig'de
  yine yapılacak, önce zamanlama.

## Neden Docker, neden 2.9.9

Mac'te Docker Desktop zaten kurulu ve Linux'u arm64 olarak yerel hızda çalıştırıyor;
ayrı bir VM'e gerek yok. Laptop'taki live USB 2.9.10, ama 2.9.10'un arm64 paketi
yok (linuxcnc.org'da en yenisi 2.9.9, Debian 13'ün kendi paketi 2.9.4; 2026-10-19).
Aradaki fark hata düzeltmeleri. Docker'ın varsayılanında olmayan tek yetki
`IPC_OWNER`: setuid root çalışan `rtapi_app`'in, başka kullanıcının açtığı ortak
belleğe erişmesi için gerekli.
