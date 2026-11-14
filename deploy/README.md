# deploy/ — kiosk ve makineye uzaktan erişim

İki iş:

1. **Kiosk:** makineye güç verilince LinuxCNC açılır, ekrana yalnız bizim arayüz
   gelir. Operatör arayüzden çıkamaz: masaüstü yok, tarayıcı çerçevesi yok,
   konsola geçiş yok.
2. **Uzaktan erişim:** Mac'ten SSH ile makinede komut çalıştırmak, kodu USB'siz
   göndermek, makinenin arayüzünü Mac'te görmek.

Hedef: şimdi test tezgahının live USB'si (LinuxCNC 2.9.10, Debian 13 amd64) — hangi PC'ye
takılırsa (2026-08 bir Monster laptop, 2026-11-12 MSI anakartlı bir masaüstü PC) —, sonra
Raspberry Pi 4 (arm64). Kod hepsinde aynı; simülatör (`sim/`) Pi'nin mimarisinde çalışıyor.

---

## Kiosk nasıl çalışıyor

```
güç → laptop: BIOS USB'den / Pi: SD'den → Linux → systemd
  └ bettercnc-kiosk.service  (tty1, makinenin normal kullanıcısı)
      └ cage                 masaüstü yok, pencere yöneticisi yok, VT geçişi kapalı
          └ deploy/kiosk-session
              ├ bettercnc.bootpage :8079   "başlatılıyor" / "LinuxCNC durdu: sebep"
              ├ bettercnc.browserwatch     Chromium --kiosk → :8079; kapanırsa 1 s'de, sayfası ölürse
              │                            ~10 s'de yeniden açar; Chromium'un kendi sayfasını (çöken
              │                            sekme, "bağlanılamıyor") hemen bizim sayfamızla değiştirir
              └ döngü: linuxcnc <makine>-kiosk.ini          durursa sebebi yaz, 5 s sonra yeniden
                    └ [DISPLAY] = deploy/bettercnc-display
                          └ bettercnc.app --adapter linuxcnc :8080 (yalnız 127.0.0.1)
                                LinuxCNC yarı canlıysa (bir parçası öldü) 3 s'de kapanır → temiz yeniden başlatma
```

- **Arayüz, LinuxCNC'nin kendi DISPLAY programı.** LinuxCNC hazır olunca onu
  başlatır, aynı kullanıcıyla ve aynı HOME ile. Arayüzün istediği iki koşul
  (docs/11, "Deployment rule") böylece yapı gereği sağlanır. Arayüz çökerse
  LinuxCNC kapanır (hareket durur) ve döngü ikisini birlikte yeniden başlatır.
- **Kiosk INI'si** (`make_kiosk_ini.py`) makinenin INI'sinin birebir kopyası;
  yalnız `[DISPLAY]DISPLAY` farklı. Kaynak INI'nin yanına yazılır, böylece HAL,
  parametre dosyası ve takım tablosu axis'le çalışırkenkilerin aynısı olur. Her
  başlatmada yeniden yazılır ve git'e girmez.
- **Başlatma ekranı** (`bettercnc.bootpage`) LinuxCNC'ye hiç dokunmaz. Bu yüzden
  her LinuxCNC yeniden başlatmasından sağ çıkar ve ekranda her zaman doğru bir
  şey yazar.
- **Bağlantı koparsa** arayüz donmaz. 3 s içinde ekranı "BAĞLANTI YOK — ekrandaki
  değerler ESKİ" ile örter, 10 s sonra başlatma ekranına döner (`app.js`). Başlatma
  ekranı o an cevap vermiyorsa gitmez, örtülü bekler; yoksa Chromium'un
  "bağlanılamıyor" sayfası görünürdü. Örtünün üstünde «YAZILIM ACİL STOP GÖNDER»
  düğmesi durur (2026-10-21 denetimi: örtü bu tezgahın tek acil stopunu da
  kapatıyordu): sunucu ölü değil yavaşsa komut gider — sunucu acil stopu kilit
  beklemeden işler — ulaşıp ulaşmadığı bilinmiyorsa ekran öyle yazar.
- **Kontrol sistemi durunca sebebi** başlatma ekranına ve yeni açılan arayüzün ilk
  mesaj satırına yazılır (`$XDG_RUNTIME_DIR/bettercnc/last-stop.txt`,
  `app.py --startup-note-file`): başlatma ekranındaki "durdu" satırı yalnız 5 s
  yaşıyor ve sayfa 10 s'den kısa kesintide başlatma ekranına hiç gitmiyordu, yani
  operatör sebebi hiç görmüyordu (denetim). LinuxCNC'nin kendi logu: tty'siz
  çalışırken `/tmp/linuxcnc.print.*` / `.debug.*` (`ls -t | head -1`), hatayla
  çıkınca `~/linuxcnc_print.txt` ve `~/linuxcnc_debug.txt`; `$RUN/linuxcnc.log`
  yalnız betiğin ilk satırlarını tutar.
- LinuxCNC, X ekranı (`DISPLAY`) ve terminal olmadan başlatılır. Aksi hâlde
  LinuxCNC betiği hata ve kilit dosyası durumlarında Tk pencereleri açıp tıklama
  bekler; simülatörde bu yaşandı (`kiosk-session` içindeki yorum).

### Neler kilitli

Hedef: operatör tarayıcıda olduğunu **görsel olarak anlamaz**. Sekme, adres çubuğu,
menü görmez; başka bir sayfaya gidemez; arayüzü kapatamaz ya da geçemez. Makineyle
ilgili görebileceği tek şey bu arayüzdür (SSH dışında). Dört katman var, her biri
diğeri atlatılsa da tutacak şekilde:

| Katman | Ne yapar | Nasıl sınandı |
|---|---|---|
| **Klavye haritası** (`files/xkb`, XKB seçeneği `bettercnc:kiosk`) | Ctrl, Alt, Windows, Menü, F1–F24 ve tarayıcı tuşları (Geri, İleri, Yenile, Ana sayfa, Ara, Kapat…) **hiçbir şey üretmez**. Ctrl+T, Ctrl+N, Ctrl+L, Ctrl+W, Alt+F4, F5, F11, F12 yazılamaz. Tab da üretmez: odağı düğmeden düğmeye gezdirip sayfanın tarayıcı olduğunu ele verirdi. Harfler, rakamlar, Enter, Backspace çalışır; Sağ Alt yalnız AltGr'dır (Türkçe klavyede MDI için `#` `[` `]`). Kapatma kısayolları (Ctrl+W, Ctrl+F4, Ctrl+Q) burada doğar: harita onları üretmez | libxkbcommon'un kendisiyle: harita derlenip tuşlara "basılıyor" (`test_deploy.TestKioskKeymap`, konteynerde koşar) |
| **Sayfa** (`app.js`, başlatma ekranı; yalnız kiosk modunda) | Sağ tık menüsü, sürükleme, seçme, yakınlaştırma, farenin geri/ileri tuşları yok; sayfanın durdurabildiği her kısayol ve Tab durdurulur. Tarayıcı izleri yok: üzerine gelince çıkan ipucu balonları (`title`) kaldırılır, odak çerçevesi çizilmez, imleç düz metin üzerinde ok (düğmede el, giriş kutusunda I), kaydırma çubuğu arayüzün renginde | Haritayı atlayan sanal klavyeyle (`wtype`) Ctrl+T/N/L/W/P, Ctrl+Shift+I, F11, F12, Alt+F4 gönderildi: ekran değişmedi, tarayıcı kapanmadı. Tuşların gerçekten sayfaya ulaştığı, o sırada engellenmeyen Tab+Enter ile kanıtlandı (Tab sonra kapatıldı) |
| **Chromium politikası** (`files/chromium-policy.json`) | Yalnız `127.0.0.1:8079`, `:8080` ve bekleme sayfası (`file:///usr/share/bettercnc/wait.html`); `chrome://`, başka `file://`, kaynak görüntüleme yasak. Yeni sekme, yeni pencere ve ana sayfa = bizim başlatma ekranı. Adres çubuğundan arama yok, açılır pencere yok, geliştirici araçları, yazdırma, indirme, çeviri, oturum açma, eklenti yok. Tarayıcının tema rengi arayüzün zemin rengi (`BrowserThemeColor`): yeni açılan sekmenin ilk boş karesi denemelerin yaklaşık yarısında beyaz yerine koyu lacivert olur | Politika bağlıyken kiosk açıldı ve geçişler çalıştı |
| **Ekran bekçisi** (`bettercnc.browserwatch`, Chromium'un DevTools borusu) | Chromium'un kendi sayfaları değiştirilemez ("Aw, Snap", "bağlanılamıyor", "engellendi"); bekçi onları ekranda tutmaz. Sekmeleri 0,5 s'de bir, bir sekme çökünce hemen yoklar. Çöken sekme, sunucusu cevap vermeyen bizim adresimizdeki hata sayfası hemen; bizim olmayan başka her sayfa 1,5 s sonra kapatılır. Yerine sırayla: cevap veriyorsa arayüz, değilse başlatma ekranı, o da değilse sunucusuz bekleme sayfası (`files/wait.html`: "Bir hata oluştu — lütfen bekleyin. Ekran kendiliğinden geri gelecek."), o da sunuculardan biri cevap verince geri döner. Boru yalnız bekçinin elinde; ağ portu açılmaz. Politika gereği sayfanın içine girilemez (geliştirici araçları kapalı), yalnız sekme açıp kapatabilir | Simülatörde, ~17 ms arayla ekran görüntüsüyle (aşağıda "Simülatörde denemek") |

Bunlara ek olarak:

| | |
|---|---|
| Masaüstü, pencere yöneticisi | yok (`cage` tek uygulamayı tam ekran gösterir) |
| Ctrl+Alt+F1…F6 (konsol) | kapalı (`cage` VT geçişine yalnız `-s` ile izin verir; servis vermez; Ctrl ve Alt zaten üretilmiyor) |
| Tarayıcıyı kapatmak / sekmenin çökmesi | `browserwatch`: kapanırsa 1 s'de yeniden açar; sekme çökerse ekran bekçisi hemen değiştirir; sayfa ölüp de bekçi göremezse ~10 s'de tarayıcıyı yeniden açar |
| Başlatma ekranı sunucusu ölürse | 1 s'de yeniden başlar (yoksa Chromium'un "bağlanılamıyor" sayfası görünürdü) |
| SysRq, Ctrl+Alt+Del | kapalı (`files/90-bettercnc-kiosk.conf`, `ctrl-alt-del.target` maskeli) |
| Laptop kapağı, uyku tuşları | hiçbir şey yapmaz (`files/logind-bettercnc-kiosk.conf`; açılıştan sonra geçerli). Güç tuşu temiz kapatır |
| Ağdan arayüz | kapalı: arayüz yalnız 127.0.0.1'de dinler, Mac'ten SSH tüneliyle görülür |

**Tam ekran, sekme çubuğu yok:** `--kiosk` adres çubuğunu, sekme çubuğunu ve pencere
düğmelerini hiç çizmez; bekçi bir an ikinci sekme açtığında da görünen tek şey sayfadır.
cage tek uygulamayı tüm ekrana koyar. Simülatör görüntüleri operatörün gördüğü ekranın
tamamıdır.

**Geçişlerde animasyon yok:** ekranlar arasında ara kare yok. Arayüz ↔ "BAĞLANTI YOK"
örtüsü ↔ başlatma ekranı geçişleri ~17 ms arayla çekilen görüntülerde tek adımda değişti.
Bekçi sekme değiştirdiğinde (çöken sekme) ve tarayıcı her açıldığında, sayfamız çizilmeden
önce yeni sekmenin boş hâli **tek bir kare** (~16 ms) görünür. Tema rengiyle bu kare
denemelerin yaklaşık yarısında koyu lacivert, yarısında hâlâ beyaz (tema rengi olmadan
10/10 beyazdı). `--force-dark-mode` ve `--default-background-color` hiçbir şey değiştirmedi.
Tamamen gidermenin yolu bulunamadı. Sekme çökmesi normal çalışmada olmaz.

**Kapatılamaz:** operatörün bir sekmeyi ya da tarayıcıyı kapatma yolu yok: kapatma
düğmesi ve sekme çubuğu yok; Ctrl+W, Ctrl+F4, Alt+F4 ve Ctrl+Q yazılamaz, çünkü klavye
haritası Ctrl ve Alt'ı üretmez. Harita atlanarak gönderildiklerinde de (yukarıdaki
`wtype` denemesi) tarayıcı kapanmadı; bunun sebebi — kiosk modunda Chromium'un yok
sayması mı, sayfanın engellemesi mi — ayrıştırılmadı, politikada kısayol ayarı yok,
cage'de Alt+F4 gibi bir pencere yöneticisi kısayolu yok. Olur da kapanırsa bekçi 1 s'de
yeniden açar. Tarayıcı yine
de kapanırsa ya da çökerse arkasında masaüstü yok (`kiosk.sh enable` masaüstünü kapatır):
ekran 1–2 s siyah kalır, tarayıcı kendiliğinden geri gelir. Bekçi kendisi de son sekmeyi
hiç kapatmaz: önce yenisini açar. Bekçinin kendisi ölürse tarayıcı da çıkar (boru
kapanır); `kiosk-session` bekçiyi de döngüde tutar ve bekçi kendi hatasını yakalayıp
tarayıcıyı yeniden açar (denetim 2026-10-21).

Bizim olmayan bir sayfa, bizim sunucularımızın dışında bir adreste açılabilirse (üç
katman atlatılırsa) en çok ~2,5 s görünür (0,5 s bakış + bir bakışlık liste gecikmesi +
1,5 s eşik); bizim hiç sekmemiz kalmamışsa ve son 5 s içinde bir değiştirme yapılmışsa
`REPLACE_AGAIN_S` dolana kadar.

Kilitli **olmayanlar**: fiziksel erişim (USB bellek, SD kart, BIOS) ve güç anahtarı.

### Bakım için çıkış yolları

1. **SSH** (olağan yol): `sudo deploy/kiosk.sh disable --now`.
2. **Çekirdek parametresi** `bettercnc.kiosk=0`: laptop'ta GRUB menüsünde `e` ile
   satır sonuna eklenir; Pi'de SD kartın `cmdline.txt`'sine başka bir bilgisayarda
   eklenir. O açılışta kiosk başlamaz.
3. **Dosya** `/etc/bettercnc/kiosk-disabled`: varken kiosk başlamaz.

### LinuxCNC açılmıyorsa: sahipsiz paylaşımlı bellek

2026-11-12'de makinede görüldü: LinuxCNC TERM ile kapatıldıktan sonra bazen motion'ın SysV
paylaşımlı bellek bölümünü sahipsiz bırakıyor — key `0x00000064` (`[EMCMOT]SHMEM_KEY`'in
varsayılanı 100, `emcmotcfg.h:49`; test tezgahının INI'si başka değer vermiyor), `ipcs -m`'de
`nattch` 0. Sonraki açılış düşüyor: axis "A configuration error is preventing LinuxCNC from
starting" diyor (`s.joints` hiç gelmiyor, `axis.py:3554-3562`). Kiosk'un yeniden başlatma
döngüsü de bu bölüm kaldıkça her denemede düşerdi. Bu yüzden kiosk'un başlatma döngüsü
(`deploy/kiosk-session`) her LinuxCNC başlatmasından önce `deploy/kiosk.sh clean-shm`
çalıştırır: hiçbir LinuxCNC süreci yoksa key `0x00000064`, `nattch` 0 bölümünü siler.
Elle (axis'le çalışırken) aynı komut, LinuxCNC'yi başlatan kullanıcıyla sudo'suz
(bölüm o kullanıcınındır, `uspace_common.h:103-119`):

```bash
deploy/kiosk.sh clean-shm
```

ya da adım adım:

```bash
ipcs -m                                      # key 0x00000064, nattch 0 → sahipsiz
pgrep -a 'rtapi_app|linuxcncsvr|milltask'    # hiçbir şey basmamalı
sudo ipcrm -M 0x00000064
```

LinuxCNC çalışırken silmeyin: bölüm o zaman sahipsiz değildir.

---

## Kurulum sırası

### 1. SSH — makine başında bir kez

Mac'te:

```bash
deploy/machine.sh bootstrap
```

Bu komut ekrana tek bir satır basar, örneğin `curl -fsSL http://192.168.1.103:8765/b | bash`.
O satırı makinede, normal kullanıcının terminalinde yazın. Betik
(`bootstrap-ssh.sh`) şunları yapar:
- SSH sunucusunu kurar.
- **Şifreyle girişi kapatır.** Live USB'nin şifresi "live", herkesin bildiği bir şifre.
  Sıra önemli (denetim 2026-10-21): Debian sshd'yi paket kurulur kurulmaz şifreli
  girişe açık başlatır; betik bu yüzden önce anahtarları çekip anahtar-yalnız ayarı
  yazar, SONRA paketi kurar ve kurulu yapılandırmanın etkin değerini `sshd -T` ile
  doğrular (drop-in `Include` ile okunuyor mu) — şifreli giriş açık görünürse sshd'yi
  durdurup hata verir. Anahtar gelmezse hiçbir şey kurulmaz.
- GitHub'daki açık anahtarları (`github.com/basarahmetoguzhan.keys`, Mac'inki dahil)
  yetkilendirir.
- Sonunda kullanıcı adını, adresi ve `sudo`'nun şifre sorup sormadığını basar.

Sonra Mac'te `deploy/machine.local` dosyasını oluşturun (git'e girmez):

```
CNC_HOST=192.168.1.xx
CNC_USER=user
```

Ev ağındaki adres DHCP'den gelir ve değişebilir (2026-11-12: 192.168.1.104 → .107; USB Wi-Fi
çubuğu da sık kopuyor, OPEN-QUESTIONS B11). Değişince `CNC_HOST`'u güncelleyin. Erişim noktası
istemcilerin MAC adresini değiştirdiği için PC ARP tablosundan tanınamaz; makinenin adı
`debian` — onu arayın (ad Mac'te çözülüyorsa `CNC_HOST=debian` de olur).

### 2. Mac'ten

```bash
deploy/machine.sh info
```

`info` şunları gösterir: sistem, LinuxCNC sürümü, açılış satırı (persistence var mı),
ağ arayüzleri, masaüstü, makinedeki reponun durumu.

```bash
deploy/machine.sh sync
```

`sync` önce deneme yapar ve neyin kopyalanacağını listeler. Uygulamak için:

```bash
deploy/machine.sh sync --apply
```

`--apply`, önce makinedeki çalışma ağacının kendi git HEAD'inden farkını
`~/better-cnc-yedek/<zaman>/` altına yedekler (ilk sync'ten sonra bu farkın çoğu
önceki sync'lerin getirdikleridir; makinede elle yapılmış bir değişiklik için
emniyet ağı, daha incesi değil), sonra Mac'teki çalışma ağacını kopyalar:
- `.git`, `reference/`, `.claude/`, `deploy/machine.local`, `*-kiosk.ini` ve
  `docs/PLAN-BUGUN.md` kopyalanmaz (plan, git'e girmediği gibi makineye de gitmez).
- Makinede hiçbir dosya silinmez.

Makine artık açılışta `git pull` yapmamalı; kod `sync` ile gider.

```bash
deploy/machine.sh ui
```

Makinedeki arayüzü SSH tüneliyle Mac'te http://localhost:8781/ adresinde açar.

### 3. Makinede (SSH üstünden)

```bash
sudo deploy/kiosk.sh board-network
```

Kartın adresini kalıcı yapar (10.10.10.1/24), **hangi PC olursa olsun.** Live USB PC'den PC'ye
gidiyor ve her PC Ethernet portuna başka ad veriyor (ağustostaki laptop `enp3s0`, 2026-11-12'deki
PC `enp12s0`). Bu yüzden profil (`bettercnc-board`) bir ada bağlı değil: kablolu her portta geçerli
(USB adaptörleri, `enx…`, hariç) ve öncelikli. Yani **Ethernet portu kartındır**; kartın kablosu
takılınca profil kendiliğinden devreye girer. Ev ağı ve SSH Wi-Fi'dan gelir. Kabloyla internet
gerekirse ağ menüsünden "Wired connection"ı seçin (ya da `nmcli connection up "Wired connection 1"`).
Tek portlu, Wi-Fi'ı çalışmayan bir PC'de kart takılıyken Mac'ten SSH olmaz: ev ağı için bir USB
Wi-Fi çubuğu ya da USB Ethernet adaptörü gerekir (kart PC'nin kendi portunda kalır: `hm2_eth(9)`
USB arkasındaki ağ kartlarını uygun saymaz).

Komut açık bir bağlantıyı kesmez: SSH o porttan geliyorsa profili kurar ama açmaz, kart kablosu
takılınca açılır. Kablosu takılı ve boşta tek bir port varsa onu hemen açar. Birden çok Ethernet
portu olan PC'de `--iface <port>` profili o porta bağlar ve hemen açar (o porttan SSH geliyorsa
bağlantı kopar). Kartın bağlantısı hiçbir zaman varsayılan rota olmaz, yani interneti kesmez.

```bash
sudo deploy/kiosk.sh install
```

`cage`, `chromium` ve DejaVu yazı tipi paketlerini, servisi, PAM tanımını, Chromium
politikasını, sysctl ve logind ayarlarını, bekleme sayfasını
(`/usr/share/bettercnc/wait.html`) kurar. Açılışı değiştirmez. Seçenekler: `--user`,
`--ini`, `--profile`, `--layout` (tr), `--tz` (Europe/Istanbul; ekrandaki alarm
saatleri bu dilimde — live USB UTC'de çalışır). Servis ağı beklemez
(`network-online.target` yok: kiosk ağ istemez, beklemek menzil dışı bir Wi-Fi'da
30–60 s siyah ekran demekti).

```bash
sudo deploy/kiosk.sh enable
```

Bir sonraki açılıştan itibaren kiosk çalışır; hemen geçmek için `--now`. Geri almak
için `disable`; bu, kiosktan önceki masaüstünü geri açar.

### Güç verilince gerçekten açılması için (live USB'li PC)

- BIOS'ta açılış sırası: USB önce.
- Live USB'nin GRUB menüsünde **varsayılan** satır persistence'lı olmalı ve kısa
  bir zaman aşımı olmalı. Persistence'sız açılırsa kurduğumuz hiçbir şey yoktur.
  `deploy/machine.sh info` → "açılış" satırında `persistence` görünmeli.
- **2026-11-12'deki MSI PC, binadaki elektrik kesintisinden beri yalnız GRUB'un failsafe
  satırıyla kullanılabilir açılıyor** (`noapic noapm nodma nomce nosmp nosplash vga=788`,
  tek CPU). Normal satırda masaüstü arka planından sonra ekran kararıyor; persistence
  (`/dev/sda2`, ext3) temiz; monitör AMD iGPU'da (HDMI), PC'de ayrıca bir NVIDIA GA102 var
  (OPEN-QUESTIONS B10). Sebep bulunana kadar kiosk ancak failsafe satırı varsayılan ve
  persistence'lı olursa güç verilince açılır — yukarıdaki `info` kontrolü onda da geçmeli.
- Gecikme: failsafe'te servo-thread `tmax` ~0,52 ms, paket hatası 0 (önceki normal açılışta,
  çok çekirdekte 3,4 ms). Ama tek çekirdekte Firefox açmak `tmax`'ı 1,25 ms'ye çıkardı — servo
  periyodu 1 ms. Kiosk'un Chromium'u aynı tek çekirdeği paylaşacak: `enable`'dan önce makinede
  ölçülmeli (`servo-thread.tmax`).
- Pi 4'te bu sorunlar yok: SD karttan doğrudan açılır.

---

## Test tezgahında ne zaman açılmalı

**Faz 1–9 (PLAN-BUGUN) ve PC deneme listesi bitmeden açmayın.** (2026-11-12: Faz 1–8 geçti;
bekleyenler Faz 9, arayüzün makinede denenmesi ve PC'nin normal açılışı, OPEN-QUESTIONS B10.)
Kiosk axis'in yerini alır. Fazlarda axis'in kullanılan iki özelliği arayüzde yoktu; ikisi de
**kapandı** (Mac'te, sahte LinuxCNC ve simülatörle; makinede henüz denenmedi):

- ~~Faz 6.2: eksenleri tek tek home'lama~~ — kapandı (2026-11-14): her eksenin çipinde
  «Z referansla» (docs/11, "M"), simülatörde denendi (`sim/e2e/test_homing.py`).
- ~~Faz 8: G-code programı açıp koşturma~~ — kapandı (2026-11-14): Program ekranı
  `PROGRAM_PREFIX` dizinini listeler ve seçileni yükler; duraklat / devam / tek adım da var
  (docs/11, "H"); simülatörde yüklenip sonuna kadar koşturuldu (`sim/e2e/test_program.py`,
  `sim/e2e/test_run.py`; kiosk'ta da aynı liste).

AXIS ile arayüz arasında elle geçiş (kiosk servisi olmadan) ve makinede yapılacak denemelerin
tam listesi: [docs/12-operator-handover.md](../docs/12-operator-handover.md).

`install` şimdiden yapılabilir. **`enable` kararı** o listenin 22. adımıdır: önceki adımlar
makinede geçtikten ve kullanıcı karar verdikten sonra; ilk kiosk açılışında kısa duman testiyle.
Mermer makinesinde (Pi 4) kiosk baştan açık olacak.

---

## Süreler

"X saniye olunca şu olsun" kurallarının hepsi burada: değer, neden o değer,
kısa ya da uzun seçilirse ne olur. **Ölçülecek** işaretli olanlar tahmin; makinede
(laptop'ta, sonra Pi 4'te) ölçülüp düzeltilecek.

Her birinin dayandığı iki sayı:
- **Normal en yavaş cevap ~1,2 s.** Bir eylem oturum kilidini en çok "Yazılım sıfırla"da
  tutar: acil stoptan çıkma beklemesi 0,3 s, açma beklemesi 0,25 s, referans silme
  0,25 s, birkaç `halcmd` ve komut gönderimi. O sırada gelen ekran yenilemesi de
  bunu bekler.
- **Anormal: 5 s.** LinuxCNC'nin görev süreci takılırsa tek bir komut gönderimi 5 s'ye
  kadar bekleyebilir (`emcmodule.cc:209`).

### Ekran (tarayıcı, `ui/bettercnc/static/app.js`)

| Ad | Değer | Neden | Kısa olursa / uzun olursa |
|---|---|---|---|
| `POLL_EVERY_MS` | 1 s | Konum ve durum için yeterli, Pi'yi yormaz | Pi'de yük artar / ekran gecikir |
| `POLL_TIMEOUT_MS` | 2,5 s | Normal en yavaş cevabın (~1,2 s) iki katı | Yoğun anda boşuna "bağlantı yok" / donma geç fark edilir |
| `ACTION_TIMEOUT_MS` | 4 s | Eylemin kendi beklemeleri ~1,2 s; görev takılırsa 5 s'ye varır | Normal eylem "sonuç bilinmiyor" görünür / operatör sonucu geç öğrenir |
| `LOST_MS` | 3 s | Normal en uzun boşluk ~2,2 s (1 s yenileme + 1,2 s yavaş cevap) | Yanlış "bağlantı yok" / donmuş ekran daha uzun canlı görünür |
| `BOOT_MS` | 10 s | Simülatörde LinuxCNC + arayüz yeniden başlaması ~8 s (5 s bekleme + ~3 s açılış). **Ölçülecek** | Her yeniden başlatmada başlatma ekranına gidip gelir / sebep ekranı geç gelir |
| `INSTANT_GUARD_MS` | 0,5 s | ACİL STOP ve DURDUR basışta (pointerdown) gider; aynı basışın ardından gelen tıklama bu süre içinde yok sayılır. Klavyeyle (Enter/Space) basılan, öncesinde basış olmayan tıklama gönderilir. Seçildi, ölçülmedi | Çok uzun bir basışın tıklaması ikinci kez gönderilebilir (zararsız: ikinci durdurma) / klavye basışı bir basıştan hemen sonra yok sayılabilir |
| `SWAP_AFTER_UP_MS` | 0,05 s | Basılıyken gelen ekran yenilemesi bırakıştan bu kadar sonra uygulanır: tıklama olayı bırakışın hemen ardından gelir, düğme o sırada yerinde olmalı. Seçildi, ölçülmedi | Yenileme tıklamadan önce düğmeyi değiştirip tıklamayı kaybettirebilir / ekran bırakıştan sonra biraz geç yenilenir |
| `SWAP_HOLD_MAX_MS` | 3 s | Basılı tutulan bir parmak ya da fare ekranı en çok bu kadar dondurur: `LOST_MS` ile aynı, cevapsız ekranın örtüldüğü süre. Daha uzun basış zaten tıklama değildir | Uzun basışlı tıklama yenilemeye kaybolabilir / yaslanan bir parmak ekranı daha uzun eski gösterir |
| `READY_MARGIN_MS` | 0,03 s | Onay bandındaki «Evet», sorudan `CONFIRM_MIN_S` sonra açılır; bant kaç ms kaldığını söyler (`data-ready-in-ms`), sayfa o an + bu pay kadar sonra bir kez yeniler (P0 temizliği; önceden sonraki 1 Hz yenilemeyi, 1-2 s, bekliyordu). Sunucu kuralı aynı: erken «Evet» cevap sayılmaz. Seçildi, ölçülmedi | Yenileme sunucunun saatinden önce gelir, «Evet» bir sonraki yenilemeye kalır / «Evet» biraz geç açılır |

### Kiosk

| Ad | Değer | Neden | Kısa olursa / uzun olursa |
|---|---|---|---|
| `BETTERCNC_LOST_EXIT_S` (arayüz) | 3 s | LinuxCNC'nin bir parçası ölünce ya da durumu okunamayınca arayüz kapanır, kiosk her şeyi temiz başlatır. Normalde hiç olmaz, geçici bir hâli yok. Görev süreci ölmüşse ekrandaki acil stop makineye **ulaşmaz**; ne kadar çabuk, o kadar iyi. 1 s'de bir kontrol, 3 kontrol üst üste: tek bir okuma hatasıyla kapanmasın | Tek okuma hatasında yeniden başlar / makine yarı canlı ve e-stop'suz daha uzun kalır |
| `BETTERCNC_RESTART_S` | 5 s | LinuxCNC durduktan sonra, yeniden başlatmadan önce: kartın, ağın ve bir önceki oturumun bırakılması için pay. Hata sürekli tekrarlıyorsa döngü işlemciyi yormaz | Kart/ağ hazır olmadan tekrar dener / makine daha uzun kapalı kalır |
| bettercnc-display hazır bekleme | 30 s | LinuxCNC DISPLAY'i en son başlatır, durum o an genelde hazırdır; 30 s geniş pay | — / gerçek bir hata 30 s geç bildirilir |
| `STALE_S` (`browserwatch`) | 10 s | Sayfa saniyede bir sorar; normal en uzun boşluk ~3,5 s (2,5 s zaman aşımı + 1 s) | Yavaş anda tarayıcı boşuna yeniden açılır / çökmüş sekme daha uzun ekranda kalır |
| `BOOT_HANDOFF_S` (`browserwatch`) | 15 s | Arayüz gidince (LinuxCNC yeniden başlıyor ya da arayüz takıldı) sayfa canlıdır ama soracak kimsesi yoktur; `BOOT_MS` (10 s) sonra başlatma ekranına geçer. Sayfaya bu geçiş için tanınan süre: 10 s + yönlendirme ve yükleme payı. İlk sürüm beklemiyordu; simülatörde her arayüz yeniden başlamasında tarayıcıyı da yeniden başlattı | Arayüz her yeniden başladığında tarayıcı da gereksiz kapanır / o sırada çöken sekme daha uzun ekranda kalır |
| `GRACE_S` (`browserwatch`) | 30 s | Chromium'un ilk açılışı. Pi 4'te SD karttan birkaç saniye ile ~20 s arası olabilir. **Ölçülecek** (Pi'de) | Açılmakta olan tarayıcı kesilir, döngüye girer / ilk açılışta çöken sekme geç fark edilir |
| `CHECK_EVERY_S` | 2 s | Sunuculara soru sıklığı; `STALE_S`'yi beşe böler. İlk soru tarayıcı açılır açılmaz | — |
| `GUARD_EVERY_S` (ekran bekçisi) | 0,5 s | Sekme listesine bakma sıklığı; çöken sekme bunu beklemez, anında haber gelir. Yerel boru, maliyeti yok denecek kadar az | — / bizim olmayan sayfa ekranda biraz daha uzun kalır |
| `NOT_OURS_S` (ekran bekçisi) | 1,5 s | Bizim sayfamız yüklenirken başlığı bir an adres olur; yerel sayfalarımız 1 s'nin çok altında yüklenir. Sunucusu cevap vermeyen adresteki hata sayfası bunu beklemez | Yüklenen kendi sayfamız kapatılır / yabancı sayfa daha uzun görünür |
| `REPLACE_AGAIN_S` (ekran bekçisi) | 5 s | Yerine açılan sekme en çok bu sıklıkla: bekçi bir gün yanılsa bile tarayıcıyı sekmeyle doldurmasın diye. Normalde değiştirme bir kez olur | Yanılan bekçi sekme yağdırır / yanılırsa ekran daha uzun bekler |
| `BOOT_WAIT_S` (ekran bekçisi) | 5 s | Tarayıcı açılmadan önce başlatma ekranına cevap vermesi için tanınan süre (güç verilince ikisi aynı anda başlar). Cevap gelmezse tarayıcı bekleme sayfasında açılır. Ekran bu arada siyah | Normal açılışta bir an "Bir hata oluştu" görünür / sunucusu bozuksa ekran daha uzun siyah kalır |
| `LOOP_STALL_S` (adaptör) | 2 s | LinuxCNC'nin görev ve IO döngüleri her turda bir kez uyur; çekirdek bunu sayar (`/proc/PID/status`, simülatörde görev ~87/s, IO ~9/s, donmuş süreç 0). Görev kuyrukta iş varken uyumaz ("eager", emctaskmain.cc:3593; uzun bir program satırı dizisi bunu sürdürebilir), bu yüzden CPU zamanı (`/proc/PID/stat`) da canlılık sayılır: ne uyuyan ne çalışan süreç takılıdır (denetim 2026-10-21). 2 s = görevin 200, IO'nun 20 turu. Görevin kendi heartbeat'i Python'a açık olmadığı için onun yerine geçer. `rtapi_app` yalnız var/yok diye izlenir: `/proc` ana iş parçacığını sayar, o da gerçek zamanlı iş parçacığı çalışırken boşta durur | Ağır yükte boşuna "cevap vermiyor" / takılan görevle ekran ve e-stop daha uzun "canlı" kalır |
| `HALCMD_TIMEOUT_S` (adaptör) | 2 s | `halcmd` onlarca milisaniyede cevap verir; uzarsa HAL kilidi başkasındadır | Yavaş Pi'de bağlantı sıfırlama boşuna "olmadı" der / takılan `halcmd` sıfırlamayı 2 s'den fazla tutar |
| `LINK_SETTLE_S` (adaptör) | 0,5 s | `io_error` temizlenince sürücü kartı yeniden okur: kartın kaydettiği ısırık (`has_bit`) bir-iki servo periyodunda gelir, kablo hâlâ yoksa `io_error` 5 kayıp pakette (5 ms) geri gelir; 0,5 s ikisinin yüzlerce katı | Isırık gelmeden devam edilir, mandal sıfırlamanın ortasında düşer / sıfırlama boşuna uzar |
| `STUCK_LOOKS` (ekran bekçisi) | 20 bakış = 10 s | Tarayıcı boruya hiç cevap vermiyorsa (ana iş parçacığı takılı, sayfa hâlâ soruyor olabilir) `STALE_S` kadar beklenir, sonra yeniden açılır; ölü sayfa gibi ancak `GRACE_S`'den sonra (soğuk Chromium ana döngüsü çalışana dek boruya cevap vermez) | Yüklü anda boşuna yeniden açılır / takılı tarayıcı daha uzun kalır |

### LinuxCNC ve kart (değiştirmedik; sürücülerin kendi değerleri)

| Ad | Değer | Kaynak | Not |
|---|---|---|---|
| hm2 watchdog `timeout_ns` | 5 ms | `watchdog.c:172` | Servo periyodu 1 ms → 5 döngü kaçarsa ısırır. Gecikme ölçülünce gözden geçirilecek |
| hm2_eth `packet-error-limit` | 10 | `hm2_eth.c:1532` | Bu kadar hata birikince kalıcı iletişim hatası |
| hm2_eth `packet-read-timeout` | servo periyodunun %80'i | `hm2_eth.c:1524` | — |
| `WAIT_TIMEOUT_S` (adaptör) | 0,25 s | docs/11 | 25 görev döngüsü; seçildi. **Ölçülecek** (OPEN-QUESTIONS B13) |
| Acil stoptan çıkma süresi | 0,3 s | docs/11 | 3 × `[EMCIO]CYCLE_TIME`; seçildi. **Ölçülecek** (OPEN-QUESTIONS B13) |

## Simülatörde denemek

```bash
sim/sim.sh kiosk
```

Kiosk'un tamamını konteynerde, ekransız (headless) başlatır: cage, Chromium, LinuxCNC
ve arayüz. Makinedeki Chromium politikası da bağlanır.

```bash
sim/sim.sh shot ekran.png
```

Operatörün gördüğü ekranın görüntüsünü alır.

```bash
sim/sim.sh kiosk-down
```

```bash
sim/sim.sh lint
```

`lint`, betikleri shellcheck ile denetler ve servis dosyasını `systemd-analyze verify`
ile doğrular.

2026-10-20'de simülatörde denenenler:
- Açılış: başlatma ekranından ~4 s'de arayüze geçiyor.
- Arayüz öldürülünce: "BAĞLANTI YOK" geliyor, LinuxCNC kapanıyor, 5 s sonra
  ikisi de geri geliyor.
- Tarayıcı öldürülünce yeniden açılıyor.
- INI yoksa başlatma ekranı sebebi tek satır yazıyor.
- Politika dosyası bağlıyken geçiş çalışıyor.
- Kart bağlantısı (gerçek `halcmd` ile, 2026-10-21'den itibaren gerçek sürücü gibi
  canlandırılıyor — kablo çıkınca `has_bit` değil `io_error` kalkar, mandal düşmez,
  LinuxCNC AÇIK der; taklidin ilk sürümü tersini yapıyordu ve sıfırlama makinede
  "zaten açık" deyip hiçbir şeyi temizlemeyecekti): kablo çıkınca arayüz 1 s içinde
  makineyi acil stopa alıp "Kartla bağlantı koptu — makine durduruldu" diyor; kablo
  hâlâ yokken «Yazılım sıfırla» → "bağlantı kurulamadı" (`io_error` 5 ms'de geri
  geliyor); kablo takılıp sıfırlayınca `io_error` temizleniyor, sürücünün o an
  bulduğu ısırık (`has_bit`) temizleniyor, referanslar siliniyor, makine açılıyor
  ("bağlantı yeniden kuruldu"); sıfırlama 0,5–0,8 s sürüyor.
- LinuxCNC'nin görev süreci öldürülünce arayüz 3,85 s'de kapandı, kiosk temiz başlattı;
  başlatma ekranında "Makinenin kontrol sistemi durdu: LinuxCNC'nin bir parçası çalışmıyor
  (milltask)". Tarayıcı yeniden başlatılmadı.
- Donmuş arayüz (`SIGSTOP`): 4,5 s'de örtü, 12,5 s'de başlatma ekranı, devam edince geri.
- Çöken sekme (bütün `--type=renderer` süreçleri `kill -9`), ~17 ms arayla ekran görüntüsüyle:
  ~60 ms'de tek beyaz kare, ~80 ms'de arayüz geri; "Aw, Snap" hiçbir karede yok. (Bekçinin ilk
  sürümü sekme tablosunu yalnız olaylardan tutuyordu; başlık güncellemesi gelmediği için kendi
  sayfalarımızı yabancı sanıp 2 s'de bir kapattı. Şimdi her bakışta listeyi istiyor ve bekleme
  sayfasını başlığına bakmadan tanıyor.) Daha önce, bekçi yokken: "Aw, Snap" 10 s ekranda
  kaldı, sonra tarayıcı yeniden açıldı.
- İki sunucu da kapalıyken tarayıcı yeniden açılınca: siyah → bekleme sayfası → arayüz gelince
  arayüz. Chromium'un "bağlanılamıyor" sayfası hiçbir karede yok (önce ~1,5 s, sunucu
  kontrolünden sonra ~0,3 s görünüyordu; tarayıcı artık cevap vermeyen başlatma ekranında
  açılmıyor).
- Normal açılış: siyah → başlatma ekranı → arayüz; bekleme sayfası görünmüyor.
- 2026-10-21 (denetim sonrası): bekçi öldürülünce `kiosk-session` 1 s'de yeniden başlattı,
  tarayıcı geri geldi; arayüz öldürülünce `last-stop.txt` yazıldı ve başlatma ekranı
  "başlatılıyor (deneme 2). Önceki durma: …" dedi; `kiosk-down` (TERM) 1 s'de bitti
  (eskiden 10 s sonra SIGKILL).
- Geçişler kare kare (~17 ms): arayüz → örtü → başlatma ekranı → arayüz (arayüz `SIGSTOP`/`SIGCONT`)
  ara karesiz. Tarayıcı yeniden açılışı: siyah → tek boş kare → başlatma ekranı → arayüz. Bekçi
  durdurulup sekme çöktürülünce Chromium "Aw, Snap"ı ~66 ms'de çiziyor; bekçi varken o çizilmeden
  yeni sekme öne geçiyor.
- Tıklanan MDI kutusu: çerçevesi renk değiştiriyor, imleç yanıp sönüyor, 1 Hz yenilemede odak
  kaybolmuyor (kiosk modunda, gerçek Chromium'da).
- Donmuş görev süreci (`SIGSTOP`, ölmeden takılma): normal simülatörde 3 s'de ekran "DURUM
  BİLİNMİYOR", konumlar "—", jog komut gönderilmeden reddedildi; kiosk'ta arayüz 5,8 s'de
  kapandı, LinuxCNC donmuş süreci 7,9 s'de temizledi, arayüz 13,7 s'de geri geldi.
- Kısayollar (haritayı atlayan `wtype` ile): Ctrl+T/N/L/W/P, Ctrl+Shift+I, F11, F12, Alt+F4 →
  ekran ve tarayıcı yerinde.

Simülatörde **denenemeyenler**:
- Gerçek klavyenin cage üzerinden haritayla geçişi (harita libxkbcommon'la ayrıca sınandı),
  dokunmatik ekran hareketleri, gerçek farenin sağ tıkı.
- systemd ile açılış: konteynerde systemd PID 1 değil.
- Gerçek ekran kartı.

Bunlar makinede, `enable --now` ile ilk denemede kontrol edilecek.
