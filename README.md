# Telegram Grup Dışa Aktarıcı

> **English summary.** A read-only exporter that writes the messages of a Telegram (super)group you are a member of into a plain-text file, using your own account through Telethon. Media is not downloaded, only labelled; runs resume from the last exported message.
> The rest of this README is in Turkish.

Üyesi olduğun bir Telegram (süper)grubundaki mesajları **salt okuma** ile `.txt`
dosyasına aktarır. Medya indirilmez, yalnız türü etiketlenir.

## ⚠️ Önce sen yapacaksın: API kimlik bilgileri

Betik çalışmadan önce **senin** iki değeri girmen gerekiyor:

1. <https://my.telegram.org> → **API development tools** → uygulama oluştur.
2. `api_id` ve `api_hash` değerlerini al.
3. `cp .env.example .env` ve dosyayı doldur.

```bash
cp .env.example .env
$EDITOR .env
```

İlk çalıştırmada Telethon telefon numaranı ve Telegram'ın gönderdiği kodu
soracak (iki adımlı doğrulama açıksa şifreni de). **Bunları terminalde sen
gireceksin**; betik bunları saklamaz, sonuç `*.session` dosyasına yazılır.

## ❌ Bot token ile ÇALIŞMAZ

`Please enter your phone (or bot token):` sorusuna BotFather'dan aldığın bot
token'ını girme. Botlar Telegram'da geçmiş mesaj okuyamaz; MTProto bot
hesapları için `GetHistoryRequest`'i reddeder:

```
BotMethodInvalidError: The API access for bot users is restricted.
The method you tried to invoke cannot be executed as a bot (caused by GetHistoryRequest)
```

Botu gruba eklemek veya yönetici yapmak bunu değiştirmez. Grubun **üyesi olan
kendi hesabınla** (telefon numarası + kod) giriş yapmalısın. Betik bot oturumu
tespit ederse çıkış kodu 3 ile uyararak durur.

## 🔐 Session dosyası hakkında

`telegram_export.session` dosyası **Telegram hesabına tam erişim sağlar**.
Şifre kadar gizlidir:

- Kimseyle paylaşma, commit etme, yedeğe/bulut senkronuna koyma.
- Betik dosyayı otomatik olarak `600` (yalnız sahibi okur/yazar) izinlerine çeker.
- İş bitince oturumu kapat:

```bash
python export_telegram.py --logout
```

Bu komut `client.log_out()` çağırır ve `.session` / `.session-journal`
dosyalarını siler. `.gitignore` zaten `.env`, `*.session` ve çıktı dosyalarını
dışarıda tutuyor.

## Kurulum

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt
```

veya:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Kullanım

```bash
# 0) Grup forum (başlıklı) mu? Başlıkları listele
.venv/bin/python export_telegram.py --list-topics

# 1) Küçük test — 20 mesaj
.venv/bin/python export_telegram.py --limit 20 --output test.txt

# 2) Tam dışa aktarma (tüm grup)
.venv/bin/python export_telegram.py --output grup_tam.txt

# 3) Tek bir forum başlığı
.venv/bin/python export_telegram.py --topic 1310 --output baslik_1310.txt

# 4) Belirli tarihten itibaren
.venv/bin/python export_telegram.py --since 2026-01-01 --output 2026.txt
```

### Parametreler

| Parametre | Açıklama |
|---|---|
| `--chat` | Grup: `-1001234567890`, `@kullaniciadi` veya `https://t.me/c/.../...` linki. Verilmezse `.env` içindeki `CHAT_ID`. |
| `--topic` | Forum başlık ID'si. Yalnız o başlığı çeker (`reply_to=<topic_id>`). |
| `--output` | Çıktı dosyası (varsayılan `telegram_export.txt`). |
| `--since` | `YYYY-MM-DD` veya `YYYY-MM-DD HH:MM` — **İstanbul saati** olarak yorumlanır. |
| `--limit` | En fazla N mesaj (test için). |
| `--list-topics` | Forum başlıklarını listeler ve çıkar. |
| `--state` | State dosyası yolu (varsayılan `<output>.state.json`). |
| `--session` | Session dosya adı (varsayılan `telegram_export`). |
| `--logout` | Oturumu kapat, session dosyasını sil. |

## Çıktı biçimi

```
[YYYY-MM-DD HH:MM] #mesaj_id [konu: Başlık] ↳#yanıtlanan_id Gönderen: mesaj metni
    çok satırlı mesajın devamı girintilidir
```

Örnek (uydurma veri):

```
[2026-09-07 21:03] #1302 [konu: Kitap alım satım] Ayşe K.: İkinci baskısı elinde olan var mı?
[2026-09-07 21:11] #1318 [konu: Mikrobiyoloji çalışma grubu] Mehmet: 1. hafta notları
[2026-09-07 21:11] #1319 [konu: Mikrobiyoloji çalışma grubu] Mehmet: [belge: 01-mikrobiyoloji-notlar.pdf] Birinci hafta
    ikinci satırı olan mesajın devamı
[2026-09-07 20:50] #1294 Mehmet: [sistem: TopicCreate]
```

- `[konu: ...]` yalnız forum gruplarında ve `--topic` verilmediğinde eklenir
  (tek başlık aktarırken gereksiz olurdu).
- Gönderen: **ad + soyad** → yoksa **@kullanıcı adı** → yoksa kanal başlığı →
  yoksa `Bilinmeyen`.
- Tarihler `Europe/Istanbul`, dosya UTF-8.
- Medya etiketleri: `[fotoğraf]`, `[video]`, `[belge: ad]`, `[ses: ad]`,
  `[sesli mesaj]`, `[yuvarlak video]`, `[GIF]`, `[çıkartma: emoji]`, `[anket: soru]`,
  `[konum]`, `[kişi]`, `[zar]`. **Medya dosyası indirilmez.**

### Forumda `↳` ile `[konu:]` farkı

Telegram forum gruplarında `reply_to` alanı iki şeyi birden taşır. Ölçülmüş
davranış (`reply_to_top_id` alanına bakarak ayırt edilir):

| `forum_topic` | `reply_to_top_id` | `reply_to_msg_id` anlamı |
|---|---|---|
| `True` | `None` | **Başlık ID'si** — mesaj yanıt değil, sadece o başlıkta |
| `True` | `N` | `N` başlık, `reply_to_msg_id` **gerçek yanıt** |
| yok / `False` | — | Sıradan yanıt |

Ayrıca forumda `reply_to` alanı **hiç bulunmayan** iki durum var:

- **Başlığın kök mesajı** (`TopicCreate`): mesaj ID'si = başlık ID'si.
- **General başlığı** (bu grupta adı *Duyuru*): başlık ID'si her zaman `1`.

Betik ikisini de doğru etiketler. Bu kural olmasaydı bu gruptaki 44 satır
(33 kök mesaj + Duyuru'daki 11 mesaj) başlıksız kalırdı.

Bu yüzden `↳#id` yalnız gerçek yanıtlarda yazılır; başlık üyeliği `[konu: ...]`
olarak gösterilir. (Bu ayrım yapılmazsa forumdaki her mesaj "yanıt" gibi görünür.)

## Kaldığı yerden devam

Her koşuda son yazılan mesaj ID'si `<output>.state.json` içine kaydedilir
(chat + topic başına ayrı anahtar). Betiği tekrar çalıştırdığında Telethon
`min_id` ile **yalnız yeni mesajları** dosyanın sonuna ekler:

```bash
.venv/bin/python export_telegram.py --output grup_tam.txt   # ilk koşu
# ... bir hafta sonra ...
.venv/bin/python export_telegram.py --output grup_tam.txt   # yalnız yeniler eklenir
```

Baştan almak istersen `<output>.state.json` dosyasını sil (ve gerekiyorsa
`.txt` dosyasını da).

> Not: Devam koşusunda `--since` etkisizdir — Telethon'da `offset_id`,
> `offset_date`'e göre önceliklidir ve devam ederken ID kullanılır.

## Dayanıklılık

- `FloodWaitError` yakalanır: Telegram'ın istediği süre + 5 sn beklenir, sonra
  son yazılan ID'den itibaren devam edilir. Yazılanlar kaybolmaz.
- Her 200 mesajda dosya `fsync` edilir ve state güncellenir; `Ctrl-C` güvenlidir.
- İlerleme: TTY'de `tqdm` çubuğu, boru hattında her 500 mesajda bir log.

## Salt okuma garantisi

Betikte mesaj gönderme/silme/düzenleme veya üye işlemi yapan hiçbir çağrı yok.
Kendin doğrula:

```bash
grep -nE "send_message|send_file|delete_messages|edit_message|forward_messages|pin_message|kick_participant|edit_admin|ban_participant" export_telegram.py
```

Kullanılan tüm Telegram çağrıları: `get_entity`, `iter_messages`,
`GetForumTopicsRequest` (hepsi okuma) ve yalnız `--logout` ile `log_out`.

## Sürüm notu

Telethon 1.45'te `GetForumTopicsRequest` `functions.channels` altından
`functions.messages` altına taşındı ve `channel` parametresi `peer` oldu.
Betik iki sürümü de destekler (çalışma anında imzaya bakar).

## Lisans

[MIT](LICENSE). Bu proje Telegram ile bağlantılı değildir. Dışa aktardığın mesajlar başkalarının kişisel verisini içerir; paylaşmadan önce düşün.
