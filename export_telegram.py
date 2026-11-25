#!/usr/bin/env python3
"""
export_telegram.py — Telethon ile bir Telegram (süper)grubundaki mesajları .txt'ye aktarır.

SALT OKUMA: Bu betikte mesaj gönderme / silme / düzenleme veya üye işlemi yapan
hiçbir çağrı yoktur. Kullanılan Telegram çağrıları:
  - client.get_entity / get_input_entity   (okuma)
  - client.iter_messages                   (okuma)
  - GetForumTopicsRequest                  (okuma)
  - client.log_out                         (yalnız --logout ile, kullanıcı ister)
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import ForumTopic, MessageService

# GetForumTopicsRequest Telethon 1.45'te functions.channels -> functions.messages'a taşındı
# ve 'channel' parametresi 'peer' oldu. İki sürümü de destekle.
try:  # eski Telethon (<=1.4x, layer <2xx)
    from telethon.tl.functions.channels import GetForumTopicsRequest
except ImportError:  # Telethon 1.45+
    from telethon.tl.functions.messages import GetForumTopicsRequest

_TOPICS_PEER_KW = (
    "peer"
    if "peer" in inspect.signature(GetForumTopicsRequest.__init__).parameters
    else "channel"
)

try:
    from tqdm import tqdm
except ImportError:  # tqdm yoksa sade log'a düşeriz
    tqdm = None

TZ = ZoneInfo("Europe/Istanbul")
LOG_EVERY = 500
FLUSH_EVERY = 200


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[{datetime.now(TZ):%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def secure_file(path: Path) -> None:
    """Dosya izinlerini 600 yap (yalnız sahibi okuyup yazabilsin)."""
    try:
        if path.exists():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        log(f"UYARI: {path} izinleri 600 yapılamadı: {exc}")


def parse_chat(value: str) -> int | str:
    """--chat değerini Telethon'un anlayacağı biçime çevir.

    Kabul edilenler: -1001234567890, 1234567890, @kullaniciadi,
    https://t.me/c/1234567890/1310, https://t.me/kullaniciadi
    """
    value = value.strip()
    m = re.search(r"t\.me/c/(\d+)", value)
    if m:
        return int(f"-100{m.group(1)}")
    m = re.search(r"t\.me/(?:s/)?([A-Za-z][\w\d_]{3,})", value)
    if m:
        return m.group(1)
    if value.startswith("@"):
        return value
    if re.fullmatch(r"-?\d+", value):
        n = int(value)
        return n if str(n).startswith("-100") else n
    return value


def parse_since(value: str) -> datetime:
    """'YYYY-MM-DD' veya 'YYYY-MM-DD HH:MM' -> UTC aware datetime (girdi İstanbul saati)."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=TZ).astimezone(timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"--since biçimi anlaşılmadı: {value!r} (YYYY-MM-DD veya 'YYYY-MM-DD HH:MM')"
    )


def sender_name(msg, cache: dict) -> str:
    """Ad + soyad, yoksa kullanıcı adı, yoksa kanal başlığı, yoksa 'Bilinmeyen'."""
    ent = msg.sender
    if ent is None and msg.sender_id is not None:
        ent = cache.get(msg.sender_id)
    if ent is not None:
        first = (getattr(ent, "first_name", None) or "").strip()
        last = (getattr(ent, "last_name", None) or "").strip()
        full = " ".join(p for p in (first, last) if p)
        if full:
            return full
        username = getattr(ent, "username", None)
        if username:
            return f"@{username}"
        title = (getattr(ent, "title", None) or "").strip()
        if title:
            return title
        # Silinmiş hesaplarda first_name/username/title'ın hepsi boştur. Bunları
        # göndereni olmayan mesajlardan ayır; id'yi koru ki farklı kişiler
        # aynı etikete düşüp tek kişi gibi görünmesin.
        if getattr(ent, "deleted", False):
            return f"[silinmiş hesap #{ent.id}]"
    if getattr(msg, "post_author", None):
        return msg.post_author
    return "Bilinmeyen"


def describe_media(msg) -> str:
    """Medya türünü etiketle. Medya İNDİRİLMEZ, yalnız türü yazılır."""
    if msg.photo:
        return "[fotoğraf]"
    if msg.voice:
        return "[sesli mesaj]"
    if msg.video_note:
        return "[yuvarlak video]"
    if msg.gif:
        return "[GIF]"
    if msg.sticker:
        emoji = ""
        for attr in getattr(msg.sticker, "attributes", []):
            emoji = getattr(attr, "alt", "") or emoji
        return f"[çıkartma{': ' + emoji if emoji else ''}]"
    if msg.audio:
        name = getattr(msg.file, "name", None) or ""
        return f"[ses{': ' + name if name else ''}]"
    if msg.video:
        return "[video]"
    if msg.contact:
        return "[kişi]"
    if msg.geo:
        return "[konum]"
    if msg.poll:
        q = getattr(getattr(msg.poll, "poll", None), "question", None)
        q = getattr(q, "text", q) or ""
        return f"[anket{': ' + q if q else ''}]"
    if msg.dice:
        return "[zar]"
    if msg.document:
        name = getattr(msg.file, "name", None) or getattr(msg.file, "mime_type", None) or "adsız"
        return f"[belge: {name}]"
    if msg.media is not None and not msg.web_preview:
        return f"[medya: {type(msg.media).__name__}]"
    return ""


def split_reply(msg) -> tuple[int | None, int | None]:
    """(başlık_id, yanıt_id) döndür.

    Forum gruplarında reply_to alanı İKİ şeyi birden taşır: mesajın hangi
    başlıkta olduğunu ve (varsa) neyi yanıtladığını. Doğrulanmış davranış:
      - forum_topic=True, reply_to_top_id=None -> reply_to_msg_id BAŞLIK id'sidir,
        mesaj bir yanıt DEĞİLDİR.
      - forum_topic=True, reply_to_top_id=N    -> N başlık, reply_to_msg_id gerçek yanıt.
      - forum_topic yok/False                  -> reply_to_msg_id sıradan yanıt.
    """
    r = getattr(msg, "reply_to", None)
    if r is None:
        return None, None
    rid = getattr(r, "reply_to_msg_id", None)
    top = getattr(r, "reply_to_top_id", None)
    if getattr(r, "forum_topic", False):
        return (top, rid) if top else (rid, None)
    return None, rid


def format_message(msg, cache: dict, topics: dict | None = None) -> str:
    """[YYYY-MM-DD HH:MM] #id ↳#reply_id Gönderen: metin  (devam satırları girintili)"""
    ts = msg.date.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    head = f"[{ts}] #{msg.id}"

    topic_id, reply_id = split_reply(msg)
    if topics:
        if topic_id is None:
            # Forumda reply_to taşımayan iki durum var (ölçüldü):
            #  1) başlığın kök mesajı (TopicCreate) -> mesaj id'si = başlık id'si
            #  2) General/"Duyuru" başlığındaki mesajlar -> başlık id'si her zaman 1
            topic_id = msg.id if msg.id in topics else (1 if 1 in topics else None)
        title = topics.get(topic_id)
        if title:
            head += f" [konu: {title}]"
        elif topic_id:
            head += f" [konu #{topic_id}]"
    if reply_id:
        head += f" ↳#{reply_id}"

    if isinstance(msg, MessageService):
        action = type(msg.action).__name__.replace("MessageAction", "")
        return f"{head} {sender_name(msg, cache)}: [sistem: {action}]"

    parts = []
    media = describe_media(msg)
    if media:
        parts.append(media)
    text = (msg.message or "").strip()
    if text:
        parts.append(text)
    if not parts:
        parts.append("[boş mesaj]")
    body = " ".join(parts)  # medya etiketi her zaman ilk satırda kalsın

    lines = body.split("\n")
    out = f"{head} {sender_name(msg, cache)}: {lines[0]}"
    for extra in lines[1:]:
        out += "\n    " + extra
    return out


def state_path_for(output: Path, explicit: str | None) -> Path:
    return Path(explicit) if explicit else output.with_suffix(output.suffix + ".state.json")


def load_state(path: Path, key: str) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key, {})
    except (json.JSONDecodeError, OSError) as exc:
        log(f"UYARI: state okunamadı ({exc}); baştan başlanıyor.")
        return {}


def save_state(path: Path, key: str, data: dict) -> None:
    blob = {}
    if path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            blob = {}
    blob[key] = data
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Telegram işleri (hepsi okuma)
# --------------------------------------------------------------------------- #
async def fetch_topics(client, entity) -> dict:
    """{başlık_id: başlık_adı} — forum değilse boş sözlük. Salt okuma."""
    if not getattr(entity, "forum", False):
        return {}
    topics: dict = {}
    offset_date, offset_id, offset_topic = None, 0, 0
    while True:
        res = await client(
            GetForumTopicsRequest(
                **{_TOPICS_PEER_KW: entity},
                offset_date=offset_date,
                offset_id=offset_id,
                offset_topic=offset_topic,
                limit=100,
            )
        )
        batch = [t for t in res.topics if isinstance(t, ForumTopic)]
        if not res.topics:
            break
        for t in batch:
            topics[t.id] = t.title
        last = res.topics[-1]
        offset_topic = last.id
        offset_id = getattr(last, "top_message", 0) or 0
        offset_date = getattr(last, "date", None)
        if len(res.topics) < 100:
            break
    return topics


async def list_topics(client, entity) -> None:
    if not getattr(entity, "forum", False):
        print("Bu grup bir forum (başlıklı grup) DEĞİL — --topic kullanmana gerek yok.")
        return
    topics = await fetch_topics(client, entity)
    print(f"Forum başlıkları — {getattr(entity, 'title', entity.id)}\n")
    print(f"{'ID':>8}  Başlık")
    print("-" * 60)
    for tid, title in topics.items():
        print(f"{tid:>8}  {title}")
    print(f"\nToplam {len(topics)} başlık. Tek başlığı almak için: --topic <ID>")


async def export(client, entity, args, topics: dict | None = None) -> None:
    output = Path(args.output)
    spath = state_path_for(output, args.state)
    key = f"{getattr(entity, 'id', entity)}:{args.topic or 'all'}"
    state = load_state(spath, key)
    last_id = int(state.get("last_id", 0))
    written_before = int(state.get("count", 0))

    kwargs = {"reverse": True}  # kronolojik: eskiden yeniye
    if args.topic:
        kwargs["reply_to"] = args.topic
    if args.limit:
        kwargs["limit"] = args.limit
    if args.since:
        kwargs["offset_date"] = args.since
    if last_id:
        kwargs["min_id"] = last_id
        kwargs["offset_id"] = last_id
        log(f"Devam: state'teki son mesaj ID {last_id}, yalnız yenileri eklenecek.")
    else:
        log("Baştan dışa aktarım.")

    new_file = not output.exists()
    fh = output.open("a", encoding="utf-8")
    if new_file:
        fh.write(
            f"# Telegram dışa aktarım — {getattr(entity, 'title', entity)} "
            f"(chat {getattr(entity, 'id', entity)}"
            + (f", başlık {args.topic}" if args.topic else "")
            + f") — {datetime.now(TZ):%Y-%m-%d %H:%M %Z}\n\n"
        )

    cache: dict = {}
    count = 0
    # TTY değilse çubuk yerine periyodik log kullan (devre dışı çubuk log'u da susturuyordu)
    use_bar = tqdm is not None and sys.stderr.isatty()
    bar = tqdm(unit=" msg", desc="mesaj") if use_bar else None

    try:
        while True:
            try:
                async for msg in client.iter_messages(entity, **kwargs):
                    if msg.sender_id is not None and msg.sender_id not in cache:
                        cache[msg.sender_id] = msg.sender
                    fh.write(format_message(msg, cache, topics) + "\n")
                    last_id = max(last_id, msg.id)
                    count += 1
                    if bar is not None:
                        bar.update(1)
                    elif count % LOG_EVERY == 0:
                        log(f"{count} mesaj yazıldı (son ID {last_id})")
                    if count % FLUSH_EVERY == 0:
                        fh.flush()
                        os.fsync(fh.fileno())
                        save_state(spath, key, {
                            "last_id": last_id,
                            "count": written_before + count,
                            "updated": datetime.now(TZ).isoformat(timespec="seconds"),
                        })
                break
            except FloodWaitError as exc:
                wait = exc.seconds + 5
                log(f"FloodWait: Telegram {exc.seconds} sn bekleme istedi. {wait} sn uyuyup devam.")
                fh.flush()
                await asyncio.sleep(wait)
                kwargs["min_id"] = last_id
                kwargs["offset_id"] = last_id
                kwargs.pop("offset_date", None)  # devam ederken ID ile ilerliyoruz
                if args.limit:
                    kwargs["limit"] = max(args.limit - count, 1)
                continue
    finally:
        if bar is not None:
            bar.close()
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        save_state(spath, key, {
            "last_id": last_id,
            "count": written_before + count,
            "updated": datetime.now(TZ).isoformat(timespec="seconds"),
        })

    log(f"Bitti: bu koşuda {count} yeni mesaj -> {output} (toplam {written_before + count}, son ID {last_id})")


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Telegram grubundaki mesajları .txt dosyasına aktarır (salt okuma).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Örnekler:\n"
            "  python export_telegram.py --list-topics\n"
            "  python export_telegram.py --limit 20 --output test.txt\n"
            "  python export_telegram.py --topic 1310 --output baslik_1310.txt\n"
            "  python export_telegram.py --since 2026-01-01 --output 2026.txt\n"
        ),
    )
    p.add_argument("--chat", help="Grup: -1001234567890 / @kullaniciadi / t.me linki (yoksa .env CHAT_ID)")
    p.add_argument("--topic", type=int, help="Forum başlık ID'si (yalnız o başlık)")
    p.add_argument("--output", default="telegram_export.txt", help="Çıktı .txt (varsayılan: telegram_export.txt)")
    p.add_argument("--since", type=parse_since, help="Bu tarihten itibaren (YYYY-MM-DD, İstanbul saati)")
    p.add_argument("--limit", type=int, help="En fazla N mesaj (test için)")
    p.add_argument("--list-topics", action="store_true", help="Forum başlıklarını listele ve çık")
    p.add_argument("--state", help="State dosyası (varsayılan: <output>.state.json)")
    p.add_argument("--session", default="telegram_export", help="Session dosya adı (varsayılan: telegram_export)")
    p.add_argument("--logout", action="store_true", help="Oturumu kapat ve session dosyasını sil")
    return p


async def amain() -> int:
    args = build_parser().parse_args()
    # .env'i betiğin yanından oku: hangi dizinden çağrılırsa çağrılsın bulunsun
    load_dotenv(Path(__file__).resolve().with_name(".env"))

    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    if not api_id or not api_hash:
        print(
            "HATA: API_ID / API_HASH bulunamadı.\n"
            "  1) https://my.telegram.org -> API development tools\n"
            "  2) cp .env.example .env  ve değerleri doldur.",
            file=sys.stderr,
        )
        return 2
    try:
        api_id = int(api_id)
    except ValueError:
        print("HATA: API_ID sayı olmalı.", file=sys.stderr)
        return 2

    session_file = Path(f"{args.session}.session")
    client = TelegramClient(args.session, api_id, api_hash)
    await client.start()  # telefon + kod doğrulamasını terminalde sen yaparsın
    secure_file(session_file)

    try:
        if args.logout:
            await client.log_out()
            for p in (session_file, Path(f"{args.session}.session-journal")):
                if p.exists():
                    p.unlink()
                    log(f"Silindi: {p}")
            print("Oturum kapatıldı ve session dosyası silindi.")
            return 0

        me = await client.get_me()
        if getattr(me, "bot", False):
            print(
                "HATA: Bu oturum bir BOT hesabı (@%s).\n"
                "Botlar Telegram'da geçmiş mesajları okuyamaz — MTProto, bot hesapları için\n"
                "GetHistoryRequest'i reddeder (BotMethodInvalidError). Botu gruba eklemek veya\n"
                "yönetici yapmak bunu değiştirmez.\n"
                "Yapılacak: BotFather'da /revoke ile token'ı iptal et, ardından\n"
                "  python export_telegram.py --logout\n"
                "çalıştırıp betiği tekrar başlat ve 'phone' sorusuna KENDİ telefon numaranı\n"
                "(+90...) gir. Grubun üyesi olan hesapla giriş yapman gerekiyor."
                % (me.username or me.id),
                file=sys.stderr,
            )
            return 3

        raw_chat = args.chat or os.getenv("CHAT_ID")
        if not raw_chat:
            print("HATA: --chat verilmedi ve .env içinde CHAT_ID yok.", file=sys.stderr)
            return 2

        entity = await client.get_entity(parse_chat(raw_chat))
        is_forum = bool(getattr(entity, "forum", False))
        log(f"Grup: {getattr(entity, 'title', entity.id)} (id={entity.id}) — forum: {'EVET' if is_forum else 'hayır'}")

        if args.list_topics:
            await list_topics(client, entity)
            return 0

        if args.topic and not is_forum:
            log("UYARI: Bu grup forum değil; --topic yok sayılmayacak ama beklediğin sonucu vermeyebilir.")
        if is_forum and not args.topic:
            log("Forum grubu, --topic verilmedi: TÜM başlıklardaki mesajlar alınacak.")

        topics = None
        if is_forum and not args.topic:
            topics = await fetch_topics(client, entity)
            log(f"{len(topics)} forum başlığı çözüldü; satırlara [konu: ...] eklenecek.")

        await export(client, entity, args, topics)
        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        log("Kullanıcı iptal etti; yazılanlar ve state korundu.")
        sys.exit(130)
