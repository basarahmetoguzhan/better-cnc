# ui/ — Operator arayüzü

`design/prototype-source.jsx`'in düzenini ve görsel dilini alıp, **gerçek donanım
verileriyle** yeniden uygulanmış hâli.

Tam gerekçe ve prototipte düzeltilen her gerçeğin listesi:
**[docs/11-ui-implementation.md](../docs/11-ui-implementation.md)**

---

## Çalıştırma

```bash
make ui              # test tezgahı profili, http://127.0.0.1:8080/
make ui-marble       # mermer profili — neyin TODO olduğunu görmek için
make dump SCREEN=makine > /tmp/makine.html   # sunucusuz tek ekran
```

`make` olmadan: paket kurulu değil, `ui/` import yoluna eklenmeli.

```bash
PYTHONPATH=ui python3 -m bettercnc.app --profile test-rig --port 8080
```

Makine bağlantısı **yok** — bu sürüm `MockAdapter`'dan render ediyor. Gerçek
makinede tek satır değişiyor (`app.build_session`):

```python
adapter = LinuxCncAdapter(profile)   # MockAdapter(profile) yerine
```

## Test

```bash
make test            # HER ŞEY: ui/tests + scripts/test_grbl2ini.py
make test-v          # ayrıntılı
make coverage        # satır kapsaması (stdlib, `coverage` paketi gerekmiyor)
```

315 test, hepsi geçiyor. Kapsama **%94.0**.

## Neden saf stdlib

Hedef makine bir LinuxCNC live USB — üzerinde hiçbir üçüncü parti Python paketi
yok ve bir hatayla teşhis arasına `pip install` koymak istemiyoruz. `linuxcnc`
modülü de PyPI'da olmayan, makineyle gelen bir CPython modülü. Dolayısıyla:

- **Python + stdlib**, başka hiçbir şey
- **Sunucu tarafı HTML** — `unittest` ile string olarak doğrulanabiliyor,
  tarayıcı veya DOM gerekmiyor
- `unittest` (pytest kurulu olan bir makinede `pytest` de çalışır)

## Katmanlar

```
app.py          stdlib http.server
render.py       view model -> HTML          (sunucu tarafı)
viewmodel.py    (profil, config, state) -> dict     ← testlerin çoğu burada
adapter.py      LinuxCNC sınırı: MachineAdapter / MockAdapter / LinuxCncAdapter
state.py        estop, homing, link health
configmodel.py  eksen/zamanlama değerleri — INI'den
profiles.py     test-rig ve marble tanımları
capabilities.py yetenek kataloğu
iniload.py      INI ayrıştırma + "ölçülmedi" tespiti
```

`configmodel.py`'nin üstünde **hiçbir donanım sayısı sabit yazılı değil** —
hepsi `configs/*/**.ini`'ye ya da `profiles.py`'deki bir `docs/` atfına
dayanıyor.

## Tasarımın iki kuralı

**1. Olmayan yetenek gizlenmez, devre dışı görünür.**
Render `CAPABILITY_CATALOGUE`'u dolaşır, profilin ne beyan ettiğini değil. Bir
yeteneği beyan etmeyi unutan profil `validate_profile`'da **hata verir** —
sessizce kaybolmaz. (`docs/MERMER-GECIS.md:43-45`)

**2. Ölçülmemiş sayı gösterilmez.**
INI'de strok değerleri var (LinuxCNC'nin bir şeye ihtiyacı var) ama
`# ASSUMPTION:` ile işaretli. `iniload` bu işareti okuyor, UI "ölçülmedi"
yazıyor. Strok ölçülüp yorum silindiğinde UI **kod değişikliği olmadan**
sayıları göstermeye başlar.

## Profil eklemek

`profiles.py`'de bir `MachineProfile` döndüren fonksiyon yaz, `PROFILES`'a
ekle. `validate_profile` her katalog yeteneğini beyan etmeye zorlar; bilmiyorsan
`todo(...)` kullan — `absent(...)` "baktım, yok" demektir.
